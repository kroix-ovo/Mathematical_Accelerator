"""Bit-accurate model of the CORDIC unit in hw/rtl/fp/fp_cordic.sv (all six phase 3 ops).

This is not a reference for what sin x *should* be (that is mpref.py). It is a
description of what my hardware computes, bit for bit, written with Python
integers. It has two jobs:

1. Choosing the widths. tools/measure_ulp.py runs this model against mpmath
   for different datapath widths, so I can pick the narrowest widths that
   still keep every result within budget before writing any RTL.
2. Checking the RTL. The pyuvm testbench compares every RTL result with this
   model exactly. A mismatch is an RTL bug; the ULP error of the algorithm
   itself is measured separately against mpmath.

The constants (2/pi window ROM, arctangent table, CORDIC gain) are computed
here with mpmath, and tools/gen_cordic.py writes the same numbers into
hw/rtl/fp/cordic_pkg.sv, so the model and the RTL can't drift apart.

How sin and cos are computed (x is the fp32 input, all angles in quarter turns,
i.e. units of pi/2):

1. Tiny inputs, |x| < 2^-12: sin x rounds to x itself and cos x rounds to 1,
   because x^3/6 and x^2/2 are below half an ULP of the result. No reduction.
2. Payne-Hanek reduction: x = M * 2^E (24-bit M). I multiply M by an L-bit
   window of 2/pi chosen by E (a ROM indexed by the exponent) and keep the
   product modulo 2^L. Its top two bits are the quadrant q of x and the
   remaining F = L-2 bits are the fraction f of a quarter turn, so
   x = (4k + q + f) * pi/2 for some integer k.
3. Fold: if f >= 1/2, use f' = 1 - f and swap the roles of sin and cos. Now
   the angle t = f' * pi/2 is at most pi/4.
4. CORDIC rotation: start at (K, 0), where K undoes the CORDIC gain, and rotate
   by +-atan(2^-i) for i = 0 .. N-1, steering the remaining angle z to zero.
   The end point is (cos t, sin t) in fixed point with W fraction bits.
5. Small angles: fixed point loses relative precision when sin t is small, so
   if f' < 2^-13 the sin t result comes from a separate path instead:
   sin t = t, computed as f' (normalized) times pi/2 with a 32 x 32 multiply.
   t^2/6 < 2^-27 relative there, under 0.1 ULP. f' is never below about
   2^-30 (tools/ph_worst_case.py), so 32 bits of it are always available.
6. Pick cos t or sin t and the sign from the quadrant, normalize, round.

How atan2(y, x) and hypot(x, y) are computed (milestone 3), in the same
stages run in vectoring mode, where each step turns the vector toward the
x axis (steering y to zero) and adds up the angles it turned through:

1. Special values (NaN, zeros, infinities) by rule, as in mpref.special().
   The non-exact rule results (pi, pi/2, pi/4, 3pi/4) are fixed constants.
2. Unpack both inputs and normalize subnormal significands (shift the leading
   1 up, lower the exponent), so both are 1.m * 2^e with 24-bit m.
3. Order them: B is the input with the larger magnitude, S the smaller, and
   d = e_B - e_S >= 0. For atan2, "swap" means |y| > |x|.
4. Multiply both significands by K (the CORDIC gain correction) and halve
   them, then shift S right by d so both have B's exponent. The vector
   (x0, y0) = (B, S) starts in the first octant, with x0 in [0.3, 0.6).
5. Vectoring stages: drive y to zero. x ends at sqrt(x0^2 + y0^2), the gain
   already removed by the K pre-scale, which gives hypot. z adds up the angle,
   in radians, which gives atan(S/B).
6. Quadrant: atan2 is atan(S/B), pi/2 - that (swap), pi - that (x < 0) or
   pi/2 + that (both). Instead of adding the base afterwards, I load z with
   the base (0, pi/2 or pi) and flip the sign of the angle steps when the
   result is base minus the angle. The sign of y gives the sign of the result.
7. Tiny atan2 results: when x > 0, no swap, and d >= ATAN_D, the result is
   about S/B < 2^-13, and fixed point would lose its relative precision. Those
   inputs run the same stages in linear mode instead, a shift-and-subtract
   division that leaves S/B (unshifted significands, so a ratio in (1/2, 2))
   in z. atan(r) = r - r^3/3 and r^2/3 < 2^-27.6 there, under 0.1 ULP. The
   exponent -d goes back in when the result is packed, and it may be subnormal.

How exp and ln are computed (milestone 4), with hyperbolic steps: turning by
+-atanh(2^-s) with x' = x + y*2^-s, y' = y + x*2^-s. Hyperbolic CORDIC only
converges if shifts 4 and 13 are done twice, so in hyperbolic modes stage k
uses shift s(k) = 1, 2, 3, 4, 4, 5, ..., 13, 13, 14, ... instead of k.

exp x:
1. Special values, |x| < 2^-25 (rounds to 1) and |x| >= 128 (overflow to
   +inf, or underflow to +0) by rule.
2. Reduction, with the same 24 x 96 multiplier as sin/cos: a second small
   ROM, indexed by the exponent, holds 1/ln2 lined up so that the product is
   t = x/ln2 with 86 fraction bits. Then k = round(t) and f = t - k is in
   [-1/2, 1/2], so x = (k + f) * ln2 and e^x = 2^k * e^(f ln2). f stays in
   units of ln2 and the atanh table is scaled to match (the same trick as
   quarter turns for sin/cos), so nothing is multiplied by ln2.
3. Hyperbolic rotation from (G/2, G/2), G undoing the hyperbolic gain: the
   vector ends at (e^r/2, e^r/2) with r = f ln2, since cosh + sinh = exp.
   Halving leaves headroom for the intermediate values. k (plus 1 for the
   halving) goes into the exponent; results may overflow or be subnormal.

ln x:
1. Special values by rule: NaN, ln(+-0) = -inf (DZ), negative -> NaN (NV),
   ln(+inf) = +inf, ln(1) = +0.
2. Near 1, |d| = |x - 1| < 2^-LN_T: ln(1 + d) = 2 atanh(d / (2 + d)), and
   atanh(u) = u to within u^2/3 relative (under 2^-27, 0.1 ULP), so the
   linear (division) mode computes d / (2 + d) with d normalized, and the
   exponent of d goes back in at packing. d = x - 1 is exact here.
3. Otherwise x = m * 2^e with m in [sqrt(1/2), sqrt(2)): hyperbolic vectoring
   of ((m+1)/4, (m-1)/4) leaves atanh((m-1)/(m+1)) = ln(m)/2 in z. z starts at
   |e| * ln2/2, and for x < 1 the steps are negated, so z ends at |ln x| / 2,
   always positive; the sign comes from x < 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from functools import cache

import mpmath

from mathaccel._generated.spec import FpuOp
from mathaccel.fp32ref import CANON_NAN, DZ, NV, NX, OF, UF, is_inf, is_nan, is_snan, is_zero, round_fraction

ONE = 0x3F800000


@dataclass(frozen=True)
class CordicParams:
    """Datapath widths. The defaults are the ones the RTL is built with.

    Chosen with tools/measure_ulp.py --sweep (October 2026, 100k inputs per op):
    Z = W = 42 still met the budget (0.60 ULP) and 40 did not (1.16); N = 38
    gave 0.82 ULP and 36 failed (2.17). I took two bits and two stages of
    margin over the narrowest passing widths. L made no difference between
    84 and 96 on samples; 96 keeps four 24 x 24 multiplies and margin at the
    worst-case input 0x6F79BE45. With N = 40 a million screened inputs per op
    measured 0.575 ULP (sin) and 0.590 ULP (cos) at most.

    Milestone 3 raised N from 40 to 42. atan2 results just above the y/x
    threshold (ATAN_D = 14, results near 2^-14) are limited by the angle left
    over after the last stage: on 200k pairs N = 40 gave 0.775 ULP, N = 42
    gave 0.589, while two more bits of W and Z without more stages only got to
    0.711. ATAN_D = 12 failed (1.66 ULP, the dropped r^3/3 term), 13 to 15
    all passed with N = 40.
    """
    L: int = 96          # bits of 2/pi in the Payne-Hanek window (and of the product kept)
    Z: int = 44          # fraction bits of the CORDIC angle z, in quarter turns
    W: int = 44          # fraction bits of the CORDIC x and y
    N: int = 42          # CORDIC iterations (pipeline stages)
    TINY_E: int = 115    # biased exponents below this (|x| < 2^-12) take the tiny-input path
    SMALL_T: int = 13    # f' < 2^-SMALL_T quarter turns takes the sin t = t path
    ATAN_D: int = 14     # atan2 with x > 0, |y| < |x| and e_x - e_y >= ATAN_D takes the y/x path
    LN_T: int = 12       # ln with |x - 1| < 2^-LN_T takes the d / (2 + d) path
    EXP_TINY_E: int = 102  # exp of |x| < 2^-25 (biased exponent below this) rounds to 1
    EXP_BIG_E: int = 134   # exp of |x| >= 128 (biased exponent from this) overflows or underflows

    def __post_init__(self):
        # The back end normalizes x, y and z through one window, so their
        # binary points must line up.
        """Keep x/y and angle binary points aligned for the shared RTL normalization stage."""
        assert self.Z == self.W, "the RTL assumes CZ == CW"

    @property
    def F(self) -> int:
        """Fraction bits of the reduced angle f."""
        return self.L - 2


DEFAULT = CordicParams()

# The ROM covers biased exponents TINY_E .. 254 (finite inputs that reach the
# reduction). 255 (inf/NaN) never reads it.
E_MAX = 254

# pi/2 as an unsigned 32-bit fixed-point number with 31 fraction bits, used by
# the small-angle path. Rounded to nearest; the error is 2^-33 relative.
PIO2_BITS = 32


# ---------------------------------------------------------------------------
# Constant tables (also written into cordic_pkg.sv by tools/gen_cordic.py)
# ---------------------------------------------------------------------------

@cache
def two_over_pi_bits(nbits: int) -> int:
    """floor(2/pi * 2^nbits): the first nbits fraction bits of 2/pi as an integer."""
    with mpmath.workprec(nbits + 64):
        return int(mpmath.floor(2 / mpmath.pi * mpmath.mpf(2) ** nbits))


@cache
def ph_rom(p: CordicParams = DEFAULT) -> tuple[int, ...]:
    """Payne-Hanek window ROM, one L-bit entry per biased exponent TINY_E .. 254.

    For x = M * 2^E (E = e - 150), bits b_i of 2/pi with i <= E-2 only add
    whole turns, so the window starts at b_(E-1). Windows that start at or
    before b_0 (small x) are padded with zeros at the top, which is what
    "b_i = 0 for i <= 0" means for 2/pi = 0.b1 b2 b3 ...
    """
    total = E_MAX - 151 + p.L + 8          # enough bits for the last window
    bits = two_over_pi_bits(total)         # b_1 is bit total-1 of this integer
    rom = []
    for e in range(p.TINY_E, E_MAX + 1):
        s = e - 151                        # first bit index b_s of the window
        # Bits b_s .. b_(s+L-1). Indices <= 0 are zero.
        # Shifting right drops the bits after b_(s+L-1); the mask drops the
        # bits before b_s. For s <= 0 there is nothing above b_1 to drop, and
        # the window simply comes out with leading zeros.
        rom.append((bits >> (total - (s + p.L - 1))) & ((1 << p.L) - 1))
    return tuple(rom)


@cache
def atan_table(p: CordicParams = DEFAULT) -> tuple[int, ...]:
    """alpha_i = atan(2^-i) in quarter turns with Z fraction bits, rounded to nearest."""
    with mpmath.workprec(200):
        scale = mpmath.mpf(2) ** p.Z * 2 / mpmath.pi
        return tuple(int(mpmath.nint(mpmath.atan(mpmath.mpf(2) ** -i) * scale)) for i in range(p.N))


@cache
def cordic_k(p: CordicParams = DEFAULT) -> int:
    """Start value x0 = prod 1/sqrt(1 + 2^-2i) with W fraction bits, rounded.

    Each CORDIC step lengthens the vector by sqrt(1 + 2^-2i); starting from
    this x0 instead of 1 makes the end point land on the unit circle.
    """
    with mpmath.workprec(200):
        k = mpmath.mpf(1)
        for i in range(p.N):
            k /= mpmath.sqrt(1 + mpmath.mpf(2) ** (-2 * i))
        return int(mpmath.nint(k * mpmath.mpf(2) ** p.W))


@cache
def atan_rad_table(p: CordicParams = DEFAULT) -> tuple[int, ...]:
    """atan(2^-i) in radians with Z fraction bits, rounded, for vectoring mode."""
    with mpmath.workprec(200):
        scale = mpmath.mpf(2) ** p.Z
        return tuple(int(mpmath.nint(mpmath.atan(mpmath.mpf(2) ** -i) * scale)) for i in range(p.N))


@cache
def kv_const(p: CordicParams = DEFAULT) -> int:
    """The CORDIC gain correction K with 32 fraction bits, for pre-scaling the
    vectoring inputs. Same K as cordic_k(), rounded to 32 bits."""
    with mpmath.workprec(200):
        k = mpmath.mpf(1)
        for i in range(p.N):
            k /= mpmath.sqrt(1 + mpmath.mpf(2) ** (-2 * i))
        return int(mpmath.nint(k * mpmath.mpf(2) ** 32))


@cache
def pi_z(p: CordicParams = DEFAULT) -> tuple[int, int]:
    """(pi/2, pi) in radians with Z fraction bits, rounded: the atan2 base angles."""
    with mpmath.workprec(200):
        s = mpmath.mpf(2) ** p.Z
        return int(mpmath.nint(mpmath.pi / 2 * s)), int(mpmath.nint(mpmath.pi * s))


@cache
def hyp_shifts(p: CordicParams = DEFAULT) -> tuple[int, ...]:
    """Shift used by each stage in hyperbolic mode: 1, 2, 3, 4, 4, 5, ... with
    shifts 4, 13 and 40 repeated, which hyperbolic CORDIC needs to converge."""
    out: list[int] = []
    s = 1
    while len(out) < p.N:
        out.append(s)
        if s in (4, 13, 40) and out.count(s) == 1:
            continue                       # do this shift once more
        s += 1
    return tuple(out)


@cache
def atanh_tables(p: CordicParams = DEFAULT) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """atanh(2^-s(k)) per stage with Z fraction bits, rounded: (in units of ln2
    for exp, natural units for ln)."""
    with mpmath.workprec(200):
        sc = mpmath.mpf(2) ** p.Z
        nat = [mpmath.atanh(mpmath.mpf(2) ** -s) for s in hyp_shifts(p)]
        return (tuple(int(mpmath.nint(v / mpmath.log(2) * sc)) for v in nat),
                tuple(int(mpmath.nint(v * sc)) for v in nat))


@cache
def hyp_g2(p: CordicParams = DEFAULT) -> int:
    """Start value for exp: G/2 with W fraction bits, where G = 1 / prod sqrt(1 - 2^-2s)."""
    with mpmath.workprec(200):
        a = mpmath.mpf(1)
        for s in hyp_shifts(p):
            a *= mpmath.sqrt(1 - mpmath.mpf(2) ** (-2 * s))
        return int(mpmath.nint(mpmath.mpf(2) ** p.W / a / 2))


@cache
def ln2h_const(p: CordicParams = DEFAULT) -> int:
    """ln2 / 2 with Z + 8 fraction bits (8 spare bits absorb the |e| multiply)."""
    with mpmath.workprec(200):
        return int(mpmath.nint(mpmath.log(2) / 2 * mpmath.mpf(2) ** (p.Z + 8)))


@cache
def exp_rom(p: CordicParams = DEFAULT) -> tuple[int, ...]:
    """1/ln2 windows for exp, one per biased exponent EXP_TINY_E .. EXP_BIG_E-1.

    x = M * 2^(e-150), so x/ln2 * 2^86 = M * 2^(e-64)/ln2: the entry is that
    constant rounded. The largest product is below 2^94, so it fits the 96-bit
    multiplier without wrapping.
    """
    with mpmath.workprec(200):
        return tuple(int(mpmath.nint(mpmath.mpf(2) ** (e - 64) / mpmath.log(2)))
                     for e in range(p.EXP_TINY_E, p.EXP_BIG_E))


# sqrt(2) as a 24-bit significand (leading 1 at bit 23), rounded up: a
# normalized significand m >= SQRT2_M means m/2^23 >= sqrt(2).
SQRT2_M = 0xB504F4


@cache
def pio2_const() -> int:
    """round(pi/2 * 2^31), a 32-bit constant."""
    with mpmath.workprec(100):
        return int(mpmath.nint(mpmath.pi / 2 * mpmath.mpf(2) ** (PIO2_BITS - 1)))


# ---------------------------------------------------------------------------
# Datapath pieces
# ---------------------------------------------------------------------------

def lzc(v: int, width: int) -> int:
    """Leading zeros of a width-bit value (width when v is 0), like lzc24 in fp_pkg."""
    return width - v.bit_length() if v else width


def reduce(a: int, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """Payne-Hanek: (quadrant q, fraction f with F bits) for |a|, biased exponent >= TINY_E."""
    e = (a >> 23) & 0xFF
    m = (a & 0x7FFFFF) | 0x800000          # e >= TINY_E > 0, so the number is normal
    prod = (m * ph_rom(p)[e - p.TINY_E]) & ((1 << p.L) - 1)
    return prod >> p.F, prod & ((1 << p.F) - 1)


# Stage modes. Each value carries its own mode down the pipeline.
ROT, VEC, LIN, HROT, HVEC = 0, 1, 2, 3, 4


def cordic_run(mode: int, x: int, y: int, z: int, zneg: int = 0,
               p: CordicParams = DEFAULT) -> tuple[int, int, int]:
    """N CORDIC steps in one mode. Returns the final (x, y, z).

    ROT   rotation, z in quarter turns: turn by +-atan(2^-i) to drive z to 0.
    VEC   vectoring, z in radians: turn by +-atan(2^-i) to drive y to 0.
    LIN   linear vectoring: y -= +-x*2^-i and z += +-2^-i, so z ends at y0/x0.
    HROT  hyperbolic rotation, z in units of ln2 (exp).
    HVEC  hyperbolic vectoring, z in natural units (ln).
    zneg flips the sign of every z step: z then ends at z0 - angle instead of
    z0 + angle.

    "ccw" is the direction with y increasing. Circular steps move x the
    opposite way from y (x - y*2^-s), hyperbolic steps the same way
    (x + y*2^-s), which is the only difference between the two kinds.

    Python's >> on negative integers rounds toward minus infinity, the same as
    an arithmetic right shift (>>>) of a two's complement number in SV.
    """
    aq, ar = atan_table(p), atan_rad_table(p)
    hl, hn = atanh_tables(p)
    hs = hyp_shifts(p)
    hyp = mode in (HROT, HVEC)
    for i in range(p.N):
        s = hs[i] if hyp else i
        ccw = (z >= 0) if mode in (ROT, HROT) else (y < 0)
        c = {ROT: aq[i], VEC: ar[i], LIN: 1 << (p.Z - i), HROT: hl[i], HVEC: hn[i]}[mode]
        ystep = 0 if mode == LIN else (y >> s)   # linear mode leaves x alone
        xsub = ccw != hyp
        x, y = (x - ystep if xsub else x + ystep), (y + (x >> s) if ccw else y - (x >> s))
        z = z - c if ccw != bool(zneg) else z + c
    return x, y, z


def cordic_rotate(z0: int, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """N rotation-mode steps from (K, 0). Returns (x, y) = (cos t, sin t), W fraction bits."""
    x, y, _ = cordic_run(ROT, cordic_k(p), 0, z0, 0, p)
    return x, y


def small_sin(fp: int, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """sin t = t for f' < 2^-SMALL_T. Returns (biased exponent, 26-bit sig) for rounding.

    I take 64 bits of f' starting just below the bits known to be zero, find
    the leading 1 among their top 32 (f' >= 2^-30 always, so it is there),
    and multiply the normalized top 32 bits by pi/2.
    """
    top = p.F - p.SMALL_T - 1              # highest bit that can be 1 on this path
    win = (fp >> (top - 63)) & ((1 << 64) - 1)
    lz = lzc(win >> 32, 32)                # 0 .. 31 (32 would mean f' < 2^-46: impossible)
    m32 = ((win << lz) >> 32) & 0xFFFFFFFF
    prod = m32 * pio2_const()              # 64-bit product, leading 1 at bit 63 or 62
    # f' = win * 2^(top-63-F) quarter turns and win = m32 * 2^(32-lz) to 32
    # bits, so t = f' * pi/2 = prod * 2^(32 - lz + top - 63 - F - 31). If
    # prod's leading 1 is at bit 63, t = 1.xxx * 2^(63 + that exponent).
    # Keep 25 bits from the leading 1 and OR the rest into the sticky bit.
    if prod >> 63:
        sig = (prod >> 39) << 1 | (prod & ((1 << 39) - 1) != 0)
        exp = 127 + (63 + 32 - lz + top - 63 - p.F - 31)
    else:
        sig = (prod >> 38) << 1 | (prod & ((1 << 38) - 1) != 0)
        exp = 127 + (62 + 32 - lz + top - 63 - p.F - 31)
    return exp, sig


def normalize_cordic(v: int, ebase: int = 127, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """(biased exponent, 26-bit sig) of a CORDIC output with W fraction bits.

    v is x, y or z, all with W fraction bits (Z == W). The value is
    v * 2^-W * 2^(ebase - 127). The RTL searches 24 bit positions for the
    leading 1, from bit W+6 (64, room for |ln x|/2 up to 52) down to bit W-17.
    Every value that gets here is at least 2^-14 (sin above 2^-12.4, atan
    above 2^-14, |ln x|/2 above 2^-13, ratios and exp/hypot's x above 1/3),
    so the leading 1 is always in that window.
    """
    assert v > 0, "CORDIC output not positive: a path threshold is wrong"
    lz = lzc((v >> (p.W - 17)) & 0xFFFFFF, 24)
    sh = (v << lz) & ((1 << (p.W + 7 + 24)) - 1)
    # Bit W+6 of sh is the leading 1. Keep 25 bits from there plus a sticky bit.
    sig = ((sh >> (p.W - 18)) & ((1 << 25) - 1)) << 1 | ((sh & ((1 << (p.W - 18)) - 1)) != 0)
    return ebase + 6 - lz, sig


def pack(sign: int, exp: int, sig: int) -> tuple[int, int]:
    """Round {sign, exp, sig} like round_pre/round_post in fp_pkg, with the phase 3 flags.

    sig's bit 25 is the leading 1, bits 24..2 the fraction, bit 1 the guard
    bit and bit 0 the sticky bit; the value is sig * 2^(exp - 127 - 25).
    Rounding that value exactly with round_fraction gives the same result as
    round_pre/round_post, because the sticky bit already says whether
    anything nonzero was below the guard bit (the argument is the same for the
    extra right shift of a subnormal result).

    NX is always set (phase 3 rule). Every result is inexact by that rule, so
    a result that ends up below 2^-126 also gets UF, even in the rare case
    where the rounding itself happened to be exact.
    """
    bits, flags = round_fraction(sign, Fraction(sig) * Fraction(2) ** (exp - 127 - 25))
    if (bits & 0x7FFFFFFF) < 0x00800000:
        flags |= UF
    return bits, flags | NX


# ---------------------------------------------------------------------------
# The whole unit
# ---------------------------------------------------------------------------

def sincos(op: int, a: int, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """What fp_cordic.sv returns for SIN or COS of a. Returns (result_bits, flags)."""
    a &= 0xFFFFFFFF
    # Special values: the same rules as mpref.special().
    if is_nan(a):
        return CANON_NAN, NV if is_snan(a) else 0
    if is_inf(a):
        return CANON_NAN, NV
    if is_zero(a):
        return (a, 0) if op == FpuOp.SIN else (ONE, 0)
    sign = a >> 31
    e = (a >> 23) & 0xFF
    if e < p.TINY_E:
        # sin x -> x (UF too if x is subnormal: inexact and tiny), cos x -> 1.
        if op == FpuOp.SIN:
            return a, NX | (UF if e == 0 else 0)
        return ONE, NX

    q, f = reduce(a, p)
    swap = f >> (p.F - 1)                  # f >= 1/2
    fp = ((1 << p.F) - f) if swap else f   # f' in [0, 1/2]
    small = (fp >> (p.F - p.SMALL_T)) == 0
    x, y = cordic_rotate(fp >> (p.F - p.Z), p)

    # sin|x| uses cos t in odd quadrants, cos|x| in even ones; the fold swaps once more.
    want_cos = (q & 1) if op == FpuOp.SIN else 1 - (q & 1)
    take_x = want_cos ^ swap
    # Signs: sin is odd (follows the input sign) and negative in quadrants 2, 3;
    # cos is even and negative in quadrants 1, 2.
    neg = ((q >> 1) ^ sign) if op == FpuOp.SIN else int(q in (1, 2))
    if take_x:
        exp, sig = normalize_cordic(x, 127, p)
    elif small:
        exp, sig = small_sin(fp, p)
    else:
        exp, sig = normalize_cordic(y, 127, p)
    return pack(neg, exp, sig)


# Correctly rounded fp32 constants for the atan2 rule results (all inexact).
PI_F, PIO2_F, PIO4_F, P3O4_F = 0x40490FDB, 0x3FC90FDB, 0x3F490FDB, 0x4016CBE4
SIGN = 0x80000000
POS_INF = 0x7F800000


def vec_special(op: int, a: int, b: int) -> tuple[int, int] | None:
    """Rule results for ATAN2(a=y, b=x) and HYPOT(a, b), as decided in stage 1."""
    if op == FpuOp.HYPOT:
        if is_inf(a) or is_inf(b):
            return POS_INF, NV if (is_snan(a) or is_snan(b)) else 0
        if is_nan(a) or is_nan(b):
            return CANON_NAN, NV if (is_snan(a) or is_snan(b)) else 0
        if is_zero(b):
            return a & ~SIGN, 0
        if is_zero(a):
            return b & ~SIGN, 0
        return None
    y, x = a, b
    if is_nan(y) or is_nan(x):
        return CANON_NAN, NV if (is_snan(y) or is_snan(x)) else 0
    ys = y & SIGN
    xneg = bool(x & SIGN)
    if is_inf(y):
        if is_inf(x):
            return ys | (P3O4_F if xneg else PIO4_F), NX
        return ys | PIO2_F, NX
    if is_zero(y):
        return (ys | PI_F, NX) if xneg else (ys, 0)
    if is_inf(x):
        return (ys | PI_F, NX) if xneg else (ys, 0)
    if is_zero(x):
        return ys | PIO2_F, NX
    return None


def unpack_norm(bits: int) -> tuple[int, int]:
    """(24-bit significand with its leading 1 at bit 23, exponent) of a finite nonzero
    float. Subnormals are shifted up and get an exponent below 1, as in fp_mul."""
    e = (bits >> 23) & 0xFF
    m = (bits & 0x7FFFFF) | (0x800000 if e else 0)
    lz = lzc(m, 24)
    return (m << lz) & 0xFFFFFF, (e if e else 1) - lz


def vector(op: int, a: int, b: int, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """What fp_cordic.sv returns for ATAN2(a=y, b=x) or HYPOT(a, b)."""
    a &= 0xFFFFFFFF
    b &= 0xFFFFFFFF
    rule = vec_special(op, a, b)
    if rule is not None:
        return rule
    # u is the operand that atan2 calls y; w is x. For hypot the order
    # doesn't matter, so the same code serves.
    mu, eu = unpack_norm(a)
    mw, ew = unpack_norm(b)
    swap = (eu, mu) > (ew, mw)             # |a| > |b|
    (mb, eb), (ms, es) = ((mu, eu), (mw, ew)) if swap else ((mw, ew), (mu, eu))
    d = eb - es
    xneg = b >> 31
    lin = op == FpuOp.ATAN2 and not swap and not xneg and d >= p.ATAN_D
    # Pre-scale by K and halve: (m / 2^23) * K / 2 with W fraction bits is
    # (m * KV) >> (55 + 1 - W), since KV has 32 fraction bits.
    pb, ps = mb * kv_const(p), ms * kv_const(p)
    x0 = pb >> (56 - p.W)
    y0 = ps >> (56 - p.W)
    if not lin:
        y0 >>= min(d, 63)                  # align S to B's exponent
    if op == FpuOp.HYPOT:
        x, _, _ = cordic_run(VEC, x0, y0, 0, 0, p)
        # x = (B/2) * sqrt(1 + (S/B)^2) * 2^(eb-127): one more power of two.
        exp, sig = normalize_cordic(x, eb + 1, p)
        return pack(0, exp, sig)
    if lin:
        _, _, z = cordic_run(LIN, x0, y0, 0, 0, p)
        exp, sig = normalize_cordic(z, 127 - d, p)
    else:
        pio2, pi = pi_z(p)
        # Base angle and direction for each case (see step 6 in the header).
        z0, zneg = {(0, 0): (0, 0), (1, 0): (pio2, 1), (0, 1): (pi, 1), (1, 1): (pio2, 0)}[(int(swap), xneg)]
        _, _, z = cordic_run(VEC, x0, y0, z0, zneg, p)
        exp, sig = normalize_cordic(z, 127, p)
    return pack(a >> 31, exp, sig)


def exp_(a: int, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """What fp_cordic.sv returns for EXP of a."""
    a &= 0xFFFFFFFF
    if is_nan(a):
        return CANON_NAN, NV if is_snan(a) else 0
    if is_inf(a):
        return (0, 0) if a >> 31 else (POS_INF, 0)
    if is_zero(a):
        return ONE, 0
    sign, e = a >> 31, (a >> 23) & 0xFF
    if e < p.EXP_TINY_E:
        return ONE, NX                     # e^x = 1 + x rounds to 1 for |x| < 2^-25
    if e >= p.EXP_BIG_E:
        return (0, UF | NX) if sign else (POS_INF, OF | NX)
    m = (a & 0x7FFFFF) | 0x800000
    prod = m * exp_rom(p)[e - p.EXP_TINY_E]    # |x|/ln2 with 86 fraction bits
    kmag = (prod >> 86) + ((prod >> 85) & 1)   # round to nearest integer
    f = prod & ((1 << 86) - 1)
    if f >> 85:
        f -= 1 << 86                       # fraction past 1/2: take it from the next integer
    k, f = (-kmag, -f) if sign else (kmag, f)
    g = hyp_g2(p)
    x, _, _ = cordic_run(HROT, g, g, f >> (86 - p.Z), 0, p)
    # x = e^(f ln2) / 2, so the result is x * 2^(k+1).
    exp, sig = normalize_cordic(x, 128 + k, p)
    return pack(0, exp, sig)


def ln_(a: int, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """What fp_cordic.sv returns for LN of a."""
    a &= 0xFFFFFFFF
    if is_nan(a):
        return CANON_NAN, NV if is_snan(a) else 0
    if is_zero(a):
        return 0xFF800000, DZ
    if a >> 31:
        return CANON_NAN, NV
    if is_inf(a):
        return POS_INF, 0
    if a == ONE:
        return 0, 0
    e = (a >> 23) & 0xFF
    W = p.W
    # Near 1: |d| = D * 2^-23 (x in [1, 2)) or D * 2^-24 (x in [1/2, 1)).
    if e == 127 and (a & 0x7FFFFF) < (1 << (23 - p.LN_T)):
        dmag, below = a & 0x7FFFFF, 0
    elif e == 126 and (1 << 24) - ((a & 0x7FFFFF) | 0x800000) < (1 << (24 - p.LN_T)):
        dmag, below = (1 << 24) - ((a & 0x7FFFFF) | 0x800000), 1
    else:
        dmag = None
    if dmag is not None:
        lz = lzc(dmag, 24)
        dn = dmag << lz                    # |d| = dn * 2^-23 * 2^-s
        s = lz + below
        y0 = dn << (W - 25)                # (dn / 2^23) / 4
        # x0 = (2 + d) / 4 = (1 + x) / 4, exact.
        x0 = (1 << (W - 1)) - (dmag << (W - 26)) if below else (1 << (W - 1)) + (dmag << (W - 25))
        _, _, z = cordic_run(LIN, x0, y0, 0, 0, p)
        # ln(1 + d) = +-2 * z * 2^-s.
        exp, sig = normalize_cordic(z, 128 - s, p)
        return pack(below, exp, sig)
    mn, en = unpack_norm(a)
    big = mn >= SQRT2_M                    # m would be >= sqrt(2): halve it
    ep = en - 127 + big                    # x = m * 2^ep, m in [sqrt(1/2), sqrt(2))
    msh = mn << (W - 26) if big else mn << (W - 25)   # m / 4
    x0, y0 = msh + (1 << (W - 2)), msh - (1 << (W - 2))
    below = int(en < 127)                  # x < 1: ln x < 0
    z0 = (abs(ep) * ln2h_const(p)) >> 8
    _, _, z = cordic_run(HVEC, x0, y0, z0, below, p)
    exp, sig = normalize_cordic(z, 128, p)     # ln x = +-2z
    return pack(below, exp, sig)


def mpref_value(a: int) -> float:
    """The value of a finite fp32 pattern as a Python float (for coverage bins)."""
    import struct
    return struct.unpack("<f", struct.pack("<I", a & 0xFFFFFFFF))[0]


def evaluate(op: int, a: int, b: int = 0, p: CordicParams = DEFAULT) -> tuple[int, int]:
    """Model entry point with the same shape as fp32ref.evaluate / mpref.evaluate."""
    if op in (FpuOp.SIN, FpuOp.COS):
        return sincos(op, a, p)
    if op in (FpuOp.ATAN2, FpuOp.HYPOT):
        return vector(op, a, b, p)
    if op == FpuOp.EXP:
        return exp_(a, p)
    if op == FpuOp.LN:
        return ln_(a, p)
    raise ValueError(f"not a CORDIC op: {op}")
