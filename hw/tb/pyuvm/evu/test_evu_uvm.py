"""pyuvm environment for the expression unit (phase 4, milestone 3).

The DUT is evu_dut.sv: the real evu_core, fp_accum, fpu and scratch_mem together.
One transaction is a whole run: the scratchpad image, then start, then done.
  sequence -> sequencer -> driver -> DUT pins -> monitor -> scoreboard/coverage

The scoreboard predicts from what the monitor saw on the pins, not from what the
sequence meant to send. It rebuilds the run from the words written to the
scratchpad, using read_descriptor, and hands that to sw/mathaccel/evu_model.py.
For every run it checks:

1. Every word the unit wrote: right addresses, right values, nothing else, each
   once. In a reduce run that's the sum and its flags. In a map run it's r[i] and
   f[i] for every point, so a wrong grid order or a flag leaking between points
   shows up here.
2. aux, the OR of every point's flags.
3. Every FPU request: the set of (op, a, b) must match the model's trace exactly,
   so an instruction that was lost, run twice, or sent with the wrong operand
   fails even if it happens not to change the answer.

The monitor also checks the rule the whole design leans on: a context never has
two instructions in flight, and no result arrives for a context with none.

A descriptor is checked before anything else: the status has to match
check_descriptor, and a refused run must make no FPU request and write nothing.
RefuseSeq sends every reason there is, and random mixtures of them. EvuLimitTest
tests the point count at its exact limits, on grids far too large to run.

Cycle counts are not predicted (docs/phase4-plan.md explains why). EvuGateTest
reports them instead, which is what the milestone gate measures.

Knobs: FPU_N (default 60) sets the number of random runs.
"""

# I test the actual expression unit with its FPU and scratchpad here.
# I reconstruct what was loaded from monitored writes, then check every
# output and accepted FPU request. I watch context ownership too: one point
# can wait for an instruction, but it must never own two in-flight results.

# build_phase creates the components, connect_phase wires their software ports,
# and the run_phase coroutines drive or watch the simulated pins.

from __future__ import annotations

import json
import os
import random
from collections import Counter
from pathlib import Path

import cocotb
import numpy as np
import pyuvm
import sympy as sp
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, with_timeout
from pyuvm import (
    ConfigDB,
    uvm_agent,
    uvm_analysis_port,
    uvm_driver,
    uvm_env,
    uvm_monitor,
    uvm_sequence,
    uvm_sequence_item,
    uvm_sequencer,
    uvm_subscriber,
    uvm_test,
)

from mathaccel import evu_model
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp, Status
from mathaccel.compiler.evu_gen import (
    CORDIC,
    NEG_INF,
    POS_INF,
    QNAN,
    SNAN,
    UNARY,
    f,
    prog,
    random_run,
    table,
)
from mathaccel.compiler.evu_isa import (
    OP_LOAD,
    Instr,
    Run,
    check_descriptor,
    const,
    memory_image,
    read_descriptor,
    reg,
    word_problem,
)
from mathaccel.compiler.problems import PROBLEMS
from mathaccel.compiler.quadrature import Axis, integral_run

N_RUNS = int(os.environ.get("FPU_N", "60"))
SEED = int(os.environ.get("LCG_SEED", "1"), 16)
FIFO_DEPTH = int(os.environ.get("PARAM_FD", "64"))
CONTEXTS = int(os.environ.get("PARAM_CONTEXTS", str(spec.EVU_CONTEXTS)))



class EvuPhase:
    """The phase_t value of evu_core.sv this testbench looks for."""
    PROG = 3


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class RunItem(uvm_sequence_item):
    """One run. tamper can edit the image after it's built, to send what the
    compiler never would. base moves the whole image in the scratchpad."""

    def __init__(self, name="run", run=None, tamper=None, base=0, label=""):
        super().__init__(name)
        self.run, self.tamper, self.base, self.label = run, tamper, base, label

    def __str__(self):
        return f"{self.label}: {len(self.run.program.words)} instructions, grid {self.run.grid}"


class Observed(uvm_sequence_item):
    """What a run did at the pins."""

    def __init__(self, name="observed"):
        super().__init__(name)
        self.desc = 0
        self.mem: dict[int, int] = {}       # the scratchpad as the run found it
        self.writes: list[tuple[int, int]] = []
        self.requests: list[tuple[int, int, int]] = []
        self.aux = 0
        self.status = 0
        self.read_program = False           # got past the descriptor checks
        self.clocks = 0
        self.refused: Counter = Counter()
        self.max_pending = 0


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------

class BaseSeq(uvm_sequence):
    async def send(self, run, tamper=None, base=0, label=""):
        item = RunItem("it", run, tamper, base, label)
        await self.start_item(item)
        await self.finish_item(item)


class DirectedSeq(BaseSeq):
    """Cases random programs would rarely hit."""

    async def body(self):
        one, two = f(1.0), f(2.0)
        add = int(FpuOp.ADD)
        mul = int(FpuOp.MUL)
        load = OP_LOAD
        # The smallest programs: one instruction, in both modes.
        for reduce in (True, False):
            await self.send(Run(prog(Instr(add, 0, const(0), const(1)), constants=[one, two]), [], (1,), reduce))
            await self.send(Run(prog(Instr(add, 0, const(0), const(1)), constants=[one, two]), [], (5,), reduce))
            await self.send(Run(prog(Instr(load, 0, 0), constants=[]), [table([1.5, 2.5, 3.5])], (3,), reduce))
        # Grid shapes, read in map mode so the order of the points is checked.
        # The table values name their index, so a mix-up can't cancel out.
        for grid in [(1, 1, 1), (3, 1, 2), (1, 5), (5, 1), (2, 3, 4), (4, 1, 1), (1, 1, 6), (3, 7)]:
            tables = [table([float(100 * d + k) for k in range(n)], d) for d, n in enumerate(grid)]
            lines = [Instr(load, d, d) for d in range(len(grid))]
            total = Instr(add, 10, reg(0), reg(1)) if len(grid) > 1 else Instr(add, 10, reg(0), reg(0))
            lines += [total]
            if len(grid) > 2:
                lines += [Instr(mul, 11, reg(10), reg(2))]
            await self.send(Run(prog(*lines), tables, grid, reduce=False), label=f"grid {grid}")
        # Several tables on one dimension, as an integral's nodes and weights are.
        await self.send(Run(prog(Instr(load, 0, 0), Instr(load, 1, 1), Instr(load, 2, 2), Instr(load, 3, 3),
                                 Instr(load, 4, 4), Instr(load, 5, 5), Instr(add, 6, reg(0), reg(1)),
                                 Instr(add, 6, reg(6), reg(2)), Instr(add, 6, reg(6), reg(3)),
                                 Instr(add, 6, reg(6), reg(4)), Instr(add, 6, reg(6), reg(5))),
                            [table([1.0 * k + d * 0.25 for k in range(3 if d % 2 else 4)], d % 2) for d in range(6)],
                            (4, 3), reduce=False))
        # The highest register and constant.
        pool = [f(float(k)) for k in range(64)]
        await self.send(Run(prog(Instr(add, 63, const(63), const(62)), Instr(mul, 62, reg(63), const(1)),
                                 Instr(add, 0, reg(62), reg(63)), constants=pool), [], (3,), reduce=False))
        # Flags stay with their point: one point divides by zero, the next doesn't.
        await self.send(Run(prog(Instr(load, 0, 0), Instr(int(FpuOp.DIV), 1, const(0), reg(0)), constants=[one]),
                            [table([1.0, 0.0, 2.0, 0.0, 4.0, 8.0])], (6,), reduce=False))
        # Special values reaching the sum. A LOAD passes an sNaN on unchanged.
        for values in ([POS_INF, 1.0], [NEG_INF, 1.0], [POS_INF, NEG_INF], [QNAN, 1.0], [SNAN, 1.0], [0.0, 0x80000000]):
            await self.send(Run(prog(Instr(load, 0, 0)), [table(values)], (len(values),)))
            await self.send(Run(prog(Instr(load, 0, 0)), [table(values)], (len(values),), reduce=False))
        # Sum to exactly zero, and a sum with no rounding room.
        await self.send(Run(prog(Instr(load, 0, 0)), [table([1.0, -1.0, 0.5, -0.5])], (4,)))
        # The largest program.
        lines = [Instr(load, 0, 0)] + [Instr(add, 0, reg(0), const(0)) for _ in range(spec.EVU_PROGRAM_LEN - 1)]
        await self.send(Run(prog(*lines, constants=[one]), [table([0.0, 1.0])], (2,)))
        # A program that never says last stops at the end of its length.
        run = Run(prog(Instr(load, 0, 0), Instr(add, 1, reg(0), const(0)), constants=[one]), [table([3.0, 4.0])], (2,))
        await self.send(run, tamper=lambda w, im: self.clear_last(w, im), label="no last bit")
        # A last bit partway through ends the program there.
        run = Run(prog(Instr(load, 0, 0), Instr(add, 1, reg(0), const(0)), Instr(add, 2, reg(1), const(0)),
                       constants=[one]), [table([3.0, 4.0])], (2,), reduce=False)
        await self.send(run, tamper=lambda w, im: self.early_last(w, im), label="early last bit")
        # A divide on every point, which keeps the divider busy.
        await self.send(Run(prog(Instr(load, 0, 0), Instr(int(FpuOp.DIV), 1, const(0), reg(0)),
                                 Instr(int(FpuOp.SQRT), 2, reg(1)), constants=[one]),
                            [table([float(k + 1) for k in range(60)])], (60,)))
        # Only loads, the fastest points there are. In map mode they outrun the
        # two scratchpad writes per point, so the output side must hold them back.
        await self.send(Run(prog(Instr(load, 0, 0)), [table([float(k) for k in range(300)])], (300,), reduce=False))
        # The same image somewhere else in the scratchpad.
        await self.send(Run(prog(Instr(load, 0, 0), Instr(mul, 1, reg(0), reg(0))), [table([1.0, 2.0, 3.0])], (3,),
                            reduce=False), base=0xF000)

    def clear_last(self, words, image):
        program = int(words[spec.EVU_DESC_PROG])
        for k in range(int(words[spec.EVU_DESC_PROG_LEN])):
            words[program - image.base + k] &= ~(1 << spec.EVU_LAST_SHIFT) & 0xFFFFFFFF

    def early_last(self, words, image):
        program = int(words[spec.EVU_DESC_PROG])
        words[program - image.base + 1] |= 1 << spec.EVU_LAST_SHIFT


class RandomSeq(BaseSeq):
    count = 60
    seed = 1

    async def body(self):
        rng = random.Random(self.seed)
        for k in range(self.count):
            await self.send(random_run(rng), base=rng.choice([0, 0, 0x100, 0x8000]), label=f"random {k}")


def class_runs():
    """Each class problem at its smallest rule, then a few made-up shapes."""
    # I compile the small application rules into the same runs the host would send.
    for p in PROBLEMS:
        yield p.name, integral_run(p.expr, p.axes(p.rules[0]))


def shape_runs(n: int = 256):
    """Programs that show the limits: all fast ops, all CORDIC, one divide, and
    the 20-op program with 3 CORDIC ops the plan's estimates used."""
    # I stress both output modes with grid shapes that move through contexts differently.
    x = [float(k + 1) / n for k in range(n)]
    add, mul = int(FpuOp.ADD), int(FpuOp.MUL)
    fast = [Instr(OP_LOAD, 0, 0)] + [Instr(add if k % 2 else mul, 0, reg(0), const(0)) for k in range(19)]
    cordic = [Instr(OP_LOAD, 0, 0)] + [Instr(int(FpuOp.SIN), 0, reg(0)) for k in range(19)]
    divide = [Instr(OP_LOAD, 0, 0)] + [Instr(mul, 0, reg(0), const(0)) for k in range(18)]
    divide.append(Instr(int(FpuOp.DIV), 0, const(0), reg(0)))
    mixed = [Instr(OP_LOAD, 0, 0)]
    for k in range(19):
        op = int(FpuOp.SIN) if k in (4, 10, 16) else (add if k % 2 else mul)
        mixed.append(Instr(op, 0, reg(0), const(0)))
    for name, lines in (("20 fast ops", fast), ("20 ops, 3 CORDIC", mixed), ("20 ops, 1 divide", divide),
                        ("20 CORDIC ops", cordic)):
        yield name, Run(prog(*lines, constants=[f(1.0)]), [table(x)], (n,))


def tiny_run(reduce: bool) -> Run:
    """A small good run for the refusal tests to spoil: two instructions, one
    constant, one table, four points."""
    lines = [Instr(OP_LOAD, 0, 0), Instr(FpuOp.ADD, 1, reg(0), const(0))]
    return Run(prog(*lines, constants=[f(1.0)]), [table([1.0, 2.0, 3.0, 4.0])], (4,), reduce)


def poke(*edits):
    """A tamper function: each edit is (word offset in the descriptor, value)."""
    def tamper(words, image):
        for offset, value in edits:
            words[offset] = value
    return tamper


def poke_program(k: int, word: int):
    """A tamper function that replaces instruction k of the program."""
    def tamper(words, image):
        words[spec.EVU_DESC_WORDS + k] = word
    return tamper


TOP = spec.SCRATCH_WORDS
GRID, TABLE = spec.EVU_DESC_GRID, spec.EVU_DESC_TABLE
# One of each way to be refused, with the status check_descriptor gives it. The
# scoreboard works the status out on its own; these labels are for the log.
REFUSALS = [
    ("mode bits", poke((spec.EVU_DESC_MODE, 2))),
    ("no program", poke((spec.EVU_DESC_PROG_LEN, 0))),
    ("program too long", poke((spec.EVU_DESC_PROG_LEN, spec.EVU_PROGRAM_LEN + 1))),
    ("program length high bit", poke((spec.EVU_DESC_PROG_LEN, 1 << 31 | 2))),
    ("too many constants", poke((spec.EVU_DESC_CONST_LEN, spec.EVU_CONSTANTS + 1))),
    ("zero n0", poke((GRID, 0))),
    ("zero n1", poke((GRID + 1, 0))),
    ("zero n2", poke((GRID + 2, 0))),
    ("n0 too large", poke((GRID, 1 << 16))),
    ("n2 too large", poke((GRID + 2, 1 << 24 | 1))),
    ("table dimension 3", poke((TABLE, 3 << spec.EVU_DESC_DIM_SHIFT))),
    ("table spare bits", poke((TABLE + 5, 1 << 18))),
    ("program past the end", poke((spec.EVU_DESC_PROG, TOP - 1))),
    ("program address high", poke((spec.EVU_DESC_PROG, TOP))),
    ("constants past the end", poke((spec.EVU_DESC_CONST, TOP - 0), (spec.EVU_DESC_CONST_LEN, 1))),
    ("constants address high", poke((spec.EVU_DESC_CONST, 1 << 20))),
    ("r address high", poke((spec.EVU_DESC_R, TOP + 3))),
    ("f address high", poke((spec.EVU_DESC_F, 0xFFFF0000))),
    ("table past the end", poke((TABLE, TOP - 3))),
    *[(f"table {k} past the end, used or not", poke((TABLE + k, TOP - 3))) for k in range(spec.EVU_TABLES)],
    ("second dimension of a table", poke((GRID + 1, 5), (TABLE + 1, TOP - 4 | 1 << spec.EVU_DESC_DIM_SHIFT))),
    ("unknown op", poke_program(1, Instr(18, 1, reg(0)).encode())),
    ("last unknown op", poke_program(1, Instr(30, 1, reg(0), last=True).encode())),
    ("table 6", poke_program(0, Instr(OP_LOAD, 0, spec.EVU_TABLES).encode())),
    ("constant a not in the pool", poke_program(1, Instr(FpuOp.ADD, 1, const(1), reg(0)).encode())),
    ("constant b not in the pool", poke_program(1, Instr(FpuOp.ADD, 1, reg(0), const(63)).encode())),
    ("constant b of a LOAD", poke_program(0, Instr(OP_LOAD, 0, 0, const(1)).encode())),
    ("spare bits of a word", poke_program(1, Instr(FpuOp.ADD, 1, reg(0), reg(0)).encode() | 0x20)),
    # A map run's four outputs don't fit, though a reduce run's one would.
    ("map r past the end", poke((spec.EVU_DESC_R, TOP - 3))),
    ("map f past the end", poke((spec.EVU_DESC_F, TOP - 1))),
    ("points past the limit", poke((GRID, 32768), (GRID + 1, 32768), (GRID + 2, 5))),
    ("largest grid", poke((GRID, 65535), (GRID + 1, 65535), (GRID + 2, 65535))),
    ("a field and a span", poke((spec.EVU_DESC_MODE, 4), (spec.EVU_DESC_R, TOP))),
    ("a span and a program", lambda words, image: (poke((spec.EVU_DESC_R, TOP))(words, image),
                                                   poke_program(1, Instr(18, 1, reg(0)).encode())(words, image))),
]


def random_tamper(rng: random.Random):
    """One to three spoiled fields. These can come out as good runs, and that's
    fine, as long as no good run can overlap its own output or read a register
    before writing it, which would be undefined. So grids only change to 1 or to
    something refused, tables only move to where nothing writes, and a program
    word only changes to one that's refused."""
    edits = []
    words = []
    for _ in range(rng.randint(1, 3)):
        field = rng.choice(["mode", "len", "pool", "grid", "table", "r", "f", "base", "word"])
        if field == "mode":
            edits.append((spec.EVU_DESC_MODE, rng.choice([2, 3, 1 << 31, rng.getrandbits(32) | 2])))
        elif field == "len":
            edits.append((spec.EVU_DESC_PROG_LEN, rng.choice([0, 257, 511, 1 << 16, rng.getrandbits(32) | 1 << 9])))
        elif field == "pool":
            edits.append((spec.EVU_DESC_CONST_LEN, rng.choice([0, 65, 127, 1 << 7, rng.getrandbits(32) | 1 << 8])))
        elif field == "grid":
            edits.append((GRID + rng.randrange(3), rng.choice([0, 1, 1 << 16, rng.getrandbits(32) | 1 << 16])))
        elif field == "table":
            k = rng.randrange(spec.EVU_TABLES)
            edits.append((TABLE + k, rng.choice([TOP - 1, TOP - 3, TOP - 4, TOP - 5, 3 << 16, 2 << 16,
                                                 1 << 18, rng.getrandbits(32) | 3 << 16])))
        elif field == "r":
            edits.append((spec.EVU_DESC_R, rng.choice([TOP - 1, TOP - 2, TOP, TOP + 1, rng.getrandbits(32) | TOP])))
        elif field == "f":
            edits.append((spec.EVU_DESC_F, rng.choice([TOP - 8, TOP - 5, TOP, 1 << 31, rng.getrandbits(32) | TOP])))
        elif field == "base":
            edits.append((rng.choice([spec.EVU_DESC_PROG, spec.EVU_DESC_CONST]),
                          rng.choice([TOP - 1, TOP, TOP + 7, rng.getrandbits(32) | TOP])))
        else:
            while True:
                word = rng.getrandbits(32)
                if word_problem(word, 1):
                    break
            words.append((rng.randrange(2), word))

    def tamper(buffer, image):
        poke(*edits)(buffer, image)
        for k, word in words:
            buffer[spec.EVU_DESC_WORDS + k] = word
    return tamper


class RefuseSeq(BaseSeq):
    """Every way to be refused, then random mixtures. A good run follows each
    group, because a refused run must leave the unit ready for the next one."""
    count = 60
    seed = 1

    async def body(self):
        rng = random.Random(self.seed)
        for reduce in (True, False):
            for label, tamper in REFUSALS:
                await self.send(tiny_run(reduce), tamper=tamper, label=label)
            await self.send(tiny_run(reduce), label="good after refusals")
        for k in range(self.count):
            await self.send(tiny_run(k % 2 == 0), tamper=random_tamper(rng), label=f"random {k}")
            if k % 5 == 0:
                await self.send(tiny_run(k % 2 == 1), label="good")


class ProblemSeq(BaseSeq):
    async def body(self):
        for name, run in class_runs():
            await self.send(run, label=name)


class ShapeSeq(BaseSeq):
    async def body(self):
        for name, run in shape_runs():
            await self.send(run, label=name)


# ---------------------------------------------------------------------------
# Agent components
# ---------------------------------------------------------------------------

class RunDriver(uvm_driver):
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")

    async def run_phase(self):
        dut = self.dut
        await FallingEdge(dut.rst)
        while True:
            item = await self.seq_item_port.get_next_item()
            image = memory_image(item.run, item.base)
            words = image.words.copy()
            if item.tamper:
                item.tamper(words, image)
            # Everything changes on the falling edge; the DUT samples on the rising one.
            dut.tb_we.value = 1
            dut.tb_en.value = 1
            for k, word in enumerate(words):
                dut.tb_addr.value = image.base + k
                dut.tb_wdata.value = int(word)
                await FallingEdge(dut.clk)
            dut.tb_we.value = 0
            dut.tb_en.value = 0
            dut.desc_addr.value = image.desc
            dut.start.value = 1
            await FallingEdge(dut.clk)
            dut.start.value = 0
            limit = 100 * len(item.run.program.words) * item.run.points + 20000
            await with_timeout(RisingEdge(dut.done), limit * 10, "ns")
            await FallingEdge(dut.clk)
            await FallingEdge(dut.clk)
            self.seq_item_port.item_done()


class PinMonitor(uvm_monitor):
    """Rebuilds each run from the pins. The scratchpad contents come from the
    testbench port, and what the unit did from its FPU and write ports."""

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.ap = uvm_analysis_port("ap", self)

    async def run_phase(self):
        dut = self.dut
        mem: dict[int, int] = {}
        seen = Observed()
        inflight: set[int] = set()
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            # The scratchpad has no reset, so this comes first.
            if int(dut.tb_en.value) and int(dut.tb_we.value):
                mem[int(dut.tb_addr.value)] = int(dut.tb_wdata.value)
            if int(dut.rst.value):
                continue
            if int(dut.start.value):
                seen = Observed()
                seen.desc = int(dut.desc_addr.value)
                seen.mem = mem
                inflight = set()
            if int(dut.done.value):             # done comes the clock after busy falls
                seen.aux = int(dut.aux.value)
                seen.status = int(dut.status.value)
                self.ap.write(seen)
            if not int(dut.busy.value):
                continue
            seen.clocks += 1
            seen.read_program |= int(dut.cov_phase.value) == EvuPhase.PROG
            seen.max_pending = max(seen.max_pending, int(dut.cov_pending.value))
            if int(dut.fpu_rsp_valid.value):
                tag = int(dut.fpu_rsp_tag.value)
                if tag not in inflight:
                    self.logger.error(f"result for context {tag}, which has nothing in flight")
                inflight.discard(tag)
            if int(dut.fpu_req_valid.value):
                op = int(dut.fpu_req_op.value)
                if int(dut.fpu_req_ready.value):
                    tag = int(dut.fpu_req_tag.value)
                    if tag in inflight:
                        self.logger.error(f"context {tag} has two instructions in flight")
                    inflight.add(tag)
                    seen.requests.append((op, int(dut.fpu_req_a.value), int(dut.fpu_req_b.value)))
                elif int(dut.cov_old.value):
                    seen.refused["old"] += 1
                elif int(dut.cov_full.value) and op in CORDIC:
                    seen.refused["full"] += 1
                elif int(dut.cov_ds_busy.value) and op in (FpuOp.DIV, FpuOp.SQRT):
                    seen.refused["divider"] += 1
                else:
                    seen.refused["other"] += 1
            if int(dut.b_we.value):
                seen.writes.append((int(dut.b_addr.value), int(dut.b_wdata.value)))
                mem[seen.writes[-1][0]] = seen.writes[-1][1]


class RunAgent(uvm_agent):
    def build_phase(self):
        self.sqr = uvm_sequencer("sqr", self)
        self.drv = RunDriver("drv", self)
        self.mon = PinMonitor("mon", self)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.sqr.seq_item_export)


# ---------------------------------------------------------------------------
# Scoreboard and coverage
# ---------------------------------------------------------------------------

def request_set(trace: list) -> Counter:
    """Every (op, a, b) the model says the FPU should see. b counts as 0 where it's ignored."""
    # I count accepted op/operand tuples so missing or duplicate work fails even if its sum looks right.
    want: Counter = Counter()
    for op, a, b in trace:
        b = np.zeros(len(a), dtype=np.uint32) if b is None else b
        want.update((op, int(x), int(y)) for x, y in zip(a, b, strict=True))
    return want


class RunScoreboard(uvm_subscriber):
    def build_phase(self):
        self.checked = self.errors = self.points = self.ops = self.refused = 0
        self.clocks = 0

    def fail(self, text: str):
        self.errors += 1
        if self.errors <= 20:
            self.logger.error(text)

    def write(self, seen: Observed):
        # The memory changes as results go in, but only outside the image.
        wanted_status = check_descriptor(lambda a: seen.mem.get(a, 0), seen.desc)
        if seen.status != wanted_status:
            self.fail(f"status {seen.status}, model {Status(wanted_status).name}")
        if wanted_status != Status.OK:
            self.refused += 1
            if seen.writes or seen.requests or seen.aux:
                self.fail(f"refused run: {len(seen.writes)} writes, {len(seen.requests)} FPU requests, aux {seen.aux}")
            return
        run, r_addr, f_addr = read_descriptor(lambda a: seen.mem.get(a, 0), seen.desc)
        trace: list = []
        m = evu_model.execute(run, trace=trace)
        self.checked += 1
        self.points += run.points
        self.ops += len(seen.requests)
        self.clocks += seen.clocks
        if run.reduce:
            want = {r_addr: m.sum, f_addr: m.sum_flags}
        else:
            want = {r_addr + i: int(x) for i, x in enumerate(m.results)}
            want.update({f_addr + i: int(x) for i, x in enumerate(m.flags)})
        got = dict(seen.writes)
        if len(seen.writes) != len(want) or len(got) != len(seen.writes):
            self.fail(f"{len(seen.writes)} writes to {len(got)} addresses, expected {len(want)}")
        for addr, value in want.items():
            if got.get(addr) != value:
                self.fail(f"grid {run.grid} word {addr:#x}: got {got.get(addr, -1):#x}, model {value:#x}")
                break
        if seen.aux != m.aux:
            self.fail(f"aux {seen.aux:#x}, model {m.aux:#x}")
        requests = Counter((op, a, 0 if (op == OP_LOAD or op in UNARY) else b) for op, a, b in seen.requests)
        wanted = request_set(trace)
        if requests != wanted:
            lost, extra = wanted - requests, requests - wanted
            self.fail(f"FPU requests differ: {sum(lost.values())} missing {list(lost)[:3]}, "
                      f"{sum(extra.values())} extra {list(extra)[:3]}")

    def report_phase(self):
        self.logger.info(f"checked {self.checked} runs, {self.refused} refused descriptors, {self.points} points, "
                         f"{self.ops} FPU operations, {self.clocks} clocks, {self.errors} errors")


class RunCoverage(uvm_subscriber):
    GOAL = ({f"op{k}" for k in range(spec.FPU_OP_COUNT)}
            | {"load", "a_reg", "a_const", "b_reg", "b_const", "len_1", "len_short", "len_long", "len_max",
               "dims_1", "dims_2", "dims_3", "map", "reduce", "refused_divider", "refused_old", "contexts_full",
               "output_full", "sum_nan", "sum_inf", "sum_zero", "point_flags", "no_last", "early_last",
               "status_bad_arg", "status_bad_addr", "status_bad_program"})

    def build_phase(self):
        self.hits: Counter = Counter()
        if FIFO_DEPTH < 64:
            self.GOAL = self.GOAL | {"refused_full"}

    def write(self, seen: Observed):
        if seen.status != Status.OK:
            self.hits["status_bad_addr" if seen.status == Status.BAD_ADDR
                      else "status_bad_program" if seen.read_program else "status_bad_arg"] += 1
            return
        run, _, _ = read_descriptor(lambda a: seen.mem.get(a, 0), seen.desc)
        ins = run.program.instrs()
        d = [seen.mem.get(seen.desc + k, 0) for k in range(spec.EVU_DESC_WORDS)]
        written = [Instr.decode(seen.mem.get(d[spec.EVU_DESC_PROG] + k, 0)) for k in range(d[spec.EVU_DESC_PROG_LEN])]
        hit = {"reduce" if run.reduce else "map"}
        for i in ins:
            hit.add("load" if i.op == OP_LOAD else f"op{i.op}")
            if i.op != OP_LOAD:
                hit.add("a_const" if i.a & spec.EVU_SRC_CONST else "a_reg")
                if i.op not in UNARY:
                    hit.add("b_const" if i.b & spec.EVU_SRC_CONST else "b_reg")
        n = len(ins)
        kind = "len_1" if n == 1 else "len_max" if n == spec.EVU_PROGRAM_LEN else "len_short" if n < 16 else "len_long"
        hit.add(kind)
        hit.add(f"dims_{sum(1 for g in run.grid if g > 1) or 1}")
        hit.update(f"refused_{k}" for k in seen.refused)
        if seen.max_pending >= CONTEXTS:
            hit.add("contexts_full")
        if seen.max_pending >= 32:
            hit.add("output_full")
        if not any(i.last for i in written):
            hit.add("no_last")
        elif any(i.last for i in written[:-1]):
            hit.add("early_last")
        if seen.aux:
            hit.add("point_flags")
        if run.reduce:
            bits = dict(seen.writes).get(d[spec.EVU_DESC_R], 0)
            if bits & 0x7F800000 == 0x7F800000:
                hit.add("sum_nan" if bits & 0x7FFFFF else "sum_inf")
            if bits == 0:
                hit.add("sum_zero")
        self.hits.update(hit)

    def report_phase(self):
        self.logger.info(f"coverage {len(self.GOAL & set(self.hits))}/{len(self.GOAL)} bins, "
                         f"missed {sorted(self.GOAL - set(self.hits))}")
        self.logger.info("refusals: " + ", ".join(f"{k} runs {v}" for k, v in sorted(self.hits.items())
                                                  if k.startswith("refused")))


class RunEnv(uvm_env):
    def build_phase(self):
        self.agent = RunAgent("agent", self)
        self.sb = RunScoreboard("sb", self)
        self.cov = RunCoverage("cov", self)

    def connect_phase(self):
        self.agent.mon.ap.connect(self.sb.analysis_export)
        self.agent.mon.ap.connect(self.cov.analysis_export)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class BaseTest(uvm_test):
    check_coverage = True

    def build_phase(self):
        ConfigDB().set(None, "*", "DUT", cocotb.top)
        self.env = RunEnv("env", self)

    async def reset(self):
        dut = cocotb.top
        Clock(dut.clk, 10, unit="ns").start()
        dut.rst.value = 1
        dut.start.value = 0
        dut.desc_addr.value = 0
        dut.tb_en.value = 0
        dut.tb_we.value = 0
        dut.tb_addr.value = 0
        dut.tb_wdata.value = 0
        for _ in range(5):
            await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        dut.rst.value = 0

    async def run_seqs(self):
        raise NotImplementedError

    async def run_phase(self):
        self.raise_objection()
        await self.reset()
        await self.run_seqs()
        for _ in range(20):
            await RisingEdge(cocotb.top.clk)
        self.drop_objection()

    def final_phase(self):
        sb, cov = self.env.sb, self.env.cov
        assert sb.errors == 0 and sb.checked > 0, f"{sb.errors} errors, {sb.checked} checked"
        if self.check_coverage:
            missed = cov.GOAL - set(cov.hits)
            assert not missed, f"coverage bins not hit: {sorted(missed)}"


@pyuvm.test()
class EvuDirectedTest(BaseTest):
    check_coverage = False

    async def run_seqs(self):
        await DirectedSeq("directed").start(self.env.agent.sqr)


@pyuvm.test()
class EvuRegressTest(BaseTest):
    async def run_seqs(self):
        await DirectedSeq("directed").start(self.env.agent.sqr)
        await ProblemSeq("problems").start(self.env.agent.sqr)
        refuse = RefuseSeq("refuse")
        refuse.count = N_RUNS
        refuse.seed = SEED
        await refuse.start(self.env.agent.sqr)
        rnd = RandomSeq("random")
        rnd.count = N_RUNS
        rnd.seed = SEED
        await rnd.start(self.env.agent.sqr)


@pyuvm.test()
class EvuRefuseTest(BaseTest):
    check_coverage = False

    async def run_seqs(self):
        refuse = RefuseSeq("refuse")
        refuse.count = N_RUNS
        refuse.seed = SEED
        await refuse.start(self.env.agent.sqr)


@pyuvm.test()
class EvuLimitTest(BaseTest):
    """The point limits, right at their edges. A grid that passes would run for
    hours, so I stop each one as soon as it has passed the checks. This test talks
    to the pins itself, because no run here ever finishes the normal way."""
    check_coverage = False

    def grids(self):
        rng = random.Random(SEED)
        cases = [(1, 1, 1), (65535, 65535, 1), (65535, 65535, 2), (65535, 65535, 65535), (32768, 32768, 4),
                 (32768, 32768, 5), (32768, 65535, 2), (256, 256, 1), (256, 256, 2), (257, 255, 1), (2, 32768, 1),
                 (2, 32769, 1), (1, 65535, 1), (3, 21845, 1), (3, 21846, 1)]
        # Powers of two. The product's low bits are all zero, so if the high bits
        # were lost in the shift-and-add it would look like a small number.
        cases += [(1024, 256, 32768), (32768, 32768, 32768), (16384, 16384, 32768), (4096, 512, 32768),
                  (2048, 1024, 16384), (32768, 2, 32768), (8192, 8192, 8192), (2, 2, 32768)]
        # A small third factor on a large product: the running sum carries out of
        # its top bit, and what's left looks like a legal count.
        cases += [(65535, 65535, 3), (65535, 65535, 7), (65535, 32768, 5)]
        cases = [(g, reduce) for g in cases for reduce in (True, False)]
        for _ in range(max(N_RUNS, 40)):
            limit = 1 << spec.EVU_MAX_POINTS_LOG2 if rng.random() < 0.5 else spec.SCRATCH_WORDS
            n0 = rng.randint(2, 65535)
            n1 = rng.randint(2, 2000) if limit > 1 << 20 else rng.randint(1, 300)
            n2 = limit // (n0 * n1) + rng.randint(-1, 2)
            if 1 <= n2 <= 65535:
                cases.append(((n0, n1, n2), rng.random() < 0.5))
        return cases

    async def write_word(self, address: int, value: int):
        dut = cocotb.top
        self.mem[address] = value
        dut.tb_en.value = 1
        dut.tb_we.value = 1
        dut.tb_addr.value = address
        dut.tb_wdata.value = value
        await FallingEdge(dut.clk)
        dut.tb_en.value = 0
        dut.tb_we.value = 0

    async def run_seqs(self):
        dut = cocotb.top
        self.mem: dict[int, int] = {}
        self.limit_errors = self.limit_passed = self.limit_refused = 0
        word = Instr(FpuOp.ADD, 0, reg(0), reg(0), last=True).encode()
        await self.write_word(spec.EVU_DESC_WORDS, word)
        await self.write_word(spec.EVU_DESC_PROG, spec.EVU_DESC_WORDS)
        await self.write_word(spec.EVU_DESC_PROG_LEN, 1)
        for grid, reduce in self.grids():
            await self.write_word(spec.EVU_DESC_MODE, int(reduce))
            for k, n in enumerate(grid):
                await self.write_word(GRID + k, n)
            want = check_descriptor(lambda a: self.mem.get(a, 0), 0)
            dut.desc_addr.value = 0
            dut.start.value = 1
            await FallingEdge(dut.clk)
            dut.start.value = 0
            for _ in range(400):
                await FallingEdge(dut.clk)
                if int(dut.done.value) or int(dut.cov_phase.value) == EvuPhase.PROG:
                    break
            passed = int(dut.cov_phase.value) == EvuPhase.PROG
            status = Status.OK if passed else int(dut.status.value)
            if status != want:
                self.limit_errors += 1
                self.logger.error(f"grid {grid} reduce {reduce}: status {status}, model {want}")
            self.limit_passed += passed
            self.limit_refused += not passed
            if passed:
                dut.rst.value = 1
                for _ in range(3):
                    await FallingEdge(dut.clk)
                dut.rst.value = 0
            # One clock so the monitor hands over this run before the next write.
            await FallingEdge(dut.clk)
        self.logger.info(f"limits: {self.limit_passed} grids passed, {self.limit_refused} refused, "
                         f"{self.limit_errors} errors")

    def final_phase(self):
        assert self.limit_errors == 0 and self.limit_passed > 10 and self.limit_refused > 10


class GateLog(uvm_subscriber):
    """Prints speed per run. This is the milestone 3 measurement."""

    def write(self, seen: Observed):
        run, _, _ = read_descriptor(lambda a: seen.mem.get(a, 0), seen.desc)
        ops = len(seen.requests)
        self.logger.info(f"GATE {len(run.program.words):3d} instrs {run.points:6d} points {seen.clocks:8d} clocks "
                         f"{ops / seen.clocks:5.3f} ops/clock {seen.clocks / run.points:7.1f} clocks/point "
                         f"refused {dict(seen.refused)}")


PROFILE_NAMES = ("setup", "issue_arith", "issue_load", "refused_divider", "refused_cordic", "refused_other",
                 "wait_divider", "wait_cordic", "dep_divider", "dep_cordic", "dep_fast", "output_hold", "drain", "fill",
                 "finish")
PROFILE_OUT = Path(__file__).resolve().parents[4] / "build" / "evu_profile.json"


@pyuvm.test()
class EvuProfileTest(uvm_test):
    """Where the clocks go, on every rule of the class list.

    Needs --param PROFILE=1. This drives the pins itself with no monitors: a Python
    call on every clock would make the 70-million-clock runs impractical. It checks
    three things per run: the categories add up to the whole run, the issue count
    is exactly instructions times points, and the sum and flags equal the model's.
    Set FPU_N to skip runs with more points than that.
    """

    async def word(self, address: int, value: int | None = None) -> int:
        """One scratchpad access through the testbench port."""
        dut = cocotb.top
        dut.tb_en.value = 1
        dut.tb_we.value = int(value is not None)
        dut.tb_addr.value = address
        dut.tb_wdata.value = value or 0
        await FallingEdge(dut.clk)
        dut.tb_en.value = 0
        dut.tb_we.value = 0
        await FallingEdge(dut.clk)
        return int(dut.tb_rdata.value)

    async def run_phase(self):
        self.raise_objection()
        dut = cocotb.top
        await BaseTest.reset(self)
        limit = int(os.environ.get("FPU_N", "0")) or 1 << 40
        rows = []
        self.bad = 0
        runs = [(p.name, integral_run(p.expr, p.axes(rule))) for p in PROBLEMS for rule in p.rules]
        x, y = sp.symbols("x y", real=True)
        mixed = sp.sin(x) * sp.exp(-y) / (1 + x**2) + sp.sqrt(y) * sp.cos(x * y) + x * y
        runs.append(("long_mixed", integral_run(mixed, [Axis(x, 0, 2, 16, 16), Axis(y, 0.1, 2, 16, 16)])))
        for name, run in runs:
            if run.points > limit:
                continue
            image = memory_image(run)
            for k, value in enumerate(image.words):
                await self.word(image.base + k, int(value))
            dut.desc_addr.value = image.desc
            dut.start.value = 1
            await FallingEdge(dut.clk)
            dut.start.value = 0
            await FallingEdge(dut.busy)
            profile = int(dut.prof.value)
            counts = {n: (profile >> (48 * k)) & ((1 << 48) - 1) for k, n in enumerate(PROFILE_NAMES)}
            await FallingEdge(dut.clk)
            await FallingEdge(dut.clk)
            clocks = sum(counts.values())
            want = evu_model.execute(run)
            got = (await self.word(image.r), await self.word(image.f))
            issued = counts["issue_arith"] + counts["issue_load"]
            good = issued == len(run.program.words) * run.points and got == (want.sum, want.sum_flags)
            self.bad += not good
            rows.append({"name": name, "points": run.points, "instructions": len(run.program.words), "clocks": clocks,
                         "counts": counts, "good": good})
            self.logger.info(f"PROFILE {name} {run.points} points {clocks} clocks issue {issued} good {good}")
        PROFILE_OUT.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
        self.drop_objection()

    def final_phase(self):
        assert self.bad == 0, f"{self.bad} runs failed a profile check"


@pyuvm.test()
class EvuGateTest(BaseTest):
    check_coverage = False

    def connect_phase(self):
        self.env.agent.mon.ap.connect(self.log.analysis_export)

    def build_phase(self):
        super().build_phase()
        self.log = GateLog("log", self)

    async def run_seqs(self):
        await ProblemSeq("problems").start(self.env.agent.sqr)
        await ShapeSeq("shapes").start(self.env.agent.sqr)
