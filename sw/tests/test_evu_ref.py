"""The C port of the expression unit (hw/tb/uvm/dpi/evu_ref.c) against the Python model.

The SystemVerilog top scoreboard checks the RTL against evu_ref.c, and the pyuvm
top scoreboard checks it against DeviceModel and evu_model.py. Both must describe
the same hardware, so every status, written word, aux word and clock bound of the
C port has to equal the Python model's on the same scratchpad. That covers the
class problems, random programs including wild values, and spoiled descriptors.
"""

# I cross-check the C interpreter used by the SV scoreboard against the
# Python interpreter here. I compare status, written addresses, result bits
# and flags. I want the two differently structured models to catch a shared
# misreading of the bytecode before either one grades the RTL.

from __future__ import annotations

import random
import struct

import numpy as np
import pytest

from mathaccel import cref as cref_mod
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp, Kernel, Op, Status
from mathaccel.compiler.evu_gen import NEG_INF, POS_INF, QNAN, SNAN, f, prog, random_run, table
from mathaccel.compiler.evu_isa import OP_LOAD, Instr, Run, const, memory_image, reg
from mathaccel.compiler.problems import PROBLEMS
from mathaccel.compiler.quadrature import integral_run
from mathaccel.driver import protocol as P
from mathaccel.driver.model import DeviceModel


@pytest.fixture(scope="module")
def cref():
    """Compile/load the same C model used by SV; explicitly skip if no compiler is available."""
    lib = cref_mod.load()
    if lib is None:
        pytest.skip("no C compiler for the DPI reference")
    return lib


def compare(cref, words: np.ndarray, base: int) -> Status:
    """I run one image through both models and require them to agree on everything."""
    model = DeviceModel()
    model.mem[base:base + len(words)] = words
    before = model.mem.copy()
    status, data = model.execute(Op.RUN, bytes([Kernel.EVU]) + P.evu_args(base))
    got, aux, low, high, writes = cref.evu_run(before, base)
    assert got == status
    if status != Status.OK:
        assert not writes and (model.mem == before).all()
        return Status(status)
    cycles, want_aux = struct.unpack("<II", data)
    assert aux == want_aux
    assert (low, high) == model.evu_bounds and low == cycles
    after = before.copy()
    for addr, word in writes:
        after[addr] = word
    assert (after == model.mem).all(), f"{int((after != model.mem).sum())} scratchpad words differ"
    return Status.OK


def test_constants_match_the_spec(cref):
    """A descriptor's edge values, so a changed spec shows up here instead of in a scoreboard."""
    words = memory_image(_tiny()).words
    assert compare(cref, words, 0) == Status.OK
    for offset, value, want in ((spec.EVU_DESC_PROG_LEN, spec.EVU_PROGRAM_LEN + 1, Status.BAD_ARG),
                                (spec.EVU_DESC_CONST_LEN, spec.EVU_CONSTANTS + 1, Status.BAD_ARG),
                                (spec.EVU_DESC_R, spec.SCRATCH_WORDS, Status.BAD_ADDR),
                                (spec.EVU_DESC_TABLE, spec.SCRATCH_WORDS - 3, Status.BAD_ADDR)):
        spoiled = words.copy()
        spoiled[offset] = value
        assert compare(cref, spoiled, 0) == want


def _tiny(reduce=True):
    return Run(prog(Instr(OP_LOAD, 0, 0), Instr(FpuOp.ADD, 1, reg(0), const(0)), constants=[f(1.0)]),
               [table([1.0, 2.0, 3.0, 4.0])], (4,), reduce)


@pytest.mark.parametrize("problem", PROBLEMS, ids=lambda p: p.name)
@pytest.mark.parametrize("reduce", [True, False])
def test_class_problems(cref, problem, reduce):
    run = integral_run(problem.expr, problem.axes(problem.rules[0]))
    run.reduce = reduce
    image = memory_image(run)
    assert compare(cref, image.words, image.base) == Status.OK


def test_random_programs(cref):
    rng = random.Random(0xE7A)
    for _ in range(150):
        image = memory_image(random_run(rng, max_points=300), rng.choice([0, 0x100, 0x8000]))
        assert compare(cref, image.words, image.base) == Status.OK


@pytest.mark.parametrize(("values", "name"), [
    ([POS_INF, 1.0], "inf"), ([NEG_INF, 1.0], "neg inf"), ([POS_INF, NEG_INF], "opposite inf"),
    ([QNAN, 1.0], "qnan"), ([SNAN, 1.0], "snan"), ([0x80000000, 0x80000000], "minus zero"),
    ([1.0, -1.0], "cancels"), ([1, 1], "subnormal"), ([0x7F7FFFFF, 0x7F7FFFFF], "overflow"),
    ([0xFF7FFFFF, 0xFF7FFFFF, 0xFF7FFFFF], "negative overflow"), ([0x7F7FFFFF, 0xF3800000], "rounds to max"),
    ([1.0, 2.0 ** -24], "tie to even"), ([1.0, 2.0 ** -24, 2.0 ** -40], "above the tie"),
    ([1.0000001, 2.0 ** -24], "tie up"), ([0x00800000, 0x807FFFFF], "smallest difference"),
    ([0x7F000000, 0x7F000000, 0xFF000000, 1.0], "large cancelling"),
])
def test_sum_edges(cref, values, name):
    """The exact sum's rounding and special values, on sums chosen to stress them."""
    run = Run(prog(Instr(OP_LOAD, 0, 0)), [table(values)], (len(values),))
    image = memory_image(run)
    assert compare(cref, image.words, 0) == Status.OK


def test_random_sums(cref):
    rng = random.Random(77)
    for _ in range(200):
        n = rng.randint(1, 40)
        scale = rng.choice([1, 8, 255])
        values = [(rng.getrandbits(1) << 31) | (rng.randrange(0, scale) + rng.randint(100, 120)) << 23
                  | rng.getrandbits(23) for _ in range(n)]
        run = Run(prog(Instr(OP_LOAD, 0, 0)), [table(values)], (n,))
        assert compare(cref, memory_image(run).words, 0) == Status.OK


def test_spoiled_descriptors(cref):
    """Random damage to a good image, with either status allowed as long as both agree."""
    rng = random.Random(5)
    good = memory_image(_tiny(reduce=False), 0)
    seen = set()
    edges = [0, 1, 2, 3, 63, 64, 65, 255, 256, 257, 65534, 65535, 65536, 1 << 18, 1 << 31, 0xFFFFFFFF]
    for _ in range(3000):
        words = good.words.copy()
        for _ in range(rng.randint(1, 3)):
            k = rng.randrange(len(words))
            words[k] = rng.choice([rng.choice(edges), rng.getrandbits(32),
                                   Instr(rng.randrange(32), 1, rng.randrange(128), rng.randrange(128)).encode()])
        # A grid this could really run would take a while, in either model.
        grid = [int(n) for n in words[spec.EVU_DESC_GRID:spec.EVU_DESC_GRID + 3]]
        if all(0 < n < 1 << 16 for n in grid) and np.prod(np.array(grid, dtype=np.float64)) > 5000:
            continue
        # Overlapping outputs are undefined, because the hardware's order isn't fixed.
        r, g = int(words[spec.EVU_DESC_R]), int(words[spec.EVU_DESC_F])
        if not words[spec.EVU_DESC_MODE] & 1 and abs(r - g) < np.prod(np.array(grid, dtype=np.float64)):
            continue
        seen.add(compare(cref, words, 0))
    assert seen == {Status.OK, Status.BAD_ARG, Status.BAD_ADDR}
