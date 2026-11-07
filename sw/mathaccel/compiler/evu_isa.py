"""The expression unit's bytecode: instruction words and what a run is made of.

A program is a list of 32-bit instruction words that every point of a grid
runs from the first word to the one marked last. The field layout is in the
[evu] section of spec/accel.toml, so the host and the RTL share one definition.

An instruction is either an FPU op, R[dst] = op(a, b), or a LOAD, which reads a
node table. There is no branch and no memory write: a program is a straight
line of arithmetic, so it always finishes and its length is known in advance.

A source field names either a register or a constant from a pool shared by all
points. The top bit of the field says which, so a program never needs an
instruction just to load a constant.
"""

# I keep the host's view of the bytecode and descriptor together here.
# I count addresses in 32-bit words, not bytes. I check both the friendly Run
# objects and raw descriptors, because the board only sees words and must
# refuse malformed input before it writes any answer.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from mathaccel._generated import spec
from mathaccel._generated.spec import Status

OP_LOAD = spec.EVU_OP_LOAD
SRC_CONST = spec.EVU_SRC_CONST
_FIELD = 0x7F           # a source field is 7 bits
_REG = 0x3F             # a register number is 6 bits


class EvuError(ValueError):
    """A program or run that the expression unit could not accept."""


def reg(n: int) -> int:
    """I name a context register with this source field."""
    return n


def const(n: int) -> int:
    """I set the source tag bit so this operand comes from the shared constant bank."""
    return SRC_CONST | n


@dataclass(frozen=True)
class Instr:
    """One decoded instruction. op is an FPU op code or OP_LOAD; a and b are
    source fields, except that a LOAD's a field is the table number."""
    op: int
    dst: int
    a: int = 0
    b: int = 0
    last: bool = False

    def encode(self) -> int:
        """I pack into the 32-bit word the hardware reads."""
        return (self.op << spec.EVU_OP_SHIFT | int(self.last) << spec.EVU_LAST_SHIFT
                | self.dst << spec.EVU_DST_SHIFT | self.a << spec.EVU_A_SHIFT | self.b << spec.EVU_B_SHIFT)

    @staticmethod
    def decode(word: int) -> Instr:
        """I unpack a 32-bit instruction word."""
        return Instr(op=word >> spec.EVU_OP_SHIFT, dst=(word >> spec.EVU_DST_SHIFT) & _REG,
                     a=(word >> spec.EVU_A_SHIFT) & _FIELD, b=(word >> spec.EVU_B_SHIFT) & _FIELD,
                     last=bool((word >> spec.EVU_LAST_SHIFT) & 1))


def word_problem(word: int, constants: int) -> str | None:
    """I explain why the board would refuse this word, or return None when it is valid.

    constants is the size of the pool. evu_core.sv makes the same checks as it
    copies a program in, on every word up to the length.
    """
    i = Instr.decode(word)
    if word & 0x3F:
        return "the spare low bits must be zero"
    if i.op != OP_LOAD and i.op >= spec.FPU_OP_COUNT:
        return f"unknown op {i.op}"
    if i.op == OP_LOAD and i.a >= spec.EVU_TABLES:
        return f"table {i.a}, the limit is {spec.EVU_TABLES}"
    for s in (i.b,) if i.op == OP_LOAD else (i.a, i.b):
        if s & SRC_CONST and (s & _REG) >= constants:
            return f"constant {s & _REG} is not in the pool"
    return None


@dataclass
class Program:
    """Instruction words plus the constant pool, as they will sit in the scratchpad."""
    words: list[int]
    constants: list[int] = field(default_factory=list)

    def instrs(self) -> list[Instr]:
        """The decoded instructions, in order."""
        return [Instr.decode(w) for w in self.words]

    def check(self) -> None:
        """I raise EvuError unless the program fits the hardware's limits."""
        ins = self.instrs()
        if not 1 <= len(ins) <= spec.EVU_PROGRAM_LEN:
            raise EvuError(f"{len(ins)} instructions, the limit is {spec.EVU_PROGRAM_LEN}")
        if len(self.constants) > spec.EVU_CONSTANTS:
            raise EvuError(f"{len(self.constants)} constants, the limit is {spec.EVU_CONSTANTS}")
        if [i.last for i in ins] != [False] * (len(ins) - 1) + [True]:
            raise EvuError("exactly the final instruction must be marked last")
        for w in self.words:
            problem = word_problem(w, len(self.constants))
            if problem:
                raise EvuError(problem)


@dataclass
class Table:
    """A node table: one fp32 pattern per index along grid dimension dim."""
    name: str
    values: np.ndarray
    dim: int


@dataclass
class Run:
    """Everything one RUN of the expression unit needs. reduce=True adds the
    points' outputs into one sum; False returns one result per point."""
    program: Program
    tables: list[Table]
    grid: tuple[int, ...]
    reduce: bool = True

    def check(self) -> None:
        """I raise EvuError unless the run fits the hardware's limits."""
        self.program.check()
        if not 1 <= len(self.grid) <= spec.EVU_DIMS or min(self.grid) < 1:
            raise EvuError(f"bad grid {self.grid}")
        if max(self.grid) >= 1 << 16:
            raise EvuError(f"grid {self.grid}: each dimension is limited to 65,535 points")
        if self.points > 1 << spec.EVU_MAX_POINTS_LOG2:
            raise EvuError(f"grid {self.grid}: {self.points:,} points, the limit is 2^{spec.EVU_MAX_POINTS_LOG2}")
        if len(self.tables) > spec.EVU_TABLES:
            raise EvuError(f"{len(self.tables)} tables, the limit is {spec.EVU_TABLES}")
        for t in self.tables:
            if not 0 <= t.dim < len(self.grid) or len(t.values) != self.grid[t.dim]:
                raise EvuError(f"table {t.name} does not fit dimension {t.dim} of grid {self.grid}")
        for i in self.program.instrs():
            if i.op == OP_LOAD and i.a >= len(self.tables):
                raise EvuError(f"instruction loads table {i.a}, the run has {len(self.tables)}")

    @property
    def points(self) -> int:
        """Grid points in the run."""
        return int(np.prod(self.grid, dtype=np.int64))


@dataclass
class Image:
    """A run laid out for the scratchpad: words[k] goes to address base + k. The
    descriptor is at desc, and the run's output goes to r and f."""
    base: int
    words: np.ndarray
    desc: int
    r: int
    f: int


def memory_image(run: Run, base: int = 0) -> Image:
    """I lay out the descriptor, program, constants and tables in scratchpad word order.

    Results and flags go straight after the image, one word each in a reduce run
    and one word each per point in a map run.
    """
    run.check()
    desc = base
    program = desc + spec.EVU_DESC_WORDS
    constants = program + len(run.program.words)
    next_free = constants + len(run.program.constants)
    words = [0] * spec.EVU_DESC_WORDS + run.program.words + run.program.constants
    entries = []
    for t in run.tables:
        entries.append(next_free | t.dim << spec.EVU_DESC_DIM_SHIFT)
        words += [int(v) for v in t.values]
        next_free += len(t.values)
    size = 1 if run.reduce else run.points
    r, f = next_free, next_free + size
    grid = list(run.grid) + [1] * (spec.EVU_DIMS - len(run.grid))
    words[spec.EVU_DESC_MODE] = int(run.reduce)
    words[spec.EVU_DESC_PROG] = program
    words[spec.EVU_DESC_PROG_LEN] = len(run.program.words)
    words[spec.EVU_DESC_CONST] = constants
    words[spec.EVU_DESC_CONST_LEN] = len(run.program.constants)
    words[spec.EVU_DESC_GRID:spec.EVU_DESC_GRID + spec.EVU_DIMS] = grid
    words[spec.EVU_DESC_R] = r
    words[spec.EVU_DESC_F] = f
    words[spec.EVU_DESC_TABLE:spec.EVU_DESC_TABLE + len(entries)] = entries
    if f + size > spec.SCRATCH_WORDS:
        raise EvuError(f"the run needs {f + size} scratchpad words")
    return Image(base, np.array(words, dtype=np.uint32), desc, r, f)


def check_descriptor(mem: Callable[[int], int], desc: int) -> int:
    """I check raw words in the same priority order as the hardware.

    mem(address) returns a word. Bad fields come first (BAD_ARG), then address
    spans (BAD_ADDR), and instruction words last (BAD_ARG), because evu_core.sv
    only copies the program once the descriptor is good. docs/protocol.md has the
    same list.
    """
    d = [mem(desc + k) for k in range(spec.EVU_DESC_WORDS)]
    length, pool = d[spec.EVU_DESC_PROG_LEN], d[spec.EVU_DESC_CONST_LEN]
    grid = d[spec.EVU_DESC_GRID:spec.EVU_DESC_GRID + spec.EVU_DIMS]
    entries = d[spec.EVU_DESC_TABLE:spec.EVU_DESC_TABLE + spec.EVU_TABLES]
    dims = [(e >> spec.EVU_DESC_DIM_SHIFT) & 3 for e in entries]
    points = grid[0] * grid[1] * grid[2]
    reduce = bool(d[spec.EVU_DESC_MODE] & 1)

    tables_good = all(dim < spec.EVU_DIMS and not e >> (spec.EVU_DESC_DIM_SHIFT + 2)
                      for dim, e in zip(dims, entries, strict=True))
    if (d[spec.EVU_DESC_MODE] >> 1 or not 1 <= length <= spec.EVU_PROGRAM_LEN or pool > spec.EVU_CONSTANTS
            or any(not 1 <= n < 1 << 16 for n in grid) or not tables_good
            or points > 1 << spec.EVU_MAX_POINTS_LOG2):
        return Status.BAD_ARG

    outputs = 1 if reduce else points
    spans = [(d[spec.EVU_DESC_PROG], length), (d[spec.EVU_DESC_CONST], pool),
             (d[spec.EVU_DESC_R], outputs), (d[spec.EVU_DESC_F], outputs)]
    # A table's address is its entry's low 16 bits, so only its end can be out of
    # range. For the others the start is tested too: an empty constant pool at a
    # bad address reads nothing, but the hardware refuses any address that doesn't
    # fit its 16 bits.
    spans += [(e & (spec.SCRATCH_WORDS - 1), grid[dim]) for dim, e in zip(dims, entries, strict=True)]
    if any(base >= spec.SCRATCH_WORDS or base + count > spec.SCRATCH_WORDS for base, count in spans):
        return Status.BAD_ADDR

    for k in range(length):
        if word_problem(mem(d[spec.EVU_DESC_PROG] + k), pool):
            return Status.BAD_ARG
    return Status.OK


def read_descriptor(mem: Callable[[int], int], desc: int) -> tuple[Run, int, int]:
    """I reconstruct the run the hardware sees, including an early last instruction.

    mem(address) returns a word. Returns the run plus the r and f addresses. A
    program ends at the first instruction marked last or at the end of its
    length, whichever comes first, because that is where the hardware stops.
    """
    d = [mem(desc + k) for k in range(spec.EVU_DESC_WORDS)]
    length = d[spec.EVU_DESC_PROG_LEN]
    words = [mem(d[spec.EVU_DESC_PROG] + k) for k in range(length)]
    for k, word in enumerate(words):
        if Instr.decode(word).last or k == length - 1:
            words = words[:k] + [word | 1 << spec.EVU_LAST_SHIFT]
            break
    constants = [mem(d[spec.EVU_DESC_CONST] + k) for k in range(d[spec.EVU_DESC_CONST_LEN])]
    grid = tuple(d[spec.EVU_DESC_GRID:spec.EVU_DESC_GRID + spec.EVU_DIMS])
    tables = []
    for k in range(spec.EVU_TABLES):
        entry = d[spec.EVU_DESC_TABLE + k]
        base, dim = entry & (spec.SCRATCH_WORDS - 1), entry >> spec.EVU_DESC_DIM_SHIFT
        values = np.array([mem(base + i) for i in range(grid[dim])], dtype=np.uint32)
        tables.append(Table(f"table{k}", values, dim))
    run = Run(Program(words, constants), tables, grid, bool(d[spec.EVU_DESC_MODE] & 1))
    return run, d[spec.EVU_DESC_R], d[spec.EVU_DESC_F]
