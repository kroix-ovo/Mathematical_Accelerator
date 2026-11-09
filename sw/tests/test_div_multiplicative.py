"""Checks for my multiplicative divider (hw/rtl/fp/fp_div.sv, experiment E).

The FPU testbenches compare the RTL's results with the exact oracle. These
tests check the error bound the design depends on, for every possible divisor,
which random vectors can't do:
* the reciprocal estimate Y1t, computed with exactly the RTL's integer widths
  and floors, is within 32 units of 2^55 / D for all 2^23 divisor significands;
* so Q0 = floor(X * Y1t / 2^29) is within 1 of X * 2^26 / D for every X, and
  the remainder correction recovers floor(X * 2^26 / D) and the sticky bit,
  checked on random and boundary operands at the full width.
"""

from __future__ import annotations

import random

import numpy as np


def seed(i):
    """The RTL's table: round(2^24 / (1025 + 2i)), as add-one-half-and-floor."""
    return (((1 << 25) // (1025 + 2 * i)) + 1) >> 1


def reciprocal(d):
    """Y1t for divisor significand(s) d, exactly as the RTL computes it (numpy int64)."""
    y0 = seed((d >> 14) & 511)
    e = (1 << 37) - d * y0                 # e = 1 - d*y0 at scale 2^-37
    eh = e >> 11                           # numpy >> on int64 is a floor, like >>> in SV
    es = e >> 4
    s = es + ((eh * eh) >> 19)             # e + e^2 at scale 2^-33
    return ((y0 << 33) + y0 * s) >> 15     # y1 at scale 2^-32


def divide(x: int, d: int) -> tuple[int, bool]:
    """The whole RTL datapath on Python ints: estimate, then correct by +-1."""
    y1t = int(reciprocal(np.int64(d)))
    q0 = (x * y1t) >> 29
    # The RTL keeps only the low 27 bits of R0 and reads them as signed.
    r0 = (((x & 1) << 26) - ((q0 * d) & ((1 << 27) - 1))) & ((1 << 27) - 1)
    if r0 >= 1 << 26:
        r0 -= 1 << 27
    if r0 < 0:
        return q0 - 1, r0 + d != 0
    if r0 >= d:
        return q0 + 1, r0 - d != 0
    return q0, r0 != 0


def test_table_fits_14_bits():
    entries = [seed(i) for i in range(512)]
    assert min(entries) >= 1 << 13 and max(entries) < 1 << 14


def test_reciprocal_error_for_every_divisor():
    """All 2^23 divisors. The limit is 32 units: with X < 2^24 it keeps
    |X * Y1t / 2^29 - X * 2^26 / D| below 1."""
    d = np.arange(1 << 23, 1 << 24, dtype=np.int64)
    y1t = reciprocal(d)
    assert int(y1t.max()) < 1 << 32                       # fits the RTL's 32-bit register
    e = (1 << 37) - d * seed((d >> 14) & 511)
    assert int(np.abs(e >> 11).max()) < 1 << 17           # Eh fits 18 bits signed
    s = (e >> 4) + (((e >> 11) * (e >> 11)) >> 19)
    assert int(np.abs(s).max()) < 1 << 24                 # S fits 25 bits signed
    # 2^55 / D in floating point is off by far less than 0.01 units here.
    err = y1t.astype(np.float64) - np.ldexp(1.0, 55) / d.astype(np.float64)
    assert np.abs(err).max() < 31.0, np.abs(err).max()


def test_corrected_quotient_is_exact():
    rng = random.Random(0xE5)
    edges = [1 << 23, (1 << 23) + 1, (1 << 24) - 1, (1 << 24) - 2, 0xAAAAAA, 0xD55555, 3 << 22]
    divisors = edges + [(1 << 23) + (i << 14) + off for i in range(512) for off in (0, 1)] + [(1 << 24) - 1 - (i << 14)
                                                                                           for i in range(512)]
    cases = [(x, d) for d in divisors for x in edges]
    cases += [(rng.randrange(1 << 23, 1 << 24), rng.randrange(1 << 23, 1 << 24)) for _ in range(30000)]
    for x, d in cases:
        assert divide(x, d) == ((x << 26) // d, (x << 26) % d != 0), (x, d)
