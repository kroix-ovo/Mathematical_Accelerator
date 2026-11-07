"""Random and hand-built runs for the expression unit's testbenches.

The block environment and the top-level environment both use these, and neither
can import the other: pyuvm registers a test as soon as its module is imported.
Nothing here decides what is correct. That's evu_model.py's job.
"""

# I keep shared test inputs here so the block and UART environments can
# use the same kinds of programs without importing each other's tests.
# I choose operands from values already written, and mix ordinary numbers
# with special values. I leave deciding the right answer to the reference.

from __future__ import annotations

import random

import numpy as np

from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp
from mathaccel.compiler.evu_isa import OP_LOAD, Instr, Program, Run, Table, const, reg

POS_INF, NEG_INF, QNAN, SNAN = 0x7F800000, 0xFF800000, 0x7FC00000, 0x7FA00000
UNARY = {FpuOp.SQRT, FpuOp.I2F, FpuOp.F2I, FpuOp.SIN, FpuOp.COS, FpuOp.EXP, FpuOp.LN}
CORDIC = [FpuOp.SIN, FpuOp.COS, FpuOp.ATAN2, FpuOp.HYPOT, FpuOp.EXP, FpuOp.LN]
FAST = [FpuOp.ADD, FpuOp.SUB, FpuOp.MUL, FpuOp.MIN, FpuOp.MAX, FpuOp.EQ, FpuOp.LT, FpuOp.LE, FpuOp.I2F, FpuOp.F2I]


def f(x: float) -> int:
    """The fp32 pattern of a Python float."""
    return int(np.array([x], dtype=np.float32).view(np.uint32)[0])


def table(values, dim=0, name="t") -> Table:
    return Table(name, np.array([v if isinstance(v, int) else f(v) for v in values], dtype=np.uint32), dim)


def prog(*instrs: Instr, constants=()) -> Program:
    """A program from instructions. The final one is marked last here."""
    words = [i.encode() for i in instrs[:-1]]
    tail = instrs[-1]
    words.append(Instr(tail.op, tail.dst, tail.a, tail.b, last=True).encode())
    return Program(words, list(constants))


def random_bits(rng: random.Random, kind: str) -> int:
    """One fp32 pattern. Most are ordinary numbers, so a program doesn't just go
    to NaN after two instructions, but every special class comes up."""
    if kind == "normal" or rng.random() < 0.7:
        return f(rng.uniform(-4.0, 4.0))
    return rng.choice([rng.getrandbits(32), POS_INF, NEG_INF, QNAN, SNAN, 0, 0x80000000, 1, 0x00800000, f(1e30)])


def random_run(rng: random.Random, max_points: int = 1500, max_length: int = spec.EVU_PROGRAM_LEN) -> Run:
    """I build a seeded program that only reads registers after writing them.
    limits keep the image small enough to send over a simulated UART."""
    style = rng.choice(["mixed", "mixed", "fast", "cordic", "divide"])
    data = rng.choice(["normal", "normal", "wild"])
    length = rng.choice([1, 2, 3, rng.randint(4, 12), rng.randint(13, 40), rng.randint(41, 120), spec.EVU_PROGRAM_LEN])
    length = min(length, max_length)
    budget = 24000 if style == "fast" else 9000
    points = max(1, min(rng.choice([1, 2, 7, 40, 200, 600, 1500]), budget // length, max_points))
    dims = rng.randint(1, 3)
    grid = [1] * dims
    for _ in range(12):
        d = rng.randrange(dims)
        if int(np.prod(grid)) * 2 <= points:
            grid[d] *= 2
    grid = tuple(grid)
    tables = []
    for _ in range(rng.randint(0, spec.EVU_TABLES)):
        d = rng.randrange(dims)
        tables.append(table([random_bits(rng, data) for _ in range(grid[d])], d))
    constants = [random_bits(rng, data) for _ in range(rng.choice([0, 1, 4, 20, spec.EVU_CONSTANTS]))]
    written: list[int] = []
    lines = []

    def source() -> int:
        if constants and (not written or rng.random() < 0.3):
            return const(rng.randrange(len(constants)))
        return reg(rng.choice(written))

    def pick_op() -> int:
        if style == "fast":
            return rng.choice(FAST)
        if style == "cordic":
            return rng.choice(CORDIC + CORDIC + FAST[:3])
        if style == "divide":
            return rng.choice([FpuOp.DIV, FpuOp.SQRT, FpuOp.DIV, FpuOp.ADD, FpuOp.MUL])
        return rng.randrange(spec.FPU_OP_COUNT)

    for k in range(length):
        dst = rng.randrange(spec.EVU_REGISTERS)
        have_source = bool(written or constants)
        if tables and (not have_source or rng.random() < 0.2):
            lines.append(Instr(OP_LOAD, dst, rng.randrange(len(tables)), rng.randrange(64), last=k == length - 1))
        elif have_source:
            op = int(pick_op())
            b = rng.randrange(64) if op in UNARY else source()
            lines.append(Instr(op, dst, source(), b, last=k == length - 1))
        else:
            constants.append(random_bits(rng, data))
            lines.append(Instr(FpuOp.SQRT, dst, const(0), 0, last=k == length - 1))
        written.append(dst)
    return Run(Program([i.encode() for i in lines], constants), tables, grid, reduce=rng.random() < 0.5)
