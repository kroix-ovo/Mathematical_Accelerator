"""Bit-accurate model of what the expression unit computes (phase 4).

Like cordic_model.py, this is a description of the hardware, not a reference for
what an integral should be (that is mpmath). It predicts every point's result
and flags, and the final sum, bit for bit. It does not model time: the order
points run in can't change any result, because each point has its own registers
and the sum doesn't depend on order. So I can evaluate one instruction at a time
across all points at once, instead of one point at a time. A million-point run
then takes seconds.

Each FPU op goes through the C models when they are available, because they
process a whole array per call: fp_ref.c for ops 0-11 and cordic_ref.c for the
CORDIC ops. Both are tested bit for bit against the Python references. With no C
compiler, the same code falls back to fpu_evaluate one element at a time.

The sum models the planned exact accumulator. Every finite fp32 value is an
integer multiple of 2^-149, so adding them as integers is exact in any order.
Nothing is rounded until the end.
"""

# I use this as the bit-level prediction for a run, not as a model of its
# clock-by-clock scheduler. I evaluate the same rounded instructions and keep
# each point's flags. For reduce mode I add the resulting terms exactly, then
# round once, which is the contract I want the hardware to meet.

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from mathaccel import cref
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp
from mathaccel.compiler.evu_isa import OP_LOAD, SRC_CONST, Run
from mathaccel.driver.model import (
    CORDIC_OPS,
    DIV_INTERVAL,
    DIV_LANES,
    SQRT_INTERVAL,
    SQRT_LANES,
    fpu_evaluate,
)
from mathaccel.fp32ref import CANON_NAN, NV, round_fraction

_CREF = cref.load()
EXP_MASK = 0xFF
POS_INF = 0x7F800000
NEG_INF = 0xFF800000


def fpu_many(op: int, a: np.ndarray, b: np.ndarray, use_c: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """One FPU op over arrays of operands. Returns result and flag arrays."""
    if use_c and _CREF is not None:
        many = _CREF.cordic_many if op in CORDIC_OPS else _CREF.eval_many
        return many(op, a, b)
    pairs = [fpu_evaluate(op, int(x), int(y)) for x, y in zip(a, b, strict=True)]
    return (np.array([p[0] for p in pairs], dtype=np.uint32), np.array([p[1] for p in pairs], dtype=np.int32))


def exact_sum(values: np.ndarray) -> tuple[int, int]:
    """I add the already-rounded fp32 terms exactly, then round the total once.

    An exact zero is +0. Infinities and NaN can't sit in a fixed-point register,
    so the hardware keeps them as state bits, with the same results repeated
    ADD gives: +inf and -inf together are NaN with NV, and so is an sNaN.
    """
    v = np.asarray(values, dtype=np.uint32)
    exponent = (v >> 23) & EXP_MASK
    special = exponent == EXP_MASK
    if special.any():
        s = v[special]
        fraction = s & 0x7FFFFF
        snan = ((fraction != 0) & ((s & 0x400000) == 0)).any()
        pos, neg = (s == POS_INF).any(), (s == NEG_INF).any()
        if (fraction != 0).any() or (pos and neg):
            return CANON_NAN, NV if (snan or (pos and neg)) else 0
        return (POS_INF if pos else NEG_INF), 0
    # value = +-m * 2^shift * 2^-149. Group by shift: each group fits an int64
    # (2^24 * 2^39 terms), and Python's big integers do the final combining.
    m = (v & 0x7FFFFF).astype(np.int64) | ((exponent > 0).astype(np.int64) << 23)
    m = np.where(v >> 31, -m, m)
    shift = np.maximum(exponent.astype(np.int64), 1) - 1
    total = 0
    for s in np.unique(shift):
        total += int(m[shift == s].sum()) << int(s)
    if total == 0:
        return 0, 0
    return round_fraction(int(total < 0), Fraction(abs(total), 1 << 149))


@dataclass
class EvuResult:
    """What one run returned. In a reduce run, sum and sum_flags hold the sum;
    in a map run, results and flags hold one word per point. aux is the OR of
    every point's flags, the word the RUN reply carries."""
    results: np.ndarray | None
    flags: np.ndarray | None
    sum: int | None
    sum_flags: int | None
    aux: int
    outputs: np.ndarray            # one fp32 pattern per point, in grid order


def evaluate_points(run: Run, use_c: bool = True, trace: list | None = None) -> tuple[np.ndarray, np.ndarray]:
    """I run the bytecode over all points and keep every instruction flag.

    If trace is a list, each instruction appends (op, a, b): the op code as the
    FPU sees it, and the operands of every point. b is None where the FPU ignores
    it, and a LOAD appears with the table values as a. The block testbench uses
    this to check each FPU request, not only the final answers.
    """
    run.check()
    n = run.points
    index = np.unravel_index(np.arange(n, dtype=np.int64), run.grid)   # first dimension is the slowest
    regs: dict[int, np.ndarray] = {}
    pool = run.program.constants
    flags = np.zeros(n, dtype=np.int32)

    def source(s: int) -> np.ndarray:
        if s & SRC_CONST:
            return np.full(n, pool[s & 0x3F], dtype=np.uint32)
        # A register nothing has written reads as zero here. The hardware reads
        # whatever an earlier point left, so docs/protocol.md calls that
        # undefined. The compiler never does it.
        return regs.get(s, np.zeros(n, dtype=np.uint32))

    outputs = None
    for ins in run.program.instrs():
        if ins.op == OP_LOAD:
            t = run.tables[ins.a]
            r = np.asarray(t.values, dtype=np.uint32)[index[t.dim]]
            if trace is not None:
                trace.append((ins.op, r, None))
        else:
            # Unary ops ignore b, and b's field is 0, which may name a register
            # nothing has written yet. The hardware just reads whatever is
            # there; here zeros stand in for it, which has the same effect.
            b = np.zeros(n, dtype=np.uint32) if _is_unary(ins.op) else source(ins.b)
            a = source(ins.a)
            r, f = fpu_many(ins.op, a, b, use_c)
            if trace is not None:
                trace.append((ins.op, a, None if _is_unary(ins.op) else b))
            flags |= f
        regs[ins.dst] = r
        outputs = r
    return outputs, flags


def _is_unary(op: int) -> bool:
    return op in (FpuOp.SQRT, FpuOp.I2F, FpuOp.F2I, FpuOp.SIN, FpuOp.COS, FpuOp.EXP, FpuOp.LN)


def execute(run: Run, use_c: bool = True, trace: list | None = None) -> EvuResult:
    """I run the whole kernel: every point, then the sum in a reduce run."""
    outputs, flags = evaluate_points(run, use_c, trace)
    aux = int(np.bitwise_or.reduce(flags))
    if run.reduce:
        bits, sum_flags = exact_sum(outputs)
        return EvuResult(None, None, bits, sum_flags, aux, outputs)
    return EvuResult(outputs, flags, None, None, aux, outputs)


# What a run costs apart from its points, in clocks: descriptor 16 words, the
# CHECK phase, and a few clocks for each copy to start and finish.
CYCLES_START = spec.EVU_DESC_WORDS + 47
CYCLES_SUM = 29         # fp_accum's fixed time from finish to result
# The longest a point's instruction can be held up, per kind of op. These are for
# the upper bound only, so they are generous: a trip down the pipeline, the unit's
# own time, and room for refusals.
_SLOW_FAST, _SLOW_DIVIDE, _SLOW_CORDIC = 40, 120, 160


def cycle_bounds(run: Run) -> tuple[int, int]:
    """I bound the run time without pretending to predict every scheduler decision.

    I don't model time exactly: that would mean a clock-accurate copy of the ready
    queues and the FPU's arbitration. So scoreboards compare results exactly and
    check cycles against these two numbers.

    The low bound counts what must happen even with no waiting. The FPU takes one
    op per clock, the divider at most DIV_LANES ops per DIV_INTERVAL clocks and
    the square-root unit SQRT_LANES per SQRT_INTERVAL. The two units work at the
    same time, so the slowest of the three limits sets the floor, not their sum.
    I round the units' share down, so the bound stays a lower bound. The high bound
    is every instruction of every point being as slow as it can be, with nothing
    running at the same time.
    """
    ops = [i.op for i in run.program.instrs()]
    copy = CYCLES_START + len(run.program.words) + len(run.program.constants)
    if run.reduce:
        copy += CYCLES_SUM
    divides = sum(op == FpuOp.DIV for op in ops)
    roots = sum(op == FpuOp.SQRT for op in ops)
    floor_ops = max(len(ops) * run.points, divides * DIV_INTERVAL * run.points // DIV_LANES,
                    roots * SQRT_INTERVAL * run.points // SQRT_LANES)
    low = copy + floor_ops
    each = sum(_SLOW_DIVIDE if op in (FpuOp.DIV, FpuOp.SQRT) else _SLOW_CORDIC if op in CORDIC_OPS else _SLOW_FAST
               for op in ops)
    return low, copy + 200 + each * run.points


def fp32_value(bits: int) -> float:
    """An fp32 pattern as a Python float (exact, since doubles hold every fp32)."""
    return float(np.array([bits], dtype=np.uint32).view(np.float32)[0])
