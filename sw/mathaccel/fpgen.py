"""Seeded fp32 stimulus generator, mirrored exactly in SystemVerilog.

The SV twin is the fp_lcg class in hw/tb/uvm/fpu/fp_lcg.svh. Both use the same
32-bit linear congruential generator (LCG) and the same decision order, so a
seed produces the same (op, a, b) stream in xsim UVM, in pyuvm, and in the
Python tests. That lets me reproduce a failing seed in either environment.

An LCG is the simplest repeatable random generator: next = state * A + C,
keeping only the low 32 bits. It is not good randomness for statistics, but
it is fully deterministic and trivial to write identically in two languages.
Low LCG bits are weak (bit 0 just alternates), so every decision below uses
the high bits of the state.

Uniform random 32-bit patterns almost never hit the interesting floating-point
cases (zeros, subnormals, infinities, NaNs, ties), so operands are drawn from
weighted classes instead.
"""

from __future__ import annotations

from enum import IntEnum

LCG_A = 1664525
LCG_C = 1013904223
MASK32 = 0xFFFFFFFF


class OperandClass(IntEnum):
    ZERO = 0
    SUBNORMAL = 1
    NORMAL = 2
    NEAR_ONE = 3        # exponent close to 1.0: ordinary arithmetic values
    INF = 4
    QNAN = 5
    SNAN = 6
    BOUNDARY = 7        # just around the smallest normal number 2^-126
    LARGE = 8           # near the largest finite value (overflow candidates)
    INT_LIKE = 9        # magnitudes up to 2^31, useful for F2I
    RAW = 10            # any 32-bit pattern
    RELATED = 11        # b only: a with its low bits changed (cancellation, ties)


# Cumulative weights out of 256, indexed by the top byte of the LCG state.
# The order of this table is part of the cross-language contract.
CLASS_WEIGHTS = (
    (OperandClass.ZERO, 16),
    (OperandClass.SUBNORMAL, 24),
    (OperandClass.NORMAL, 64),
    (OperandClass.NEAR_ONE, 48),
    (OperandClass.INF, 10),
    (OperandClass.QNAN, 8),
    (OperandClass.SNAN, 8),
    (OperandClass.BOUNDARY, 16),
    (OperandClass.LARGE, 16),
    (OperandClass.INT_LIKE, 22),
    (OperandClass.RAW, 24),
)
assert sum(w for _, w in CLASS_WEIGHTS) == 256

NUM_OPS = 12


class FpLcg:
    def __init__(self, seed: int) -> None:
        self.state = seed & MASK32

    def next(self) -> int:
        """Advance the LCG and return the new 32-bit state."""
        self.state = (self.state * LCG_A + LCG_C) & MASK32
        return self.state

    def pick_class(self) -> OperandClass:
        r = self.next() >> 24                     # top byte, 0..255
        for cls, weight in CLASS_WEIGHTS:
            if r < weight:
                return cls
            r -= weight
        raise AssertionError("weights must sum to 256")

    def operand(self, cls: OperandClass, other: int = 0) -> int:
        """Build one fp32 bit pattern of class `cls` (other = a, for RELATED)."""
        r = self.next()
        sign = (r >> 31) << 31                    # top bit chooses the sign
        if cls == OperandClass.ZERO:
            return sign
        if cls == OperandClass.SUBNORMAL:
            return sign | ((self.next() >> 9) | 1)       # 23-bit fraction, nonzero
        if cls == OperandClass.NORMAL:
            exp = 1 + ((r >> 8) & 0xFF) % 254             # 1..254
            return sign | (exp << 23) | (self.next() >> 9)
        if cls == OperandClass.NEAR_ONE:
            exp = 124 + ((r >> 8) & 0x7)                  # 124..131
            return sign | (exp << 23) | (self.next() >> 9)
        if cls == OperandClass.INF:
            return sign | 0x7F800000
        if cls == OperandClass.QNAN:
            return sign | 0x7FC00000 | (self.next() >> 10)    # 22 payload bits
        if cls == OperandClass.SNAN:
            return sign | 0x7F800000 | ((self.next() >> 10) | 1)  # quiet bit clear
        if cls == OperandClass.BOUNDARY:
            # exponent field 0 or 1 with fractions near all-ones or near zero
            exp = (r >> 8) & 1
            low = self.next() >> 28                       # 0..15
            frac = (0x7FFFFF - low) if (r >> 9) & 1 else low
            return sign | (exp << 23) | frac
        if cls == OperandClass.LARGE:
            exp = 250 + ((r >> 8) & 0x3)                  # 250..253, plus 254 below
            exp = 254 if (r >> 10) & 1 else exp
            return sign | (exp << 23) | (self.next() >> 9)
        if cls == OperandClass.INT_LIKE:
            exp = 127 + ((r >> 8) & 0x1F)                 # 2^0 .. 2^31
            return sign | (exp << 23) | (self.next() >> 9)
        if cls == OperandClass.RAW:
            return self.next()
        if cls == OperandClass.RELATED:
            # Same value with a few low bits changed and a random sign: a-b then
            # cancels almost completely, and a/b sits near 1.0.
            return ((other ^ (self.next() >> 28)) & 0x7FFFFFFF) | sign
        raise ValueError(cls)

    def stimulus(self, op: int | None = None) -> tuple[int, int, int]:
        """Return (op, a, b). op None picks one of the 12 FPU ops uniformly."""
        if op is None:
            op = (self.next() >> 24) % NUM_OPS
        a = self.operand(self.pick_class())
        # One in eight b operands is RELATED to a.
        if (self.next() >> 29) == 0:
            b = self.operand(OperandClass.RELATED, a)
        else:
            b = self.operand(self.pick_class())
        return op, a, b


def stream(seed: int, count: int, op: int | None = None):
    """Yield `count` (op, a, b) tuples from one seed."""
    g = FpLcg(seed)
    for _ in range(count):
        yield g.stimulus(op)
