/* I use this independent, scalar interpreter to grade whole EVU runs.
 * I decode the scratchpad image, check it before execution, and record
 * every output write. I keep the exact sum in integer limbs so the
 * scoreboard checks the same numerical contract without copying RTL. */
/*
 * C port of what the expression unit computes, for the SystemVerilog top-level
 * scoreboard. It does the job of check_descriptor and read_descriptor in
 * sw/mathaccel/compiler/evu_isa.py and of execute and cycle_bounds in
 * sw/mathaccel/evu_model.py. sw/tests/test_evu_ref.py requires this file and the
 * Python model to agree on every status, result and flag.
 *
 * The two are written differently on purpose. Python runs one instruction across
 * every point as arrays. Here I run one point at a time through all its
 * instructions, which is how a programmer would read the instruction set. If they
 * disagree, one of them has misunderstood docs/protocol.md.
 *
 * Each FPU op goes through the same references as the FPU kernel's scoreboard:
 * fp_ref_eval for ops 0-11 and cordic_ref_eval for ops 12-17.
 *
 * DPI calls here pass one number at a time, so there's no shared array to set up
 * on the SV side. A run goes:
 *   evu_ref_clear();  evu_ref_poke(addr, word) for every nonzero scratchpad word;
 *   status = evu_ref_run(desc, &aux, &low, &high);
 *   evu_ref_write_addr(i), evu_ref_write_word(i) for i < evu_ref_writes().
 * The writes are the words the run put in the scratchpad, in order.
 */
#include <stdint.h>
#include <string.h>

/* Windows only exports what's marked, so the Python cross-check needs this. */
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

extern uint32_t fp_ref_eval(int op, uint32_t a, uint32_t b, int *flags);
extern uint32_t cordic_ref_eval(int op, uint32_t a, uint32_t b, int *flags);

/* These match spec/accel.toml. test_evu_ref.py checks them against it. */
enum {
    WORDS = 65536, DESC_WORDS = 16, PROGRAM_LEN = 256, CONSTANTS = 64, TABLES = 6, REGISTERS = 64,
    FPU_OP_COUNT = 18, OP_LOAD = 31, OP_DIV = 3, OP_SQRT = 4, OP_FIRST_CORDIC = 12,
    D_MODE = 0, D_PROG = 1, D_PROG_LEN = 2, D_CONST = 3, D_CONST_LEN = 4, D_GRID = 5, D_R = 8, D_F = 9, D_TABLE = 10,
    DIM_SHIFT = 16, LAST_BIT = 26,
    ST_OK = 0, ST_BAD_ADDR = 3, ST_BAD_ARG = 6,
    FLAG_NV = 0x10, FLAG_OF = 0x04, FLAG_NX = 0x01
};

#define CANON_NAN 0x7FC00000u
#define POS_INF   0x7F800000u
#define NEG_INF   0xFF800000u

/* The cost of a run, for the clock count bounds. See cycle_bounds in evu_model.py. */
enum { CYCLES_START = DESC_WORDS + 47, CYCLES_SUM = 29, DS_PERIOD = 33,
       SLOW_FAST = 40, SLOW_DIVIDE = 120, SLOW_CORDIC = 160 };

static uint32_t mem[WORDS];

/* What a run writes. A map run has two words per point, for at most WORDS points. */
static struct { uint32_t addr, word; } out[2 * WORDS];
static int out_count;

EXPORT void evu_ref_clear(void) { memset(mem, 0, sizeof mem); out_count = 0; }
EXPORT void evu_ref_poke(unsigned addr, unsigned word) { mem[addr & (WORDS - 1)] = word; }
EXPORT unsigned evu_ref_writes(void) { return (unsigned)out_count; }
EXPORT unsigned evu_ref_write_addr(unsigned i) { return out[i].addr; }
EXPORT unsigned evu_ref_write_word(unsigned i) { return out[i].word; }

static void emit(uint32_t addr, uint32_t word) {
    out[out_count].addr = addr;
    out[out_count].word = word;
    out_count++;
}

static int field_a(uint32_t w) { return (w >> 13) & 0x7F; }
static int field_b(uint32_t w) { return (w >> 6) & 0x7F; }
static int field_op(uint32_t w) { return (int)(w >> 27); }
static int field_dst(uint32_t w) { return (w >> 20) & 0x3F; }

/* A source field that names a constant the pool doesn't have. */
static int bad_constant(int s, uint32_t pool) { return (s & 0x40) && (uint32_t)(s & 0x3F) >= pool; }

/* Same rules, same order as check_descriptor. */
static int bad_word(uint32_t w, uint32_t pool) {
    int op = field_op(w);
    if (w & 0x3F) return 1;
    if (op != OP_LOAD && op >= FPU_OP_COUNT) return 1;
    if (op == OP_LOAD) return field_a(w) >= TABLES || bad_constant(field_b(w), pool);
    return bad_constant(field_a(w), pool) || bad_constant(field_b(w), pool);
}

static int bad_span(uint64_t base, uint64_t count) { return base >= WORDS || base + count > WORDS; }

/* I keep argument errors ahead of address errors, matching the wire protocol. */
static int check_descriptor(const uint32_t *d) {
    uint64_t points = 1, outputs;
    int k;
    for (k = 0; k < 3; k++) points *= d[D_GRID + k];
    for (k = 0; k < TABLES; k++) {
        uint32_t e = d[D_TABLE + k];
        if (((e >> DIM_SHIFT) & 3) == 3 || (e >> (DIM_SHIFT + 2))) return ST_BAD_ARG;
    }
    if ((d[D_MODE] >> 1) || d[D_PROG_LEN] == 0 || d[D_PROG_LEN] > PROGRAM_LEN || d[D_CONST_LEN] > CONSTANTS)
        return ST_BAD_ARG;
    for (k = 0; k < 3; k++)
        if (d[D_GRID + k] == 0 || d[D_GRID + k] > 0xFFFFu) return ST_BAD_ARG;
    /* The grid is in range now, so this product can't have wrapped. */
    if (points > (1ull << 32)) return ST_BAD_ARG;

    outputs = (d[D_MODE] & 1) ? 1 : points;
    if (bad_span(d[D_PROG], d[D_PROG_LEN]) || bad_span(d[D_CONST], d[D_CONST_LEN]) ||
        bad_span(d[D_R], outputs) || bad_span(d[D_F], outputs))
        return ST_BAD_ADDR;
    for (k = 0; k < TABLES; k++) {
        uint32_t e = d[D_TABLE + k];
        if (bad_span(e & (WORDS - 1), d[D_GRID + ((e >> DIM_SHIFT) & 3)])) return ST_BAD_ADDR;
    }
    for (k = 0; k < (int)d[D_PROG_LEN]; k++)
        if (bad_word(mem[d[D_PROG] + k], d[D_CONST_LEN])) return ST_BAD_ARG;
    return ST_OK;
}

/*
 * The exact sum. I keep one running integer per exponent, each a sum of signed
 * 24-bit significands, and only put them together at the end. A finite fp32 value
 * is m * 2^shift * 2^-149, and an int64 holds 2^39 of those at one shift, far more
 * than a simulation will run.
 */
static int64_t sum_by_shift[254];
static int sum_pos, sum_neg, sum_nan, sum_snan;

static void sum_clear(void) {
    memset(sum_by_shift, 0, sizeof sum_by_shift);
    sum_pos = sum_neg = sum_nan = sum_snan = 0;
}

/* I collect signed significands by exponent before carrying and rounding. */
static void sum_add(uint32_t v) {
    int e = (v >> 23) & 0xFF;
    int64_t m = v & 0x7FFFFF;
    if (e == 0xFF) {
        if (v == POS_INF) sum_pos = 1;
        else if (v == NEG_INF) sum_neg = 1;
        else {
            sum_nan = 1;
            if (!(v & 0x400000)) sum_snan = 1;
        }
        return;
    }
    if (e) m |= 1 << 23;
    sum_by_shift[(e ? e : 1) - 1] += (v >> 31) ? -m : m;
}

enum { LIMBS = 20 };                    /* 640 bits, two's complement, 32 bits a limb */

/* Add value << shift to a long two's complement number. */
/* I move a signed bin into the wide integer without using host float addition. */
static void limb_add(uint32_t *n, int64_t value, int shift) {
    uint64_t mag = value < 0 ? (uint64_t)-value : (uint64_t)value;
    uint64_t low = (mag & 0xFFFFFFFFu) << (shift % 32), high = (mag >> 32) << (shift % 32);
    uint32_t pieces[LIMBS] = {0};
    int word = shift / 32, k;
    int64_t carry = 0;                  /* -1, 0 or 1 */

    pieces[word] = (uint32_t)low;
    pieces[word + 1] = (uint32_t)(low >> 32) | (uint32_t)high;
    pieces[word + 2] = (uint32_t)(high >> 32);
    for (k = 0; k < LIMBS; k++) {
        int64_t t = (int64_t)n[k] + (value < 0 ? -(int64_t)pieces[k] : (int64_t)pieces[k]) + carry;
        n[k] = (uint32_t)t;
        carry = t >> 32;                /* arithmetic shift: a borrow comes out as -1 */
    }
}

/* Round the whole sum once, to nearest with ties to even. */
/* I resolve NaNs and infinities first, then round the finite total once. */
static uint32_t sum_result(int *flags) {
    uint32_t n[LIMBS] = {0};
    uint32_t sign, sig, bits;
    int k, top, guard, sticky, e;

    *flags = 0;
    if (sum_nan || (sum_pos && sum_neg)) {
        if (sum_snan || (sum_pos && sum_neg)) *flags = FLAG_NV;
        return CANON_NAN;
    }
    if (sum_pos) return POS_INF;
    if (sum_neg) return NEG_INF;

    for (k = 0; k < 254; k++)
        if (sum_by_shift[k]) limb_add(n, sum_by_shift[k], k);

    sign = n[LIMBS - 1] >> 31;
    if (sign) {                         /* magnitude: invert and add one */
        uint64_t carry = 1;
        for (k = 0; k < LIMBS; k++) {
            carry += (uint32_t)~n[k];
            n[k] = (uint32_t)carry;
            carry >>= 32;
        }
    }
    top = -1;
    for (k = LIMBS * 32 - 1; k >= 0; k--)
        if ((n[k / 32] >> (k % 32)) & 1) { top = k; break; }
    if (top < 0) return 0;              /* an exact zero is +0 */
    sign <<= 31;
    if (top < 24) return sign | n[0];   /* fits as it stands, subnormal or not */

    /* The 24 bits from top down, the guard bit under them, and the rest. */
    sig = 0;
    for (k = 0; k < 24; k++) sig |= ((n[(top - k) / 32] >> ((top - k) % 32)) & 1u) << (23 - k);
    guard = (n[(top - 24) / 32] >> ((top - 24) % 32)) & 1;
    sticky = 0;
    for (k = 0; k < top - 24; k++)
        if ((n[k / 32] >> (k % 32)) & 1) sticky = 1;
    e = top - 22;                       /* value = sig * 2^(top-23) * 2^-149 */
    if (guard && (sticky || (sig & 1))) {
        sig++;
        if (sig == 1u << 24) { sig >>= 1; e++; }
    }
    if (e >= 255) { *flags = FLAG_OF | FLAG_NX; return sign | POS_INF; }
    if (guard || sticky) *flags = FLAG_NX;
    bits = sign | ((uint32_t)e << 23) | (sig & 0x7FFFFF);
    return bits;
}

static void fpu(int op, uint32_t a, uint32_t b, uint32_t *result, int *flags) {
    *result = op >= OP_FIRST_CORDIC ? cordic_ref_eval(op, a, b, flags) : fp_ref_eval(op, a, b, flags);
}

static int is_unary(int op) {
    /* SQRT, I2F, F2I, SIN, COS, EXP, LN */
    return op == 4 || op == 5 || op == 6 || op == 12 || op == 13 || op == 16 || op == 17;
}

static int is_divide(int op) { return op == OP_DIV || op == OP_SQRT; }

/* I run points in grid order here; hardware is free to finish them out of order. */
EXPORT int evu_ref_run(unsigned desc, unsigned *aux, unsigned *low, unsigned *high) {
    uint32_t d[DESC_WORDS], program[PROGRAM_LEN], pool[CONSTANTS];
    uint32_t tbase[TABLES], tdim[TABLES], n[3];
    uint32_t i0, i1, i2, r_addr, f_addr;
    uint64_t point = 0, points, each = 0, copy, floor_ops;
    int len, k, reduce, st, divides = 0;
    uint32_t flags_or = 0;

    out_count = 0;
    *aux = 0;
    *low = *high = 0;
    for (k = 0; k < DESC_WORDS; k++) d[k] = mem[desc + k];
    st = check_descriptor(d);
    if (st != ST_OK) return st;

    /* Same stopping rule as the hardware: the first last bit, or the length. */
    len = (int)d[D_PROG_LEN];
    for (k = 0; k < len; k++) {
        program[k] = mem[d[D_PROG] + k];
        if ((program[k] >> LAST_BIT) & 1) { len = k + 1; break; }
    }
    for (k = 0; k < (int)d[D_CONST_LEN]; k++) pool[k] = mem[d[D_CONST] + k];
    for (k = 0; k < TABLES; k++) {
        tbase[k] = d[D_TABLE + k] & (WORDS - 1);
        tdim[k] = (d[D_TABLE + k] >> DIM_SHIFT) & 3;
    }
    for (k = 0; k < 3; k++) n[k] = d[D_GRID + k];
    reduce = d[D_MODE] & 1;
    r_addr = d[D_R];
    f_addr = d[D_F];
    points = (uint64_t)n[0] * n[1] * n[2];
    sum_clear();

    /* The clock count bounds. */
    copy = CYCLES_START + (uint64_t)len + d[D_CONST_LEN] + (reduce ? CYCLES_SUM : 0);
    for (k = 0; k < len; k++) {
        int op = field_op(program[k]);
        if (is_divide(op)) { divides++; each += SLOW_DIVIDE; }
        else if (op != OP_LOAD && op >= OP_FIRST_CORDIC) each += SLOW_CORDIC;
        else each += SLOW_FAST;
    }
    floor_ops = (uint64_t)(divides * DS_PERIOD > len ? divides * DS_PERIOD : len);
    *low = (unsigned)(copy + floor_ops * points);
    *high = (unsigned)(copy + 200 + each * points);

    for (i0 = 0; i0 < n[0]; i0++)
        for (i1 = 0; i1 < n[1]; i1++)
            for (i2 = 0; i2 < n[2]; i2++, point++) {
                /* A register nothing wrote reads as zero, as in the Python model. */
                uint32_t regs[REGISTERS] = {0}, index[3], last = 0;
                int flags = 0;
                index[0] = i0; index[1] = i1; index[2] = i2;
                for (k = 0; k < len; k++) {
                    uint32_t w = program[k], a, b, result;
                    int op = field_op(w), sa = field_a(w), sb = field_b(w), f = 0;
                    if (op == OP_LOAD) {
                        result = mem[tbase[sa] + index[tdim[sa]]];
                    } else {
                        a = (sa & 0x40) ? pool[sa & 0x3F] : regs[sa];
                        b = is_unary(op) ? 0 : (sb & 0x40) ? pool[sb & 0x3F] : regs[sb];
                        fpu(op, a, b, &result, &f);
                        flags |= f;
                    }
                    regs[field_dst(w)] = result;
                    last = result;
                }
                flags_or |= (uint32_t)flags;
                if (reduce) {
                    sum_add(last);
                } else {
                    emit(r_addr + (uint32_t)point, last);
                    emit(f_addr + (uint32_t)point, (uint32_t)flags);
                }
            }
    if (reduce) {
        int flags;
        uint32_t bits = sum_result(&flags);
        emit(r_addr, bits);
        emit(f_addr, (uint32_t)flags);
    }
    *aux = flags_or;
    return ST_OK;
}
