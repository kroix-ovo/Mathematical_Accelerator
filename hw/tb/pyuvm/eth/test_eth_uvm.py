"""pyuvm environment for the Ethernet transport: the Python mirror of hw/tb/uvm/eth.

The DUT is accel_core. The testbench plays the laptop and the LAN8720A PHY:

  command sequences -> eth CmdSequencer -> CmdToEthSeq (COBS/CRC frame inside
      UDP/IPv4/Ethernet + FCS) -> RmiiSequencer -> RmiiDriver -> RmiiPhy -> RMII rx pins
  raw frame sequences (ARP, ping, junk) -> RmiiSequencer
  RMII rx pins -> RmiiRxMonitor --\\
  RMII tx pins -> RmiiTxMonitor ---+--> EthScoreboard (EthBoardModel + DeviceModel)
  UART pins    -> UartFrameMonitor x2 --/
  RmiiTxMonitor -> UdpReplyExtractor -> eth CmdSequencer.rsp_fifo (so command
      sequences can wait for their replies, as in the UART environment)

The UART agent, command items, command sequencer and command sequences come
from hw/tb/pyuvm/top/test_top_uvm.py unchanged; only the bottom layer is new.
One DeviceModel sits behind both transports, so a word written over UDP and
read over UART, and the summed STATUS counters, are checked like the hardware.

RmiiPhy is the PHY model. It drives the RMII receive pins the way the
LAN8720A does: the outputs change a few ns after each rising edge of the
50 MHz reference clock, CRS_DV can rise a few dibits before the preamble, the
preamble can be shorter than 7 bytes, CRS_DV can toggle during the last
nibbles of a frame (when the PHY's FIFO drains after the carrier is gone),
and RX_ER can be raised in the middle of a frame. Each frame item picks those
details, so the same commands are received under many PHY behaviours.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import cocotb
import pyuvm
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer
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
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "top"))
from test_top_uvm import (  # noqa: E402
    CmdBaseSeq,
    CmdErrorSeq,
    CmdFpuSeq,
    CmdMemSeq,
    CmdSequencer,
    CmdSmokeSeq,
    CmdToUartSeq,
    UartAgent,
    WireFrameItem,
)

from mathaccel._generated import spec  # noqa: E402
from mathaccel._generated.spec import Kernel, Op  # noqa: E402
from mathaccel.driver import protocol as P  # noqa: E402
from mathaccel.driver.framing import cobs_encode  # noqa: E402
from mathaccel.driver.model import DeviceModel  # noqa: E402
from mathaccel.driver.protocol import Frame  # noqa: E402
from mathaccel.net import frames as F  # noqa: E402
from mathaccel.net.board_model import EthBoardModel  # noqa: E402

HOST_MAC = bytes.fromhex("D8BBC1A0B0C0")
HOST_IP = F.HOST_IP
HOST_PORT = 51515
TCO_NS = 6           # PHY output delay after the rising REFCLK edge
IFG_CLKS = 48        # 96 bit times at 2 bits per clock


# ---------------------------------------------------------------------------
# Item and PHY model
# ---------------------------------------------------------------------------

class EthFrameItem(uvm_sequence_item):
    """One Ethernet frame (destination MAC .. FCS) and how the PHY presents it.

    lead_in: 00 dibits with CRS_DV high before the preamble
    pre_dibits: number of 01 preamble dibits before the SFD (normally 28)
    toggle_nibbles: how many final nibbles are sent with CRS_DV toggling
    rx_er_at: dibit index (from the first data dibit) where RX_ER pulses, or None
    gap: idle clocks after the frame
    """

    def __init__(self, name="eth", data=b"", lead_in=0, pre_dibits=28, toggle_nibbles=0, rx_er_at=None,
                 gap=IFG_CLKS):
        super().__init__(name)
        self.data, self.lead_in, self.pre_dibits = data, lead_in, pre_dibits
        self.toggle_nibbles, self.rx_er_at, self.gap = toggle_nibbles, rx_er_at, gap
        self.rx_er = False          # set by the rx monitor

    def randomize_phy(self, rng: random.Random) -> EthFrameItem:
        """Pick PHY details at random (used for most frames in the tests)."""
        self.lead_in = rng.choice([0, 0, 1, 2, 5])
        self.pre_dibits = rng.choice([28, 28, 28, 20, 9])
        self.toggle_nibbles = rng.choice([0, 0, 1, 2, 3])
        self.gap = rng.choice([IFG_CLKS, IFG_CLKS, 60, 200])
        return self


class RmiiPhy:
    """LAN8720A model, receive direction: frame bytes -> RMII pins, clock by clock."""

    def __init__(self, dut):
        self.dut = dut

    async def _dibit(self, crs: int, rxd: int, er: int = 0) -> None:
        # The PHY updates its outputs TCO_NS after the edge it is clocked by;
        # the DUT samples them at the next rising edge.
        await RisingEdge(self.dut.clk_rmii)
        await Timer(TCO_NS, unit="ns")
        self.dut.rmii_crs_dv.value = crs
        self.dut.rmii_rxd.value = rxd
        self.dut.rmii_rx_er.value = er

    async def send(self, it: EthFrameItem) -> None:
        for _ in range(it.lead_in):
            await self._dibit(1, 0b00)
        for _ in range(it.pre_dibits):
            await self._dibit(1, 0b01)
        for d in (0b01, 0b01, 0b01, 0b11):                 # SFD 0xD5, low dibit first
            await self._dibit(1, d)
        dibits = [(b >> (2 * i)) & 3 for b in it.data for i in range(4)]
        n = len(dibits)
        toggle_from = n - 2 * it.toggle_nibbles
        for i, d in enumerate(dibits):
            # In the toggling tail CRS_DV is low on the first dibit of each
            # nibble and high on the second; the data stays valid throughout.
            crs = 0 if (i >= toggle_from and i % 2 == 0) else 1
            await self._dibit(crs, d, 1 if it.rx_er_at == i else 0)
        await self._dibit(0, 0b00)
        for _ in range(max(it.gap, IFG_CLKS) - 1):
            await self._dibit(0, 0b00)


# ---------------------------------------------------------------------------
# RMII agent
# ---------------------------------------------------------------------------

class RmiiSequencer(uvm_sequencer):
    pass


class RmiiDriver(uvm_driver):
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.phy = RmiiPhy(self.dut)

    async def run_phase(self):
        while True:
            item = await self.seq_item_port.get_next_item()
            await self.phy.send(item)
            self.seq_item_port.item_done()


class RmiiRxMonitor(uvm_monitor):
    """Rebuild frames from the receive pins, the way a MAC must read them.

    Samples on the falling edge of the 50 MHz clock, half a period after the
    PHY changed the pins. A dibit counts while CRS_DV is high now or was high
    one sample ago; the frame ends after two low samples.
    """

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.ap = uvm_analysis_port("ap", self)

    async def run_phase(self):
        dut = self.dut
        prev = 0
        state, pre, dib, er = "hunt", False, [], False
        while True:
            await FallingEdge(dut.clk_rmii)
            crs, rxd, rer = int(dut.rmii_crs_dv.value), int(dut.rmii_rxd.value), int(dut.rmii_rx_er.value)
            carrier = crs or prev
            prev = crs
            if state == "hunt":
                if not carrier:
                    pre = False
                elif rxd == 1:
                    pre = True
                elif rxd == 3 and pre:
                    state, dib, er = "data", [], False
                elif rxd != 0:
                    pre = False
            elif not carrier:
                nbytes = len(dib) // 4
                data = bytes(sum(dib[4 * k + i] << (2 * i) for i in range(4)) for k in range(nbytes))
                item = EthFrameItem("rx", data)
                item.rx_er = er
                self.ap.write(item)
                state, pre = "hunt", False
            else:
                dib.append(rxd)
                er = er or bool(rer)


class RmiiTxMonitor(uvm_monitor):
    """Collect frames the DUT sends and check their RMII-level shape.

    Samples on the rising edge, as the PHY does; the DUT changes TXD and TX_EN
    on the falling edge, so the values are half a period old and stable.

    Every frame must be 7 x 0x55, 0xD5, then a whole number of bytes with a
    good FCS, with TX_EN high without gaps, and at least 48 clocks (96 bit
    times) of TX_EN low before the next frame.
    """

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.ap = uvm_analysis_port("ap", self)
        self.errors = 0
        self.frames = 0

    def error(self, msg):
        self.errors += 1
        self.logger.error(msg)

    async def run_phase(self):
        dut = self.dut
        dib: list[int] = []
        idle = 10_000
        while True:
            await RisingEdge(dut.clk_rmii)
            en = int(dut.rmii_tx_en.value)
            if en:
                if not dib and idle < IFG_CLKS:
                    self.error(f"inter-frame gap of {idle} clocks")
                dib.append(int(dut.rmii_txd.value))
                idle = 0
                continue
            idle += 1
            if not dib:
                continue
            raw = bytes(sum(dib[4 * k + i] << (2 * i) for i in range(4)) for k in range(len(dib) // 4))
            if len(dib) % 4:
                self.error(f"frame of {len(dib)} dibits is not whole bytes")
            if raw[:8] != b"\x55" * 7 + b"\xd5":
                self.error(f"bad preamble {raw[:8].hex()}")
            frame = raw[8:]
            if not F.fcs_ok(frame):
                self.error(f"bad FCS on sent frame {frame.hex()}")
            if len(frame) < F.MIN_FRAME:
                self.error(f"sent runt of {len(frame)} bytes")
            self.frames += 1
            self.ap.write(EthFrameItem("tx", frame))
            dib = []


class RmiiAgent(uvm_agent):
    def build_phase(self):
        self.sqr = RmiiSequencer("sqr", self)
        self.drv = RmiiDriver("drv", self)
        self.rx_mon = RmiiRxMonitor("rx_mon", self)
        self.tx_mon = RmiiTxMonitor("tx_mon", self)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.sqr.seq_item_export)


# ---------------------------------------------------------------------------
# Command layer over UDP
# ---------------------------------------------------------------------------

def udp_frame(wire: bytes, sport: int = HOST_PORT) -> bytes:
    return F.udp_frame(wire, HOST_MAC, HOST_IP, sport)


class CmdToEthSeq(uvm_sequence):
    """Layering sequence: command item -> COBS/CRC frame -> one UDP datagram."""

    def __init__(self, name, up: CmdSequencer, seed: int = 1):
        super().__init__(name)
        self.up = up
        self.rng = random.Random(seed)

    async def body(self):
        while True:
            cmd = await self.up.seq_item_export.get_next_item()
            raw = bytearray(Frame(cmd.op, cmd.tag, cmd.payload).to_raw())
            if cmd.corrupt == "crc":
                raw[-1] ^= 0x5A
            elif cmd.corrupt == "short":
                raw = raw[:3]
            item = EthFrameItem("cmd", udp_frame(cobs_encode(bytes(raw)) + b"\x00")).randomize_phy(self.rng)
            await self.start_item(item)
            await self.finish_item(item)
            self.up.seq_item_export.item_done()


class UdpReplyExtractor(uvm_subscriber):
    """Turn reply datagrams back into WireFrameItems for the command sequencer."""

    def write(self, item):
        p = F.parse(item.data)
        if p.kind == "udp":
            for chunk in p.udp_payload.split(b"\x00"):
                if chunk:
                    self.fifo.analysis_export.write(WireFrameItem("frame", chunk))


class RawEthSeq(uvm_sequence):
    """Send a list of complete frames (or EthFrameItems) on the RMII sequencer."""

    def __init__(self, name, frames, seed: int = 7):
        super().__init__(name)
        self.frames = frames
        self.rng = random.Random(seed)

    async def body(self):
        for f in self.frames:
            item = f if isinstance(f, EthFrameItem) else EthFrameItem("raw", f).randomize_phy(self.rng)
            await self.start_item(item)
            await self.finish_item(item)


# ---------------------------------------------------------------------------
# Scoreboard
# ---------------------------------------------------------------------------

class _Tap(uvm_subscriber):
    """Forward one analysis stream to a scoreboard method, in arrival order."""

    def __init__(self, name, parent, fn):
        super().__init__(name, parent)
        self.fn = fn

    def write(self, item):
        self.fn(item)


class EthScoreboard(uvm_component):
    """Predict every reply on both transports with one shared device model.

    Requests are applied to the model the moment their monitor sees them end,
    which is the order the DUT handles them in. Predicted replies wait in one
    queue per transport and must match the observed bytes exactly; on
    Ethernet that is the whole frame including the FCS.
    """

    def build_phase(self):
        self.device = DeviceModel()
        self.eth = EthBoardModel(self.device)
        self.exp_eth: list[bytes] = []
        self.exp_uart: list[bytes] = []
        self.errors = 0
        self.n_eth_rx = self.n_eth_tx = self.n_uart_rx = self.n_uart_tx = 0
        self.kinds: dict[str, int] = {}
        self.eth_rx = _Tap("eth_rx", self, self.on_eth_rx)
        self.eth_tx = _Tap("eth_tx", self, self.on_eth_tx)
        self.uart_rx = _Tap("uart_rx", self, self.on_uart_rx)
        self.uart_tx = _Tap("uart_tx", self, self.on_uart_tx)

    def on_eth_rx(self, item):
        self.n_eth_rx += 1
        replies = self.eth.handle_frame(item.data, item.rx_er)
        for r in replies:
            k = F.parse(r).kind
            self.kinds[k] = self.kinds.get(k, 0) + 1
        self.exp_eth += replies

    def on_uart_rx(self, item):
        self.n_uart_rx += 1
        r = self.device.handle_wire(item.data)
        if r is not None:
            self.exp_uart.append(r)

    def _compare(self, queue, got, what):
        if not queue:
            self.errors += 1
            self.logger.error(f"unexpected {what} {got.hex()}")
            return
        exp = queue.pop(0)
        if got != exp:
            self.errors += 1
            self.logger.error(f"{what} mismatch\n  expected {exp.hex()}\n  got      {got.hex()}")

    def on_eth_tx(self, item):
        self.n_eth_tx += 1
        self._compare(self.exp_eth, item.data, "Ethernet frame")

    def on_uart_tx(self, item):
        self.n_uart_tx += 1
        self._compare(self.exp_uart, item.data + b"\x00", "UART reply")

    def check_phase(self):
        for q, what in ((self.exp_eth, "Ethernet"), (self.exp_uart, "UART")):
            if q:
                self.errors += 1
                self.logger.error(f"{len(q)} predicted {what} replies never arrived")

    def report_phase(self):
        self.logger.info(f"Ethernet: {self.n_eth_rx} frames in, {self.n_eth_tx} out {self.kinds}; "
                         f"UART: {self.n_uart_rx} in, {self.n_uart_tx} out; model rx_errors "
                         f"{self.eth.rx_errors}, rx_drops {self.eth.rx_drops}; {self.errors} errors")


class EthEnv(uvm_env):
    def build_phase(self):
        self.rmii = RmiiAgent("rmii", self)
        self.uart = UartAgent("uart", self)
        self.eth_cmd = CmdSequencer("eth_cmd", self)
        self.uart_cmd = CmdSequencer("uart_cmd", self)
        self.replies = UdpReplyExtractor("replies", self)
        self.sb = EthScoreboard("sb", self)

    def connect_phase(self):
        self.rmii.rx_mon.ap.connect(self.sb.eth_rx.analysis_export)
        self.rmii.tx_mon.ap.connect(self.sb.eth_tx.analysis_export)
        self.uart.rx_mon.ap.connect(self.sb.uart_rx.analysis_export)
        self.uart.tx_mon.ap.connect(self.sb.uart_tx.analysis_export)
        self.rmii.tx_mon.ap.connect(self.replies.analysis_export)
        self.replies.fifo = self.eth_cmd.rsp_fifo
        self.uart.tx_mon.ap.connect(self.uart_cmd.rsp_fifo.analysis_export)

    async def run_phase(self):
        cocotb.start_soon(CmdToEthSeq("eth_layer", self.eth_cmd).start(self.rmii.sqr))
        await CmdToUartSeq("uart_layer", self.uart_cmd).start(self.uart.sqr)


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------

def junk_frames() -> list[bytes]:
    """Frames a Windows laptop really sends on a fresh link, plus broken ones."""
    wire = Frame(Op.PING, 0xEE, b"").encode()
    good = udp_frame(wire)
    mdns_ip = F.ip_bytes("224.0.0.251")
    frames = [
        # mDNS query to a multicast MAC: not our MAC, not broadcast.
        F.ethernet(bytes.fromhex("01005E0000FB"), HOST_MAC, 0x0800,
                   F.ipv4(HOST_IP, mdns_ip, 17, F.udp(HOST_IP, mdns_ip, 5353, 5353, b"\x00" * 40))),
        # IPv6 router solicitation (EtherType 86DD).
        F.ethernet(bytes.fromhex("333300000002"), HOST_MAC, 0x86DD, bytes(56)),
        # Broadcast NetBIOS/UDP to another port, and UDP to our IP on the wrong port.
        F.ethernet(F.BROADCAST, HOST_MAC, 0x0800,
                   F.ipv4(HOST_IP, F.ip_bytes("192.168.10.255"), 17, F.udp(HOST_IP, F.ip_bytes("192.168.10.255"),
                                                                            137, 137, bytes(50)))),
        F.udp_frame(wire, HOST_MAC, HOST_IP, HOST_PORT, dport=spec.UDP_PORT + 1),
        # Right port, wrong IP; ARP request for another address; a gratuitous ARP reply.
        F.udp_frame(wire, HOST_MAC, HOST_IP, HOST_PORT, dst_ip=F.ip_bytes("192.168.10.3")),
        F.arp_request_frame(HOST_MAC, HOST_IP, F.ip_bytes("192.168.10.77")),
        F.ethernet(F.BROADCAST, HOST_MAC, F.ETH_TYPE_ARP, F.arp(2, HOST_MAC, HOST_IP, HOST_MAC, HOST_IP)),
        # IHL = 6 (options) and a fragment: the board ignores both.
        F.ethernet(F.BOARD_MAC, HOST_MAC, 0x0800, b"\x46" + good[15:]),
        F.ethernet(F.BOARD_MAC, HOST_MAC, 0x0800,
                   F.ipv4(HOST_IP, F.BOARD_IP, 17, F.udp(HOST_IP, F.BOARD_IP, HOST_PORT, spec.UDP_PORT, wire),
                          flags_frag=0x2000)),
        # Bad FCS and a runt (both counted as receive errors), and an oversized frame (a drop).
        good[:-1] + bytes([good[-1] ^ 0x01]),
        good[:30] + F.fcs(good[:30]),
        F.ethernet(F.BOARD_MAC, HOST_MAC, 0x0800, bytes(1520)),
    ]
    return frames


class EthBaseTest(uvm_test):
    def build_phase(self):
        ConfigDB().set(None, "*", "DUT", cocotb.top)
        self.env = EthEnv("env", self)

    async def run_phase(self):
        dut = cocotb.top
        self.raise_objection()
        # Both clocks start at time 0, so every rising edge of the 50 MHz
        # clock lines up with a rising edge of the 100 MHz clock (one MMCM on
        # the board makes them the same way).
        Clock(dut.clk, 10, unit="ns").start()
        Clock(dut.clk_rmii, 20, unit="ns").start()
        dut.rmii_crs_dv.value = 0
        dut.rmii_rxd.value = 0
        dut.rmii_rx_er.value = 0
        dut.uart_rx_i.value = 1
        dut.rst.value = 1
        await ClockCycles(dut.clk, 10)
        dut.rst.value = 0
        await ClockCycles(dut.clk, 20)
        await self.run_seqs()
        await ClockCycles(dut.clk, 30_000)
        self.drop_objection()

    async def run_seqs(self):
        raise NotImplementedError

    async def settle(self, clks=20_000):
        await ClockCycles(cocotb.top.clk, clks)

    def final_phase(self):
        sb = self.env.sb
        mon = self.env.rmii.tx_mon
        assert sb.errors == 0 and mon.errors == 0, f"{sb.errors} scoreboard and {mon.errors} RMII errors"
        assert sb.n_eth_tx > 0, "no Ethernet frames came out"


@pyuvm.test()
class EthSmokeTest(EthBaseTest):
    """ARP, one ping and the command smoke sequence over UDP."""

    async def run_seqs(self):
        rmii = self.env.rmii.sqr
        await RawEthSeq("arp", [F.arp_request_frame(HOST_MAC, HOST_IP)]).start(rmii)
        await RawEthSeq("ping", [F.ping_frame(HOST_MAC, HOST_IP, 1, 1, bytes(range(32)))]).start(rmii)
        await self.settle()
        await CmdSmokeSeq("smoke").start(self.env.eth_cmd)


@pyuvm.test()
class EthRegressTest(EthBaseTest):
    """Everything: ARP, pings of several sizes, every command sequence over
    UDP, junk traffic, multi-frame and back-to-back datagrams, UART and UDP
    commands mixed, and STATUS after errors on both transports."""

    async def run_seqs(self):
        rmii, eth, uart = self.env.rmii.sqr, self.env.eth_cmd, self.env.uart_cmd
        seed = int(os.environ.get("ETH_SEED", "1"))
        rng = random.Random(seed)
        await RawEthSeq("arp", [F.arp_request_frame(HOST_MAC, HOST_IP)], seed).start(rmii)
        for size in (0, 1, 32, 333, 1472):
            data = bytes(rng.getrandbits(8) for _ in range(size))
            await RawEthSeq("ping", [F.ping_frame(HOST_MAC, HOST_IP, 0x1234, size, data)], seed).start(rmii)
            await self.settle(5_000 + size * 20)
        await CmdSmokeSeq("smoke").start(eth)
        await CmdMemSeq("mem").start(eth)
        await CmdFpuSeq("fpu").start(eth)
        await CmdErrorSeq("err").start(eth)
        # Junk between commands: none of it may produce a reply.
        await RawEthSeq("junk", junk_frames(), seed).start(rmii)
        # The PHY flags a receive error halfway through a good frame.
        bad = EthFrameItem("rx_er", udp_frame(Frame(Op.PING, 0xEF, b"").encode()), rx_er_at=200)
        await RawEthSeq("rx_er", [bad]).start(rmii)
        await self.settle()
        # Two frames in one datagram (the second without its 0x00), then two
        # datagrams back to back without waiting: both slots fill, both answer.
        two = Frame(Op.PING, 0xA1, b"").encode() + Frame(Op.RUN, 0xA2, bytes([Kernel.NOP])).encode()[:-1]
        await RawEthSeq("multi", [udp_frame(two)], seed).start(rmii)
        await self.settle()
        await RawEthSeq("b2b", [EthFrameItem("b1", udp_frame(Frame(Op.PING, 0xB1, b"").encode(), 40001)),
                                EthFrameItem("b2", udp_frame(Frame(Op.STATUS, 0xB2, b"").encode(), 40002))]).start(rmii)
        await self.settle()
        # Mixed transports: write over UDP, read back over UART, and the reverse.
        await CmdMemSeq("mem_uart").start(uart)
        op, payload = P.write_mem(123, [0xCAFEF00D, 0x12345678])
        await _send(eth, op, payload)
        await _send(uart, *P.read_mem(123, 2))
        await _send(uart, *P.write_mem(500, [0x0BADBEEF]))
        await _send(eth, *P.read_mem(500, 1))
        # STATUS now reports CRC/frame errors from both transports together.
        await CmdErrorSeq("err_uart").start(uart)
        await _send(eth, *P.status())
        await CmdSmokeSeq("smoke_end").start(eth)


class _OneCmd(CmdBaseSeq):
    def __init__(self, name, op, payload):
        super().__init__(name)
        self.op, self.payload = op, payload

    async def body(self):
        await self.send(self.op, self.payload)


async def _send(sqr, op, payload=b""):
    await _OneCmd("one", op, payload).start(sqr)


@pyuvm.test()
class EthMultiTest(EthBaseTest):
    """Directed: several frames in one datagram, and datagrams back to back."""

    async def run_seqs(self):
        rmii = self.env.rmii.sqr
        two = Frame(Op.PING, 0xA1, b"").encode() + Frame(Op.RUN, 0xA2, bytes([Kernel.NOP])).encode()[:-1]
        await RawEthSeq("multi", [EthFrameItem("multi", udp_frame(two))]).start(rmii)
        await self.settle()
        await RawEthSeq("b2b", [EthFrameItem("b1", udp_frame(Frame(Op.PING, 0xB1, b"").encode(), 40001)),
                                EthFrameItem("b2", udp_frame(Frame(Op.STATUS, 0xB2, b"").encode(), 40002))]).start(rmii)
        await self.settle()
