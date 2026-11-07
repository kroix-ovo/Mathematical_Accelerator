"""Is the CPU benchmark really measuring the program the board runs?

A speed comparison is only worth reading if both sides compute the same thing.
Add, subtract, multiply, divide and square root are correctly rounded on the CPU
and on the FPU, so on problems using only those, the CPU's per-point outputs must
equal the bit-accurate model's exactly. The accumulation modes are checked against
sums I can work out by hand.
"""

# I check that the CPU comparison really evaluates the board's program.
# I compare per-point values and summing behavior before treating a timing
# as useful evidence. I don't turn a different reduction order into an
# exact-match requirement when it deliberately changes rounding.

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from mathaccel import evu_model
from mathaccel.compiler.problems import PROBLEMS
from mathaccel.compiler.quadrature import integral_run

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cpu_bench", ROOT / "tools" / "cpu_bench.py")
B = importlib.util.module_from_spec(SPEC)
sys.modules["cpu_bench"] = B
SPEC.loader.exec_module(B)


def problem(name):
    return next(p for p in PROBLEMS if p.name == name)


def rule_run(name, index=0):
    p = problem(name)
    return p, p.rules[index], integral_run(p.expr, p.axes(p.rules[index]))


@pytest.mark.parametrize("name", ["poly_box", "plate_potential", "cube_large", "circulation"])
def test_numpy_terms_equal_the_model_on_exact_ops(name):
    """These programs have no CORDIC op, so every point must match to the bit."""
    _, _, run = rule_run(name)
    terms = B.numpy_terms(B.numpy_program(run), B.numpy_shapes(run), run.grid)
    want = evu_model.execute(run).outputs
    assert (np.ascontiguousarray(terms).ravel().view(np.uint32) == want).all()


def test_numpy_uses_float32_throughout():
    _, _, run = rule_run("gaussian_2d")
    terms = B.numpy_terms(B.numpy_program(run), B.numpy_shapes(run), run.grid)
    assert terms.dtype == np.float32


def test_every_op_in_the_class_list_is_supported():
    for p in PROBLEMS:
        for rule in p.rules:
            run = integral_run(p.expr, p.axes(rule))
            B.numpy_program(run)
            B.numba_source(run, "seq32", False)


def test_sum_modes_on_a_case_that_separates_them():
    """2^24 + 1 + 1 + ... rounds each 1 away in fp32 when added one at a time."""
    terms = np.concatenate([np.array([2.0**24], dtype=np.float32), np.ones(1000, dtype=np.float32)])
    assert B.sum_terms(terms, "seq32") == 2.0**24
    assert B.sum_terms(terms, "wide64") == 2.0**24 + 1000
    assert B.sum_terms(terms, "exact") == 2.0**24 + 1000
    assert 2.0**24 <= B.sum_terms(terms, "pairwise") <= 2.0**24 + 1000


def test_exact_sum_of_the_terms_is_the_boards_sum():
    """On exact ops the CPU's terms with an exact sum must give the board's word."""
    _, _, run = rule_run("plate_potential")
    terms = B.numpy_terms(B.numpy_program(run), B.numpy_shapes(run), run.grid)
    want = evu_model.fp32_value(evu_model.execute(run).sum)
    assert B.sum_terms(terms, "exact") == want


@pytest.mark.parametrize("how", ["seq32", "wide64"])
def test_numba_matches_a_plain_loop_bit_for_bit(how):
    """Compiled, strict, single-threaded: same terms and the same adds, so the same bits."""
    pytest.importorskip("numba")
    _, _, run = rule_run("plate_potential")
    fn, arrays, pool, _ = B.compile_numba(run, how, False)
    got = float(fn(*arrays, pool))
    terms = np.ascontiguousarray(B.numpy_terms(B.numpy_program(run), B.numpy_shapes(run), run.grid)).ravel()
    want = float(np.cumsum(terms, dtype=np.float32)[-1]) if how == "seq32" else float(np.sum(terms, dtype=np.float64))
    if how == "seq32":
        assert got == want
    else:
        assert got == pytest.approx(want, rel=1e-12)


def test_numba_stays_in_single_precision():
    pytest.importorskip("numba")
    _, _, run = rule_run("gaussian_2d")
    fn, *_ = B.compile_numba(run, "seq32", False)
    assert "float32" in str(fn.nopython_signatures[0].return_type)


def test_parallel_numba_gives_a_close_answer():
    pytest.importorskip("numba")
    p, _, run = rule_run("gaussian_2d")
    fn, arrays, pool, _ = B.compile_numba(run, "wide64", True)
    assert float(fn(*arrays, pool)) == pytest.approx(float(p.exact()), rel=1e-4)


def test_reference_error_is_zero_on_the_reference():
    p = problem("poly_box")
    assert B.reference_ulps(float(p.exact()), p) < 1e-6
