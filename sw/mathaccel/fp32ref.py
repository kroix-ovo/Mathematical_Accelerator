"""Exact IEEE-754 single-precision reference model with exception flags.

This is the Python golden model for the FPU. It works on raw 32-bit patterns
and returns (result_bits, flags), matching what fpu.sv must produce.

How it stays independent of the hardware design: every finite operation is
done exactly with Python's Fraction (a ratio of unlimited-size integers), and
the exact answer is rounded to fp32 once, in round_fraction(). There is no
float arithmetic anywhere, so the host CPU's FPU, NumPy, and my RTL share no
code path with this model. The tests in sw/tests/test_fp32ref.py check this
model against NumPy (results) and against the C/DPI reference (results and
flags), which together give three independent opinions on every answer.

Behaviour (also written up in docs/numerics.md):
* Round to nearest, ties to even is the only rounding mode.
* Subnormal inputs and outputs are fully supported (no flush to zero).
* Every NaN result is the canonical quiet NaN 0x7FC00000.
* Flags use the RISC-V fflags bit order: NV DZ OF UF NX (bit 4 .. bit 0).
* Underflow is raised when the result is tiny *after rounding* and inexact,
  which is the rule x86 SSE and RISC-V both use.
* MIN/MAX/EQ/LT/LE and the integer conversions follow RISC-V F-extension rules.
"""

from __future__ import annotations

import math
from fractions import Fraction

from mathaccel._generated.spec import FpuFlag, FpuOp

NX, UF, OF, DZ, NV = (int(FpuFlag.NX), int(FpuFlag.UF), int(FpuFlag.OF),
                      int(FpuFlag.DZ), int(FpuFlag.NV))

CANON_NAN = 0x7FC00000
POS_INF = 0x7F800000
NEG_INF = 0xFF800000
INT_MAX = 0x7FFFFFFF
INT_MIN = 0x80000000

# Smallest normal magnitude is 2^-126. Subnormals are multiples of 2^-149.
MIN_NORMAL_EXP = -126
SUBNORMAL_QUANTUM = Fraction(1, 2 ** 149)


# ---------------------------------------------------------------------------
# Field access and classification
# ---------------------------------------------------------------------------

def fields(bits: int) -> tuple[int, int, int]:
    """Split a 32-bit pattern into (sign 1 bit, exponent 8 bits, fraction 23 bits)."""
    return (bits >> 31) & 1, (bits >> 23) & 0xFF, bits & 0x7FFFFF


def is_nan(bits: int) -> bool:
    _, e, f = fields(bits)
    return e == 0xFF and f != 0


def is_snan(bits: int) -> bool:
    # A signaling NaN has the top fraction bit (the "quiet" bit) clear.
    _, e, f = fields(bits)
    return e == 0xFF and f != 0 and not (f >> 22) & 1


def is_inf(bits: int) -> bool:
    _, e, f = fields(bits)
    return e == 0xFF and f == 0


def is_zero(bits: int) -> bool:
    return bits & 0x7FFFFFFF == 0


def to_fraction(bits: int) -> Fraction:
    """Exact value of a finite fp32 pattern."""
    s, e, f = fields(bits)
    if e == 0:
        # Subnormal (or zero): no hidden leading 1, fixed exponent 2^-126.
        mag = Fraction(f, 2 ** 149)
    else:
        # Normal: hidden leading 1 in front of the 23 fraction bits.
        mag = Fraction((1 << 23) | f, 2 ** 23) * Fraction(2) ** (e - 127)
    return -mag if s else mag


def _floor_log2(v: Fraction) -> int:
    """Largest integer k with 2^k <= v, for v > 0, using integer bit lengths."""
    k = v.numerator.bit_length() - v.denominator.bit_length()
    # The bit-length difference is either right or one too high.
    if Fraction(2) ** k > v:
        k -= 1
    return k


def _round_to_quantum(v: Fraction, quantum: Fraction) -> tuple[int, bool]:
    """Round v / quantum to an integer with ties-to-even. Returns (n, inexact)."""
    q = v / quantum
    n = q.numerator // q.denominator          # floor, v is positive
    rem = q - n                               # fractional part in [0, 1)
    if rem > Fraction(1, 2) or (rem == Fraction(1, 2) and n & 1):
        n += 1
    return n, rem != 0


def round_fraction(sign: int, v: Fraction) -> tuple[int, int]:
    """Round a positive exact magnitude v to fp32 with RNE. Returns (bits, flags).

    This one function decides rounding, overflow, underflow, and subnormal
    encoding for every arithmetic operation in the model.
    """
    assert v > 0
    k = _floor_log2(v)                        # v = 1.xxx * 2^k
    flags = 0

    # Tininess "after rounding": round to 24 significant bits as if the
    # exponent range were unlimited, and see whether that is below 2^-126.
    n_unb, _ = _round_to_quantum(v, Fraction(2) ** (k - 23))
    tiny = n_unb * Fraction(2) ** (k - 23) < Fraction(2) ** MIN_NORMAL_EXP

    if k < MIN_NORMAL_EXP:
        # Subnormal range: the spacing between representable values is fixed
        # at 2^-149, so round to a multiple of that.
        n, inexact = _round_to_quantum(v, SUBNORMAL_QUANTUM)
        if inexact:
            flags |= NX
            if tiny:
                flags |= UF
        if n >= 1 << 23:
            # Rounded up into the smallest normal number 2^-126.
            return (sign << 31) | (1 << 23), flags
        return (sign << 31) | n, flags

    # Normal range: 24 significant bits, spacing 2^(k-23).
    n, inexact = _round_to_quantum(v, Fraction(2) ** (k - 23))
    if n == 1 << 24:
        # Rounding carried into a new leading bit (e.g. 1.111...1 -> 10.000).
        n >>= 1
        k += 1
    if inexact:
        flags |= NX
    biased = k + 127
    if biased >= 0xFF:
        # Too large for fp32. With RNE every overflow rounds to infinity.
        return (sign << 31) | POS_INF, OF | NX
    return (sign << 31) | (biased << 23) | (n & 0x7FFFFF), flags


def _signed_result(v: Fraction, zero_sign: int) -> tuple[int, int]:
    """Round an exact signed value; an exact zero gets the sign the caller chose."""
    if v == 0:
        return zero_sign << 31, 0
    return round_fraction(1 if v < 0 else 0, abs(v))


def _nan_flags(*ops: int) -> int:
    """Invalid flag when any operand is a signaling NaN."""
    return NV if any(is_snan(x) for x in ops) else 0


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def fadd(a: int, b: int) -> tuple[int, int]:
    if is_nan(a) or is_nan(b):
        return CANON_NAN, _nan_flags(a, b)
    sa, sb = a >> 31, b >> 31
    if is_inf(a) or is_inf(b):
        if is_inf(a) and is_inf(b) and sa != sb:
            return CANON_NAN, NV               # inf - inf has no answer
        return (a if is_inf(a) else b), 0
    # An exact zero sum is +0 in RNE, except (-0) + (-0) which stays -0.
    zero_sign = 1 if (is_zero(a) and is_zero(b) and sa and sb) else 0
    return _signed_result(to_fraction(a) + to_fraction(b), zero_sign)


def fsub(a: int, b: int) -> tuple[int, int]:
    # a - b is a + (-b). Flipping a NaN's sign does not change NaN handling.
    return fadd(a, b ^ 0x80000000)


def fmul(a: int, b: int) -> tuple[int, int]:
    if is_nan(a) or is_nan(b):
        return CANON_NAN, _nan_flags(a, b)
    sign = (a ^ b) >> 31
    if is_inf(a) or is_inf(b):
        if is_zero(a) or is_zero(b):
            return CANON_NAN, NV               # inf * 0
        return (sign << 31) | POS_INF, 0
    return _signed_result(to_fraction(a) * to_fraction(b), sign)


def fdiv(a: int, b: int) -> tuple[int, int]:
    if is_nan(a) or is_nan(b):
        return CANON_NAN, _nan_flags(a, b)
    sign = (a ^ b) >> 31
    if is_inf(a):
        if is_inf(b):
            return CANON_NAN, NV               # inf / inf
        return (sign << 31) | POS_INF, 0
    if is_inf(b):
        return sign << 31, 0                   # finite / inf = signed zero
    if is_zero(b):
        if is_zero(a):
            return CANON_NAN, NV               # 0 / 0
        return (sign << 31) | POS_INF, DZ      # nonzero / 0
    return _signed_result(to_fraction(a) / to_fraction(b), sign)


def fsqrt(a: int) -> tuple[int, int]:
    if is_nan(a):
        return CANON_NAN, _nan_flags(a)
    if is_zero(a):
        return a, 0                            # sqrt(+-0) = +-0
    if a >> 31:
        return CANON_NAN, NV                   # negative, including -inf
    if is_inf(a):
        return POS_INF, 0
    v = to_fraction(a)
    # sqrt(v) is usually irrational, so I compute root = floor(sqrt(v) * 2^s)
    # with an exact integer square root. s is large enough that root has far
    # more bits than the 24 kept by fp32 plus the guard bit RNE looks at.
    # If root*root is not exactly v*2^(2s), the true root lies strictly
    # between root and root+1, so I use root + 1/2 as a stand-in: it is on the
    # same side of every fp32 rounding boundary as the true value, and it is
    # never mistaken for an exact or exactly-halfway result.
    s = 200  # sqrt(2^-149) is about 2^-75; 200 bits leaves a wide margin
    # v*2^(2s) is an integer because v's denominator is at most 2^149.
    scaled = v * Fraction(2) ** (2 * s)
    root = math.isqrt(scaled.numerator // scaled.denominator)
    exact = root * root * scaled.denominator == scaled.numerator
    approx = Fraction(2 * root + (0 if exact else 1), 2 ** (s + 1))
    return round_fraction(0, approx)


def fi2f(a: int) -> tuple[int, int]:
    """Signed 32-bit integer to fp32."""
    v = a - (1 << 32) if a & 0x80000000 else a
    return _signed_result(Fraction(v), 0)


def ff2i(a: int) -> tuple[int, int]:
    """fp32 to signed 32-bit integer, RNE, saturating (RISC-V fcvt.w.s)."""
    if is_nan(a):
        return INT_MAX, NV
    if is_inf(a):
        return (INT_MIN if a >> 31 else INT_MAX), NV
    v = to_fraction(a)
    n, inexact = _round_to_quantum(abs(v), Fraction(1))
    n = -n if v < 0 else n
    if n > 2 ** 31 - 1:
        return INT_MAX, NV                     # out of range: no NX, only NV
    if n < -(2 ** 31):
        return INT_MIN, NV
    return n & 0xFFFFFFFF, NX if inexact else 0


def _value(bits: int) -> Fraction | float:
    """Exact value of a non-NaN pattern; infinities become +-math.inf.

    Python compares Fraction and float exactly, so mixing them is safe here.
    """
    if is_inf(bits):
        return -math.inf if bits >> 31 else math.inf
    return to_fraction(bits)


def _total_less(a: int, b: int) -> bool:
    """a < b for non-NaN values, with -0 ordered below +0 (used by MIN/MAX)."""
    va, vb = _value(a), _value(b)
    if va == vb:
        return bool(a >> 31) and not (b >> 31)  # only differs for -0 vs +0
    return va < vb


def fmin(a: int, b: int) -> tuple[int, int]:
    flags = _nan_flags(a, b)
    if is_nan(a) and is_nan(b):
        return CANON_NAN, flags
    if is_nan(a):
        return b, flags                        # a NaN loses to a number
    if is_nan(b):
        return a, flags
    return (a if _total_less(a, b) or a == b else b), flags


def fmax(a: int, b: int) -> tuple[int, int]:
    flags = _nan_flags(a, b)
    if is_nan(a) and is_nan(b):
        return CANON_NAN, flags
    if is_nan(a):
        return b, flags
    if is_nan(b):
        return a, flags
    return (b if _total_less(a, b) else a), flags


def feq(a: int, b: int) -> tuple[int, int]:
    # Quiet comparison: only a signaling NaN raises invalid.
    if is_nan(a) or is_nan(b):
        return 0, _nan_flags(a, b)
    return int(_value(a) == _value(b)), 0     # -0 == +0 here


def flt(a: int, b: int) -> tuple[int, int]:
    # Signaling comparison: any NaN raises invalid.
    if is_nan(a) or is_nan(b):
        return 0, NV
    return int(_value(a) < _value(b)), 0


def fle(a: int, b: int) -> tuple[int, int]:
    if is_nan(a) or is_nan(b):
        return 0, NV
    return int(_value(a) <= _value(b)), 0


_OPS = {
    FpuOp.ADD: fadd, FpuOp.SUB: fsub, FpuOp.MUL: fmul, FpuOp.DIV: fdiv,
    FpuOp.MIN: fmin, FpuOp.MAX: fmax, FpuOp.EQ: feq, FpuOp.LT: flt, FpuOp.LE: fle,
}
_UNARY = {FpuOp.SQRT: fsqrt, FpuOp.I2F: fi2f, FpuOp.F2I: ff2i}


def evaluate(op: int, a: int, b: int = 0) -> tuple[int, int]:
    """Apply FPU operation `op` to raw operand bits. Returns (result_bits, flags)."""
    if op in _UNARY:
        return _UNARY[op](a & 0xFFFFFFFF)
    if op in _OPS:
        return _OPS[op](a & 0xFFFFFFFF, b & 0xFFFFFFFF)
    raise ValueError(f"unknown FPU op {op}")


def is_unary(op: int) -> bool:
    return op in _UNARY
