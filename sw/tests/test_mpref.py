"""Checks for the phase 3 references (mpref.py, fp_ref.c) and the ULP tools.

* Directed values: correctly rounded results I can check against published
  float values (sinf(1), logf(2), pi ...) and the flag rules.
* The C reference against mpmath on generated inputs for all six functions,
  results and flags. They share no code: one is libm in double precision with
  its own argument reduction, the other mpmath at 256 bits.
* The ULP arithmetic in ulp.py, on values whose error I can work out by hand.
"""

from __future__ import annotations

import random
import struct

import pytest

from mathaccel import cref, mpref, ulp
from mathaccel._generated.spec import FpuOp as O
from mathaccel.fp32ref import CANON_NAN, DZ, NV, NX, OF, UF


def f32(x: float) -> int:
    """Encode a readable test value as fp32 bits before asking either reference to evaluate it."""
    return struct.unpack("<I", struct.pack("<f", x))[0]


ONE, INF, NINF = 0x3F800000, 0x7F800000, 0xFF800000
SNAN, QNAN = 0x7F800001, 0x7FC00000


@pytest.mark.parametrize(("op", "a", "b", "want"), [
    # Published float values (C's sinf, cosf, expf, logf, atan2f with correct rounding).
    (O.SIN, ONE, 0, (0x3F576AA4, NX)),
    (O.COS, ONE, 0, (0x3F0A5140, NX)),
    (O.EXP, ONE, 0, (0x402DF854, NX)),
    (O.LN, 0x40000000, 0, (0x3F317218, NX)),
    (O.ATAN2, ONE, 0xBF800000, (0x4016CBE4, NX)),         # 3*pi/4
    (O.ATAN2, 0x80000000, 0x80000000, (0xC0490FDB, NX)),  # atan2(-0, -0) = -pi
    (O.ATAN2, ONE, 0, (0x3FC90FDB, NX)),                  # pi/2
    (O.SIN, 0x7F7FFFFF, 0, (0xBF0599B3, NX)),             # sin(FLT_MAX) = -0.5218771
    (O.HYPOT, f32(3.0), f32(4.0), (f32(5.0), NX)),        # exact value, NX by rule
    # The exact list: no flags.
    (O.SIN, 0x80000000, 0, (0x80000000, 0)),
    (O.COS, 0x80000000, 0, (ONE, 0)),
    (O.EXP, 0x80000000, 0, (ONE, 0)),
    (O.LN, ONE, 0, (0, 0)),
    (O.ATAN2, 0x80000000, ONE, (0x80000000, 0)),
    (O.ATAN2, ONE, INF, (0, 0)),
    (O.HYPOT, 0xC0400000, 0x80000000, (0x40400000, 0)),
    (O.EXP, NINF, 0, (0, 0)),
    (O.EXP, INF, 0, (INF, 0)),
    (O.LN, INF, 0, (INF, 0)),
    # Invalid, divide by zero, overflow, underflow.
    (O.SIN, INF, 0, (CANON_NAN, NV)),
    (O.COS, SNAN, 0, (CANON_NAN, NV)),
    (O.COS, QNAN, 0, (CANON_NAN, 0)),
    (O.LN, 0xBF800000, 0, (CANON_NAN, NV)),
    (O.LN, 0x80000000, 0, (NINF, DZ)),
    (O.EXP, f32(89.0), 0, (INF, OF | NX)),
    (O.EXP, f32(-104.0), 0, (0x00000000, UF | NX)),
    (O.SIN, 0x00000005, 0, (0x00000005, UF | NX)),        # sin x = x for tiny x, still inexact
    (O.HYPOT, INF, SNAN, (INF, NV)),                      # inf beats NaN, sNaN still signals
    (O.HYPOT, QNAN, ONE, (CANON_NAN, 0)),
    (O.HYPOT, 0x7F7FFFFF, 0x7F7FFFFF, (INF, OF | NX)),
])
def test_directed(op, a, b, want):
    """Pin known function values, signed axes and invalid cases independently of random sampling."""
    assert mpref.evaluate(op, a, b) == want


@pytest.fixture(scope="module")
def c():
    """Load the independent C arithmetic reference when a compiler is available."""
    lib = cref.load()
    if lib is None:
        pytest.skip("no C compiler for the DPI reference")
    return lib


def _inputs(op: int, n: int, seed: int) -> list[tuple[int, int]]:
    """Operands for one function: directed and random, spread over the exponent range."""
    rng = random.Random(seed)
    if op in (O.SIN, O.COS):
        return [(x, 0) for x in ulp.sincos_inputs(n, seed)]
    if op == O.EXP:
        return [(ulp.random_exponent_bits(rng, 100, 133), 0) for _ in range(n)] + \
               [(rng.getrandbits(32), 0) for _ in range(n // 8)]
    if op == O.LN:
        return [(rng.getrandbits(31), 0) for _ in range(n)] + [(rng.getrandbits(32), 0) for _ in range(n // 8)]
    return [(ulp.random_exponent_bits(rng, 90, 160), ulp.random_exponent_bits(rng, 90, 160)) for _ in range(n)] + \
           [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(n // 4)]


@pytest.mark.parametrize("op", list(mpref.OPS))
def test_c_reference_matches_mpmath(c, op):
    """Results and flags agree on every input. A disagreement would mean one of
    the two references is wrong, or (rarely, for C) a result within 2^-29 ULP
    of a halfway point; none occur in these sets."""
    bad = []
    for a, b in _inputs(op, 1500, 0x3000 + op):
        got, want = c.eval(op, a, b), mpref.evaluate(op, a, b)
        if got != want:
            bad.append(f"a={a:08x} b={b:08x} C={got[0]:08x}/{got[1]:02x} mpmath={want[0]:08x}/{want[1]:02x}")
    assert not bad, f"{O(op).name}: {len(bad)} differences:\n" + "\n".join(bad[:10])


def test_c_batch_matches_single(c):
    """Require the fast array interface to preserve the single-operation result and flags."""
    xs = [x for x, _ in _inputs(O.SIN, 200, 7)]
    r, f = c.eval_many(O.SIN, xs)
    assert [(int(v), int(g)) for v, g in zip(r, f, strict=True)] == [c.eval(O.SIN, x) for x in xs]


def test_ulp_of():
    """Check the fp32 spacing calculation across ordinary magnitudes and the subnormal floor."""
    from fractions import Fraction
    assert ulp.ulp_of(Fraction(1)) == Fraction(1, 2 ** 23)
    assert ulp.ulp_of(Fraction(3, 2)) == Fraction(1, 2 ** 23)
    assert ulp.ulp_of(Fraction(-2)) == Fraction(1, 2 ** 22)
    assert ulp.ulp_of(Fraction(1, 2 ** 140)) == Fraction(1, 2 ** 149)   # subnormal floor


def test_ulp_error_of_neighbours():
    """The correctly rounded result is within 0.5 ULP; its neighbours are
    between 0.5 and 1.5 ULP away; two steps away is more than 1.5."""
    for x in (ONE, 0x40490FDB, 0x3A000123, 0x7F000000):
        v = mpref.exact_value(O.SIN, x)
        r, _ = mpref.evaluate(O.SIN, x)
        assert ulp.ulp_error(r, v) <= 0.5
        for nb in (r - 1, r + 1):
            assert 0.5 <= ulp.ulp_error(nb, v) < 1.5
        assert ulp.ulp_error(r + 2, v) > 1.5


def test_stats_flag_a_bad_result():
    """Make sure an intentionally wrong answer is counted as a failure by the accuracy checker."""
    st = ulp.UlpStats(budget=1.0)
    x = f32(0.7)
    r, f = mpref.evaluate(O.COS, x)
    assert st.add(O.COS, x, 0, r, f) is None
    # The other float bracketing the true value is under 1 ULP away; the
    # neighbour on the far side is over 1 ULP away.
    other = r + 1 if mpref.exact_value(O.COS, x) > mpref.to_mpf(r) else r - 1
    assert st.add(O.COS, x, 0, other, f) is None
    assert st.add(O.COS, x, 0, r + 3, f) is not None   # three is not
    assert st.add(O.COS, x, 0, r, 0) is not None       # missing NX
    assert st.add(O.SIN, INF, 0, CANON_NAN, 0) is not None  # rule result without NV
    assert st.errors() == 3
