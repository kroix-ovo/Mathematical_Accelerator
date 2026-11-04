/*
 * C reference model for the FPU, called from the SystemVerilog UVM
 * scoreboard through DPI-C ("Direct Programming Interface": SV code can call
 * a C function as if it were an SV function).
 *
 * Why C: the arithmetic here is done by the host CPU's own IEEE-754
 * single-precision hardware (SSE on x86-64), and the exception flags come
 * from the CPU's status register through <fenv.h>. That gives an answer that
 * shares no code with my RTL or with the Python model in fp32ref.py.
 *
 * The SV side declares:
 *   import "DPI-C" function int unsigned fp_ref_eval(
 *       input int op, input int unsigned a, input int unsigned b,
 *       output int flags);
 * which maps to the plain C signature below, so this file also builds as an
 * ordinary shared library for the Python cross-check test.
 *
 * Arithmetic results and their flags (ADD..I2F) come from the CPU. The
 * compare flags and the negative-sqrt case are written out as rules,
 * because older compilers and C libraries get them wrong (details inline).
 *
 * Rules on top of the raw hardware result (see docs/numerics.md):
 *   - every NaN result becomes the canonical quiet NaN 0x7FC00000;
 *   - flags use RISC-V fflags order: NV=0x10 DZ=0x08 OF=0x04 UF=0x02 NX=0x01;
 *   - F2I saturates like RISC-V fcvt.w.s; MIN/MAX follow RISC-V fmin/fmax.
 *
 * Phase 3 ops (SIN .. LN) are a different kind of reference. Their results are
 * irrational, so there is no bit-exact answer to compare against, only a
 * correctly rounded one (sw/mathaccel/mpref.py, mpmath at 256 bits) and an
 * error budget in ULPs. Here I compute each function in double precision with
 * the C library and round the double to float once. A good libm is within
 * about 1 double ULP, which is 2^-29 of a float ULP, so the float result is
 * the correctly rounded one except when the true value lies within about
 * 2^-29 float ULP of a halfway point. That is rare, and it makes this a fast
 * second opinion: the PC can screen millions of hardware results with it and
 * recheck only the suspicious ones with mpmath. The flags are written as
 * rules (the same list as mpref.py), not read from the CPU, because the CPU
 * would report flags for the double computation, not for the float result.
 */
#include <fenv.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

/* Op codes, matching [fpu_ops] in spec/accel.toml. */
enum { OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_SQRT, OP_I2F, OP_F2I,
       OP_MIN, OP_MAX, OP_EQ, OP_LT, OP_LE,
       OP_SIN, OP_COS, OP_ATAN2, OP_HYPOT, OP_EXP, OP_LN };

/* Flag masks, matching [fpu_flags] in spec/accel.toml. */
enum { F_NX = 0x01, F_UF = 0x02, F_OF = 0x04, F_DZ = 0x08, F_NV = 0x10 };

#define CANON_NAN 0x7FC00000u
#define POS_INF   0x7F800000u
#define NEG_INF   0xFF800000u
#define ONE_BITS  0x3F800000u
#define SIGN_BIT  0x80000000u

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

/* Reinterpret bits as a float and back. memcpy is the portable way to do
 * this in C without breaking the compiler's aliasing rules. */
static float as_float(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static uint32_t as_bits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

static int is_nan_bits(uint32_t u)  { return (u & 0x7F800000u) == 0x7F800000u && (u & 0x7FFFFFu); }
static int is_snan_bits(uint32_t u) { return is_nan_bits(u) && !(u & 0x400000u); }
static int is_inf_bits(uint32_t u)  { return (u & 0x7FFFFFFFu) == 0x7F800000u; }
static int is_zero_bits(uint32_t u) { return (u & 0x7FFFFFFFu) == 0; }

/* Canonical NaN with NV only for a signaling NaN input. */
static uint32_t nan_result(uint32_t a, uint32_t b, int *flags) {
    *flags = (is_snan_bits(a) || is_snan_bits(b)) ? F_NV : 0;
    return CANON_NAN;
}

/* Round a double result to float and attach the phase 3 flags: NX always
 * (the exact cases never get here), OF when the float overflowed to infinity,
 * UF when the result is tiny after rounding. "Tiny after rounding" means the
 * value, rounded to 24 bits with no exponent limit, is below 2^-126. Values
 * just under 2^-126 sit on a 24-bit grid of spacing 2^-150, so that rounded
 * value stays below 2^-126 exactly when |d| < 2^-126 - 2^-151 (at the halfway
 * point itself, ties to even rounds up to 2^-126). Both constants are exact
 * in double. */
static uint32_t round_result(double d, int *flags) {
    volatile float r = (float)d;            /* the one rounding, RNE */
    uint32_t out = as_bits(r);
    int f = F_NX;
    if (is_inf_bits(out)) f |= F_OF;
    if (fabs(d) < ldexp(1.0, -126) - ldexp(1.0, -151)) f |= F_UF;
    *flags = f;
    return out;
}

/* ---- sin/cos argument reduction ---------------------------------------
 * The C library can't be trusted with large arguments: MinGW's sin() (the one
 * xsim's DPI code links against) gives sin(3.4e38) = 0.99998 instead of
 * -0.52188, because it reduces x modulo pi/2 with too few bits of pi. So I
 * reduce the argument myself, with integer arithmetic, and only hand the
 * library an angle in [0, pi/4], where it is accurate.
 *
 * A float is x = M * 2^E with a 24-bit integer M. Then
 *     x * (2/pi) = M * 2^E * (0.b1 b2 b3 ... in binary)
 * Bits b_i with i <= E-2 multiply M into multiples of 4, which are whole
 * turns and can be dropped. So I take 128 bits of 2/pi starting at bit E-1,
 * multiply by M modulo 2^128, and read the result as a fixed-point number
 * with 126 fraction bits: the top two bits are the quadrant q (x mod 2pi in
 * quarter turns) and the rest is the fraction f of a quarter turn. The bits
 * of 2/pi left out add less than 2^24 * 2^-126 = 2^-102 to f. The smallest f
 * any float produces is about 2^-30 (see tools/ph_worst_case.py), so f is
 * good to about 2^-72 relative, far beyond double precision.
 *
 * TWO_OVER_PI holds 2/pi's fraction bits from b1, preceded by two zero words
 * so that windows starting before b1 (small x, E-1 < 1) read zeros there. */
static const uint32_t TWO_OVER_PI[12] = {
    0u, 0u,
    0xA2F9836Eu, 0x4E441529u, 0xFC2757D1u, 0xF534DDC0u, 0xDB629599u,
    0x3C439041u, 0xFE5163ABu, 0xDEBBC561u, 0xB7246E3Au, 0x424DD2E0u };

typedef unsigned __int128 u128;

/* 128 bits of 2/pi starting at fraction bit b_s (s may be zero or negative). */
static u128 two_over_pi_window(int s) {
    int pos = s - 1 + 64;           /* bit position counting from TWO_OVER_PI[0] */
    int w = pos >> 5, r = pos & 31;
    u128 hi = ((u128)TWO_OVER_PI[w] << 96) | ((u128)TWO_OVER_PI[w + 1] << 64) |
              ((u128)TWO_OVER_PI[w + 2] << 32) | TWO_OVER_PI[w + 3];
    /* Shift left by r and fill the bottom r bits from the next word. */
    return r ? (hi << r) | (TWO_OVER_PI[w + 4] >> (32 - r)) : hi;
}

/* sin or cos of a finite, nonzero float, in double precision. */
static double sincos_ref(int want_cos, uint32_t a) {
    const double pio2 = 1.57079632679489661923;
    uint32_t e = (a >> 23) & 0xFF;
    int neg = (a >> 31) != 0;
    double x = (double)as_float(a);
    if (e < 126) {
        /* |x| < 0.5: no reduction needed, the library is accurate here. */
        return want_cos ? cos(x) : sin(x);
    }
    uint32_t m = (a & 0x7FFFFFu) | 0x800000u;     /* e >= 126, so normal */
    int E = (int)e - 150;
    u128 p = (u128)m * two_over_pi_window(E - 1); /* wraps modulo 2^128 */
    int q = (int)(p >> 126);
    u128 f = p & (((u128)1 << 126) - 1);
    /* Fold f into [0, 1/2] so the angle is at most pi/4: past half a quarter
     * turn, sin and cos swap roles (sin(pi/2 - t) = cos t). */
    int swap = (f >> 125) != 0;
    if (swap) f = ((u128)1 << 126) - f;
    double t = ldexp((double)f, -126) * pio2;      /* the reduced angle in radians */
    /* Which function of t, and which sign, for |x| in quadrant q:
     *   sin|x|: q=0  sin t, q=1  cos t, q=2 -sin t, q=3 -cos t
     *   cos|x|: q=0  cos t, q=1 -sin t, q=2 -cos t, q=3  sin t
     * (t here is the unfolded fraction; the fold swaps sin and cos once more). */
    int use_cos = (want_cos ? !(q & 1) : (q & 1)) ^ swap;
    double v = use_cos ? cos(t) : sin(t);
    int negate = want_cos ? (q == 1 || q == 2) : ((q >> 1) ^ neg);
    return negate ? -v : v;
}

/* Phase 3 functions: special values by rule (the same table as mpref.py),
 * everything else through the double-precision C library. */
static uint32_t transcend(int op, uint32_t a, uint32_t b, int *flags) {
    double x = (double)as_float(a), y = (double)as_float(b);
    const double pi = 3.14159265358979323846;
    *flags = 0;
    switch (op) {
    case OP_SIN:
    case OP_COS:
        if (is_nan_bits(a)) return nan_result(a, 0, flags);
        if (is_inf_bits(a)) { *flags = F_NV; return CANON_NAN; }
        if (is_zero_bits(a)) return (op == OP_SIN) ? a : ONE_BITS;   /* exact */
        return round_result(sincos_ref(op == OP_COS, a), flags);
    case OP_EXP:
        if (is_nan_bits(a)) return nan_result(a, 0, flags);
        if (is_inf_bits(a)) return (a & SIGN_BIT) ? 0u : POS_INF;    /* exact in the limit */
        if (is_zero_bits(a)) return ONE_BITS;
        return round_result(exp(x), flags);
    case OP_LN:
        if (is_nan_bits(a)) return nan_result(a, 0, flags);
        if (is_zero_bits(a)) { *flags = F_DZ; return NEG_INF; }
        if (a & SIGN_BIT) { *flags = F_NV; return CANON_NAN; }
        if (is_inf_bits(a)) return POS_INF;
        if (a == ONE_BITS) return 0u;
        return round_result(log(x), flags);
    case OP_ATAN2:
        /* a is y and b is x, as in C's atan2(y, x). */
        if (is_nan_bits(a) || is_nan_bits(b)) return nan_result(a, b, flags);
        /* +-0 against x = +0 or x > 0, and finite y against x = +inf: exact +-0. */
        if (!(b & SIGN_BIT) && (is_zero_bits(a) || (is_inf_bits(b) && !is_inf_bits(a))))
            return a & SIGN_BIT;
        /* The signed-zero and infinity cases follow C99 Annex F. I write them
         * out instead of trusting libm with signed zeros and infinities. */
        if (is_inf_bits(a) || is_zero_bits(a) || is_inf_bits(b) || is_zero_bits(b)) {
            double v;
            if (is_inf_bits(a))       v = is_inf_bits(b) ? ((b & SIGN_BIT) ? 0.75 * pi : 0.25 * pi) : 0.5 * pi;
            else if (is_zero_bits(a)) v = pi;          /* x is -0 or negative here */
            else if (is_inf_bits(b))  v = pi;          /* x = -inf */
            else                      v = 0.5 * pi;    /* x = +-0 */
            return round_result((a & SIGN_BIT) ? -v : v, flags);
        }
        return round_result(atan2(x, y), flags);
    case OP_HYPOT:
        /* An infinite input wins even over a NaN; an sNaN still raises NV. */
        if (is_inf_bits(a) || is_inf_bits(b)) {
            *flags = (is_snan_bits(a) || is_snan_bits(b)) ? F_NV : 0;
            return POS_INF;
        }
        if (is_nan_bits(a) || is_nan_bits(b)) return nan_result(a, b, flags);
        if (is_zero_bits(b)) return a & ~SIGN_BIT;
        if (is_zero_bits(a)) return b & ~SIGN_BIT;
        /* Squares of floats fit in double without overflow (at most about
         * 1.2e77) and are exact (48 significant bits). The sum rounds only
         * when the exponents differ a lot, where it can't matter. */
        return round_result(sqrt(x * x + y * y), flags);
    }
    *flags = F_NV;
    return CANON_NAN;
}

/* Translate the CPU's sticky exception bits into my flag mask. */
static int read_flags(void) {
    int e = fetestexcept(FE_ALL_EXCEPT), f = 0;
    if (e & FE_INEXACT)   f |= F_NX;
    if (e & FE_UNDERFLOW) f |= F_UF;
    if (e & FE_OVERFLOW)  f |= F_OF;
    if (e & FE_DIVBYZERO) f |= F_DZ;
    if (e & FE_INVALID)   f |= F_NV;
    return f;
}

EXPORT uint32_t fp_ref_eval(int op, uint32_t a, uint32_t b, int *flags) {
    /* volatile stops the compiler from folding the operation at compile time
     * or reordering it around the flag reads; the CPU must really do it. */
    volatile float x = as_float(a), y = as_float(b), r = 0.0f;
    uint32_t out;
    int f = 0;

    fesetround(FE_TONEAREST);       /* RNE is the only mode the FPU supports */
    feclearexcept(FE_ALL_EXCEPT);   /* flags are sticky; start from zero      */

    switch (op) {
    case OP_ADD:  r = x + y; break;
    case OP_SUB:  r = x - y; break;
    case OP_MUL:  r = x * y; break;
    case OP_DIV:  r = x / y; break;
    case OP_SQRT:
        /* The IEEE rule for negative inputs is stated here rather than left
         * to the C library: MinGW's libm (used by xsim's xsc at -O0) returns
         * a negative input unchanged without raising invalid. -0 stays -0. */
        if (!is_nan_bits(a) && (a >> 31) && (a & 0x7FFFFFFFu)) {
            *flags = F_NV;
            return CANON_NAN;
        }
        r = sqrtf(x);
        break;
    case OP_I2F:  r = (float)(int32_t)a; break;   /* raises NX when rounded */

    case OP_F2I: {
        /* RISC-V fcvt.w.s: NaN and +too-big saturate to INT_MAX, -too-big to
         * INT_MIN, both with only NV. In range: round to nearest even. */
        if (is_nan_bits(a)) { *flags = F_NV; return 0x7FFFFFFFu; }
        float n = nearbyintf(x);                 /* RNE, never sets NX itself */
        if (n >= 2147483648.0f)  { *flags = F_NV; return 0x7FFFFFFFu; }
        if (n < -2147483648.0f)  { *flags = F_NV; return 0x80000000u; }
        *flags = (n != x) ? F_NX : 0;
        return (uint32_t)(int32_t)n;
    }

    case OP_MIN:
    case OP_MAX: {
        /* A NaN operand loses to a number; two NaNs give the canonical NaN.
         * Only a signaling NaN raises NV. -0 counts as smaller than +0. */
        f = (is_snan_bits(a) || is_snan_bits(b)) ? F_NV : 0;
        if (is_nan_bits(a) && is_nan_bits(b)) out = CANON_NAN;
        else if (is_nan_bits(a)) out = b;
        else if (is_nan_bits(b)) out = a;
        else {
            int a_less = (x < y) || (x == y && (a >> 31) && !(b >> 31));
            out = (op == OP_MIN) ? (a_less ? a : b) : (a_less ? b : a);
        }
        *flags = f;
        return out;
    }

    /* Compare results are the integers 0/1; any NaN makes them false.
     * The invalid flag is decided by rule, not read from the CPU: gcc before
     * version 8 (xsc uses 6.2) compiles < and <= to the quiet compare
     * instruction, which does not raise invalid for a quiet NaN.
     *   EQ  quiet:     NV only for a signaling NaN input
     *   LT, LE signaling: NV for any NaN input (RISC-V flt / fle) */
    case OP_EQ:
        *flags = (is_snan_bits(a) || is_snan_bits(b)) ? F_NV : 0;
        return (uint32_t)(x == y);
    case OP_LT:
        *flags = (is_nan_bits(a) || is_nan_bits(b)) ? F_NV : 0;
        return (uint32_t)(x < y);
    case OP_LE:
        *flags = (is_nan_bits(a) || is_nan_bits(b)) ? F_NV : 0;
        return (uint32_t)(x <= y);

    case OP_SIN: case OP_COS: case OP_ATAN2: case OP_HYPOT: case OP_EXP: case OP_LN:
        return transcend(op, a, b, flags);

    default: *flags = F_NV; return CANON_NAN;
    }

    f = read_flags();
    out = as_bits(r);
    if (is_nan_bits(out)) out = CANON_NAN;   /* canonical NaN rule */
    *flags = f;
    return out;
}

/* Evaluate n operations in one call. The DPI scoreboard never uses this; it is
 * for Python (ctypes), where one call per element would cost far more than the
 * arithmetic when screening millions of hardware results. */
EXPORT void fp_ref_eval_many(int op, int n, const uint32_t *a, const uint32_t *b,
                             uint32_t *r, int *flags) {
    for (int i = 0; i < n; i++)
        r[i] = fp_ref_eval(op, a[i], b ? b[i] : 0u, &flags[i]);
}
