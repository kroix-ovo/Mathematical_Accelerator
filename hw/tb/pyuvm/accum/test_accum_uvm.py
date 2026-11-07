"""pyuvm environment for the exact accumulator fp_accum on its own (phase 4, milestone 2).

Same structure as the FPU and CORDIC block environments, except that one
transaction is a whole sum: a batch of terms, then finish. I keep that as the
item because nothing about a sum can be checked until it finishes.
  sequence -> sequencer -> driver -> DUT pins -> monitors -> scoreboard/coverage

The scoreboard predicts from what the monitor saw on the pins, not from what the
sequence meant to send, and checks every result three ways:

1. Bit for bit, result and flags, against exact_sum in sw/mathaccel/evu_model.py.
2. Against Python Fraction arithmetic, which shares no code with exact_sum.
3. Timing: out_valid exactly LAT_FINISH clocks after finish.

Knobs: FPU_N (default 400) sets the number of random batches.
"""

# I treat a whole sum as one transaction here. I observe accepted terms
# on the pins and check the final bits, flags and fixed finish latency.
# I also use Fraction arithmetic, so the scoreboard isn't only agreeing
# with another copy of the accumulator's own integer representation.

# build_phase creates the components, connect_phase wires their software ports,
# and the run_phase coroutines drive or watch the simulated pins.

from __future__ import annotations

import os
import random
from fractions import Fraction

import cocotb
import numpy as np
import pyuvm
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from pyuvm import (
    ConfigDB,
    uvm_agent,
    uvm_analysis_port,
    uvm_component,
    uvm_driver,
    uvm_env,
    uvm_monitor,
    uvm_sequence,
    uvm_sequence_item,
    uvm_sequencer,
    uvm_subscriber,
    uvm_test,
    uvm_tlm_analysis_fifo,
)

from mathaccel import evu_model, fp32ref

N_BATCHES = int(os.environ.get("FPU_N", "400"))
SEGMENTS = 10
LAT_FINISH = SEGMENTS * 2 + 9   # SETTLE (N + 2), SCAN (N + 2), NORM1-3, DONE, output register

POS_INF, NEG_INF, QNAN, SNAN = 0x7F800000, 0xFF800000, 0x7FC00000, 0x7FA00000
ONE_ULP = 0x00000001            # the smallest subnormal, 2^-149
MINUS_ULP = 0x80000001
MAX_FLT = 0x7F7FFFFF


def f(x: float) -> int:
    """The fp32 pattern of a Python float."""
    return int(np.array([x], dtype=np.float32).view(np.uint32)[0])


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class SumItem(uvm_sequence_item):
    """One sum: its terms, how long to pause before each, and where finish goes."""

    def __init__(self, name="sum", terms=(), gaps=None, with_last=False):
        super().__init__(name)
        self.terms = list(terms)
        self.gaps = gaps or [0] * len(self.terms)
        self.with_last = with_last       # finish on the same clock as the last term

    def __str__(self):
        return f"sum of {len(self.terms)} terms: {[hex(t) for t in self.terms[:6]]}..."


class ResultItem(uvm_sequence_item):
    def __init__(self, name="result", bits=0, flags=0, clocks=0):
        super().__init__(name)
        self.bits, self.flags, self.clocks = bits, flags, clocks


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------

class BaseSeq(uvm_sequence):
    async def send(self, terms, gaps=None, with_last=False):
        item = SumItem("it", terms, gaps, with_last)
        await self.start_item(item)
        await self.finish_item(item)


class DirectedSeq(BaseSeq):
    """Cases random numbers would almost never hit."""

    async def body(self):
        one, half = f(1.0), f(0.5)
        # Empty, single terms, and every kind of zero.
        await self.send([])
        await self.send([0])
        await self.send([0x80000000])
        await self.send([0x80000000, 0x80000000])
        await self.send([one])
        await self.send([f(-1.0)])
        await self.send([ONE_ULP])
        await self.send([MINUS_ULP])
        await self.send([MAX_FLT])
        await self.send([MAX_FLT | 0x80000000])
        # Exact cancellation, with and without something left over.
        await self.send([one, f(-1.0)])
        await self.send([MAX_FLT, MAX_FLT | 0x80000000])
        await self.send([MAX_FLT, ONE_ULP, MAX_FLT | 0x80000000])
        await self.send([MAX_FLT, MAX_FLT, MAX_FLT | 0x80000000])
        await self.send([ONE_ULP, MAX_FLT, MAX_FLT | 0x80000000, MINUS_ULP])
        # Longest carry: -2^-149 is all ones in every segment, so adding
        # 2^-149 back ripples through all ten of them.
        await self.send([MINUS_ULP, ONE_ULP])
        await self.send([MINUS_ULP, ONE_ULP, ONE_ULP])
        await self.send([MINUS_ULP] * 3 + [ONE_ULP] * 3)
        # A carry out of the top of a segment, at each segment boundary.
        for shift in range(0, 254, 32):
            b = (shift + 1) << 23
            await self.send([b | 0x7FFFFF, b | 0x7FFFFF, ONE_ULP, ONE_ULP])
        # Overflow: finite terms whose total doesn't fit, both signs.
        await self.send([MAX_FLT, MAX_FLT])
        await self.send([MAX_FLT | 0x80000000, MAX_FLT | 0x80000000])
        await self.send([MAX_FLT, f(2.0**103)])                # exactly half an ULP above MAX_FLT
        await self.send([MAX_FLT, f(2.0**102)])                # rounds back down to MAX_FLT
        # Rounding: ties to even both ways, and a sticky bit far below.
        await self.send([f(2.0**24), one])                     # tie, rounds down to even
        await self.send([f(2.0**24 + 2), one])                 # tie, rounds up to even
        await self.send([f(2.0**24), one, ONE_ULP])            # just above a tie: rounds up
        await self.send([f(2.0**24 + 2), one, MINUS_ULP])      # just below a tie: rounds down
        await self.send([f(2.0**100), f(1.0), ONE_ULP])
        # Subnormal totals are exact; the smallest normal; crossing between.
        await self.send([ONE_ULP] * 7)
        await self.send([0x00800000, MINUS_ULP])
        await self.send([0x007FFFFF, ONE_ULP])
        await self.send([0x00800000, 0x00800000, 0x80800001])
        # Special values, alone and mixed with numbers.
        for s in ([POS_INF], [NEG_INF], [POS_INF, one], [NEG_INF, one], [POS_INF, NEG_INF], [POS_INF, POS_INF],
                  [QNAN], [SNAN], [QNAN, one], [SNAN, one], [QNAN, POS_INF], [SNAN, POS_INF], [POS_INF, NEG_INF, QNAN]):
            await self.send(s)
        # A new sum must not inherit state: after these, sums of plain numbers.
        await self.send([half, half])
        # finish on the same clock as the last term, and with gaps.
        await self.send([one, half], with_last=True)
        await self.send([one, half, f(0.25)], [0, 3, 0])
        await self.send([one], with_last=True)
        await self.send([one, f(-3.0)], [2, 2], with_last=True)
        await self.send([half], [5])


class RandomSeq(BaseSeq):
    """Batches of several shapes, mostly with no gaps between terms."""
    count = 100
    seed = 1

    def term(self, rng: random.Random, kind: str, e0: int) -> int:
        sign = rng.getrandbits(1) << 31
        if kind == "raw":
            while True:
                t = rng.getrandbits(32)
                if (t >> 23) & 0xFF != 0xFF:
                    return t
        if kind == "near":                                     # exponents close together
            return sign | (max(1, min(254, e0 + rng.randint(-5, 5))) << 23) | rng.getrandbits(23)
        if kind == "tiny":
            return sign | rng.getrandbits(23)
        return sign | (rng.randint(1, 254) << 23) | rng.getrandbits(23)

    async def body(self):
        rng = random.Random(self.seed)
        for _ in range(self.count):
            kind = rng.choice(["raw", "near", "near", "tiny", "wide"])
            n = rng.choice([1, 2, 3, rng.randint(4, 30), rng.randint(31, 300)])
            e0 = rng.randint(1, 254)
            terms = [self.term(rng, kind, e0) for _ in range(n)]
            shape = rng.randrange(4)
            if shape == 0:      # cancel exactly, so the total is small or zero
                terms += [t ^ 0x80000000 for t in terms]
                rng.shuffle(terms)
            elif shape == 1 and rng.random() < 0.3:
                terms[rng.randrange(n)] = rng.choice([POS_INF, NEG_INF, QNAN, SNAN])
            gaps = [rng.choice([0, 0, 0, 0, 0, 1, 2, 7]) for _ in terms] if rng.random() < 0.3 else None
            await self.send(terms, gaps, with_last=rng.random() < 0.25)


class ClassShapedSeq(BaseSeq):
    """Terms shaped like a real integral: many same-sized values, then a run of
    tiny ones. These are what made plain fp32 summing go wrong."""

    async def body(self):
        rng = np.random.default_rng(7)
        large = rng.uniform(0.0, 1.0, 3000).astype(np.float32)
        tiny = (rng.uniform(0.0, 1.0, 3000) * 1e-9).astype(np.float32)
        await self.send(large.view(np.uint32).tolist())
        await self.send(np.concatenate([large, tiny]).view(np.uint32).tolist())
        await self.send(np.concatenate([tiny, -large, large]).view(np.uint32).tolist())


# ---------------------------------------------------------------------------
# Agent components
# ---------------------------------------------------------------------------

class SumDriver(uvm_driver):
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")

    async def run_phase(self):
        dut = self.dut
        dut.in_valid.value = 0
        dut.clear.value = 0
        dut.finish.value = 0
        await FallingEdge(dut.rst)
        while True:
            item = await self.seq_item_port.get_next_item()
            # Everything changes on the falling edge; the DUT samples on the rising one.
            await FallingEdge(dut.clk)
            dut.clear.value = 1
            await FallingEdge(dut.clk)
            dut.clear.value = 0
            for k, (t, gap) in enumerate(zip(item.terms, item.gaps, strict=True)):
                for _ in range(gap):
                    dut.in_valid.value = 0
                    await FallingEdge(dut.clk)
                dut.in_valid.value = 1
                dut.in_value.value = t
                if item.with_last and k == len(item.terms) - 1:
                    dut.finish.value = 1
                await FallingEdge(dut.clk)
            dut.in_valid.value = 0
            dut.finish.value = 1
            if item.with_last and item.terms:
                dut.finish.value = 0
            else:
                await FallingEdge(dut.clk)
                dut.finish.value = 0
            await RisingEdge(dut.out_valid)
            self.seq_item_port.item_done()


class CycleCounter:
    """Counts rising edges so monitors can stamp transactions with a clock number."""

    def __init__(self, dut):
        self.n = 0
        cocotb.start_soon(self._count(dut))

    async def _count(self, dut):
        while True:
            await RisingEdge(dut.clk)
            self.n += 1


class PinMonitor(uvm_monitor):
    """Rebuilds each sum from the pins: terms taken, when finish came, what came out."""

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.cc = ConfigDB().get(self, "", "CYCLES")
        self.term_ap = uvm_analysis_port("term_ap", self)
        self.done_ap = uvm_analysis_port("done_ap", self)

    async def run_phase(self):
        dut = self.dut
        terms: list[int] = []
        finish_at = None
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            if int(dut.rst.value):
                continue
            # Look at the result first: the driver can pulse clear for the next
            # sum in the same half clock that this result is showing.
            if int(dut.out_valid.value):
                fields = (dut.out_result.value, dut.out_flags.value)
                if not all(x.is_resolvable for x in fields):
                    self.logger.error(f"X/Z in result: {fields}")
                    continue
                r = ResultItem("observed", int(fields[0]), int(fields[1]), self.cc.n - finish_at)
                self.term_ap.write(list(terms))
                self.done_ap.write(r)
            if int(dut.clear.value):
                terms = []
                finish_at = None
            elif int(dut.in_valid.value) and finish_at is None:
                terms.append(int(dut.in_value.value))
            if int(dut.finish.value) and finish_at is None:
                finish_at = self.cc.n


class SumAgent(uvm_agent):
    def build_phase(self):
        self.sqr = uvm_sequencer("sqr", self)
        self.drv = SumDriver("drv", self)
        self.mon = PinMonitor("mon", self)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.sqr.seq_item_export)


# ---------------------------------------------------------------------------
# Scoreboard and coverage
# ---------------------------------------------------------------------------

def by_fractions(terms: list[int]) -> tuple[int, int]:
    """The same sum from Fractions, with the special-value rules written out."""
    exp = [(t >> 23) & 0xFF for t in terms]
    frac = [t & 0x7FFFFF for t in terms]
    nan = any(e == 255 and m for e, m in zip(exp, frac, strict=True))
    snan = any(fp32ref.is_snan(t) for t in terms)
    pos, neg = POS_INF in terms, NEG_INF in terms
    if nan or (pos and neg):
        return fp32ref.CANON_NAN, fp32ref.NV if (snan or (pos and neg)) else 0
    if pos or neg:
        return (POS_INF if pos else NEG_INF), 0
    total = sum((fp32ref.to_fraction(t) for t in terms), Fraction(0))
    return (0, 0) if total == 0 else fp32ref.round_fraction(int(total < 0), abs(total))


class SumScoreboard(uvm_component):
    def build_phase(self):
        self.term_fifo = uvm_tlm_analysis_fifo("term_fifo", self)
        self.done_fifo = uvm_tlm_analysis_fifo("done_fifo", self)
        self.checked = self.errors = self.terms = 0

    async def run_phase(self):
        while True:
            r = await self.done_fifo.get()
            terms = await self.term_fifo.get()
            self.check(terms, r)

    def check(self, terms: list[int], r: ResultItem):
        self.checked += 1
        self.terms += len(terms)
        got = (r.bits, r.flags)
        model = evu_model.exact_sum(np.array(terms, dtype=np.uint32))
        fraction = by_fractions(terms)
        if got != model or got != fraction or r.clocks != LAT_FINISH:
            self.errors += 1
            if self.errors <= 20:
                self.logger.error(f"{len(terms)} terms {[hex(t) for t in terms[:6]]}: got {r.bits:08x}/{r.flags:05b} "
                                  f"model {model[0]:08x}/{model[1]:05b} fractions {fraction[0]:08x}/{fraction[1]:05b} "
                                  f"after {r.clocks} clocks (expected {LAT_FINISH})")

    def report_phase(self):
        self.logger.info(f"checked {self.checked} sums, {self.terms} terms, {self.errors} errors")


def result_kind(terms: list[int]) -> set[str]:
    """Coverage bins this sum hits, from the terms alone."""
    bins = set()
    if not terms:
        return {"empty"}
    bits, flags = by_fractions(terms)
    if bits in (POS_INF, NEG_INF):
        bins.add("overflow" if flags else "infinity")
    elif bits == fp32ref.CANON_NAN:
        bins.add("nan_invalid" if flags else "nan_quiet")
    elif bits & 0x7FFFFFFF == 0:
        bins.add("zero")
    elif (bits >> 23) & 0xFF == 0:
        bins.add("subnormal")
    else:
        bins.add("negative" if bits >> 31 else "positive")
        bins.add("inexact" if flags else "exact")
    for t in terms:
        e = (t >> 23) & 0xFF
        if e != 255:
            bins.add(f"segment{max(e - 1, 0) >> 5}")
    return bins


class SumCoverage(uvm_subscriber):
    GOAL = {"empty", "zero", "subnormal", "positive", "negative", "exact", "inexact", "overflow", "infinity",
            "nan_invalid", "nan_quiet"} | {f"segment{s}" for s in range(8)}

    def build_phase(self):
        self.hits: dict[str, int] = {}

    def write(self, terms):
        for b in result_kind(terms):
            self.hits[b] = self.hits.get(b, 0) + 1

    def report_phase(self):
        self.logger.info(f"coverage {len(self.GOAL & set(self.hits))}/{len(self.GOAL)} bins, "
                         f"missed {sorted(self.GOAL - set(self.hits))}")


class SumEnv(uvm_env):
    def build_phase(self):
        self.agent = SumAgent("agent", self)
        self.sb = SumScoreboard("sb", self)
        self.cov = SumCoverage("cov", self)

    def connect_phase(self):
        self.agent.mon.term_ap.connect(self.sb.term_fifo.analysis_export)
        self.agent.mon.done_ap.connect(self.sb.done_fifo.analysis_export)
        self.agent.mon.term_ap.connect(self.cov.analysis_export)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class BaseTest(uvm_test):
    def build_phase(self):
        dut = cocotb.top
        ConfigDB().set(None, "*", "DUT", dut)
        ConfigDB().set(None, "*", "CYCLES", CycleCounter(dut))
        self.env = SumEnv("env", self)

    async def reset(self):
        dut = cocotb.top
        Clock(dut.clk, 10, unit="ns").start()
        dut.rst.value = 1
        dut.in_valid.value = 0
        dut.in_value.value = 0
        dut.clear.value = 0
        dut.finish.value = 0
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
        missed = cov.GOAL - set(cov.hits)
        assert not missed, f"coverage bins not hit: {sorted(missed)}"


@pyuvm.test()
class AccumDirectedTest(BaseTest):
    async def run_seqs(self):
        await DirectedSeq("directed").start(self.env.agent.sqr)


@pyuvm.test()
class AccumRegressTest(BaseTest):
    async def run_seqs(self):
        await DirectedSeq("directed").start(self.env.agent.sqr)
        await ClassShapedSeq("shaped").start(self.env.agent.sqr)
        rnd = RandomSeq("random")
        rnd.count = N_BATCHES
        rnd.seed = int(os.environ.get("LCG_SEED", "1"), 16)
        await rnd.start(self.env.agent.sqr)
