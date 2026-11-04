/*
 * Bit-accurate C model of the CORDIC unit (hw/rtl/fp/fp_cordic.sv), for the
 * SystemVerilog scoreboards.
 *
 * This is a line-by-line port of sw/mathaccel/cordic_model.py, which is the
 * real description of what the hardware computes (that file explains the
 * algorithms). The pyuvm testbenches use the Python model; the xsim UVM
 * environments can't call Python, so they call this instead through DPI-C:
 *   import "DPI-C" function int unsigned cordic_ref_eval(
 *       input int op, input int unsigned a, input int unsigned b,
 *       output int flags);
 * sw/tests/test_cordic_ref.py checks that this port and the Python model give
 * identical results and flags on a large sample of inputs, so a mismatch in
 * xsim points at the RTL, not at the port.
 *
 * It is not an accuracy reference. fp_ref.c (double-precision libm, rounded
 * once) is the independent second opinion that the scoreboards use to check
 * the ULP budget.
 *
 * Widths and tables come from cordic_tables.h, generated from the Python
 * model by tools/gen_cordic.py. Fixed-point values fit in int64_t; the
 * 24 x 96 bit products and the normalize shift need 128 bits, which gcc
 * provides as __int128. Right shifts of negative int64_t values are
 * arithmetic in gcc, the same as Python's >> and SV's >>>.
 */
#include <stdint.h>

#include "cordic_tables.h"

typedef unsigned __int128 u128;
typedef __int128 s128;

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

/* Op codes and flags, matching spec/accel.toml (same values as fp_ref.c). */
enum { C_SIN = 12, C_COS = 13, C_ATAN2 = 14, C_HYPOT = 15, C_EXP = 16, C_LN = 17 };
enum { F_NX = 0x01, F_UF = 0x02, F_OF = 0x04, F_DZ = 0x08, F_NV = 0x10 };

#define CANON_NAN 0x7FC00000u
#define POS_INF   0x7F800000u
#define NEG_INF   0xFF800000u
#define ONE_BITS  0x3F800000u
#define SIGN_BIT  0x80000000u
/* Correctly rounded fp32 constants for the atan2 rule results. */
#define PI_F      0x40490FDBu
#define PIO2_F    0x3FC90FDBu
#define PIO4_F    0x3F490FDBu
#define P3O4_F    0x4016CBE4u

/* Stage modes, as in the Python model. */
enum { ROT, VEC, LIN, HROT, HVEC };

/* Inspect encodings directly: converting an sNaN to a host float could
 * quiet it before the model has decided whether NV must be raised. */
static int c_is_nan(uint32_t u)  { return (u & 0x7F800000u) == 0x7F800000u && (u & 0x7FFFFFu); }
static int c_is_snan(uint32_t u) { return c_is_nan(u) && !(u & 0x400000u); }
static int c_is_inf(uint32_t u)  { return (u & 0x7FFFFFFFu) == 0x7F800000u; }
static int c_is_zero(uint32_t u) { return (u & 0x7FFFFFFFu) == 0; }

/* A generated ROM entry is stored as high/low 64-bit halves. Reassemble
 * it before the wide product so the upper argument-reduction bits survive. */
static u128 rom128(const uint64_t e[2]) { return ((u128)e[0] << 64) | e[1]; }

/* Leading zeros of a width-bit value (width when v is 0). */
static int lzc(uint64_t v, int width) {
    int n = 0;
    for (int i = width - 1; i >= 0 && !((v >> i) & 1); i--) n++;
    return n;
}

/* N CORDIC steps in one mode (see cordic_run in the Python model). */
static void cordic_run(int mode, int64_t *xp, int64_t *yp, int64_t *zp, int zneg) {
    int64_t x = *xp, y = *yp, z = *zp;
    int hyp = (mode == HROT || mode == HVEC);
    for (int i = 0; i < CN; i++) {
        int s = hyp ? HSHIFT[i] : i;
        int ccw = (mode == ROT || mode == HROT) ? (z >= 0) : (y < 0);
        int64_t c;
        switch (mode) {
        case ROT:  c = ATAN_Q[i]; break;
        case VEC:  c = ATAN_R[i]; break;
        case LIN:  c = (int64_t)1 << (CZ - i); break;
        case HROT: c = ATANH_L[i]; break;
        default:   c = ATANH_N[i]; break;
        }
        int64_t ystep = (mode == LIN) ? 0 : (y >> s);
        int64_t xstep = x >> s;
        int xsub = ccw != hyp;
        x = xsub ? x - ystep : x + ystep;
        y = ccw ? y + xstep : y - xstep;
        z = (ccw != zneg) ? z - c : z + c;
    }
    *xp = x; *yp = y; *zp = z;
}

/* (biased exponent, 26-bit sig) of a positive CORDIC output with CW fraction bits. */
static void normalize_cordic(int64_t v, int ebase, int *exp, uint32_t *sig) {
    int lz = lzc(((uint64_t)v >> (CW - 17)) & 0xFFFFFF, 24);
    u128 sh = ((u128)(uint64_t)v << lz) & (((u128)1 << (CW + 31)) - 1);
    uint32_t keep = (uint32_t)((sh >> (CW - 18)) & ((1u << 25) - 1));
    int sticky = (sh & (((u128)1 << (CW - 18)) - 1)) != 0;
    *sig = (keep << 1) | (uint32_t)sticky;
    *exp = ebase + 6 - lz;
}

/* Round sig * 2^(exp - 152) to fp32 (round_fraction in fp32ref.py) with the
 * phase 3 flags: NX always, UF when the result is tiny (after rounding) or
 * ends up below 2^-126, OF on overflow. sig bit 25 is the leading 1. */
static uint32_t pack(int sign, int exp, uint32_t sig, int *flags) {
    uint32_t s = sign ? SIGN_BIT : 0;
    int f = F_NX;
    /* Tininess after rounding: the value rounded to 24 bits with an unbounded
       exponent is below 2^-126. With the leading 1 at exponent exp, that is
       always true for exp < 0, and for exp == 0 unless rounding carries the
       24 ones up to 2^-126. */
    int round_up24 = ((sig >> 1) & 1) && ((sig & 1) || ((sig >> 2) & 1));
    int tiny = exp < 0 || (exp == 0 && !((sig >> 2) == 0xFFFFFFu && round_up24));
    uint64_t mant;
    int guard, sticky;
    if (exp >= 1) {
        mant = sig >> 2;
        guard = (sig >> 1) & 1;
        sticky = sig & 1;
    } else {
        /* Subnormal: shift right by 1 - exp more bits, keeping a sticky bit. */
        int sh = 1 - exp;
        uint64_t full = sig;
        if (sh > 40) {
            mant = 0; guard = 0; sticky = full != 0;
        } else {
            mant = full >> (2 + sh);
            guard = (full >> (1 + sh)) & 1;
            sticky = (full & (((uint64_t)1 << (1 + sh)) - 1)) != 0;
        }
    }
    int inexact = guard || sticky;
    if (guard && (sticky || (mant & 1))) mant++;
    uint32_t bits;
    if (exp >= 1) {
        int e = exp;
        if (mant >> 24) { mant >>= 1; e++; }
        if (e >= 255) {
            *flags = F_OF | F_NX;
            return s | POS_INF;
        }
        bits = ((uint32_t)e << 23) | (uint32_t)(mant & 0x7FFFFF);
    } else {
        bits = (uint32_t)mant;     /* a carry into bit 23 makes it 2^-126 */
    }
    if (tiny && inexact) f |= F_UF;
    if (bits < 0x00800000u) f |= F_UF;
    *flags = f;
    return s | bits;
}

/* sin t = t for f' < 2^-SMALL_T: (biased exponent, 26-bit sig). */
static void small_sin(u128 fp, int *exp, uint32_t *sig) {
    int top = PH_F - SMALL_T - 1;
    uint64_t win = (uint64_t)(fp >> (top - 63));
    int lz = lzc(win >> 32, 32);
    uint64_t m32 = ((win << lz) >> 32) & 0xFFFFFFFFull;
    uint64_t prod = m32 * PIO2_C;
    if (prod >> 63) {
        *sig = (uint32_t)((prod >> 39) << 1) | (uint32_t)((prod & (((uint64_t)1 << 39) - 1)) != 0);
        *exp = 127 + (63 + 32 - lz + top - 63 - PH_F - 31);
    } else {
        *sig = (uint32_t)((prod >> 38) << 1) | (uint32_t)((prod & (((uint64_t)1 << 38) - 1)) != 0);
        *exp = 127 + (62 + 32 - lz + top - 63 - PH_F - 31);
    }
}

static uint32_t sincos(int op, uint32_t a, int *flags) {
    if (c_is_nan(a)) { *flags = c_is_snan(a) ? F_NV : 0; return CANON_NAN; }
    if (c_is_inf(a)) { *flags = F_NV; return CANON_NAN; }
    if (c_is_zero(a)) { *flags = 0; return op == C_SIN ? a : ONE_BITS; }
    int sign = a >> 31;
    int e = (a >> 23) & 0xFF;
    if (e < TINY_E) {
        if (op == C_SIN) { *flags = F_NX | (e == 0 ? F_UF : 0); return a; }
        *flags = F_NX;
        return ONE_BITS;
    }
    /* Payne-Hanek: m times the window, modulo 2^PH_L. */
    uint64_t m = (a & 0x7FFFFF) | 0x800000;
    u128 prod = (u128)m * rom128(PH_ROM[e - TINY_E]);
    prod &= ((u128)1 << PH_L) - 1;
    int q = (int)(prod >> PH_F);
    u128 f = prod & (((u128)1 << PH_F) - 1);
    int swap = (int)(f >> (PH_F - 1)) & 1;
    u128 fp = swap ? (((u128)1 << PH_F) - f) : f;
    int is_small = (fp >> (PH_F - SMALL_T)) == 0;
    int64_t x = CORDIC_K, y = 0, z = (int64_t)(fp >> (PH_F - CZ));
    cordic_run(ROT, &x, &y, &z, 0);

    int want_cos = (op == C_SIN) ? (q & 1) : 1 - (q & 1);
    int take_x = want_cos ^ swap;
    int neg = (op == C_SIN) ? (((q >> 1) & 1) ^ sign) : (q == 1 || q == 2);
    int exp;
    uint32_t sig;
    if (take_x)        normalize_cordic(x, 127, &exp, &sig);
    else if (is_small) small_sin(fp, &exp, &sig);
    else               normalize_cordic(y, 127, &exp, &sig);
    return pack(neg, exp, sig, flags);
}

/* Rule results for ATAN2(a=y, b=x) and HYPOT(a, b); returns 0 when there is none. */
static int vec_special(int op, uint32_t a, uint32_t b, uint32_t *res, int *flags) {
    int snan = c_is_snan(a) || c_is_snan(b);
    if (op == C_HYPOT) {
        if (c_is_inf(a) || c_is_inf(b)) { *res = POS_INF; *flags = snan ? F_NV : 0; return 1; }
        if (c_is_nan(a) || c_is_nan(b)) { *res = CANON_NAN; *flags = snan ? F_NV : 0; return 1; }
        if (c_is_zero(b)) { *res = a & ~SIGN_BIT; *flags = 0; return 1; }
        if (c_is_zero(a)) { *res = b & ~SIGN_BIT; *flags = 0; return 1; }
        return 0;
    }
    uint32_t y = a, x = b;
    if (c_is_nan(y) || c_is_nan(x)) { *res = CANON_NAN; *flags = snan ? F_NV : 0; return 1; }
    uint32_t ys = y & SIGN_BIT;
    int xneg = (x & SIGN_BIT) != 0;
    if (c_is_inf(y)) {
        *res = ys | (c_is_inf(x) ? (xneg ? P3O4_F : PIO4_F) : PIO2_F);
        *flags = F_NX;
        return 1;
    }
    if (c_is_zero(y) || c_is_inf(x)) {
        if (xneg) { *res = ys | PI_F; *flags = F_NX; }
        else      { *res = ys; *flags = 0; }
        return 1;
    }
    if (c_is_zero(x)) { *res = ys | PIO2_F; *flags = F_NX; return 1; }
    return 0;
}

/* 24-bit significand with its leading 1 at bit 23, and exponent (below 1 for subnormals). */
static void unpack_norm(uint32_t bits, uint64_t *m, int *e) {
    int ex = (bits >> 23) & 0xFF;
    uint64_t mm = (bits & 0x7FFFFF) | (ex ? 0x800000 : 0);
    int lz = lzc(mm, 24);
    *m = (mm << lz) & 0xFFFFFF;
    *e = (ex ? ex : 1) - lz;
}

static uint32_t vector(int op, uint32_t a, uint32_t b, int *flags) {
    uint32_t res;
    if (vec_special(op, a, b, &res, flags)) return res;
    uint64_t mu, mw, mb, ms;
    int eu, ew, eb, es;
    unpack_norm(a, &mu, &eu);
    unpack_norm(b, &mw, &ew);
    int swap = (eu > ew) || (eu == ew && mu > mw);     /* |a| > |b| */
    if (swap) { mb = mu; eb = eu; ms = mw; es = ew; }
    else      { mb = mw; eb = ew; ms = mu; es = eu; }
    int d = eb - es;
    int xneg = b >> 31;
    int lin = op == C_ATAN2 && !swap && !xneg && d >= ATAN_D;
    int64_t x0 = (int64_t)((mb * KV) >> (56 - CW));
    int64_t y0 = (int64_t)((ms * KV) >> (56 - CW));
    if (!lin) y0 >>= (d < 63 ? d : 63);
    int64_t z0 = 0;
    int exp, zneg = 0;
    uint32_t sig;
    if (op == C_HYPOT) {
        cordic_run(VEC, &x0, &y0, &z0, 0);
        normalize_cordic(x0, eb + 1, &exp, &sig);
        return pack(0, exp, sig, flags);
    }
    if (lin) {
        cordic_run(LIN, &x0, &y0, &z0, 0);
        normalize_cordic(z0, 127 - d, &exp, &sig);
    } else {
        if (swap && !xneg)      { z0 = PIO2_Z; zneg = 1; }
        else if (!swap && xneg) { z0 = PI_Z;   zneg = 1; }
        else if (swap && xneg)  { z0 = PIO2_Z; zneg = 0; }
        cordic_run(VEC, &x0, &y0, &z0, zneg);
        normalize_cordic(z0, 127, &exp, &sig);
    }
    return pack(a >> 31, exp, sig, flags);
}

static uint32_t exp_op(uint32_t a, int *flags) {
    if (c_is_nan(a)) { *flags = c_is_snan(a) ? F_NV : 0; return CANON_NAN; }
    if (c_is_inf(a)) { *flags = 0; return (a >> 31) ? 0 : POS_INF; }
    if (c_is_zero(a)) { *flags = 0; return ONE_BITS; }
    int sign = a >> 31, e = (a >> 23) & 0xFF;
    if (e < EXP_TINY_E) { *flags = F_NX; return ONE_BITS; }
    if (e >= EXP_BIG_E) {
        if (sign) { *flags = F_UF | F_NX; return 0; }
        *flags = F_OF | F_NX;
        return POS_INF;
    }
    uint64_t m = (a & 0x7FFFFF) | 0x800000;
    u128 prod = (u128)m * rom128(EXP_ROM[e - EXP_TINY_E]);     /* |x|/ln2, 86 fraction bits */
    int64_t kmag = (int64_t)(prod >> 86) + (int64_t)((prod >> 85) & 1);
    s128 f = (s128)(prod & (((u128)1 << 86) - 1));
    if ((f >> 85) & 1) f -= (s128)1 << 86;
    int64_t k = sign ? -kmag : kmag;
    if (sign) f = -f;
    int64_t x = HYP_G2, y = HYP_G2, z = (int64_t)(f >> (86 - CZ));
    cordic_run(HROT, &x, &y, &z, 0);
    int exp;
    uint32_t sig;
    normalize_cordic(x, (int)(128 + k), &exp, &sig);
    return pack(0, exp, sig, flags);
}

static uint32_t ln_op(uint32_t a, int *flags) {
    if (c_is_nan(a)) { *flags = c_is_snan(a) ? F_NV : 0; return CANON_NAN; }
    if (c_is_zero(a)) { *flags = F_DZ; return NEG_INF; }
    if (a >> 31) { *flags = F_NV; return CANON_NAN; }
    if (c_is_inf(a)) { *flags = 0; return POS_INF; }
    if (a == ONE_BITS) { *flags = 0; return 0; }
    int e = (a >> 23) & 0xFF;
    int64_t x0, y0, z = 0;
    int exp;
    uint32_t sig;
    int64_t dmag = -1;
    int below = 0;
    if (e == 127 && (a & 0x7FFFFF) < (1u << (23 - LN_T))) {
        dmag = a & 0x7FFFFF;
        below = 0;
    } else if (e == 126 && (1 << 24) - (int64_t)((a & 0x7FFFFF) | 0x800000) < (1 << (24 - LN_T))) {
        dmag = (1 << 24) - (int64_t)((a & 0x7FFFFF) | 0x800000);
        below = 1;
    }
    if (dmag >= 0) {
        /* Near 1: linear mode divides normalized d by (1 + x). */
        int lz = lzc((uint64_t)dmag, 24);
        int64_t dn = dmag << lz;
        int s = lz + below;
        y0 = dn << (CW - 25);
        x0 = below ? ((int64_t)1 << (CW - 1)) - (dmag << (CW - 26))
                   : ((int64_t)1 << (CW - 1)) + (dmag << (CW - 25));
        cordic_run(LIN, &x0, &y0, &z, 0);
        normalize_cordic(z, 128 - s, &exp, &sig);
        return pack(below, exp, sig, flags);
    }
    uint64_t mn;
    int en;
    unpack_norm(a, &mn, &en);
    int big = mn >= SQRT2_M;
    int ep = en - 127 + big;
    int64_t msh = big ? (int64_t)(mn << (CW - 26)) : (int64_t)(mn << (CW - 25));
    x0 = msh + ((int64_t)1 << (CW - 2));
    y0 = msh - ((int64_t)1 << (CW - 2));
    below = en < 127;
    z = ((int64_t)(ep < 0 ? -ep : ep) * LN2H) >> 8;
    cordic_run(HVEC, &x0, &y0, &z, below);
    normalize_cordic(z, 128, &exp, &sig);
    return pack(below, exp, sig, flags);
}

/* Model entry point. Op codes other than 12-17 return NaN with NV. */
EXPORT uint32_t cordic_ref_eval(int op, uint32_t a, uint32_t b, int *flags) {
    switch (op) {
    case C_SIN: case C_COS:     return sincos(op, a, flags);
    case C_ATAN2: case C_HYPOT: return vector(op, a, b, flags);
    case C_EXP:                 return exp_op(a, flags);
    case C_LN:                  return ln_op(a, flags);
    default:
        *flags = F_NV;
        return CANON_NAN;
    }
}

/* A whole array per call, for the Python cross-check (fast through ctypes). */
EXPORT void cordic_ref_eval_many(int op, int n, const uint32_t *a, const uint32_t *b,
                                 uint32_t *res, int *flags) {
    for (int i = 0; i < n; i++) res[i] = cordic_ref_eval(op, a[i], b[i], &flags[i]);
}
