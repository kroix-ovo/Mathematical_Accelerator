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
 */
#include <fenv.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

/* Op codes, matching [fpu_ops] in spec/accel.toml. */
enum { OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_SQRT, OP_I2F, OP_F2I,
       OP_MIN, OP_MAX, OP_EQ, OP_LT, OP_LE };

/* Flag masks, matching [fpu_flags] in spec/accel.toml. */
enum { F_NX = 0x01, F_UF = 0x02, F_OF = 0x04, F_DZ = 0x08, F_NV = 0x10 };

#define CANON_NAN 0x7FC00000u

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

    default: *flags = F_NV; return CANON_NAN;
    }

    f = read_flags();
    out = as_bits(r);
    if (is_nan_bits(out)) out = CANON_NAN;   /* canonical NaN rule */
    *flags = f;
    return out;
}
