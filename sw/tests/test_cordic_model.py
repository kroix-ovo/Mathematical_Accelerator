"""Quick checks of the bit-accurate CORDIC model (sw/mathaccel/cordic_model.py).

The thorough accuracy measurement is tools/measure_ulp.py (a million inputs
per op); this keeps a small version of it in the normal test run, so a change
to the model or its widths that breaks the 1 ULP budget fails CI.
"""

from __future__ import annotations

from mathaccel import cordic_model as M
from mathaccel import ulp
from mathaccel._generated.spec import FpuOp


def test_rom_windows_line_up_with_two_over_pi():
    """The window for exponent e starts at fraction bit b_(e-151) of 2/pi."""
    p = M.DEFAULT
    bits = M.two_over_pi_bits(400)
    for e in (p.TINY_E, 150, 151, 152, 200, 254):
        s = e - 151
        want = 0
        for j in range(p.L):            # build the window one bit at a time
            i = s + j                   # bit index b_i, i <= 0 means zero
            bit = (bits >> (400 - i)) & 1 if i >= 1 else 0
            want = (want << 1) | bit
        assert M.ph_rom(p)[e - p.TINY_E] == want, f"e={e}"


def test_reduction_of_the_worst_case():
    """16367173 * 2^72 lies 2^-29.86 quarter turns from a multiple of pi/2."""
    q, f = M.reduce(0x6F79BE45)
    p = M.DEFAULT
    fp = min(f, (1 << p.F) - f)
    assert 2.0 ** -30 < fp / 2.0 ** p.F < 2.0 ** -29.8


def test_model_within_budget():
    """Check both the phase 3 error limit and the tighter margin expected on this fixed sample."""
    for op in (FpuOp.SIN, FpuOp.COS, FpuOp.ATAN2, FpuOp.HYPOT, FpuOp.EXP, FpuOp.LN):
        st = ulp.UlpStats(budget=1.0)
        for a, b in ulp.op_inputs(op, 1500, 21 + op):
            st.add(op, a, b, *M.evaluate(op, a, b))
        assert st.errors() == 0, st.report()
        assert st.ops[op].max_err < 0.75, st.report()


def test_atan2_quadrants_and_signs():
    """One point in each quadrant and on the y/x path, against the rounded exact angle."""
    import struct

    def f(x: float) -> int:
        return struct.unpack("<I", struct.pack("<f", x))[0]

    from mathaccel import mpref
    for y, x in [(1.0, 2.0), (2.0, 1.0), (1.0, -2.0), (2.0, -1.0), (-1.0, -2.0), (-2.0, 1.0), (1e-6, 3.0)]:
        r, fl = M.evaluate(FpuOp.ATAN2, f(y), f(x))
        assert ulp.ulp_error(r, mpref.exact_value(FpuOp.ATAN2, f(y), f(x))) < 1.0, (y, x)


def test_hyperbolic_schedule():
    """Hyperbolic stages repeat shifts 4 and 13 and otherwise count up from 1."""
    hs = M.hyp_shifts()
    assert hs[:6] == (1, 2, 3, 4, 4, 5)
    assert hs.count(4) == 2 and hs.count(13) == 2
    assert len(hs) == M.DEFAULT.N


def test_exp_ln_edges():
    """exp at the overflow and underflow edges, ln on both sides of 1."""
    from mathaccel import mpref
    for op, a in [(FpuOp.EXP, 0x42B17217), (FpuOp.EXP, 0x42B17218), (FpuOp.EXP, 0xC2CFF1B4),
                  (FpuOp.EXP, 0xC2CFF1B5), (FpuOp.EXP, 0xC2AEAC50), (FpuOp.LN, 0x3F800001),
                  (FpuOp.LN, 0x3F7FFFFF), (FpuOp.LN, 0x00000001), (FpuOp.LN, 0x7F7FFFFF)]:
        got, want = M.evaluate(op, a), mpref.evaluate(op, a)
        if want[0] in (0, 0x7F800000):
            assert got == want, (op, hex(a))       # overflow/underflow results are exact patterns
        else:
            assert ulp.ulp_error(got[0], mpref.exact_value(op, a)) < 1.0, (op, hex(a))
            assert got[1] == want[1] or got[0] != want[0], (op, hex(a))
