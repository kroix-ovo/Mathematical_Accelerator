"""Find the fp32 inputs that come closest to a multiple of pi/2.

Usage: python tools/ph_worst_case.py [--top N]

sin x or cos x is nearly zero when x is close to k * pi/2, and that is where
argument reduction loses the most: x * (2/pi) is then very close to an
integer, and the fraction left after removing the integer (the reduced angle)
has many leading zeros. The number of bits of 2/pi the Payne-Hanek step has to
use is set by how small that fraction can get, so I find the minimum by
checking every positive normal float from 2^-12 up (smaller inputs take the
sin x = x shortcut and never reach the reduction).

For each exponent the input is M * 2^E with M = 2^23 .. 2^24-1. I store
frac(2^E * 2/pi) as a 64-bit fixed-point number A; then M * A modulo 2^64 is
frac(x * 2/pi) * 2^64, with an error below M * 2^-64 < 2^-40. NumPy's uint64
multiply wraps modulo 2^64 exactly, so one vector multiply checks all 2^23
mantissas of an exponent. The whole search takes about a minute.

Result (October 2026): the closest input is 16367173 * 2^72 (0x6F79BE45),
whose reduced fraction is 2^-29.86 of a quarter turn. That matches the known
worst case for single precision.
"""

from __future__ import annotations

import argparse
import math

import mpmath
import numpy as np


def main() -> None:
    """Search every normal exponent that reaches reduction and report its closest sampled mantissa."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=6)
    args = ap.parse_args()
    mpmath.mp.prec = 400
    two_over_pi = 2 / mpmath.pi
    m = np.arange(1 << 23, 1 << 24, dtype=np.uint64)
    best = []
    # Biased exponents 115 .. 254 cover 2^-12 <= x < 2^128.
    for e in range(115, 255):
        a = two_over_pi * mpmath.mpf(2) ** (e - 150)
        frac = a - mpmath.floor(a)
        a64 = int(mpmath.floor(frac * mpmath.mpf(2) ** 64))
        v = m * np.uint64(a64)                     # frac(x * 2/pi) * 2^64, wrapped
        dist = np.minimum(v, np.uint64(0) - v)     # distance to the nearest integer
        i = int(np.argmin(dist))
        best.append((int(dist[i]) / 2.0 ** 64, e, int(m[i])))
    best.sort()
    for d, e, mant in best[:args.top]:
        bits = (e << 23) | (mant & 0x7FFFFF)
        print(f"0x{bits:08X} = {mant} * 2^{e - 150}: reduced fraction {d:.3e} = 2^{math.log2(d):.2f} quarter turns")


if __name__ == "__main__":
    main()
