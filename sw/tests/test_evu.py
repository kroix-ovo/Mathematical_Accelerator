"""The expression unit's bytecode, compiler, quadrature and bit-accurate model.

The compiler is checked against SymPy's own lambdify, in double precision, so a
compiler bug shows up as a wrong number. The model is checked two ways: its fast
array path against the one-element-at-a-time FPU references, and its exact sum
against Python's Fraction arithmetic.
"""

# I check instruction packing, compiler lowering, memory layout and the
# expression model together here. I include bad programs and descriptors
# because refusing them before writing results is part of the interface,
# just as much as getting a valid expression right.

from __future__ import annotations

import math
import random
import struct
from fractions import Fraction

import numpy as np
import pytest
import sympy as sp

from mathaccel import evu_model as M
from mathaccel import fp32ref
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp, Kernel, Op, Status
from mathaccel.compiler.evu_isa import (
    OP_LOAD,
    EvuError,
    Instr,
    Program,
    Run,
    Table,
    check_descriptor,
    const,
    memory_image,
    read_descriptor,
    reg,
)
from mathaccel.compiler.problems import PROBLEMS
from mathaccel.compiler.quadrature import Axis, float_bits, gauss_legendre, integral_run
from mathaccel.compiler.sympy_to_evu import CompileError, compile_expr, fp32_of
from mathaccel.driver import protocol as P
from mathaccel.driver.model import DeviceModel

x, y, z = sp.symbols("x y z", positive=True)
TABLES = {x: 0, y: 1, z: 2}


def run_on_points(expr, n=200, seed=1) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """I compile expr and run it on random points in (0.1, 3), as a one-dimensional
    grid of n points. Returns the model's output values and the three inputs."""
    rng = np.random.default_rng(seed)
    v = rng.uniform(0.1, 3.0, (3, n)).astype(np.float32)
    tables = [Table(s, v[k].view(np.uint32), 0) for k, s in enumerate("xyz")]
    run = Run(compile_expr(expr, TABLES), tables, (n,), reduce=False)
    res = M.execute(run)
    return res.results.view(np.float32).astype(np.float64), v[0], v[1], v[2]


def test_instruction_round_trip():
    i = Instr(FpuOp.ATAN2, dst=63, a=const(5), b=reg(62), last=True)
    assert Instr.decode(i.encode()) == i
    assert i.encode() >> 32 == 0
    load = Instr(OP_LOAD, 7, a=3)
    assert Instr.decode(load.encode()) == load


@pytest.mark.parametrize("words, constants, match", [
    ([], [], "instructions"),
    ([Instr(FpuOp.ADD, 0).encode()], [], "marked last"),
    ([Instr(FpuOp.ADD, 0, last=True).encode()] * 2, [], "marked last"),
    ([Instr(20, 0, last=True).encode()], [], "unknown op"),
    ([Instr(FpuOp.ADD, 0, const(1), 0, last=True).encode()], [0], "not in the pool"),
])
def test_program_check_rejects(words, constants, match):
    with pytest.raises(EvuError, match=match):
        Program(words, constants).check()


def test_run_check_rejects_a_table_of_the_wrong_length():
    prog = compile_expr(x, {x: 0})
    with pytest.raises(EvuError, match="does not fit"):
        Run(prog, [Table("x", np.zeros(3, dtype=np.uint32), 0)], (4,)).check()


EXPRESSIONS = [
    x + y, x - y, x * y, x / y, y - x + z, x - y - z, -x + y, 2 * x, x / 3, 1 / x, 3 / (x * y),
    x**2, x**3, x**5, x**7, x**-2, x**-3, sp.sqrt(x), 1 / sp.sqrt(x), x ** sp.Rational(3, 2), x ** sp.Rational(-3, 2),
    x**y, 2**x, x**sp.pi, sp.sin(x), sp.cos(x), sp.tan(x), sp.exp(x), sp.log(x), sp.sinh(x), sp.cosh(x), sp.tanh(x),
    sp.sec(x), sp.csc(x), sp.cot(x), sp.atan(x), sp.asin(x / 4), sp.acos(x / 4), sp.atan2(y, x), sp.Abs(x - y),
    sp.Min(x, y), sp.Max(x, y, z), sp.sqrt(x**2 + y**2), sp.sin(x) ** 2 + sp.cos(x) ** 2, sp.exp(-(x**2 + y**2)),
    x * sp.exp(-y) * sp.sin(z), (x + y) ** 2 / (1 + z), sp.log(x * y) + sp.log(x / y), 2 * sp.pi * x, x + sp.E,
    sp.sin(x) * sp.cos(y) + sp.cos(x) * sp.sin(y), sp.Rational(1, 3) * x**3 - sp.Rational(1, 7) * y,
]


@pytest.mark.parametrize("expr", EXPRESSIONS, ids=str)
def test_compiled_program_agrees_with_sympy(expr):
    got, a, b, c = run_on_points(expr)
    want = sp.lambdify((x, y, z), expr, "numpy")(a.astype(np.float64), b.astype(np.float64), c.astype(np.float64))
    want = np.broadcast_to(want, got.shape)
    ok = np.isclose(got, want, rtol=3e-5, atol=3e-6) | (np.isnan(got) & np.isnan(want))
    assert ok.all(), f"{expr}: first bad {got[~ok][0]} vs {want[~ok][0]}"


def test_constant_expression_still_produces_an_instruction():
    got, *_ = run_on_points(sp.pi * 2)
    assert got[0] == pytest.approx(2 * math.pi, rel=1e-6)


def test_common_subexpressions_are_computed_once():
    p = compile_expr(sp.sin(x) ** 2 + sp.sin(x) + sp.sin(x) ** 3, TABLES)
    assert sum(i.op == FpuOp.SIN for i in p.instrs()) == 1
    assert sum(i.op == OP_LOAD for i in p.instrs()) == 1


def test_numbers_are_rounded_once():
    assert fp32_of(sp.pi) == 0x40490FDB
    assert fp32_of(2 * sp.pi) == 0x40C90FDB
    assert fp32_of(sp.Rational(1, 10)) == 0x3DCCCCCD
    assert fp32_of(-sp.Rational(1, 2)) == 0xBF000000


def test_registers_are_reused():
    expr = sum(sp.sin(x * k) for k in range(1, 30))
    p = compile_expr(expr, TABLES)
    assert len({i.dst for i in p.instrs()}) < 6
    assert len(p.words) > 50


@pytest.mark.parametrize("expr, match", [
    (sp.erf(x), "no FPU equivalent"),
    (sp.Piecewise((x, x < 1), (y, True)), "no FPU equivalent"),
    (x + sp.Symbol("q"), "has no value"),
])
def test_compile_errors(expr, match):
    with pytest.raises(CompileError, match=match):
        compile_expr(expr, TABLES)


def test_compile_error_when_the_program_is_too_long():
    with pytest.raises(EvuError):
        compile_expr(sum(sp.sin(x * k) for k in range(1, 300)), TABLES)


def test_array_path_matches_the_one_at_a_time_references():
    """The fast C path and fpu_evaluate must agree on every op, with bad inputs too."""
    rng = random.Random(4)
    specials = [0, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7FA00000, 1, 0x7F7FFFFF, 0x3F800000]
    a = np.array([rng.choice(specials) if k % 3 == 0 else rng.getrandbits(32) for k in range(300)], dtype=np.uint32)
    b = np.array([rng.choice(specials) if k % 5 == 0 else rng.getrandbits(32) for k in range(300)], dtype=np.uint32)
    for op in range(spec.FPU_OP_COUNT):
        fast = M.fpu_many(op, a, b, use_c=True)
        slow = M.fpu_many(op, a, b, use_c=False)
        assert (fast[0] == slow[0]).all() and (fast[1] == slow[1]).all(), FpuOp(op).name


def exact_by_fractions(values) -> tuple[int, int]:
    total = sum((fp32ref.to_fraction(int(v)) for v in values), Fraction(0))
    return (0, 0) if total == 0 else fp32ref.round_fraction(int(total < 0), abs(total))


def test_exact_sum_matches_fractions():
    rng = np.random.default_rng(2)
    for scale in (0, 1):
        v = rng.integers(0, 2**32, 2000, dtype=np.uint32)
        v = v[((v >> 23) & 0xFF) != 0xFF]
        if scale:
            v &= 0x807FFFFF           # subnormals and zeros only
        assert M.exact_sum(v) == exact_by_fractions(v)


def test_exact_sum_does_not_depend_on_order():
    v = float_bits(np.array([1e30, 1.0, -1e30, 3.0, 1e-30, 2.5e7]))
    want = M.exact_sum(v)
    for seed in range(5):
        assert M.exact_sum(np.random.default_rng(seed).permutation(v)) == want
    assert M.fp32_value(want[0]) == pytest.approx(1 + 3 + 2.5e7 + 1e-30, rel=1e-7)


def test_exact_sum_keeps_what_plain_fp32_loses():
    v = float_bits(np.array([2.0**24] + [1.0] * 1000 + [-(2.0**24)]))
    assert M.fp32_value(M.exact_sum(v)[0]) == 1000.0
    assert float(np.add.accumulate(v.view(np.float32), dtype=np.float32)[-1]) == 0.0


@pytest.mark.parametrize("words, want", [
    ([0x7F800000, 0x3F800000], (0x7F800000, 0)),
    ([0xFF800000, 0x3F800000], (0xFF800000, 0)),
    ([0x7F800000, 0xFF800000], (0x7FC00000, fp32ref.NV)),
    ([0x7FC00000, 0x3F800000], (0x7FC00000, 0)),
    ([0x7FA00000, 0x3F800000], (0x7FC00000, fp32ref.NV)),
    ([0x7F7FFFFF, 0x7F7FFFFF], (0x7F800000, fp32ref.OF | fp32ref.NX)),
    ([0x80000000, 0x80000000], (0, 0)),
    ([0x3F800000, 0xBF800000], (0, 0)),
])
def test_exact_sum_special_values(words, want):
    assert M.exact_sum(np.array(words, dtype=np.uint32)) == want


def test_grid_order_and_map_mode():
    """In a map run the first dimension is the slowest, and flags are per point."""
    xs = float_bits(np.array([1.0, 2.0]))
    ys = float_bits(np.array([0.0, 10.0, 20.0]))
    prog = compile_expr(x / y, {x: 0, y: 1})
    res = M.execute(Run(prog, [Table("x", xs, 0), Table("y", ys, 1)], (2, 3), reduce=False))
    assert res.results.view(np.float32).tolist()[1:3] == pytest.approx([0.1, 0.05], rel=1e-6)
    assert res.flags[0] == fp32ref.DZ and res.flags[3] == fp32ref.DZ
    assert res.aux & fp32ref.DZ and res.aux & fp32ref.NX
    assert res.sum is None


def test_gauss_legendre_is_exact_on_polynomials():
    n, w = gauss_legendre(-1.0, 3.0, 2, 5)
    assert len(n) == 10
    assert float(np.sum(w)) == pytest.approx(4.0)
    assert float(np.sum(w * n**9)) == pytest.approx((3.0**10 - 1) / 10)


def test_integral_run_multiplies_in_the_weights():
    run = integral_run(x * y, [Axis(x, 0, 1, 1, 4), Axis(y, 0, 2, 1, 4)])
    res = M.execute(run)
    assert run.points == 16 and len(run.tables) == 4
    assert M.fp32_value(res.sum) == pytest.approx(1.0, rel=1e-6)


@pytest.mark.parametrize("p", PROBLEMS, ids=lambda p: p.name)
def test_class_problems_are_close_on_their_smallest_rule(p):
    rule = min(p.rules, key=lambda r: math.prod(a * b for a, b in r))
    res = M.execute(integral_run(p.expr, p.axes(rule)))
    # A loose check that the model and the reference agree on the problem. The
    # real measurement of the error is tools/evu_gate.py.
    assert M.fp32_value(res.sum) == pytest.approx(float(p.exact()), rel=2e-2 if p.name == "plate_potential" else 2e-4)


def _image_reader(image):
    """mem(address) for an image alone in a zeroed scratchpad."""
    mem = np.zeros(spec.SCRATCH_WORDS, dtype=np.uint32)
    mem[image.base:image.base + len(image.words)] = image.words
    return lambda a: int(mem[a])


@pytest.mark.parametrize("reduce", [True, False])
def test_memory_image_round_trip(reduce):
    v = float_bits(np.array([1.0, 2.0, 3.0]))
    w = float_bits(np.array([0.5, 0.25]))
    program = Program([Instr(OP_LOAD, 0, 0).encode(), Instr(OP_LOAD, 1, 1).encode(),
                       Instr(FpuOp.MUL, 2, reg(0), reg(1)).encode(),
                       Instr(FpuOp.ADD, 3, reg(2), const(0), last=True).encode()],
                      [int(float_bits(np.array([1.0]))[0])])
    run = Run(program, [Table("x", v, 1), Table("w", w, 0)], (2, 3), reduce)
    image = memory_image(run, base=0x40)
    got, r, f = read_descriptor(_image_reader(image), image.desc)
    assert (r, f) == (image.r, image.f)
    assert f - r == (1 if reduce else run.points)
    assert got.program.words == program.words and got.reduce == reduce and got.grid == (2, 3, 1)
    a, b = M.execute(run), M.execute(got)
    assert (a.outputs == b.outputs).all() and a.aux == b.aux and a.sum == b.sum


def test_read_descriptor_stops_where_the_hardware_stops():
    load, add = Instr(OP_LOAD, 0, 0).encode(), Instr(FpuOp.ADD, 1, reg(0), reg(0)).encode()
    run = Run(Program([load, add | 1 << spec.EVU_LAST_SHIFT]), [Table("x", float_bits(np.ones(2)), 0)], (2,))
    image = memory_image(run)
    program = int(image.words[spec.EVU_DESC_PROG])
    # No last bit: the end of the length counts as last.
    no_last = Instr.decode(int(image.words[program + 1]))
    image.words[program + 1] &= ~(1 << spec.EVU_LAST_SHIFT) & 0xFFFFFFFF
    got, _, _ = read_descriptor(_image_reader(image), image.desc)
    assert [i.last for i in got.program.instrs()] == [False, True] and no_last.last
    # A last bit early: the program ends there.
    image.words[program] |= 1 << spec.EVU_LAST_SHIFT
    got, _, _ = read_descriptor(_image_reader(image), image.desc)
    assert len(got.program.words) == 1


def test_memory_image_must_fit():
    n = spec.SCRATCH_WORDS // 2 + 1
    run = Run(Program([Instr(OP_LOAD, 0, 0, last=True).encode()]), [Table("x", np.zeros(n, dtype=np.uint32), 0)], (n,),
              reduce=False)
    with pytest.raises(EvuError, match="scratchpad"):
        memory_image(run)


def test_trace_lists_every_fpu_request():
    x_bits = float_bits(np.array([1.0, 4.0]))
    program = Program([Instr(OP_LOAD, 0, 0).encode(), Instr(FpuOp.SQRT, 1, reg(0)).encode(),
                       Instr(FpuOp.ADD, 2, reg(0), const(0), last=True).encode()], [x_bits[0]])
    trace: list = []
    M.execute(Run(program, [Table("x", x_bits, 0)], (2,)), trace=trace)
    assert [t[0] for t in trace] == [OP_LOAD, FpuOp.SQRT, FpuOp.ADD]
    assert trace[0][2] is None and trace[1][2] is None
    assert (trace[1][1] == x_bits).all() and (trace[2][2] == x_bits[0]).all()


# ---- descriptor checks and the EVU kernel in DeviceModel ------------------------

def _small_run(reduce=True):
    x = float_bits(np.array([1.0, 2.0, 4.0]))
    program = Program([Instr(OP_LOAD, 0, 0).encode(), Instr(FpuOp.MUL, 1, reg(0), const(0), last=True).encode()],
                      [int(float_bits(np.array([0.5]))[0])])
    return Run(program, [Table("x", x, 0)], (3,), reduce)


def _load(run, base=0x100):
    model = DeviceModel()
    image = memory_image(run, base)
    model.mem[base:base + len(image.words)] = image.words
    return model, image


def _run(model, desc):
    return model.execute(Op.RUN, bytes([Kernel.EVU]) + P.evu_args(desc))


def _status_after(change, reduce=True):
    """The status once change(model, image) has edited a good run in memory."""
    model, image = _load(_small_run(reduce))
    change(model, image)
    return _run(model, image.desc)[0]


def _word(offset, value):
    def change(model, image):
        model.mem[image.desc + offset] = value
    return change


def _table(k, base, dim=0):
    return _word(spec.EVU_DESC_TABLE + k, base | dim << spec.EVU_DESC_DIM_SHIFT)


@pytest.mark.parametrize("reduce", [True, False])
def test_kernel_runs_a_good_descriptor(reduce):
    model, image = _load(_small_run(reduce))
    status, data = _run(model, image.desc)
    assert status == Status.OK
    cycles, aux = struct.unpack("<II", data)
    assert model.evu_bounds[0] == cycles <= model.evu_bounds[1]
    assert aux == 0 and model.last_cycles == cycles
    if reduce:
        assert model.mem[image.r] == float_bits(np.array([3.5]))[0] and model.mem[image.f] == 0
    else:
        assert (model.mem[image.r:image.r + 3] == float_bits(np.array([0.5, 1.0, 2.0]))).all()
        assert (model.mem[image.f:image.f + 3] == 0).all()


@pytest.mark.parametrize("change", [
    _word(spec.EVU_DESC_MODE, 2),
    _word(spec.EVU_DESC_PROG_LEN, 0),
    _word(spec.EVU_DESC_PROG_LEN, spec.EVU_PROGRAM_LEN + 1),
    _word(spec.EVU_DESC_CONST_LEN, spec.EVU_CONSTANTS + 1),
    _word(spec.EVU_DESC_GRID, 0),
    _word(spec.EVU_DESC_GRID + 2, 0),
    _word(spec.EVU_DESC_GRID + 1, 1 << 16),
    _table(0, 0, dim=3),
    _word(spec.EVU_DESC_TABLE + 5, 1 << 18),
    lambda m, i: m.mem.__setitem__(slice(i.desc + spec.EVU_DESC_GRID, i.desc + spec.EVU_DESC_GRID + 3), 65535),
])
def test_bad_fields_are_bad_arg(change):
    assert _status_after(change) == Status.BAD_ARG


@pytest.mark.parametrize("change", [
    _word(spec.EVU_DESC_PROG, spec.SCRATCH_WORDS),
    _word(spec.EVU_DESC_PROG, spec.SCRATCH_WORDS - 1),                  # two words, one past the end
    _word(spec.EVU_DESC_CONST, 1 << 20),
    _word(spec.EVU_DESC_R, spec.SCRATCH_WORDS),
    _word(spec.EVU_DESC_F, 0xFFFFFFFF),
    _table(1, spec.SCRATCH_WORDS - 2),                                  # three words, and any table is checked
])
def test_bad_spans_are_bad_addr(change):
    assert _status_after(change) == Status.BAD_ADDR


def test_map_outputs_must_fit():
    edge = _word(spec.EVU_DESC_R, spec.SCRATCH_WORDS - 3)
    assert _status_after(edge, reduce=False) == Status.OK
    assert _status_after(_word(spec.EVU_DESC_R, spec.SCRATCH_WORDS - 2), reduce=False) == Status.BAD_ADDR
    assert _status_after(_word(spec.EVU_DESC_R, spec.SCRATCH_WORDS - 1)) == Status.OK       # one word in reduce
    # More points than the scratchpad has words, whatever the addresses say.
    assert _status_after(_word(spec.EVU_DESC_GRID + 1, 40000), reduce=False) == Status.BAD_ADDR


def test_points_limit_is_two_to_the_32():
    def points(n0, n1, n2):
        return lambda m, i: m.mem.__setitem__(slice(i.desc + spec.EVU_DESC_GRID, i.desc + spec.EVU_DESC_GRID + 3),
                                              (n0, n1, n2))
    assert evu_isa_status(points(4, 32768, 32768)) == Status.OK
    assert evu_isa_status(points(5, 32768, 32768)) == Status.BAD_ARG
    assert evu_isa_status(points(4, 65535, 16384)) == Status.OK
    assert evu_isa_status(points(65535, 65535, 65535)) == Status.BAD_ARG


def evu_isa_status(change):
    """The check alone: these grids are far too large to run."""
    model, image = _load(_small_run())
    change(model, image)
    return check_descriptor(lambda a: int(model.mem[a]), image.desc)


@pytest.mark.parametrize("word", [
    Instr(18, 1, reg(0)).encode(),                                      # first op code past the FPU's
    Instr(30, 1, reg(0)).encode(),
    Instr(OP_LOAD, 1, spec.EVU_TABLES).encode(),
    Instr(FpuOp.ADD, 1, const(1), reg(0)).encode(),                     # the pool has one constant
    Instr(FpuOp.ADD, 1, reg(0), const(63)).encode(),
    Instr(FpuOp.SQRT, 1, reg(0), const(1)).encode(),
    Instr(OP_LOAD, 1, 0, const(1)).encode(),
    Instr(FpuOp.ADD, 1, reg(0), reg(0)).encode() | 1,
])
def test_bad_instructions_are_bad_arg(word):
    def change(model, image):
        model.mem[int(model.mem[image.desc + spec.EVU_DESC_PROG]) + 1] = word
    assert _status_after(change) == Status.BAD_ARG


def test_a_word_after_last_is_still_checked():
    def change(model, image):
        program = int(model.mem[image.desc + spec.EVU_DESC_PROG])
        model.mem[program] |= 1 << spec.EVU_LAST_SHIFT
        model.mem[program + 1] = Instr(18, 1, reg(0)).encode()
    assert _status_after(change) == Status.BAD_ARG


def test_bad_field_beats_bad_address_beats_bad_program():
    def fields_and_span(model, image):
        model.mem[image.desc + spec.EVU_DESC_R] = spec.SCRATCH_WORDS
        model.mem[image.desc + spec.EVU_DESC_MODE] = 4

    def span_and_program(model, image):
        program = int(model.mem[image.desc + spec.EVU_DESC_PROG])
        model.mem[program] = Instr(18, 1, reg(0)).encode()
        model.mem[image.desc + spec.EVU_DESC_R] = spec.SCRATCH_WORDS
    assert _status_after(fields_and_span) == Status.BAD_ARG
    assert _status_after(span_and_program) == Status.BAD_ADDR


def test_refused_run_changes_nothing():
    model, image = _load(_small_run(reduce=False))
    before = model.mem.copy()
    model.last_cycles = 1234
    model.mem[image.desc + spec.EVU_DESC_MODE] = 2
    before[image.desc + spec.EVU_DESC_MODE] = 2
    assert _run(model, image.desc) == (Status.BAD_ARG, b"")
    assert (model.mem == before).all() and model.last_cycles == 1234


def test_request_checks():
    model = DeviceModel()
    assert model.execute(Op.RUN, bytes([Kernel.EVU]))[0] == Status.BAD_LEN
    assert model.execute(Op.RUN, bytes([Kernel.EVU]) + b"\x00" * 5)[0] == Status.BAD_LEN
    assert _run(model, spec.SCRATCH_WORDS - spec.EVU_DESC_WORDS + 1)[0] == Status.BAD_ADDR
    assert _run(model, 0xFFFFFFFF)[0] == Status.BAD_ADDR
    # An empty scratchpad is a descriptor too: its program length is 0.
    assert _run(model, spec.SCRATCH_WORDS - spec.EVU_DESC_WORDS)[0] == Status.BAD_ARG


def _copy_clocks(run: Run) -> int:
    """The part of cycle_bounds' low bound that doesn't depend on the points."""
    return (M.CYCLES_START + len(run.program.words) + len(run.program.constants)
            + (M.CYCLES_SUM if run.reduce else 0))


def test_cycle_bounds_follow_the_divider():
    """A program whose one divide takes longer than its two instructions is
    bounded by the divider's rate. I read that rate from the model, because each
    divider experiment changes it (it was a fixed 33 before the split)."""
    run = _small_run()
    low, high = M.cycle_bounds(run)
    assert low < high
    divide = Program([Instr(OP_LOAD, 0, 0).encode(), Instr(FpuOp.DIV, 1, reg(0), reg(0), last=True).encode()])
    slow = Run(divide, run.tables, run.grid)
    p = run.points
    assert M.cycle_bounds(slow)[0] == _copy_clocks(slow) + max(2 * p, M.DIV_INTERVAL * p // M.DIV_LANES)
    if M.DIV_INTERVAL // M.DIV_LANES > 2:
        assert M.cycle_bounds(slow)[0] - M.cycle_bounds(run)[0] >= (M.DIV_INTERVAL // M.DIV_LANES - 3) * p


def test_cycle_bounds_let_divide_and_sqrt_overlap():
    """fp_div and fp_sqrt are separate units, so a point with one of each is
    bounded by the slower unit, not by the two units' times added. Two divides
    per point both use fp_div, so those do add."""
    run = _small_run()
    both = Run(Program([Instr(OP_LOAD, 0, 0).encode(), Instr(FpuOp.SQRT, 1, reg(0)).encode(),
                        Instr(FpuOp.DIV, 2, reg(0), reg(1), last=True).encode()]), run.tables, run.grid)
    two_div = Run(Program([Instr(OP_LOAD, 0, 0).encode(), Instr(FpuOp.DIV, 1, reg(0), reg(0)).encode(),
                           Instr(FpuOp.DIV, 2, reg(0), reg(1), last=True).encode()]), run.tables, run.grid)
    p = run.points
    div_share = M.DIV_INTERVAL * p // M.DIV_LANES
    sqrt_share = M.SQRT_INTERVAL * p // M.SQRT_LANES
    assert M.cycle_bounds(both)[0] == _copy_clocks(both) + max(3 * p, div_share, sqrt_share)
    assert M.cycle_bounds(two_div)[0] == _copy_clocks(two_div) + max(3 * p, 2 * M.DIV_INTERVAL * p // M.DIV_LANES)
