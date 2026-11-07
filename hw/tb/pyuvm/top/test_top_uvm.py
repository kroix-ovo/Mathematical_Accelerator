"""pyuvm environment for the whole accelerator: the Python mirror of hw/tb/uvm/top.

Layers, same as the SV environment:
  command sequences -> CmdSequencer (no driver)
      -> CmdToUartSeq (layering sequence on the UART sequencer) -> UartDriver -> uart_rx pin
  uart_rx / uart_tx pins -> UartFrameMonitor (one per wire) -> TopScoreboard

The scoreboard predicts replies with sw/mathaccel/driver/model.py
(DeviceModel.handle_wire), the same model the Python driver's simulator uses.
That is a separate implementation from the SV reference model, so the two
environments check each other's interpretation of docs/protocol.md.

Replies are compared byte for byte, with one exception. A successful EVU run's
clock count depends on how the queues and the FPU happened to line up, which the
model doesn't track, so for those replies I compare everything else exactly and
check cycles against the model's low and high bounds. Every result still comes
back through READ_MEM and gets compared exactly.

pyuvm has no sequencer lock()/grab(). The UART sequencer here carries an
asyncio-style Lock instead; the layering sequence and raw byte sequences hold
it for a whole frame, which gives the same "no interleaved frames" guarantee.
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
from cocotb.triggers import ClockCycles, FallingEdge, First, Lock
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
    uvm_test,
    uvm_tlm_analysis_fifo,
)

from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp, Kernel, Op
from mathaccel.compiler.evu_gen import f as fp32
from mathaccel.compiler.evu_gen import prog, random_run, table
from mathaccel.compiler.evu_isa import OP_LOAD, Image, Instr, Run, const, memory_image, reg
from mathaccel.compiler.problems import PROBLEMS
from mathaccel.compiler.quadrature import integral_run
from mathaccel.driver import protocol as P
from mathaccel.driver.framing import cobs_encode
from mathaccel.driver.model import DeviceModel
from mathaccel.driver.protocol import Frame
from mathaccel.fpgen import FpLcg

CPB = 8  # clocks per UART bit, matches the CLKS_PER_BIT parameter in run.py
N_RUNS = int(os.environ.get("FPU_N", "12"))  # random EVU runs
SEED = int(os.environ.get("LCG_SEED", "1"), 16)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class UartByteItem(uvm_sequence_item):
    """One UART byte with an optional delay before the next character."""
    def __init__(self, name="byte", data=0):
        super().__init__(name)
        self.data = data


class AccelCmdItem(uvm_sequence_item):
    """One protocol command. corrupt: None, "crc" or "short"."""

    def __init__(self, name="cmd", op=0, tag=0, payload=b"", corrupt=None):
        super().__init__(name)
        self.op, self.tag, self.payload, self.corrupt = op, tag, payload, corrupt


class WireFrameItem(uvm_sequence_item):
    """Bytes between two 0x00 delimiters on one wire (still COBS encoded)."""

    def __init__(self, name="frame", data=b""):
        super().__init__(name)
        self.data = data


# ---------------------------------------------------------------------------
# UART agent
# ---------------------------------------------------------------------------

class UartSequencer(uvm_sequencer):
    """Queue byte items for the serial pin driver."""
    def __init__(self, name, parent):
        super().__init__(name, parent)
        self.frame_lock = Lock()       # stands in for SV lock()/unlock()


class UartDriver(uvm_driver):
    """Turn byte items into timed start, data and stop bits on the DUT receive pin."""
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")

    async def run_phase(self):
        dut = self.dut
        dut.uart_rx_i.value = 1
        await FallingEdge(dut.rst)
        await ClockCycles(dut.clk, 2 * CPB)
        while True:
            item = await self.seq_item_port.get_next_item()
            # 8N1: start bit, eight data bits LSB first, stop bit.
            for bit in [0, *[(item.data >> i) & 1 for i in range(8)], 1]:
                dut.uart_rx_i.value = bit
                await ClockCycles(dut.clk, CPB)
            self.seq_item_port.item_done()


class UartFrameMonitor(uvm_monitor):
    """Decodes one UART wire into bytes and publishes a frame at each 0x00."""

    def __init__(self, name, parent, watch_tx: bool):
        super().__init__(name, parent)
        self.watch_tx = watch_tx

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.ap = uvm_analysis_port("ap", self)

    async def run_phase(self):
        dut = self.dut
        line = dut.uart_tx_o if self.watch_tx else dut.uart_rx_i
        cur = bytearray()
        await FallingEdge(dut.rst)
        while True:
            await FallingEdge(line)                 # start bit begins
            await ClockCycles(dut.clk, CPB // 2)    # middle of the start bit
            b = 0
            for i in range(8):
                await ClockCycles(dut.clk, CPB)
                b |= int(line.value) << i
            await ClockCycles(dut.clk, CPB)
            if int(line.value) != 1:
                self.logger.error(f"bad stop bit on {'tx' if self.watch_tx else 'rx'}")
            if b == 0:
                self.ap.write(WireFrameItem("frame", bytes(cur)))
                cur.clear()
            else:
                cur.append(b)


class UartAgent(uvm_agent):
    """Group the serial driver, sequencer and passive byte monitors."""
    def build_phase(self):
        self.sqr = UartSequencer("sqr", self)
        self.drv = UartDriver("drv", self)
        self.rx_mon = UartFrameMonitor("rx_mon", self, watch_tx=False)
        self.tx_mon = UartFrameMonitor("tx_mon", self, watch_tx=True)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.sqr.seq_item_export)


# ---------------------------------------------------------------------------
# Command layer
# ---------------------------------------------------------------------------

class CmdSequencer(uvm_sequencer):
    """Upper-layer sequencer with no driver; replies come back through rsp_fifo."""

    def build_phase(self):
        super().build_phase()
        self.rsp_fifo = uvm_tlm_analysis_fifo("rsp_fifo", self)
        self.dut = ConfigDB().get(self, "", "DUT")


class CmdToUartSeq(uvm_sequence):
    """Layering sequence: pull commands from the command sequencer and send
    their wire bytes through the UART sequencer, one whole frame at a time."""

    def __init__(self, name, up: CmdSequencer):
        super().__init__(name)
        self.up = up

    async def body(self):
        while True:
            cmd = await self.up.seq_item_export.get_next_item()
            raw = bytearray(Frame(cmd.op, cmd.tag, cmd.payload).to_raw())
            if cmd.corrupt == "crc":
                raw[-1] ^= 0x5A
            elif cmd.corrupt == "short":
                raw = raw[:3]
            await send_bytes(self, cobs_encode(bytes(raw)) + b"\x00")
            self.up.seq_item_export.item_done()


async def send_bytes(seq: uvm_sequence, data: bytes) -> None:
    """Send bytes as one atomic burst on the UART sequencer."""
    async with seq.sequencer.frame_lock:
        for b in data:
            item = UartByteItem("b", b)
            await seq.start_item(item)
            await seq.finish_item(item)


class RawBytesSeq(uvm_sequence):
    """Send an already encoded byte string, including malformed frames used by error tests."""
    def __init__(self, name, data: bytes):
        super().__init__(name)
        self.data = data

    async def body(self):
        await send_bytes(self, self.data)


# ---------------------------------------------------------------------------
# Command sequences (run on CmdSequencer, behave like a host)
# ---------------------------------------------------------------------------

class CmdBaseSeq(uvm_sequence):
    """Express commands at the protocol level while lower sequences handle serial framing."""
    tag = 0
    timeout_clks = 400_000

    async def send(self, op, payload=b"", corrupt=None) -> bytes | None:
        """Send one command; return the decoded reply payload, or None when the
        command was deliberately corrupted (the DUT must then stay silent)."""
        cmd = AccelCmdItem("cmd", op, CmdBaseSeq.tag, payload, corrupt)
        CmdBaseSeq.tag = (CmdBaseSeq.tag + 1) & 0xFF
        await self.start_item(cmd)
        await self.finish_item(cmd)
        sqr = self.sequencer
        if corrupt:
            await ClockCycles(sqr.dut.clk, 60 * 10 * CPB)
            ok, _ = sqr.rsp_fifo.try_get()
            if ok:
                sqr.logger.error("reply to a corrupted frame")
            return None
        get = cocotb.start_soon(sqr.rsp_fifo.get())
        await First(get, ClockCycles(sqr.dut.clk, self.timeout_clks))
        if not get.done():
            get.cancel()
            raise AssertionError(f"no reply to op {op:#04x}")
        return Frame.decode(get.result().data).payload

    async def write_words(self, addr, words):
        return await self.send(*P.write_mem(addr, list(words)))

    async def read_words(self, addr, n):
        return await self.send(*P.read_mem(addr, n))


class CmdSmokeSeq(CmdBaseSeq):
    """Check board identity, simple kernels and status before the longer command scenarios."""
    async def body(self):
        await self.send(*P.ping())
        await self.send(*P.status())
        await self.send(*P.run(Kernel.NOP))


class CmdMemSeq(CmdBaseSeq):
    """Write and read memory through commands, including the legal-range boundaries."""
    async def body(self):
        import random
        rng = random.Random(11)
        for _ in range(4):
            addr = rng.randrange(0, spec.SCRATCH_WORDS - 256)
            n = rng.randint(1, 255)
            await self.write_words(addr, [rng.getrandbits(32) for _ in range(n)])
            await self.read_words(addr, n)


class CmdFpuSeq(CmdBaseSeq):
    """Run every FPU operation through scratchpad arrays and check its returned data and cycles."""
    async def body(self):
        n, A, B, R, F = 10, 8000, 8300, 8600, 8900
        g = FpLcg(0x70F0)
        for fop in range(spec.FPU_OP_COUNT):
            ops = [g.stimulus(fop) for _ in range(n)]
            await self.write_words(A, [a for _, a, _ in ops])
            await self.write_words(B, [b for _, _, b in ops])
            await self.send(*P.run(Kernel.FPU, P.fpu_args(fop, n, A, B, R, F)))
            await self.read_words(R, n)
            await self.read_words(F, n)


# I send the image through the actual UART command path, then read outputs
# back as a host would. The scoreboard checks writes through those replies.
class CmdEvuSeq(CmdBaseSeq):
    """The expression unit as a host uses it: write an image in pieces, RUN, read
    the results back. The class problems, then what only shows up at this level:
    sharing the FPU with the other kernel, using a descriptor twice, every way a
    request can be refused, and random programs."""
    timeout_clks = 2_000_000
    count = N_RUNS

    async def write_image(self, image: Image):
        for k in range(0, len(image.words), 255):
            await self.write_words(image.base + k, [int(w) for w in image.words[k:k + 255]])

    async def run_desc(self, desc: int):
        return await self.send(*P.run(Kernel.EVU, P.evu_args(desc)))

    async def read_outputs(self, run: Run, image: Image):
        n = 1 if run.reduce else min(run.points, 255)
        await self.read_words(image.r, n)
        await self.read_words(image.f, n)

    async def one_run(self, run: Run, base: int = 0):
        image = memory_image(run, base)
        await self.write_image(image)
        await self.run_desc(image.desc)
        await self.read_outputs(run, image)
        return image

    async def problems(self):
        for p in PROBLEMS:
            await self.one_run(integral_run(p.expr, p.axes(p.rules[0])))

    async def share_the_fpu(self):
        """Each kernel straight after the other, each using the slow units."""
        n, a, b, r, f = 6, 0x8000, 0x8100, 0x8200, 0x8300
        await self.write_words(a, [fp32(0.25 * (k + 1)) for k in range(n)])
        await self.write_words(b, [fp32(0.5 * (k + 1)) for k in range(n)])
        load, sin, div = OP_LOAD, int(FpuOp.SIN), int(FpuOp.DIV)
        lines = (Instr(load, 0, 0), Instr(sin, 1, reg(0)), Instr(div, 2, reg(1), const(0)))
        run = Run(prog(*lines, constants=[fp32(3.0)]),
                  [table([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])], (8,), reduce=False)
        image = await self.one_run(run)
        for fop in (FpuOp.SIN, FpuOp.DIV, FpuOp.ADD, FpuOp.LN):
            await self.send(*P.run(Kernel.FPU, P.fpu_args(fop, n, a, b, r, f)))
            await self.read_words(r, n)
            await self.read_words(f, n)
            await self.run_desc(image.desc)                        # the same descriptor again
            await self.read_outputs(run, image)

    async def refused(self):
        """Each kind of refusal. Nothing may change: the outputs read back the same,
        STATUS keeps the last good clock count, and the unit runs again after."""
        run = Run(prog(Instr(OP_LOAD, 0, 0), Instr(FpuOp.ADD, 1, reg(0), const(0)), constants=[fp32(1.0)]),
                  [table([1.0, 2.0, 3.0, 4.0])], (4,), reduce=False)
        image = await self.one_run(run)
        top = spec.SCRATCH_WORDS
        program = image.desc + spec.EVU_DESC_WORDS
        await self.send(*P.run(Kernel.EVU))                                     # BAD_LEN
        await self.send(*P.run(Kernel.EVU, b"\x00" * 5))                        # BAD_LEN
        await self.run_desc(top - spec.EVU_DESC_WORDS + 1)                         # BAD_ADDR, in cmd_proc
        await self.run_desc(0xFFFFFFFF)                                            # BAD_ADDR, must not wrap
        await self.send(*P.status())
        edits = [(spec.EVU_DESC_MODE, 6), (spec.EVU_DESC_PROG_LEN, 0), (spec.EVU_DESC_PROG_LEN, 257),
                 (spec.EVU_DESC_CONST_LEN, 65), (spec.EVU_DESC_GRID, 0), (spec.EVU_DESC_GRID + 2, 1 << 16),
                 (spec.EVU_DESC_TABLE + 3, 3 << spec.EVU_DESC_DIM_SHIFT), (spec.EVU_DESC_PROG, top),
                 (spec.EVU_DESC_R, top - 3), (spec.EVU_DESC_F, 1 << 31), (spec.EVU_DESC_TABLE + 2, top - 3)]
        for offset, value in edits:
            good = int(image.words[offset])
            await self.write_words(image.desc + offset, [value])
            await self.run_desc(image.desc)
            await self.write_words(image.desc + offset, [good])
        for word in (Instr(18, 1, reg(0)).encode(), Instr(OP_LOAD, 1, 6).encode(),
                     Instr(FpuOp.ADD, 1, reg(0), const(1)).encode()):
            await self.write_words(program + 1, [word])
            await self.run_desc(image.desc)
        await self.write_words(program + 1, [int(image.words[spec.EVU_DESC_WORDS + 1])])
        await self.send(*P.status())
        await self.read_outputs(run, image)
        await self.run_desc(image.desc)
        await self.read_outputs(run, image)
        await self.send(*P.status())

    async def random_programs(self):
        rng = random.Random(SEED)
        for _ in range(self.count):
            run = random_run(rng, max_points=48, max_length=64)
            await self.one_run(run, rng.choice([0, 0x100, 0x8000]))

    async def body(self):
        await self.problems()
        await self.share_the_fpu()
        await self.refused()
        await self.random_programs()


class CmdErrorSeq(CmdBaseSeq):
    """Send broken frames and invalid commands, then check that normal traffic still works."""
    async def body(self):
        await self.send(0x33)                                                    # BAD_OP
        await self.send(Op.PING, b"\x00")                                        # BAD_LEN
        await self.send(*P.read_mem(spec.SCRATCH_WORDS - 1, 2))                  # BAD_ADDR
        await self.send(*P.run(0x7E))                                            # BAD_KERNEL
        await self.send(*P.run(Kernel.FPU, P.fpu_args(spec.FPU_OP_COUNT, 1, 0, 0, 0, 0)))  # BAD_ARG
        await self.send(*P.run(Kernel.FILL, struct.pack("<III", spec.SCRATCH_WORDS - 1, 2, 0)))
        await self.send(*P.ping(), corrupt="crc")
        await self.send(*P.ping(), corrupt="short")
        await self.send(*P.status())


# ---------------------------------------------------------------------------
# Scoreboard and environment
# ---------------------------------------------------------------------------

class TopScoreboard(uvm_component):
    """Feeds every frame the DUT received into DeviceModel and compares the
    model's reply bytes with what the DUT actually sent."""

    def build_phase(self):
        self.req_fifo = uvm_tlm_analysis_fifo("req_fifo", self)
        self.rsp_fifo = uvm_tlm_analysis_fifo("rsp_fifo", self)
        self.model = DeviceModel()
        self.expected: list[tuple[bytes, tuple[int, int] | None]] = []
        self.n_req = self.n_rsp = self.n_silent = self.errors = 0
        self.n_evu = 0
        self.slowest = 0.0      # the largest cycles/low bound seen
        self.closest = 1.0      # the largest cycles/high bound seen

    async def run_phase(self):
        while True:
            rsp = await self.rsp_fifo.get()
            self.drain_requests()
            self.n_rsp += 1
            got = rsp.data + b"\x00"
            if not self.expected:
                self.errors += 1
                self.logger.error(f"unexpected reply {got.hex()}")
                continue
            exp, bounds = self.expected.pop(0)
            if bounds is not None:
                self.check_evu(rsp.data, exp, bounds)
            elif got != exp:
                self.errors += 1
                self.logger.error(f"reply mismatch\n  expected {exp.hex()}\n  got      {got.hex()}")

    def check_evu(self, data: bytes, exp: bytes, bounds: tuple[int, int]):
        """A successful EVU reply: exact, except that cycles only has to be in bounds."""
        got, want = Frame.decode(data), Frame.decode(exp[:-1])
        cycles, aux = P.parse_run(got.data) if got.status == 0 and len(got.data) == 8 else (0, -1)
        _, want_aux = P.parse_run(want.data)
        low, high = bounds
        self.n_evu += 1
        self.slowest = max(self.slowest, cycles / low)
        self.closest = max(self.closest if self.n_evu > 1 else 0.0, cycles / high)
        if (got.op, got.tag, got.status, aux) != (want.op, want.tag, 0, want_aux) or not low <= cycles <= high:
            self.errors += 1
            self.logger.error(f"EVU reply: op {got.op:#x} tag {got.tag} status {got.status} aux {aux:#x} "
                              f"cycles {cycles}, expected aux {want_aux:#x} and cycles {low}..{high}")
        # STATUS reports the real count, so the model has to take it from here.
        self.model.last_cycles = cycles

    def drain_requests(self):
        while True:
            ok, req = self.req_fifo.try_get()
            if not ok:
                return
            self.n_req += 1
            self.model.evu_bounds = None
            reply = self.model.handle_wire(req.data)
            if reply is None:
                self.n_silent += 1
            else:
                self.expected.append((reply, self.model.evu_bounds))

    def check_phase(self):
        self.drain_requests()
        if self.expected:
            self.errors += 1
            self.logger.error(f"{len(self.expected)} predicted replies never arrived")

    def report_phase(self):
        self.logger.info(f"{self.n_req} requests ({self.n_silent} silent by design), "
                         f"{self.n_rsp} replies, {self.errors} errors")
        if self.n_evu:
            self.logger.info(f"{self.n_evu} EVU runs: cycles reached {self.slowest:.2f} x the low bound "
                             f"and {self.closest:.3f} x the high bound")


class TopEnv(uvm_env):
    """Connect UART observations to the command reference model and coverage components."""
    def build_phase(self):
        self.uart = UartAgent("uart", self)
        self.cmd_sqr = CmdSequencer("cmd_sqr", self)
        self.sb = TopScoreboard("sb", self)

    def connect_phase(self):
        self.uart.rx_mon.ap.connect(self.sb.req_fifo.analysis_export)
        self.uart.tx_mon.ap.connect(self.sb.rsp_fifo.analysis_export)
        self.uart.tx_mon.ap.connect(self.cmd_sqr.rsp_fifo.analysis_export)

    async def run_phase(self):
        await CmdToUartSeq("layer", self.cmd_sqr).start(self.uart.sqr)


class TopBaseTest(uvm_test):
    """Reset the core, run a command scenario and wait for all expected UART replies."""
    def build_phase(self):
        ConfigDB().set(None, "*", "DUT", cocotb.top)
        self.env = TopEnv("env", self)

    async def run_phase(self):
        dut = cocotb.top
        self.raise_objection()
        Clock(dut.clk, 10, unit="ns").start()
        # The Ethernet side is idle here (hw/tb/pyuvm/eth tests it): the
        # 50 MHz clock runs edge-aligned with clk and there is no carrier.
        Clock(dut.clk_rmii, 20, unit="ns").start()
        dut.rmii_crs_dv.value = 0
        dut.rmii_rxd.value = 0
        dut.rmii_rx_er.value = 0
        dut.rst.value = 1
        dut.uart_rx_i.value = 1
        await ClockCycles(dut.clk, 10)
        dut.rst.value = 0
        await self.run_seqs()
        await ClockCycles(dut.clk, 2000)
        self.drop_objection()

    async def run_seqs(self):
        raise NotImplementedError

    def final_phase(self):
        sb = self.env.sb
        assert sb.errors == 0 and sb.n_rsp > 0, f"{sb.errors} scoreboard errors"


@pyuvm.test()
class TopSmokeTest(TopBaseTest):
    """Run only the short command smoke sequence."""
    async def run_seqs(self):
        await CmdSmokeSeq("smoke").start(self.env.cmd_sqr)


@pyuvm.test()
class TopEvuTest(TopBaseTest):
    """Only the expression unit, for working on it."""
    async def run_seqs(self):
        await CmdEvuSeq("evu").start(self.env.cmd_sqr)

    def final_phase(self):
        super().final_phase()
        assert self.env.sb.n_evu > 0, "no EVU run was checked"


@pyuvm.test()
class TopRegressTest(TopBaseTest):
    """The virtual-sequence role is played by this method: it starts command
    sequences on the command sequencer and raw sequences on the UART sequencer."""

    async def run_seqs(self):
        cmd, uart = self.env.cmd_sqr, self.env.uart.sqr
        await CmdSmokeSeq("smoke").start(cmd)
        await CmdMemSeq("mem").start(cmd)
        await CmdFpuSeq("fpu").start(cmd)
        await CmdEvuSeq("evu").start(cmd)
        await CmdErrorSeq("err").start(cmd)
        await RawBytesSeq("garbage", b"\x05\x01\x02\x00").start(uart)
        await RawBytesSeq("empty", b"\x00").start(uart)
        await ClockCycles(cocotb.top.clk, 60 * 10 * CPB)
        await CmdSmokeSeq("smoke2").start(cmd)
