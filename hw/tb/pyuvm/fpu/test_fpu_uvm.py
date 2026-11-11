"""pyuvm environment for the fpu block: the Python mirror of hw/tb/uvm/fpu.

pyuvm implements the UVM 1.2 class library in Python on top of cocotb, so
this file has the same structure as the SystemVerilog environment:
  sequence -> sequencer -> driver -> DUT pins -> monitors -> scoreboard/coverage

Differences from the SV version, on purpose:
* The reference model is sw/mathaccel/fp32ref.py (exact rational arithmetic),
  not the C/DPI model. Two environments with two independent references.
  The phase 3 CORDIC ops (SIN .. LN) are predicted by the bit-accurate model
  sw/mathaccel/cordic_model.py (through fpu_evaluate in the device model);
  their accuracy against mpmath is checked in the cordic block environment.
* Coverage is counted in plain Python dictionaries (pyuvm has no covergroups).
* Timing: cocotb has no clocking blocks, so the driver changes inputs on the
  falling clock edge and everything is sampled after the values settle
  (ReadOnly). The RTL registers on the rising edge, so there are no races.

Select a test with COCOTB_TEST_FILTER / the runner's --test option. Size knob:
the FPU_N environment variable (default 2000).

The runner's "fpu16" environment builds the same DUT with a 16-entry CORDIC
result FIFO (PARAM_FD=16). FifoProbe watches the FIFO's counters inside the
DUT. The regression test requires the age rule to have stalled requests, and
with the small FIFO also the other two stall rules (no free slot for a CORDIC
request, FIFO full).

Divide and square root are separate units (fp_div, fp_sqrt). DsProbe watches
both and the regression requires that a divide and a square root were in
progress together, and that both units once held a finished result at the
same time, which is the collision the result arbitration has to settle.
"""

# pyuvm calls build_phase to create components and connect_phase to wire
# their software ports. Concurrent run_phase coroutines then drive or
# observe the simulated pins. check/report phases run after stimulus.
# I use observations from the monitors for predictions: an item queued
# by a sequence is not evidence that the DUT actually accepted it.

from __future__ import annotations

import os
import random
import struct

import cocotb
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

from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp
from mathaccel.driver.model import CORDIC_OPS, fpu_evaluate
from mathaccel.fpgen import FpLcg, OperandClass

N_ITEMS = int(os.environ.get("FPU_N", "2000"))
FD = int(os.environ.get("PARAM_FD", "64"))  # CORDIC result FIFO depth the DUT was built with
N_OPS = spec.FPU_OP_COUNT
LAT_PIPE = 7
# The fewest clocks from an accepted DIV or SQRT to its result. A divider
# experiment that shortens fp_div lowers LAT_DIV_MIN; the result must never
# come back sooner than the unit's own pipeline allows.
LAT_DIV_MIN = 24
LAT_SQRT_MIN = 30
LAT_CORD = 54          # 52 in fp_cordic, one in the FIFO, one output register
LAT_CORD_MAX = 128     # LAT_CORD + AGE_MAX (32) + 10 + DQ (32): every promised divide may go first
UNARY = {FpuOp.SQRT, FpuOp.I2F, FpuOp.F2I, FpuOp.SIN, FpuOp.COS, FpuOp.EXP, FpuOp.LN}


def is_unary(op: int) -> bool:
    """Identify operations whose second operand is ignored when building test coverage."""
    return op in UNARY

SPECIALS = [
    0x00000000, 0x80000000, 0x00000001, 0x80000001, 0x007FFFFF, 0x00800000,
    0x00800001, 0x3F800000, 0xBF800000, 0x3F800001, 0x3FFFFFFF, 0x40000000,
    0x7F7FFFFF, 0xFF7FFFFF, 0x7F800000, 0xFF800000, 0x7FC00000, 0xFFC00001,
    0x7F800001, 0xFFA00000, 0x4F000000, 0xCF000000, 0x4EFFFFFF, 0x3F000000,
    0x3EFFFFFF, 0x34000000, 0x00400000, 0x7F000000,
]


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class FpuReqItem(uvm_sequence_item):
    """Operands and operation to offer, plus the tag and time used to track accepted work."""
    def __init__(self, name="fpu_req", op=0, a=0, b=0, gap=0):
        super().__init__(name)
        self.op, self.a, self.b, self.gap = op, a, b, gap
        self.tag = 0
        self.cycle = 0

    def randomize_with(self, rng: random.Random, op: int | None = None) -> None:
        """Python's version of randomize(): weighted operand classes, like the SV
        constraints, with the operand bits built by the shared LCG builder."""
        classes = [c for c in OperandClass if c != OperandClass.RELATED]
        weights = [16, 24, 64, 48, 10, 8, 8, 16, 16, 22, 24]
        self.op = rng.randrange(N_OPS) if op is None else op
        cls_a = rng.choices(classes, weights)[0]
        cls_b = rng.choices(classes + [OperandClass.RELATED], weights + [32])[0]
        g = FpLcg(rng.getrandbits(32))
        self.a = g.operand(cls_a)
        self.b = g.operand(cls_b, self.a)
        self.gap = rng.choices([0, rng.randint(1, 3), rng.randint(4, 20)], [80, 15, 5])[0]

    def __str__(self):
        return f"{FpuOp(self.op).name} a={self.a:08x} b={self.b:08x} tag={self.tag:02x}"


class FpuRspItem(uvm_sequence_item):
    """Observed result bits, exception flags and tag from the shared FPU output port."""
    def __init__(self, name="fpu_rsp", result=0, flags=0, tag=0, cycle=0):
        super().__init__(name)
        self.result, self.flags, self.tag, self.cycle = result, flags, tag, cycle


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------

class FpuBaseSeq(uvm_sequence):
    """Provide a common send operation for directed, random and mixed-unit sequences."""
    count = 100

    async def send(self, op, a, b, gap=0):
        item = FpuReqItem("it", op, a, b, gap)
        await self.start_item(item)
        await self.finish_item(item)


class FpuRandSeq(FpuBaseSeq):
    """Mix operations, operand classes and gaps to exercise dispatch and output arbitration."""
    seed = 1
    force_op: int | None = None

    async def body(self):
        rng = random.Random(self.seed)
        for _ in range(self.count):
            item = FpuReqItem("it")
            await self.start_item(item)
            item.randomize_with(rng, self.force_op)
            await self.finish_item(item)


class FpuLcgSeq(FpuBaseSeq):
    """Same stream as fpu_lcg_seq in SV for the same seed."""
    seed = 1
    force_op: int | None = None

    async def body(self):
        g = FpLcg(self.seed)
        for _ in range(self.count):
            op, a, b = g.stimulus(self.force_op)
            await self.send(op, a, b)


class FpuCornerSeq(FpuBaseSeq):
    """Cross the fixed special-value set so both operand positions see the numerical edge cases."""
    async def body(self):
        for op in range(N_OPS):
            for a in SPECIALS:
                for b in SPECIALS:
                    await self.send(op, a, b)


class FpuMixBurstSeq(FpuBaseSeq):
    """Divide/sqrt followed by bursts of pipelined ops, all back to back."""

    async def body(self):
        g = FpLcg(0x5EED0001)
        rng = random.Random(3)
        for _ in range(self.count):
            await self.send(*g.stimulus(rng.choice([FpuOp.DIV, FpuOp.SQRT])))
            for _ in range(rng.randint(0, 12)):
                await self.send(*g.stimulus(rng.randrange(3)))


class FpuDivSqrtOverlapSeq(FpuBaseSeq):
    """A DIV, then a SQRT on the next clock, then a long run of 6-clock ops.

    The two iterative units are separate, so the SQRT is accepted while the
    DIV is still working. Both finish while 6-clock results own the result
    port, so both have to hold their answers, and the FPU has to send them
    one at a time without losing either. Some rounds swap the order or leave
    a gap, so the second unit finishes at different points of the first's
    wait."""

    async def body(self):
        g = FpLcg(0x5EED0005)
        rng = random.Random(9)
        for _ in range(self.count):
            first, second = rng.choice([(FpuOp.DIV, FpuOp.SQRT), (FpuOp.SQRT, FpuOp.DIV)])
            await self.send(*g.stimulus(first))
            for _ in range(rng.choice([0, 0, 1, 3, 6])):
                await self.send(*g.stimulus(FpuOp.ADD))
            await self.send(*g.stimulus(second))
            for _ in range(rng.randint(30, 45)):
                await self.send(*g.stimulus(rng.choice([FpuOp.ADD, FpuOp.MUL, FpuOp.MIN])))


def div_hard_cases(rng: random.Random) -> list[tuple[int, int]]:
    """Directed divide operands that every divider version has to get exactly right.

    I picked these for the ways the divider experiments could go wrong:
    * divisor significands on the boundaries the candidates look at: the eight
      intervals SRT selects on (1 + i/8) and the 512 reciprocal table intervals
      (1 + i/512), each exactly and one ulp either side;
    * dividends with the largest and smallest significands, so the quotient
      lands just below 2 and just above 1/2;
    * exact quotients (a = b * small integer), where the remainder is zero and
      inexact must stay clear;
    * quotients a few ulps from a rounding tie, from a = q * b with q's low bits
      set to ...0111 and ...1000;
    * subnormal inputs and results, the minimum subnormal divided by two (it
      rounds to zero or to itself depending on the tie), and results that are
      tiny only before or only after rounding;
    * overflow edges: the largest normal divided by numbers just below one.
    """
    def bits(x: float) -> int:
        return struct.unpack("<I", struct.pack("<f", x))[0]

    def make(sign: int, exp: int, frac: int) -> int:
        return (sign << 31) | ((exp & 0xFF) << 23) | (frac & 0x7FFFFF)

    cases: list[tuple[int, int]] = []
    tops = [0x7FFFFF, 0x7FFFFE, 0x000000, 0x000001, 0x400000, 0x3FFFFF, 0x555555, 0x2AAAAA]
    # Divisor boundaries: SRT's 8 intervals and the reciprocal table's 512.
    for step, count in ((1 << 20, 8), (1 << 14, 512)):
        for i in range(count):
            for off in (-1, 0, 1):
                frac = i * step + off
                if not 0 <= frac < (1 << 23):
                    continue
                b = make(rng.getrandbits(1), rng.randint(100, 150), frac)
                for top in rng.sample(tops, 3):
                    cases.append((make(rng.getrandbits(1), rng.randint(100, 150), top), b))
    # Extreme significands against each other.
    for ta in tops:
        for tb in tops:
            cases.append((make(0, 127, ta), make(0, 127, tb)))
            cases.append((make(1, 200, ta), make(0, 60, tb)))
    # Exact quotients.
    for _ in range(300):
        b = rng.randrange(1 << 23, 1 << 24)
        m = rng.choice([1, 3, 5, 7, 9, 15, 17, 255, 257])
        a = b * m
        while a >= 1 << 24:
            if a & 1:
                break
            a >>= 1
        if a < 1 << 24:
            cases.append((bits(float(a)), bits(float(b))))
    # Near ties: a = q * b rounded, with q's low bits close to a half ulp.
    for _ in range(600):
        qsig = (rng.randrange(1 << 23, 1 << 24) & ~0xF) | rng.choice([0x7, 0x8, 0x9, 0xF, 0x0, 0x1])
        b = rng.randrange(1 << 23, 1 << 24)
        a = float(qsig) * float(b) / float(1 << 23)
        cases.append((bits(a), bits(float(b))))
    # Subnormals and underflow.
    one, two, half = bits(1.0), bits(2.0), bits(0.5)
    for a in (0x00000001, 0x00000002, 0x00000003, 0x007FFFFF, 0x00400000, 0x00800000, 0x00800001, 0x80000001):
        for b in (one, two, bits(3.0), bits(1.5), bits(1.0000001), bits(0.99999994), half, bits(4.0)):
            cases.append((a, b))
    for _ in range(200):
        a = make(rng.getrandbits(1), rng.randint(0, 3), rng.getrandbits(23))
        b = make(0, rng.randint(124, 160), rng.getrandbits(23))
        cases.append((a, b))
        cases.append((b, make(0, rng.randint(250, 254), rng.getrandbits(23))))
    # Overflow edges.
    big = 0x7F7FFFFF
    for b in (one, bits(0.99999994), bits(0.5), bits(0.50000006), bits(2.0 ** -126), 0x00000001, 0x00400000):
        cases.append((big, b))
        cases.append((0xFF7FFFFF, b))
        cases.append((bits(1.0), b))
    return cases


def sqrt_hard_cases(rng: random.Random) -> list[int]:
    """Directed square-root operands that every square-root version has to get exactly right.

    I picked these for the ways the square-root experiments could go wrong:
    * significands on the boundaries a seed table looks at (1 + i/512, exactly and
      one ulp either side), with both odd and even exponents, because the
      exponent's parity decides whether the root works on [1, 2) or [2, 4);
    * exact roots: perfect squares k^2 scaled by powers of 4, where the remainder
      is zero and inexact must stay clear;
    * roots just either side of a rounding boundary: for an odd 26-bit root R I
      take the radicands closest to R^2, so the remainder is tiny or the root's
      guard bit is set with almost nothing below it;
    * the smallest and largest significands, every subnormal leading-zero count,
      the largest normal, and the special values (zeros, infinities, NaNs and
      negative numbers, which give NaN).
    """
    def bits(x: float) -> int:
        return struct.unpack("<I", struct.pack("<f", x))[0]

    def make(sign: int, exp: int, frac: int) -> int:
        return (sign << 31) | ((exp & 0xFF) << 23) | (frac & 0x7FFFFF)

    cases: list[int] = []
    # Seed-table boundaries, with an even and an odd exponent each.
    for i in range(512):
        for off in (-1, 0, 1):
            frac = (i << 14) + off
            if 0 <= frac < 1 << 23:
                even = rng.randrange(2, 252, 2)
                cases += [make(0, even, frac), make(0, even + 1, frac)]
    # Exact roots.
    for k in list(range(1, 300)) + [rng.randrange(300, 4096) for _ in range(300)]:
        for j in (-40, -2, 0, 1, 30):
            cases.append(bits(float(k * k) * 4.0 ** j))
    # Near rounding boundaries. The radicand's significand na makes N = na << (27 + p)
    # (p = 1 for an odd unbiased exponent); the root R = isqrt(N) has 26 bits and
    # rounding looks at R's lowest bit. I aim N at R^2 for an odd R.
    for _ in range(1500):
        p = rng.getrandbits(1)
        r = rng.randrange(1 << 25, 1 << 26) | 1
        na = (r * r) >> (27 + p)
        for d in (-1, 0, 1, 2):
            m = na + d
            if 1 << 23 <= m < 1 << 24:
                e = 127 + 2 * rng.randint(-50, 50) + p
                cases.append(make(0, e, m - (1 << 23)))
    # Extremes and subnormals: every leading-zero count, both parities of exponent.
    for e in (1, 2, 126, 127, 128, 253, 254):
        for frac in (0, 1, 0x400000, 0x7FFFFE, 0x7FFFFF, 0x2AAAAA, 0x555555):
            cases.append(make(0, e, frac))
    for k in range(23):
        cases += [1 << k, (1 << k) | 1, (2 << k) - 1]
    cases += [make(0, 0, rng.getrandbits(23)) for _ in range(200)]
    # Special values: +-0, +-inf, quiet and signalling NaNs, negative numbers.
    cases += [0x00000000, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7FA00000, 0xFFC00001,
              0x7F800001, bits(-1.0), bits(-2.0 ** -140), 0x80000001, bits(-3.4e38)]
    return cases


class FpuSqrtHardSeq(FpuBaseSeq):
    """The directed square-root cases above, sent back to back."""

    async def body(self):
        rng = random.Random(0x5C27)
        for a in sqrt_hard_cases(rng):
            await self.send(FpuOp.SQRT, a, 0)


class FpuDivHardSeq(FpuBaseSeq):
    """The directed divide cases above, sent back to back."""

    async def body(self):
        rng = random.Random(0xD17)
        for a, b in div_hard_cases(rng):
            await self.send(FpuOp.DIV, a, b)


class FpuCordicMixSeq(FpuBaseSeq):
    """CORDIC ops in back-to-back bursts, then runs of 6-clock ops and the odd
    divide, all with no gaps. The CORDIC results come out while the 6-clock
    results own the result port, so they pile up in the FIFO.

    For a 16-entry FIFO: a burst longer than 16 stalls on reserved slots, and
    a burst of at most 16 followed by a run of 6-clock ops only (every other
    round has no CORDIC ops in the run) fills the FIFO completely."""

    async def body(self):
        g = FpLcg(0x5EED0003)
        rng = random.Random(5)
        for _ in range(self.count):
            for _ in range(rng.choice([rng.randint(1, 16), rng.randint(17, 60)])):
                await self.send(*g.stimulus(rng.choice(CORDIC_OPS)))
            # A divide that finds the divider busy stalls the stream and frees
            # the result port, so the 6-clock-only rounds leave them out too.
            p_div, p_cordic = rng.choice([(0.0, 0.0), (0.05, 0.2)])
            for _ in range(rng.randint(0, 80)):
                r = rng.random()
                if r < p_div:
                    op = rng.choice([FpuOp.DIV, FpuOp.SQRT])
                elif r < p_div + p_cordic:
                    op = rng.choice(CORDIC_OPS)
                else:
                    op = rng.choice([FpuOp.ADD, FpuOp.SUB, FpuOp.MUL, FpuOp.MIN, FpuOp.F2I])
                await self.send(*g.stimulus(op))


# ---------------------------------------------------------------------------
# Agent components
# ---------------------------------------------------------------------------

class FpuDriver(uvm_driver):
    """Hold each request stable until ready/valid acceptance, then move to the next sequence item."""
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.next_tag = 0

    async def drive_idle(self):
        self.dut.req_valid.value = 0

    async def run_phase(self):
        dut = self.dut
        await self.drive_idle()
        await FallingEdge(dut.rst)
        item = None
        while True:
            # Back to back: at the falling edge after the last request was
            # taken, take the next item if one is already queued, otherwise
            # drop valid and wait. Asking half a clock later rather than right
            # after item_done gives the sequence time to queue its next item;
            # asking at once found the queue empty every time, which left an
            # idle clock between all requests. try_next_item hands back the
            # item before the sequence has filled it in, so wait for item_ready.
            await FallingEdge(dut.clk)
            ok, item = self.seq_item_port.try_next_item()
            if ok:
                await item.item_ready.wait()
            else:
                await self.drive_idle()
                item = await self.seq_item_port.get_next_item()
                await FallingEdge(dut.clk)
            for _ in range(item.gap):
                await self.drive_idle()
                await FallingEdge(dut.clk)
            item.tag = self.next_tag
            self.next_tag = (self.next_tag + 1) & 0xFF
            # Drive on the falling edge; the DUT registers on the next rising edge.
            dut.req_valid.value = 1
            dut.req_op.value = item.op
            dut.req_a.value = item.a
            dut.req_b.value = item.b
            dut.req_tag.value = item.tag
            # Wait until ready is high just before a rising edge: that edge is
            # where the DUT takes the request.
            while True:
                await ReadOnly()
                ready = int(dut.req_ready.value)
                await RisingEdge(dut.clk)
                if ready:
                    break
                await FallingEdge(dut.clk)
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


class FpuReqMonitor(uvm_monitor):
    """Observe accepted input handshakes independently of what the driver intended to send."""
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.cc = ConfigDB().get(self, "", "CYCLES")
        self.ap = uvm_analysis_port("ap", self)

    async def run_phase(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            # valid & ready mid-cycle means the next rising edge accepts it.
            if not int(dut.rst.value) and int(dut.req_valid.value) and int(dut.req_ready.value):
                t = FpuReqItem("observed", int(dut.req_op.value), int(dut.req_a.value), int(dut.req_b.value))
                t.tag = int(dut.req_tag.value)
                t.cycle = self.cc.n
                self.ap.write(t)


class FpuRspMonitor(uvm_monitor):
    """Observe each valid result and reject X/Z values before numerical checking."""
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.cc = ConfigDB().get(self, "", "CYCLES")
        self.ap = uvm_analysis_port("ap", self)

    async def run_phase(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            if not int(dut.rst.value) and int(dut.rsp_valid.value):
                # Same rule as the a_rsp_known assertion in the SV interface: a
                # valid response must not contain X or Z bits (only visible on
                # 4-state simulators such as Icarus; Verilator is 2-state).
                fields = (dut.rsp_result.value, dut.rsp_flags.value, dut.rsp_tag.value)
                if not all(f.is_resolvable for f in fields):
                    self.logger.error(f"X/Z in response: result={fields[0]} flags={fields[1]} tag={fields[2]}")
                    continue
                self.ap.write(FpuRspItem("observed", int(fields[0]), int(fields[1]), int(fields[2]), self.cc.n))


class FpuAgent(uvm_agent):
    """Bundle the request sequencer/driver and passive input/output monitors."""
    def build_phase(self):
        self.sqr = uvm_sequencer("sqr", self)
        self.drv = FpuDriver("drv", self)
        self.req_mon = FpuReqMonitor("req_mon", self)
        self.rsp_mon = FpuRspMonitor("rsp_mon", self)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.sqr.seq_item_export)


# ---------------------------------------------------------------------------
# Scoreboard and coverage
# ---------------------------------------------------------------------------

class FpuScoreboard(uvm_component):
    """Predicts with fpu_evaluate (fp32ref, or the CORDIC model for ops 12-17) and
    matches responses to requests by tag.

    Two analysis FIFOs buffer the monitor streams; run_phase drains them.
    """

    def build_phase(self):
        self.req_fifo = uvm_tlm_analysis_fifo("req_fifo", self)
        self.rsp_fifo = uvm_tlm_analysis_fifo("rsp_fifo", self)
        self.pending: dict[int, tuple[FpuReqItem, int, int]] = {}
        self.checked = 0
        self.errors = 0
        self.cord_lat = [0, 0]      # CORDIC results that waited / came straight out
        self.cord_max = 0           # longest CORDIC latency seen
        self.per_op = {FpuOp(op).name: 0 for op in range(N_OPS)}

    async def run_phase(self):
        while True:
            # Requests always reach the scoreboard before their responses
            # (they happen at least 7 clocks earlier), so drain requests first.
            while True:
                ok, req = self.req_fifo.try_get()
                if not ok:
                    break
                if req.tag in self.pending:
                    self.logger.error(f"tag {req.tag:02x} reused while outstanding")
                self.pending[req.tag] = (req, *fpu_evaluate(req.op, req.a, req.b))
            rsp = await self.rsp_fifo.get()
            ok, req2 = self.req_fifo.try_get()
            while ok:
                self.pending[req2.tag] = (req2, *fpu_evaluate(req2.op, req2.a, req2.b))
                ok, req2 = self.req_fifo.try_get()
            self.check(rsp)

    def check(self, rsp: FpuRspItem):
        if rsp.tag not in self.pending:
            self.errors += 1
            self.logger.error(f"response with unknown tag {rsp.tag:02x}")
            return
        req, exp_r, exp_f = self.pending.pop(rsp.tag)
        self.checked += 1
        self.per_op[FpuOp(req.op).name] += 1
        if (rsp.result, rsp.flags) != (exp_r, exp_f):
            self.errors += 1
            if self.errors <= 20:
                self.logger.error(f"{req}\n  expected {exp_r:08x}/{exp_f:05b}\n  got      "
                                  f"{rsp.result:08x}/{rsp.flags:05b}")
        lat = rsp.cycle - req.cycle
        if req.op in (FpuOp.DIV, FpuOp.SQRT):
            least = LAT_DIV_MIN if req.op == FpuOp.DIV else LAT_SQRT_MIN
            if lat < least:
                self.errors += 1
                self.logger.error(f"{req}: latency {lat} < {least}")
        elif req.op in CORDIC_OPS:
            # Exactly LAT_CORD when the result port is free, later when the
            # result has to wait in the FIFO.
            if not LAT_CORD <= lat <= LAT_CORD_MAX:
                self.errors += 1
                self.logger.error(f"{req}: latency {lat} outside {LAT_CORD}..{LAT_CORD_MAX}")
            self.cord_lat[lat == LAT_CORD] += 1
            self.cord_max = max(self.cord_max, lat)
        elif lat != LAT_PIPE:
            self.errors += 1
            self.logger.error(f"{req}: latency {lat} != {LAT_PIPE}")

    def check_phase(self):
        if self.pending:
            self.errors += 1
            self.logger.error(f"{len(self.pending)} requests never got a response")

    def report_phase(self):
        self.logger.info(f"checked {self.checked} results, {self.errors} errors; per op {self.per_op}")
        self.logger.info(f"CORDIC results: {self.cord_lat[1]} at {LAT_CORD} clocks, "
                         f"{self.cord_lat[0]} waited in the FIFO, longest latency {self.cord_max}")


class FifoProbe(uvm_component):
    """Watches the CORDIC FIFO's counters inside the DUT (fpu.c_cnt, f_cnt) and
    counts the clocks where each stall rule held a request back. This is white
    box: it reads internal signals, which cocotb can see because the
    simulators are built with their internals visible."""

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.max_c = self.max_f = 0
        self.noroom = 0         # a CORDIC request waiting because every slot is reserved
        self.full = 0           # all requests waiting because the FIFO is full
        self.old = 0            # all requests waiting because the oldest result is too old

    async def run_phase(self):
        dut = self.dut
        cordic = {int(op) for op in CORDIC_OPS}
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            if int(dut.rst.value):
                continue
            c, f = int(dut.c_cnt.value), int(dut.f_cnt.value)
            self.max_c, self.max_f = max(self.max_c, c), max(self.max_f, f)
            waiting = int(dut.req_valid.value) and not int(dut.req_ready.value)
            if waiting and f == FD:
                self.full += 1
            elif waiting and int(dut.f_old.value):
                self.old += 1
            elif waiting and c == FD and int(dut.req_op.value) in cordic:
                self.noroom += 1
            if c > FD or f > c:
                self.logger.error(f"FIFO counters out of range: c_cnt={c} f_cnt={f} (depth {FD})")

    def report_phase(self):
        self.logger.info(f"CORDIC FIFO (depth {FD}): at most {self.max_c} reserved, {self.max_f} stored; "
                         f"{self.noroom} clocks waiting for a slot, {self.full} clocks stalled by a full FIFO, "
                         f"{self.old} clocks stalled by the age rule")


def unit_busy(dut, unit: str, ready: str) -> bool:
    """True while a divide or square-root unit has work in progress.

    A pipelined unit with a result queue is nearly always ready, so for one of
    those (it has a promise counter, cnt) "busy" means it holds a queue place: an
    operation in its pipeline or a result waiting in its queue. A one-at-a-time
    unit is busy exactly while it refuses new requests."""
    u = getattr(dut, unit)
    return int(u.cnt.value) != 0 if hasattr(u, "cnt") else not int(getattr(dut, ready).value)


class DsProbe(uvm_component):
    """Watches the divide and square-root units inside the DUT (white box).

    It counts clocks where both units are busy at once, which shows the split
    really lets a divide and a square root overlap, and clocks where both hold
    a finished result together. It also checks the hold rule: while fpu.sv's
    ds_block is high (in the baseline, while either unit holds a result), the
    FPU must refuse every request."""

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.overlap = 0        # clocks with both units busy
        self.both_hold = 0      # clocks with both units holding a result
        self.errors = 0

    async def run_phase(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            if int(dut.rst.value):
                continue
            div_busy = unit_busy(dut, "u_div", "div_in_ready")
            sqrt_busy = unit_busy(dut, "u_sqrt", "sqrt_in_ready")
            div_hold, sqrt_hold = int(dut.div_hold.value), int(dut.sqrt_hold.value)
            self.overlap += div_busy and sqrt_busy
            self.both_hold += div_hold and sqrt_hold
            if int(dut.ds_block.value) and int(dut.req_ready.value):
                self.errors += 1
                self.logger.error("the FPU is ready while ds_block says a held result must go first")

    def report_phase(self):
        self.logger.info(f"divide/sqrt units: {self.overlap} clocks both busy, "
                         f"{self.both_hold} clocks both holding a result")


class QueueProbe(uvm_component):
    """Watches the result queue of every pipelined divide or square-root unit
    (white box: the unit's cnt and q_cnt, and fpu.sv's div_urgent or sqrt_urgent).

    A unit without a queue (no cnt) is skipped, so the same probe works for every
    version. For each queue it checks that the promised places never exceed DQ
    and that stored results never exceed promised ones, and counts the clocks
    where out_urgent stopped requests, so the regression can require that the
    urgent rule was really exercised."""

    UNITS = (("u_div", "div_urgent"), ("u_sqrt", "sqrt_urgent"))
    DQ = 32

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.units = [(u, g) for u, g in self.UNITS if hasattr(getattr(self.dut, u), "cnt")]
        self.max_cnt = {u: 0 for u, _ in self.units}
        self.max_q = {u: 0 for u, _ in self.units}
        self.urgent = {u: 0 for u, _ in self.units}
        self.errors = 0

    async def run_phase(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            if int(dut.rst.value):
                continue
            for u, g in self.units:
                unit = getattr(dut, u)
                c, q = int(unit.cnt.value), int(unit.q_cnt.value)
                self.max_cnt[u], self.max_q[u] = max(self.max_cnt[u], c), max(self.max_q[u], q)
                self.urgent[u] += int(getattr(dut, g).value)
                if c > self.DQ or q > c:
                    self.errors += 1
                    self.logger.error(f"{u} queue counters out of range: cnt={c} q_cnt={q}")

    def report_phase(self):
        for u, _ in self.units:
            self.logger.info(f"{u} queue: at most {self.max_cnt[u]} promised, {self.max_q[u]} stored; "
                             f"{self.urgent[u]} clocks with out_urgent")


def kind_of(x: int) -> str:
    """Classify raw operand bits for coverage without changing the bits sent to the DUT."""
    e, f = (x >> 23) & 0xFF, x & 0x7FFFFF
    if e == 0xFF:
        return "inf" if f == 0 else ("qnan" if f >> 22 else "snan")
    if e == 0:
        return "zero" if f == 0 else "subnormal"
    return "normal"


class FpuCoverage(uvm_subscriber):
    """Counts op x operand-kind pairs seen on the request side."""

    KINDS = ("zero", "subnormal", "normal", "inf", "qnan", "snan")

    def build_phase(self):
        self.hits: dict[tuple[str, str, str], int] = {}

    def write(self, t: FpuReqItem):
        key = (FpuOp(t.op).name, kind_of(t.a), "-" if is_unary(t.op) else kind_of(t.b))
        self.hits[key] = self.hits.get(key, 0) + 1

    def report_phase(self):
        total = sum(len(self.KINDS) if is_unary(op) else len(self.KINDS) ** 2 for op in range(N_OPS))
        self.logger.info(f"operand-kind coverage: {len(self.hits)}/{total} bins "
                         f"({100 * len(self.hits) / total:.1f}%)")


class FpuEnv(uvm_env):
    """Wire observed requests and results into the scoreboard and coverage collector."""
    def build_phase(self):
        self.agent = FpuAgent("agent", self)
        self.sb = FpuScoreboard("sb", self)
        self.cov = FpuCoverage("cov", self)
        self.probe = FifoProbe("probe", self)
        self.ds_probe = DsProbe("ds_probe", self)
        self.q_probe = QueueProbe("q_probe", self)

    def connect_phase(self):
        self.agent.req_mon.ap.connect(self.sb.req_fifo.analysis_export)
        self.agent.rsp_mon.ap.connect(self.sb.rsp_fifo.analysis_export)
        self.agent.req_mon.ap.connect(self.cov.analysis_export)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class FpuBaseTest(uvm_test):
    """Own reset, sequence execution and enough drain time for queued CORDIC outputs."""
    def build_phase(self):
        dut = cocotb.top
        ConfigDB().set(None, "*", "DUT", dut)
        ConfigDB().set(None, "*", "CYCLES", CycleCounter(dut))
        self.env = FpuEnv("env", self)

    async def reset(self):
        dut = cocotb.top
        Clock(dut.clk, 10, unit="ns").start()
        dut.rst.value = 1
        dut.req_valid.value = 0
        for _ in range(5):
            await RisingEdge(dut.clk)
        dut.rst.value = 0

    async def run_seqs(self):
        raise NotImplementedError

    async def run_phase(self):
        self.raise_objection()
        await self.reset()
        await self.run_seqs()
        for _ in range(200):                     # drain: CORDIC takes 54 clocks plus FIFO waits
            await RisingEdge(cocotb.top.clk)
        self.drop_objection()

    def final_phase(self):
        sb = self.env.sb
        assert sb.errors == 0 and sb.checked > 0, f"{sb.errors} errors, {sb.checked} checked"
        assert self.env.ds_probe.errors == 0, f"{self.env.ds_probe.errors} hold-rule errors"
        assert self.env.q_probe.errors == 0, f"{self.env.q_probe.errors} result queue errors"


@pyuvm.test()
class FpuCornerTest(FpuBaseTest):
    """Run the directed operand matrix against every implemented operation."""
    async def run_seqs(self):
        await FpuCornerSeq("corner").start(self.env.agent.sqr)


@pyuvm.test()
class FpuLcgTest(FpuBaseTest):
    """Replay the portable integer generator with a selected operation and seed."""
    async def run_seqs(self):
        s = FpuLcgSeq("lcg")
        s.count = N_ITEMS
        s.seed = int(os.environ.get("LCG_SEED", "1"), 16)
        op = os.environ.get("FPU_OP")
        s.force_op = None if op is None else int(op)
        await s.start(self.env.agent.sqr)


@pyuvm.test()
class FpuDivTest(FpuBaseTest):
    """Divide only: the directed hard cases, then FPU_N random LCG divides."""

    async def run_seqs(self):
        await FpuDivHardSeq("hard").start(self.env.agent.sqr)
        s = FpuLcgSeq("lcg")
        s.count = N_ITEMS
        s.seed = int(os.environ.get("LCG_SEED", "D1D1"), 16)
        s.force_op = int(FpuOp.DIV)
        await s.start(self.env.agent.sqr)


@pyuvm.test()
class FpuSqrtTest(FpuBaseTest):
    """Square root only: the directed hard cases, then FPU_N random LCG square roots."""

    async def run_seqs(self):
        await FpuSqrtHardSeq("hard").start(self.env.agent.sqr)
        s = FpuLcgSeq("lcg")
        s.count = N_ITEMS
        s.seed = int(os.environ.get("LCG_SEED", "5C5C"), 16)
        s.force_op = int(FpuOp.SQRT)
        await s.start(self.env.agent.sqr)


@pyuvm.test()
class FpuRegressTest(FpuBaseTest):
    """Corner matrix, then random and mixed-burst sequences running in parallel
    on one sequencer (the sequencer interleaves their items)."""

    async def run_seqs(self):
        await FpuCornerSeq("corner").start(self.env.agent.sqr)
        rnd = FpuRandSeq("rnd")
        rnd.count = N_ITEMS
        mix = FpuMixBurstSeq("mix")
        mix.count = max(1, N_ITEMS // 16)
        tasks = [cocotb.start_soon(s.start(self.env.agent.sqr)) for s in (rnd, mix)]
        for task in tasks:
            await task
        # On its own, so the sequencer doesn't break up its bursts.
        cmix = FpuCordicMixSeq("cmix")
        cmix.count = max(1, N_ITEMS // 40)
        await cmix.start(self.env.agent.sqr)
        # Also on its own: the timing between the DIV and the SQRT matters.
        overlap = FpuDivSqrtOverlapSeq("overlap")
        overlap.count = max(4, N_ITEMS // 100)
        await overlap.start(self.env.agent.sqr)

    def final_phase(self):
        super().final_phase()
        pr = self.env.probe
        assert pr.old > 0, "the FIFO age rule never stalled a request"
        ds = self.env.ds_probe
        assert ds.overlap > 0, "a divide and a square root were never in progress together"
        assert ds.both_hold > 0, "the two units never held a result at the same time"
        for unit, clocks in self.env.q_probe.urgent.items():
            assert clocks > 0, f"the {unit} queue's urgent rule never stopped requests"
        if FD < 64:
            # The small FIFO exists to reach the stall rules; make sure it did.
            assert pr.noroom > 0 and pr.full > 0, f"stall rules not reached: {pr.noroom} / {pr.full}"
