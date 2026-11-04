"""Correctly rounded reference for the phase 3 functions, using mpmath.

The phase 3 ops (SIN, COS, ATAN2, HYPOT, EXP, LN) have irrational results, so
the hardware can't be checked bit for bit the way fp32ref.py checks the phase 2
ops. This module gives the true answer instead: I evaluate the function with
mpmath at PREC bits, far more than fp32 needs, and round the result to fp32
once with fp32ref.round_fraction(). That is the correctly rounded result, the
best any implementation could return. mathaccel.ulp measures how far the
hardware lands from the exact value.

I use 256-bit working precision to leave a large margin beyond fp32's
24 significant bits. Converting that binary approximation to a Fraction and
rounding it once avoids an extra fp32 rounding. This is a high-precision
reference, not an interval proof for every possible input: an answer extremely
close to a rounding midpoint would need a separate precision check. HYPOT
can also have an exact halfway result; its exact squared inputs preserve
that case so the final rounding can apply the ties-to-even rule.

Each function takes raw 32-bit patterns and returns (result_bits, flags), like
fp32ref.evaluate(). The rules are shared with the hardware and the C reference
(hw/tb/uvm/dpi/fp_ref.c) and come from docs/phase3-plan.md:

* Round to nearest even, canonical NaN 0x7FC00000, the same five flags.
* NV for an sNaN input, sin/cos of +-inf, ln of a negative number.
* DZ for ln(+-0) = -inf.
* OF and UF come from the rounding itself (exp overflow, tiny results). UF uses
  the same "tiny after rounding" rule as phase 2.
* NX for every result except a fixed list of exact cases. The hardware can't
  tell in general when a result is exact (hypot(3, 4) = 5 is exact but still
  raises NX), so the list is the rule:
    sin(+-0) = +-0          cos(+-0) = 1          exp(+-0) = 1
    ln(1) = +0              atan2(+-0, x) = +-0 for x = +0 or x > 0
    hypot(x, +-0) = |x| and hypot(+-0, y) = |y|
  plus results with an infinite input that are exact in the limit, following
  C99 Annex F: exp(+inf) = +inf, exp(-inf) = +0, ln(+inf) = +inf,
  hypot(+-inf, y) = +inf (even when y is a NaN), and atan2(+-y, +inf) = +-0
  for finite y. Angles such as atan2(1, 0) = pi/2 or atan2(+0, -1) = pi are
  irrational and raise NX like everything else.
"""

from __future__ import annotations

from fractions import Fraction

import mpmath

from mathaccel._generated.spec import FpuOp
from mathaccel.fp32ref import (
    CANON_NAN,
    DZ,
    NV,
    NX,
    POS_INF,
    UF,
    is_inf,
    is_nan,
    is_snan,
    is_zero,
    round_fraction,
    to_fraction,
)

# Working precision in bits. fp32 keeps 24; the rest is margin.
PREC = 256

ONE = 0x3F800000
NEG_INF = 0xFF800000
SIGN = 0x80000000

# Ops this module covers, in op-code order, and the ones that ignore b.
OPS = (FpuOp.SIN, FpuOp.COS, FpuOp.ATAN2, FpuOp.HYPOT, FpuOp.EXP, FpuOp.LN)
UNARY = (FpuOp.SIN, FpuOp.COS, FpuOp.EXP, FpuOp.LN)


def to_mpf(bits: int) -> mpmath.mpf:
    """Exact value of a finite fp32 pattern as an mpmath number."""
    fr = to_fraction(bits)
    # The denominator is a power of two, so this division is exact as long as
    # the precision covers 24 bits, which it always does here.
    return mpmath.mpf(fr.numerator) / fr.denominator


def mpf_to_fraction(v: mpmath.mpf) -> Fraction:
    """Value of an mpmath number as a Fraction. mpf stores man * 2^exp with integer man.

    The conversion is exact for every magnitude between 2^-400 and 2^400.
    Outside that range I clamp to +-2^-400 or +-2^400: fp32 rounds both the
    same way as the true value (to zero with UF, or to infinity with OF), and
    exp(-1e38) would otherwise need a Fraction with a 10^38-bit denominator.
    """
    sign = -1 if v < 0 else 1
    mag = mpmath.mag(v)                        # roughly log2 |v|
    if mag > 400:
        return sign * Fraction(2) ** 400
    if mag < -400:
        return sign * Fraction(1, 2 ** 400)
    return sign * (Fraction(int(v.man)) * Fraction(2) ** int(v.exp))


def round_mpf(v: mpmath.mpf) -> tuple[int, int]:
    """Round a finite, nonzero mpmath value to fp32 with RNE. Returns (bits, flags).

    Flags come from round_fraction: NX when the value isn't exactly
    representable, plus OF or UF when it rounds out of the normal range.
    """
    fr = mpf_to_fraction(v)
    return round_fraction(1 if fr < 0 else 0, abs(fr))


def _nan_result(*ops: int) -> tuple[int, int]:
    """Canonical NaN, with NV only when an input was a signaling NaN."""
    return CANON_NAN, NV if any(is_snan(x) for x in ops) else 0


# ---------------------------------------------------------------------------
# Rule-decided results
# ---------------------------------------------------------------------------
# special() returns the (bits, flags) of every case decided by a rule: NaNs,
# the invalid cases, and the exact list above. Those are checked bit for bit.
# It returns None for everything else, which exact_value() then computes.

def special(op: int, a: int, b: int = 0) -> tuple[int, int] | None:
    """Result and flags for NaN, invalid, and exact-list cases; None otherwise."""
    if op in (FpuOp.SIN, FpuOp.COS):
        if is_nan(a):
            return _nan_result(a)
        if is_inf(a):
            return CANON_NAN, NV               # no limit exists
        if is_zero(a):
            return (a, 0) if op == FpuOp.SIN else (ONE, 0)
        return None
    if op == FpuOp.EXP:
        if is_nan(a):
            return _nan_result(a)
        if is_inf(a):
            return (0, 0) if a & SIGN else (POS_INF, 0)
        if is_zero(a):
            return ONE, 0
        return None
    if op == FpuOp.LN:
        if is_nan(a):
            return _nan_result(a)
        if is_zero(a):
            return NEG_INF, DZ
        if a & SIGN:
            return CANON_NAN, NV               # negative numbers, including -inf
        if is_inf(a):
            return POS_INF, 0
        if a == ONE:
            return 0, 0
        return None
    if op == FpuOp.ATAN2:
        y, x = a, b
        if is_nan(y) or is_nan(x):
            return _nan_result(y, x)
        # y = +-0 with x = +0 or x > 0 (including +inf), and finite y with
        # x = +inf: the angle is exactly +-0, carrying the sign of y.
        if not x & SIGN and (is_zero(y) or (is_inf(x) and not is_inf(y))):
            return y & SIGN, 0
        return None
    if op == FpuOp.HYPOT:
        # An infinite input gives +inf even when the other input is a NaN.
        # A signaling NaN still raises NV.
        if is_inf(a) or is_inf(b):
            return POS_INF, NV if (is_snan(a) or is_snan(b)) else 0
        if is_nan(a) or is_nan(b):
            return _nan_result(a, b)
        if is_zero(b):
            return a & ~SIGN, 0
        if is_zero(a):
            return b & ~SIGN, 0
        return None
    raise ValueError(f"not a phase 3 op: {op}")


# ---------------------------------------------------------------------------
# Exact values
# ---------------------------------------------------------------------------

def _atan2_value(y: int, x: int) -> mpmath.mpf:
    """atan2 for the non-special cases, including infinities and signed zeros.

    mpmath has no signed zero and no infinite fp32 inputs, so I handle the
    axis and infinity cases by hand (C99 Annex F) and call mpmath.atan2 only
    for two finite, nonzero inputs.
    """
    neg = bool(y & SIGN)
    pi = mpmath.pi
    if is_inf(y):
        if is_inf(x):
            v = 3 * pi / 4 if x & SIGN else pi / 4
        else:
            v = pi / 2
    elif is_zero(y):
        v = pi                                 # x is -0 or negative here
    elif is_inf(x):
        v = pi                                 # x = -inf; x = +inf was special
    elif is_zero(x):
        v = pi / 2
    else:
        return mpmath.atan2(to_mpf(y), to_mpf(x))
    return -v if neg else v


def exact_value(op: int, a: int, b: int = 0) -> mpmath.mpf | None:
    """High-precision value of op(a, b), or None when special() decides the case.

    The value is finite and nonzero, except that EXP of a large negative
    number may be so small that it is still positive but far below fp32's
    range; that is fine for rounding.
    """
    if special(op, a, b) is not None:
        return None
    with mpmath.workprec(PREC):
        if op == FpuOp.SIN:
            return mpmath.sin(to_mpf(a))
        if op == FpuOp.COS:
            return mpmath.cos(to_mpf(a))
        if op == FpuOp.EXP:
            return mpmath.exp(to_mpf(a))
        if op == FpuOp.LN:
            return mpmath.log(to_mpf(a))
        if op == FpuOp.ATAN2:
            return _atan2_value(a, b)
        if op == FpuOp.HYPOT:
            # a^2 + b^2 exactly, as a Fraction, then one square root at a
            # precision wide enough to hold that sum without rounding (squares
            # of fp32 values span about 2^-298 .. 2^256, under 600 bits).
            s = to_fraction(a) ** 2 + to_fraction(b) ** 2
            with mpmath.workprec(PREC + 640):
                exact_sum = mpmath.mpf(s.numerator) / s.denominator
            return mpmath.sqrt(exact_sum)
    raise ValueError(f"not a phase 3 op: {op}")


def evaluate(op: int, a: int, b: int = 0) -> tuple[int, int]:
    """Correctly rounded op(a, b) with the phase 3 flag rules. Returns (bits, flags)."""
    a &= 0xFFFFFFFF
    b &= 0xFFFFFFFF
    rule = special(op, a, b)
    if rule is not None:
        return rule
    bits, flags = round_mpf(exact_value(op, a, b))
    # Every result off the exact list counts as inexact, even if it happens to
    # be representable (hypot(3, 4) = 5). Inexact and tiny means UF, so a
    # representable subnormal result needs UF added here: round_fraction only
    # raised it for values it actually had to round. This also covers sin(x)
    # for subnormal x, where x - x^3/6 differs from x by less than 2^-256
    # relative and mpmath returns exactly x.
    if not flags & NX and (bits & 0x7FFFFFFF) < 0x00800000:
        flags |= UF
    return bits, flags | NX


def is_unary(op: int) -> bool:
    """Say whether this function ignores operand b; atan2 and hypot need both inputs."""
    return op in UNARY
