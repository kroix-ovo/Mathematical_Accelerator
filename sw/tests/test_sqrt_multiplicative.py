"""Checks for my multiplicative square root (experiment S2, reused in hw/rtl/fp/fp_divsqrt.sv).

The FPU testbenches compare the RTL's results with the exact oracle on directed
and random operands. These tests go further and check the design's arithmetic for
every input that can change its answer. A square root's significand bits depend
only on the radicand's 23 fraction bits and whether its exponent is odd or even:
the rest of the exponent just moves the result's exponent. So 2 x 2^23 cases
cover the normalized finite-positive arithmetic classes. I run all of them
through an integer copy of the RTL datapath, with the RTL's widths and floors.
I still need the RTL tests for classification, rounding, flags, tags, pipeline
alignment and queue behavior; this Python model doesn't clock any hardware.

I keep these checks separate so I can see what broke:
* I measure how far R0 is from the true root (less than 1.1 units), then
  independently check that choosing R0 - 1, R0 or R0 + 1 really works;
* after the correction, the root and sticky bit equal the baseline's exact
  isqrt(N) and (N != R^2) for every case;
* every intermediate fits the register width I gave it in the RTL.
"""

from __future__ import annotations

from math import isqrt

import numpy as np

IB = 9                       # table index bits after the leading 1


def seed(p: int, i: int) -> int:
    """The RTL's table entry: about 2^16 / sqrt(midpoint of the interval).

    Interval i of parity p is a in [m (1 + i/512), m (1 + (i+1)/512)) with m = 1
    for an even exponent and m = 2 for an odd one. y0^2 = 2 / (lo + hi) balances
    the error e = 1 - a y0^2 at the two ends. In integers that's
    round(sqrt(2^42 / (m (1025 + 2i)))), which I compute as half of
    isqrt(2^44 / (m (1025 + 2i))) rounded, so the RTL can fill its ROM with the
    same integer loop."""
    m = 2 if p else 1
    return (isqrt((1 << 44) // (m * (1025 + 2 * i))) + 1) >> 1


SEED = np.array([seed(p, i) for p in (0, 1) for i in range(1 << IB)], dtype=np.int64)


def datapath(na: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    """The RTL's arithmetic on arrays of significands na (leading 1 at bit 23) and
    exponent parities p. numpy's >> on int64 is a floor, like SV's >>> on signed."""
    # I use parity plus nine fraction bits as the ROM address, just like RTL.
    # These stage numbers are S3's schedule; S2 reaches correction earlier.
    y0 = SEED[(p << IB) | ((na >> 14) & 511)]
    s0 = na * y0                                  # s2/s3: about sqrt(a), 40 bits
    t = s0 * y0                                   # s4/s5: about 1, 56 bits
    # I retain the mathematical E here. RTL recovers the same signed value
    # from its low 48 bits; test_register_widths checks that equivalence's range.
    e = (np.int64(1) << 55) - (t << p)            # s6: e = 1 - a*y0^2 at scale 2^-55, exact
    eh = e >> 30                                  # e at scale 2^-25, 18 bits signed
    ehalf = e >> 16                               # e/2 at scale 2^-40
    e2 = eh * eh                                  # s7/s8: e^2 at scale 2^-50
    c = ehalf + ((3 * e2) >> 13)                  # s9: e/2 + 3e^2/8 at scale 2^-40
    sh = s0 >> 16                                 # top 24 bits of s0
    pc = sh * c                                   # s10/s11
    g = (s0 >> (10 - p)) + (pc >> (34 - p))       # s12: the root with 4 fraction bits
    r0 = g >> 4
    n = na << (27 + p)                            # the radicand the baseline takes the root of
    sq = (r0 * r0) & ((1 << 29) - 1)              # s13/s14: low 29 bits of R0^2
    nlow = n & ((1 << 29) - 1)
    rem = (nlow - sq) & ((1 << 29) - 1)           # s15: N - R0^2 modulo 2^29, read as signed
    rem = np.where(rem >= 1 << 28, rem - (1 << 29), rem)
    tw = 2 * r0 + 1
    # I correct at s19 in S3 (s16 in standalone S2). Squaring R0+1 adds
    # 2*R0+1; stepping down restores 2*R0-1 to the remainder.
    dn, up = rem < 0, rem >= tw
    r = np.where(dn, r0 - 1, np.where(up, r0 + 1, r0))
    rest = np.where(dn, rem + 2 * r0 - 1, np.where(up, rem - tw, rem))
    return dict(y0=y0, s0=s0, t=t, e=e, eh=eh, ehalf=ehalf, e2=e2, c=c, sh=sh, pc=pc, g=g, r0=r0,
                n=n, rem=rem, r=r, rest=rest)


def exact_root(n: np.ndarray) -> np.ndarray:
    """isqrt for n < 2^52: float64 holds n exactly, and I fix the last unit."""
    # I use float64 only to start the independent integer-root answer.
    # The following integer comparisons repair either neighboring estimate.
    r = np.floor(np.sqrt(n.astype(np.float64))).astype(np.int64)
    r = np.where(r * r > n, r - 1, r)
    return np.where((r + 1) * (r + 1) <= n, r + 1, r)


def test_table_matches_its_definition():
    assert len(SEED) == 1024
    assert int(SEED.min()) >= 1 << 15 and int(SEED.max()) < 1 << 16   # 16 bits, top bit always set
    # Spot-check the integer formula against the real number it stands for.
    for p, i in ((0, 0), (0, 511), (1, 0), (1, 300)):
        m = 2 if p else 1
        want = (2.0 / (m * (2 + (2 * i + 1) / 512))) ** 0.5 * 65536
        assert abs(seed(p, i) - want) <= 0.5 + 1e-9


def test_every_square_root_is_exact():
    # I enumerate every normalized significand, then both exponent parities.
    # Different absolute exponents reuse this arithmetic and shift the result.
    na = np.arange(1 << 23, 1 << 24, dtype=np.int64)
    for parity in (0, 1):
        p = np.full_like(na, parity)
        d = datapath(na, p)
        truth = exact_root(d["n"])
        # I record the approximation error, then compare the corrected root
        # and exact remainder separately. The error bound alone isn't the
        # test of correctness, particularly near an integer-root boundary.
        err = np.abs(d["r0"].astype(np.float64) - np.sqrt(d["n"].astype(np.float64)))
        assert float(err.max()) < 1.1, float(err.max())
        assert np.array_equal(d["r"], truth)
        assert np.array_equal(d["rest"], d["n"] - truth * truth)
        assert int(d["r"].min()) >= 1 << 25 and int(d["r"].max()) < 1 << 26


def test_register_widths():
    """The widths I declared in the RTL hold every value, for both parities."""
    na = np.arange(1 << 23, 1 << 24, dtype=np.int64)
    for parity in (0, 1):
        d = datapath(na, np.full_like(na, parity))

        def fits_signed(x, bits):
            return int(x.min()) >= -(1 << (bits - 1)) and int(x.max()) < 1 << (bits - 1)

        assert int(d["s0"].max()) < 1 << 40
        assert int(d["t"].max()) < 1 << 56
        assert fits_signed(d["e"], 48)
        assert fits_signed(d["eh"], 18)
        assert fits_signed(d["ehalf"], 32)
        assert int(d["e2"].max()) < 1 << 36
        assert fits_signed(d["c"], 32)
        assert fits_signed(d["pc"], 57)
        assert fits_signed(d["g"], 33) and int(d["g"].min()) > 0
        assert int(d["r0"].max()) < 1 << 27
        assert fits_signed(d["rem"], 29)
