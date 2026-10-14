"""pyuvm environment for the fpu block: the Python mirror of hw/tb/uvm/fpu.

pyuvm implements the UVM 1.2 class library in Python on top of cocotb, so
this file has the same structure as the SystemVerilog environment:
  sequence -> sequencer -> driver -> DUT pins -> monitors -> scoreboard/coverage

Differences from the SV version, on purpose:
* The reference model is sw/mathaccel/fp32ref.py (exact rational arithmetic),
  not the C/DPI model. Two environments with two independent references.
* Coverage is counted in plain Python dictionaries (pyuvm has no covergroups).
* Timing: cocotb has no clocking blocks, so the driver changes inputs on the
  falling clock edge and everything is sampled after the values settle
  (ReadOnly). The RTL registers on the rising edge, so there are no races.

Select a test with COCOTB_TEST_FILTER / the runner's --test option. Size knob:
the FPU_N environment variable (default 2000).
"""

from __future__ import annotations

import os
import random

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

from mathaccel import fp32ref
from mathaccel._generated.spec import FpuOp
from mathaccel.fpgen import FpLcg, OperandClass

N_ITEMS = int(os.environ.get("FPU_N", "2000"))
LAT_PIPE = 7
LAT_DS_MIN = 30

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
        self.op = rng.randrange(12) if op is None else op
        cls_a = rng.choices(classes, weights)[0]
        cls_b = rng.choices(classes + [OperandClass.RELATED], weights + [32])[0]
        g = FpLcg(rng.getrandbits(32))
        self.a = g.operand(cls_a)
        self.b = g.operand(cls_b, self.a)
        self.gap = rng.choices([0, rng.randint(1, 3), rng.randint(4, 20)], [80, 15, 5])[0]

    def __str__(self):
        return f"{FpuOp(self.op).name} a={self.a:08x} b={self.b:08x} tag={self.tag:02x}"


class FpuRspItem(uvm_sequence_item):
    def __init__(self, name="fpu_rsp", result=0, flags=0, tag=0, cycle=0):
        super().__init__(name)
        self.result, self.flags, self.tag, self.cycle = result, flags, tag, cycle


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------

class FpuBaseSeq(uvm_sequence):
    count = 100

    async def send(self, op, a, b, gap=0):
        item = FpuReqItem("it", op, a, b, gap)
        await self.start_item(item)
        await self.finish_item(item)


class FpuRandSeq(FpuBaseSeq):
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
    async def body(self):
        for op in range(12):
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


# ---------------------------------------------------------------------------
# Agent components
# ---------------------------------------------------------------------------

class FpuDriver(uvm_driver):
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
            # Back to back: take the next item now if one is already queued,
            # otherwise drop valid and wait. try_next_item hands back the item
            # before the sequence has filled it in, so wait for item_ready.
            ok, item = self.seq_item_port.try_next_item()
            if ok:
                await item.item_ready.wait()
            else:
                await FallingEdge(dut.clk)
                await self.drive_idle()
                item = await self.seq_item_port.get_next_item()
            for _ in range(item.gap):
                await FallingEdge(dut.clk)
                await self.drive_idle()
            item.tag = self.next_tag
            self.next_tag = (self.next_tag + 1) & 0xFF
            # Drive on the falling edge; the DUT registers on the next rising edge.
            await FallingEdge(dut.clk)
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
    """Predicts with fp32ref and matches responses to requests by tag.

    Two analysis FIFOs buffer the monitor streams; run_phase drains them.
    """

    def build_phase(self):
        self.req_fifo = uvm_tlm_analysis_fifo("req_fifo", self)
        self.rsp_fifo = uvm_tlm_analysis_fifo("rsp_fifo", self)
        self.pending: dict[int, tuple[FpuReqItem, int, int]] = {}
        self.checked = 0
        self.errors = 0
        self.per_op = {op.name: 0 for op in FpuOp}

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
                self.pending[req.tag] = (req, *fp32ref.evaluate(req.op, req.a, req.b))
            rsp = await self.rsp_fifo.get()
            ok, req2 = self.req_fifo.try_get()
            while ok:
                self.pending[req2.tag] = (req2, *fp32ref.evaluate(req2.op, req2.a, req2.b))
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
            if lat < LAT_DS_MIN:
                self.errors += 1
                self.logger.error(f"{req}: latency {lat} < {LAT_DS_MIN}")
        elif lat != LAT_PIPE:
            self.errors += 1
            self.logger.error(f"{req}: latency {lat} != {LAT_PIPE}")

    def check_phase(self):
        if self.pending:
            self.errors += 1
            self.logger.error(f"{len(self.pending)} requests never got a response")

    def report_phase(self):
        self.logger.info(f"checked {self.checked} results, {self.errors} errors; per op {self.per_op}")


def kind_of(x: int) -> str:
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
        key = (FpuOp(t.op).name, kind_of(t.a), "-" if fp32ref.is_unary(t.op) else kind_of(t.b))
        self.hits[key] = self.hits.get(key, 0) + 1

    def report_phase(self):
        total = sum(len(self.KINDS) if fp32ref.is_unary(op) else len(self.KINDS) ** 2 for op in FpuOp)
        self.logger.info(f"operand-kind coverage: {len(self.hits)}/{total} bins "
                         f"({100 * len(self.hits) / total:.1f}%)")


class FpuEnv(uvm_env):
    def build_phase(self):
        self.agent = FpuAgent("agent", self)
        self.sb = FpuScoreboard("sb", self)
        self.cov = FpuCoverage("cov", self)

    def connect_phase(self):
        self.agent.req_mon.ap.connect(self.sb.req_fifo.analysis_export)
        self.agent.rsp_mon.ap.connect(self.sb.rsp_fifo.analysis_export)
        self.agent.req_mon.ap.connect(self.cov.analysis_export)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class FpuBaseTest(uvm_test):
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
        for _ in range(100):                     # drain: divide takes ~30 clocks
            await RisingEdge(cocotb.top.clk)
        self.drop_objection()

    def final_phase(self):
        sb = self.env.sb
        assert sb.errors == 0 and sb.checked > 0, f"{sb.errors} errors, {sb.checked} checked"


@pyuvm.test()
class FpuCornerTest(FpuBaseTest):
    async def run_seqs(self):
        await FpuCornerSeq("corner").start(self.env.agent.sqr)


@pyuvm.test()
class FpuLcgTest(FpuBaseTest):
    async def run_seqs(self):
        s = FpuLcgSeq("lcg")
        s.count = N_ITEMS
        s.seed = int(os.environ.get("LCG_SEED", "1"), 16)
        op = os.environ.get("FPU_OP")
        s.force_op = None if op is None else int(op)
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
        t1 = cocotb.start_soon(rnd.start(self.env.agent.sqr))
        t2 = cocotb.start_soon(mix.start(self.env.agent.sqr))
        await t1
        await t2
