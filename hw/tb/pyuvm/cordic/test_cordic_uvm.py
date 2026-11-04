"""pyuvm environment for the fp_cordic block on its own (phase 3, milestones 2-4).

Same structure as the FPU block environment in ../fpu:
  sequence -> sequencer -> driver -> DUT pins -> monitors -> scoreboard/coverage

The scoreboard distinguishes reproducing the datapath from approximating
the mathematical function. It checks every result twice:

1. Bit for bit against sw/mathaccel/cordic_model.py, the bit-accurate model
   of this datapath. Any difference is an RTL bug (or the model and RTL
   disagree about the algorithm), so the budget here is zero.
2. Against mpmath (sw/mathaccel/mpref.py) through mathaccel.ulp.UlpStats,
   which measures the error in ULPs, keeps a histogram and the worst input,
   and checks the flags against the phase 3 policy. The budget is 1 ULP.

It also checks the latency, which must be exactly LAT clocks for every
result, since the unit is fully pipelined.

Coverage counts which path each input took: for SIN/COS the special and
tiny paths, the small-angle path and the CORDIC x or y output per quadrant
and fold; for ATAN2 the special path, the four base-angle cases and the y/x
path; for HYPOT the special path, ordinary gaps and gaps so wide that the
smaller input vanishes; for EXP the rule paths (tiny, too big), k < 0, k = 0,
k > 0 and subnormal results; for LN the rule path, the near-1 path on both
sides of 1, and the main path for x < 1 and x > 1 with and without the
sqrt(2) fold, plus subnormal inputs.

Knob: FPU_N (default 3000) sets the number of random inputs.
"""

# pyuvm calls build_phase to create components and connect_phase to wire
# their software ports. Concurrent run_phase coroutines then drive or
# observe the simulated pins. check/report phases run after stimulus.
# I use observations from the monitors for predictions: an item queued
# by a sequence is not evidence that the DUT actually accepted it.

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

from mathaccel import cordic_model, ulp
from mathaccel._generated.spec import FpuOp

N_ITEMS = int(os.environ.get("FPU_N", "3000"))
P = cordic_model.DEFAULT
LAT = P.N + 10                     # must match LAT in fp_cordic.sv
OPS = (FpuOp.SIN, FpuOp.COS, FpuOp.ATAN2, FpuOp.HYPOT, FpuOp.EXP, FpuOp.LN)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class CordicReqItem(uvm_sequence_item):
    """One offered operation: raw operand bits, an optional idle gap, and an observed tag/time."""
    def __init__(self, name="cordic_req", op=FpuOp.SIN, a=0, b=0, gap=0):
        super().__init__(name)
        self.op, self.a, self.b, self.gap = op, a, b, gap
        self.tag = 0
        self.cycle = 0

    def __str__(self):
        return f"{FpuOp(self.op).name} a={self.a:08x} b={self.b:08x} tag={self.tag:02x}"


class CordicRspItem(uvm_sequence_item):
    """One observed output, kept with its tag and clock number for matching and latency checks."""
    def __init__(self, name="cordic_rsp", result=0, flags=0, tag=0, cycle=0):
        super().__init__(name)
        self.result, self.flags, self.tag, self.cycle = result, flags, tag, cycle


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------

class CordicBaseSeq(uvm_sequence):
    """Send operation descriptions to the driver without deciding when the DUT samples its pins."""
    async def send(self, op, a, b=0, gap=0):
        """Queue one request; the driver assigns its tag when it actually drives the interface."""
        item = CordicReqItem("it", op, a, b, gap)
        await self.start_item(item)
        await self.finish_item(item)


class CordicDirectedSeq(CordicBaseSeq):
    """The directed part of ulp.op_inputs for every op, back to back: for
    sin/cos the specials, thresholds and argument-reduction worst cases; for
    atan2/hypot every pair of 12 special values (zeros, infinities, NaNs,
    subnormals, the largest float) plus a few hand-picked pairs."""

    async def body(self):
        for op in OPS:
            for a, b in ulp.op_inputs(op, 0, seed=1):
                await self.send(op, a, b)


class CordicRandSeq(CordicBaseSeq):
    """Random inputs from ulp.op_inputs (for sin/cos weighted toward ordinary
    angles, huge arguments, angles near k*pi/2 and the small-angle threshold;
    for atan2/hypot toward the y/x threshold, wide exponent gaps, |y| ~ |x|,
    subnormals and overflow), ops interleaved, mostly back-to-back timing."""
    count = 1000
    seed = 1

    async def body(self):
        rng = random.Random(self.seed)
        # One stream of inputs per op, then pick the op at random for each
        # request, so different modes follow each other through the stages.
        streams = {op: iter(ulp.op_inputs(op, self.count // len(OPS), self.seed + op)) for op in OPS}
        while streams:
            op = rng.choice(list(streams))
            pair = next(streams[op], None)
            if pair is None:
                del streams[op]
                continue
            gap = rng.choices([0, rng.randint(1, 3), rng.randint(4, 60)], [85, 12, 3])[0]
            await self.send(op, pair[0], pair[1], gap)


# ---------------------------------------------------------------------------
# Agent components
# ---------------------------------------------------------------------------

class CordicDriver(uvm_driver):
    """Drives one request per clock when items are queued. The unit has no
    ready signal: it accepts a request on every clock edge where in_valid=1."""

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.next_tag = 0

    async def run_phase(self):
        dut = self.dut
        dut.in_valid.value = 0
        await FallingEdge(dut.rst)
        while True:
            # Take the next item immediately if one is already waiting, so a
            # sequence that sends back to back really fills every clock.
            ok, item = self.seq_item_port.try_next_item()
            if ok:
                await item.item_ready.wait()
            else:
                await FallingEdge(dut.clk)
                dut.in_valid.value = 0
                item = await self.seq_item_port.get_next_item()
            for _ in range(item.gap):
                await FallingEdge(dut.clk)
                dut.in_valid.value = 0
            item.tag = self.next_tag
            self.next_tag = (self.next_tag + 1) & 0xFF
            # Change inputs on the falling edge; the DUT samples on the rising one.
            await FallingEdge(dut.clk)
            dut.in_valid.value = 1
            dut.in_op.value = int(item.op)
            dut.in_a.value = item.a
            dut.in_b.value = item.b
            dut.in_tag.value = item.tag
            await RisingEdge(dut.clk)
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


class CordicReqMonitor(uvm_monitor):
    """Publish requests seen on the pins so the scoreboard checks accepted work, not sequence intent."""
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.cc = ConfigDB().get(self, "", "CYCLES")
        self.ap = uvm_analysis_port("ap", self)

    async def run_phase(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            # Valid in the middle of a clock means the next rising edge takes it.
            if not int(dut.rst.value) and int(dut.in_valid.value):
                t = CordicReqItem("observed", int(dut.in_op.value), int(dut.in_a.value), int(dut.in_b.value))
                t.tag = int(dut.in_tag.value)
                t.cycle = self.cc.n
                self.ap.write(t)


class CordicRspMonitor(uvm_monitor):
    """Publish valid outputs after checking that their result, flags and tag contain no unknown bits."""
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.cc = ConfigDB().get(self, "", "CYCLES")
        self.ap = uvm_analysis_port("ap", self)

    async def run_phase(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            await ReadOnly()
            if not int(dut.rst.value) and int(dut.out_valid.value):
                # A valid result must not contain X or Z bits (visible on
                # 4-state Icarus only; Verilator has no X).
                fields = (dut.out_result.value, dut.out_flags.value, dut.out_tag.value)
                if not all(f.is_resolvable for f in fields):
                    self.logger.error(f"X/Z in result: {fields}")
                    continue
                self.ap.write(CordicRspItem("observed", int(fields[0]), int(fields[1]), int(fields[2]),
                                            self.cc.n))


class CordicAgent(uvm_agent):
    """Keep the sequencer, pin driver and two passive monitors together as one block interface."""
    def build_phase(self):
        self.sqr = uvm_sequencer("sqr", self)
        self.drv = CordicDriver("drv", self)
        self.req_mon = CordicReqMonitor("req_mon", self)
        self.rsp_mon = CordicRspMonitor("rsp_mon", self)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.sqr.seq_item_export)


# ---------------------------------------------------------------------------
# Scoreboard and coverage
# ---------------------------------------------------------------------------

class CordicScoreboard(uvm_component):
    """Exact check against the bit-accurate model, ULP and flag check against
    mpmath, and a fixed-latency check. Results come back in order (one
    pipeline), but I still match them by tag, like the FPU scoreboard."""

    def build_phase(self):
        self.req_fifo = uvm_tlm_analysis_fifo("req_fifo", self)
        self.rsp_fifo = uvm_tlm_analysis_fifo("rsp_fifo", self)
        self.pending: dict[int, CordicReqItem] = {}
        self.ulp = ulp.UlpStats(budget=1.0)
        self.checked = 0
        self.model_errors = 0
        self.latency_errors = 0
        self.tag_errors = 0

    def _drain_requests(self):
        """File all observed requests before matching a response, including arrivals in the same timestep."""
        while True:
            ok, req = self.req_fifo.try_get()
            if not ok:
                return
            if req.tag in self.pending:
                self.tag_errors += 1
                self.logger.error(f"tag {req.tag:02x} reused while outstanding")
            self.pending[req.tag] = req

    async def run_phase(self):
        while True:
            self._drain_requests()
            rsp = await self.rsp_fifo.get()
            self._drain_requests()
            self.check(rsp)

    def check(self, rsp: CordicRspItem):
        """Match the tag, then check datapath bits, mathematical error, flags and fixed latency separately."""
        req = self.pending.pop(rsp.tag, None)
        if req is None:
            self.tag_errors += 1
            self.logger.error(f"result with unknown tag {rsp.tag:02x}")
            return
        self.checked += 1
        # 1. Bit-accurate model: must match exactly.
        want = cordic_model.evaluate(req.op, req.a, req.b, P)
        if (rsp.result, rsp.flags) != want:
            self.model_errors += 1
            if self.model_errors <= 20:
                self.logger.error(f"{req}: model {want[0]:08x}/{want[1]:05b}, "
                                  f"RTL {rsp.result:08x}/{rsp.flags:05b}")
        # 2. Accuracy and flags against mpmath.
        problem = self.ulp.add(req.op, req.a, req.b, rsp.result, rsp.flags)
        if problem:
            self.logger.error(f"{req}: {problem}")
        # 3. Fixed latency.
        lat = rsp.cycle - req.cycle
        if lat != LAT:
            self.latency_errors += 1
            self.logger.error(f"{req}: latency {lat} != {LAT}")

    def check_phase(self):
        """Catch requests still waiting after the test has allowed the pipeline to drain."""
        if self.pending:
            self.tag_errors += 1
            self.logger.error(f"{len(self.pending)} requests never got a result")

    def errors(self) -> int:
        """Combine arithmetic, protocol, timing and accuracy failures for the test verdict."""
        return self.model_errors + self.latency_errors + self.tag_errors + self.ulp.errors()

    def report_phase(self):
        self.logger.info(f"checked {self.checked} results: {self.model_errors} model mismatches, "
                         f"{self.latency_errors} latency errors, {self.ulp.errors()} ULP/flag errors\n"
                         + self.ulp.report())


def path_of(op: int, a: int, b: int = 0) -> tuple[str, str]:
    """Which datapath an input takes, and its quadrant/fold (or case), for coverage."""
    if op == FpuOp.EXP:
        e = (a >> 23) & 0xFF
        if e == 0xFF or a & 0x7FFFFFFF == 0:
            return "special", "-"
        if e < P.EXP_TINY_E:
            return "exp_rule", "tiny"
        if e >= P.EXP_BIG_E:
            return "exp_rule", "big"
        r, _ = cordic_model.exp_(a, P)
        if r & 0x7FFFFFFF < 0x00800000:
            return "exp_main", "subnormal"
        x = cordic_model.mpref_value(a)
        k = round(x / 0.6931471805599453)
        return "exp_main", "k<0" if k < 0 else ("k=0" if k == 0 else "k>0")
    if op == FpuOp.LN:
        if a >> 31 or a & 0x7FFFFFFF in (0, 0x7F800000) or (a & 0x7F800000) == 0x7F800000 or a == 0x3F800000:
            return "special", "-"
        e = (a >> 23) & 0xFF
        if e == 127 and (a & 0x7FFFFF) < (1 << (23 - P.LN_T)):
            return "ln_near", "above"
        if e == 126 and (1 << 24) - ((a & 0x7FFFFF) | 0x800000) < (1 << (24 - P.LN_T)):
            return "ln_near", "below"
        if e == 0:
            return "ln_main", "subnormal"
        mn, en = cordic_model.unpack_norm(a)
        side = "below" if en < 127 else "above"
        return "ln_main", f"{side}/{'fold' if mn >= cordic_model.SQRT2_M else 'nofold'}"
    if op in (FpuOp.ATAN2, FpuOp.HYPOT):
        if cordic_model.vec_special(op, a, b) is not None:
            return "special", "-"
        mu, eu = cordic_model.unpack_norm(a)
        mw, ew = cordic_model.unpack_norm(b)
        swap = (eu, mu) > (ew, mw)
        d = abs(eu - ew)
        if op == FpuOp.HYPOT:
            return "hypot", "vanishes" if d > P.W + 2 else "-"
        if not swap and not b >> 31 and d >= P.ATAN_D:
            return "atan_lin", "-"
        return "atan_vec", f"{'swap' if swap else 'noswap'}/{'xneg' if b >> 31 else 'xpos'}"
    e = (a >> 23) & 0xFF
    if e == 0xFF or a & 0x7FFFFFFF == 0:
        return "special", "-"
    if e < P.TINY_E:
        return "tiny", "-"
    q, f = cordic_model.reduce(a, P)
    swap = f >> (P.F - 1)
    fp = ((1 << P.F) - f) if swap else f
    want_cos = (q & 1) if op == FpuOp.SIN else 1 - (q & 1)
    if want_cos ^ swap:
        path = "cordic_x"
    elif fp >> (P.F - P.SMALL_T) == 0:
        path = "small"
    else:
        path = "cordic_y"
    return path, f"q{q}{'f' if swap else ''}"


class CordicCoverage(uvm_subscriber):
    """Counts op x path x (quadrant, fold) bins on the request side."""

    def build_phase(self):
        self.hits: dict[tuple[str, str, str], int] = {}

    def write(self, t: CordicReqItem):
        path, quad = path_of(t.op, t.a, t.b)
        key = (FpuOp(t.op).name, path, quad)
        self.hits[key] = self.hits.get(key, 0) + 1

    def report_phase(self):
        # Goal bins. For a given op, quadrant and fold, the result is either
        # always cos t (the CORDIC x output) or always sin t, which then comes
        # from the CORDIC y output or the small-angle path depending on t.
        # Plus the special and tiny paths once per op.
        goal = set()
        for op in (FpuOp.SIN, FpuOp.COS):
            for q in range(4):
                for swap in (0, 1):
                    want_cos = (q & 1) if op == FpuOp.SIN else 1 - (q & 1)
                    quad = f"q{q}{'f' if swap else ''}"
                    paths = ("cordic_x",) if want_cos ^ swap else ("cordic_y", "small")
                    goal |= {(FpuOp(op).name, p, quad) for p in paths}
            goal |= {(FpuOp(op).name, p, "-") for p in ("special", "tiny")}
        goal |= {("ATAN2", "special", "-"), ("ATAN2", "atan_lin", "-")}
        goal |= {("ATAN2", "atan_vec", f"{s}/{x}") for s in ("swap", "noswap") for x in ("xpos", "xneg")}
        goal |= {("HYPOT", "special", "-"), ("HYPOT", "hypot", "-"), ("HYPOT", "hypot", "vanishes")}
        goal |= {("EXP", "special", "-"), ("EXP", "exp_rule", "tiny"), ("EXP", "exp_rule", "big")}
        goal |= {("EXP", "exp_main", c) for c in ("k<0", "k=0", "k>0", "subnormal")}
        goal |= {("LN", "special", "-"), ("LN", "ln_near", "above"), ("LN", "ln_near", "below"),
                 ("LN", "ln_main", "subnormal")}
        goal |= {("LN", "ln_main", f"{s}/{f}") for s in ("below", "above") for f in ("fold", "nofold")}
        hit = goal & set(self.hits)
        self.logger.info(f"path coverage: {len(hit)}/{len(goal)} bins ({100 * len(hit) / len(goal):.1f}%)")
        missing = sorted(goal - hit)
        if missing:
            self.logger.info(f"  missing: {missing[:12]}")


class CordicEnv(uvm_env):
    """Connect observed traffic to independent result checks and request-path coverage."""
    def build_phase(self):
        self.agent = CordicAgent("agent", self)
        self.sb = CordicScoreboard("sb", self)
        self.cov = CordicCoverage("cov", self)

    def connect_phase(self):
        self.agent.req_mon.ap.connect(self.sb.req_fifo.analysis_export)
        self.agent.rsp_mon.ap.connect(self.sb.rsp_fifo.analysis_export)
        self.agent.req_mon.ap.connect(self.cov.analysis_export)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class CordicBaseTest(uvm_test):
    """Start the clock, reset validity, run stimulus and drain every pipeline stage before checking."""
    def build_phase(self):
        dut = cocotb.top
        ConfigDB().set(None, "*", "DUT", dut)
        ConfigDB().set(None, "*", "CYCLES", CycleCounter(dut))
        self.env = CordicEnv("env", self)

    async def reset(self):
        """Hold reset for five clock edges while offering no requests."""
        dut = cocotb.top
        Clock(dut.clk, 10, unit="ns").start()
        dut.rst.value = 1
        dut.in_valid.value = 0
        for _ in range(5):
            await RisingEdge(dut.clk)
        dut.rst.value = 0

    async def run_seqs(self):
        """Require each concrete test to choose its own directed or random stimulus."""
        raise NotImplementedError

    async def run_phase(self):
        self.raise_objection()
        await self.reset()
        await self.run_seqs()
        for _ in range(LAT + 10):              # drain the pipeline
            await RisingEdge(cocotb.top.clk)
        self.drop_objection()

    def final_phase(self):
        """Fail on any checker error or a run that never checked a result."""
        sb = self.env.sb
        assert sb.errors() == 0 and sb.checked > 0, f"{sb.errors()} errors, {sb.checked} checked"


@pyuvm.test()
class CordicDirectedTest(CordicBaseTest):
    """Run the reproducible special values and path boundaries without extra random stimulus."""
    async def run_seqs(self):
        await CordicDirectedSeq("directed").start(self.env.agent.sqr)


@pyuvm.test()
class CordicRegressTest(CordicBaseTest):
    """Directed inputs, then N_ITEMS random ones."""

    async def run_seqs(self):
        await CordicDirectedSeq("directed").start(self.env.agent.sqr)
        rnd = CordicRandSeq("rnd")
        rnd.count = N_ITEMS
        rnd.seed = int(os.environ.get("LCG_SEED", "1"), 16)
        await rnd.start(self.env.agent.sqr)
