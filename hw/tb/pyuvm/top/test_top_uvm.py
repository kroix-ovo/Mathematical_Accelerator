"""pyuvm environment for the whole accelerator: the Python mirror of hw/tb/uvm/top.

Layers, same as the SV environment:
  command sequences -> CmdSequencer (no driver)
      -> CmdToUartSeq (layering sequence on the UART sequencer) -> UartDriver -> uart_rx pin
  uart_rx / uart_tx pins -> UartFrameMonitor (one per wire) -> TopScoreboard

The scoreboard predicts replies with sw/mathaccel/driver/model.py
(DeviceModel.handle_wire), the same model the Python driver's simulator uses.
That is a separate implementation from the SV reference model, so the two
environments check each other's interpretation of docs/protocol.md.

pyuvm has no sequencer lock()/grab(). The UART sequencer here carries an
asyncio-style Lock instead; the layering sequence and raw byte sequences hold
it for a whole frame, which gives the same "no interleaved frames" guarantee.
"""

from __future__ import annotations

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
from mathaccel._generated.spec import Kernel, Op
from mathaccel.driver import protocol as P
from mathaccel.driver.framing import cobs_encode
from mathaccel.driver.model import DeviceModel
from mathaccel.driver.protocol import Frame
from mathaccel.fpgen import FpLcg

CPB = 8  # clocks per UART bit, matches the CLKS_PER_BIT parameter in run.py


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class UartByteItem(uvm_sequence_item):
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
    def __init__(self, name, parent):
        super().__init__(name, parent)
        self.frame_lock = Lock()       # stands in for SV lock()/unlock()


class UartDriver(uvm_driver):
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
    def __init__(self, name, data: bytes):
        super().__init__(name)
        self.data = data

    async def body(self):
        await send_bytes(self, self.data)


# ---------------------------------------------------------------------------
# Command sequences (run on CmdSequencer, behave like a host)
# ---------------------------------------------------------------------------

class CmdBaseSeq(uvm_sequence):
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
    async def body(self):
        await self.send(*P.ping())
        await self.send(*P.status())
        await self.send(*P.run(Kernel.NOP))


class CmdMemSeq(CmdBaseSeq):
    async def body(self):
        import random
        rng = random.Random(11)
        for _ in range(4):
            addr = rng.randrange(0, spec.SCRATCH_WORDS - 256)
            n = rng.randint(1, 255)
            await self.write_words(addr, [rng.getrandbits(32) for _ in range(n)])
            await self.read_words(addr, n)


class CmdFpuSeq(CmdBaseSeq):
    async def body(self):
        n, A, B, R, F = 10, 8000, 8300, 8600, 8900
        g = FpLcg(0x70F0)
        for fop in range(12):
            ops = [g.stimulus(fop) for _ in range(n)]
            await self.write_words(A, [a for _, a, _ in ops])
            await self.write_words(B, [b for _, _, b in ops])
            await self.send(*P.run(Kernel.FPU, P.fpu_args(fop, n, A, B, R, F)))
            await self.read_words(R, n)
            await self.read_words(F, n)


class CmdErrorSeq(CmdBaseSeq):
    async def body(self):
        await self.send(0x33)                                                    # BAD_OP
        await self.send(Op.PING, b"\x00")                                        # BAD_LEN
        await self.send(*P.read_mem(spec.SCRATCH_WORDS - 1, 2))                  # BAD_ADDR
        await self.send(*P.run(0x7E))                                            # BAD_KERNEL
        await self.send(*P.run(Kernel.FPU, P.fpu_args(12, 1, 0, 0, 0, 0)))       # BAD_ARG
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
        self.expected: list[bytes] = []
        self.n_req = self.n_rsp = self.n_silent = self.errors = 0

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
            exp = self.expected.pop(0)
            if got != exp:
                self.errors += 1
                self.logger.error(f"reply mismatch\n  expected {exp.hex()}\n  got      {got.hex()}")

    def drain_requests(self):
        while True:
            ok, req = self.req_fifo.try_get()
            if not ok:
                return
            self.n_req += 1
            reply = self.model.handle_wire(req.data)
            if reply is None:
                self.n_silent += 1
            else:
                self.expected.append(reply)

    def check_phase(self):
        self.drain_requests()
        if self.expected:
            self.errors += 1
            self.logger.error(f"{len(self.expected)} predicted replies never arrived")

    def report_phase(self):
        self.logger.info(f"{self.n_req} requests ({self.n_silent} silent by design), "
                         f"{self.n_rsp} replies, {self.errors} errors")


class TopEnv(uvm_env):
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
    async def run_seqs(self):
        await CmdSmokeSeq("smoke").start(self.env.cmd_sqr)


@pyuvm.test()
class TopRegressTest(TopBaseTest):
    """The virtual-sequence role is played by this method: it starts command
    sequences on the command sequencer and raw sequences on the UART sequencer."""

    async def run_seqs(self):
        cmd, uart = self.env.cmd_sqr, self.env.uart.sqr
        await CmdSmokeSeq("smoke").start(cmd)
        await CmdMemSeq("mem").start(cmd)
        await CmdFpuSeq("fpu").start(cmd)
        await CmdErrorSeq("err").start(cmd)
        await RawBytesSeq("garbage", b"\x05\x01\x02\x00").start(uart)
        await RawBytesSeq("empty", b"\x00").start(uart)
        await ClockCycles(cocotb.top.clk, 60 * 10 * CPB)
        await CmdSmokeSeq("smoke2").start(cmd)
