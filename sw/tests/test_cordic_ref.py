"""The C port of the CORDIC model (hw/tb/uvm/dpi/cordic_ref.c) against the Python model.

The SystemVerilog scoreboards check the RTL against cordic_ref.c, and the
pyuvm scoreboards check it against sw/mathaccel/cordic_model.py. Both must
describe the same hardware bit for bit, so here every result and flag word of
the C port has to equal the Python model's on the same inputs the ULP
measurement uses (special values, tiny inputs, near multiples of pi/2, the
exp/ln edges, all atan2 quadrants), plus random bit patterns.
"""

from __future__ import annotations

import random

import pytest

from mathaccel import cordic_model as M
from mathaccel import cref as cref_mod
from mathaccel import ulp
from mathaccel._generated.spec import FpuOp

OPS = (FpuOp.SIN, FpuOp.COS, FpuOp.ATAN2, FpuOp.HYPOT, FpuOp.EXP, FpuOp.LN)


@pytest.fixture(scope="module")
def cref():
    """Compile/load the same C model used by SV; explicitly skip if no compiler is available."""
    lib = cref_mod.load()
    if lib is None:
        pytest.skip("no C compiler for the DPI reference")
    return lib


@pytest.mark.parametrize("op", OPS)
def test_c_port_matches_python_model(cref, op):
    """Require identical bits and flags from the two datapath models, including difficult and random inputs."""
    pairs = ulp.op_inputs(op, 6000, 0xC0DE + op)
    rng = random.Random(op)
    pairs += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(2000)]
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    res, fl = cref.cordic_many(op, a, b)
    bad = []
    for k, (x, y) in enumerate(pairs):
        want = M.evaluate(op, x, y)
        got = (int(res[k]), int(fl[k]))
        if got != want:
            bad.append(f"{x:08x} {y:08x}: C {got[0]:08x}/{got[1]:05b}, Python {want[0]:08x}/{want[1]:05b}")
    assert not bad, f"{len(bad)} of {len(pairs)} differ:\n" + "\n".join(bad[:10])
