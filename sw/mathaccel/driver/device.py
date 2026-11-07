"""Give callers a command interface to the board or its Python simulator.

I keep packet matching, retries, and transfer sizes here, so the CLI can call
read_words or fill without knowing how UART frames are built. Both transports
use this same path. Floating-point helpers only store and retrieve fp32 bit
patterns.

A resend is only safe when doing a command twice changes nothing, so Device
keeps resending for memory and link commands and refuses to do it for an EVU
run. What the board does with a request that arrives during a run depends on
the transport, and neither behaviour would be what I want:

* UDP: eth_stack keeps one more datagram during a run and replays it once the
  core is idle. A resent RUN is not dropped. It waits, and starts again the
  moment the first run finishes. I measured this on the board. The running
  command holds the other receive slot, so anything after that is dropped.
* UART: frame_rx drops the frame and adds one to frame_errors. But if the board
  had already finished and only the reply was lost, a resent RUN runs again.

So evu_start sends RUN exactly once, gives it as long as cycle_bounds says it
could need, and on a timeout works out what happened without restarting it.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import numpy as np

from mathaccel._generated import spec
from mathaccel._generated.spec import Kernel, Status
from mathaccel.driver import protocol as P
from mathaccel.driver.protocol import DeviceError, Frame, FrameError
from mathaccel.driver.transport import SimTransport, Transport, UartTransport, UdpTransport

# A flags word holds five bits, so a real run can never leave this in f[0]. I
# write it just before RUN, which lets me tell afterwards whether a run ever
# happened when its reply never came.
EVU_NOT_RUN = 0xFFFFFFFF
# The 32-bit cycle counter wraps after this many seconds at 100 MHz.
CYCLE_COUNTER_SECONDS = 2**32 / spec.CLK_HZ
# Longest run the tools start without being told to. A run can't be cancelled:
# only the reset button or reprogramming stops it, so a typing mistake in a grid
# size shouldn't cost more than half a minute. It also stays inside the counter.
EVU_MAX_SECONDS = 30.0
# Added to the cycle_bounds high bound when I set a timeout. The high bound
# covers the kernel only, and the reply still has to cross the link.
EVU_LINK_MARGIN = 2.0


class DeviceTimeout(TimeoutError):
    """No matching reply arrived after all request attempts were exhausted."""


class RunTimeout(DeviceTimeout):
    """An EVU run's reply didn't arrive. Nothing was resent.

    state says what I could find out afterwards:

    * "running": the board still isn't answering, so the run is probably going.
    * "finished": the board is idle and the run wrote its flags. Only the reply
      was lost. cycles and aux are gone, and the outputs are in the scratchpad.
    * "not started": the board is idle and the run never touched its flags, so
      the request was lost. Running it again is safe, and is the caller's choice.
    """

    def __init__(self, message: str, state: str) -> None:
        super().__init__(message)
        self.state = state


class RunTooLong(RuntimeError):
    """A run would take longer than the caller allowed, so it wasn't started."""


@dataclass(frozen=True)
class RunResult:
    """Kernel time measured in core clocks, excluding UART transfer time.

    aux is kernel specific: the OR of all element flags for the FPU kernel,
    also the OR of point flags for EVU, and zero for NOP and FILL.
    """
    cycles: int
    aux: int = 0

    @property
    def seconds(self) -> float:
        """Convert the reported clocks using the configured core frequency."""
        return self.cycles / spec.CLK_HZ


@dataclass(frozen=True)
class EvuReply:
    """What one EVU run returned, with where its time went.

    results and flags hold raw words: one of each in a reduce run (the sum and
    its flags), one per point in a map run. aux is the OR of every point's flags.
    bounds is (low, high) from cycle_bounds, and the three times are wall clock
    on this laptop, so they include Python and the link.
    """
    results: np.ndarray
    flags: np.ndarray
    cycles: int
    aux: int
    bounds: tuple[int, int]
    write_seconds: float
    run_seconds: float
    read_seconds: float

    @property
    def seconds(self) -> float:
        """Kernel time from the board's own clock counter."""
        return self.cycles / spec.CLK_HZ

    @property
    def total_seconds(self) -> float:
        """Everything the laptop waited for: transfers, kernel and overhead."""
        return self.write_seconds + self.run_seconds + self.read_seconds

    @property
    def in_bounds(self) -> bool:
        """Whether the reported clocks fall inside cycle_bounds."""
        return self.bounds[0] <= self.cycles <= self.bounds[1]


class Device:
    """Execute one request at a time through the supplied byte transport."""

    def __init__(self, transport: Transport, timeout: float = 0.5, retries: int = 3) -> None:
        # retries counts extra attempts after the first send, not total sends.
        self.t = transport
        self.timeout = timeout
        self.retries = retries
        self._tag = 0
        self._evu_checked = False       # set once PING has shown the board has the EVU
        # cycle_bounds gives board clocks. Only a simulator behind a socket needs
        # this above 1: the Python model takes far longer than the hardware does.
        self.evu_time_scale = 1.0

    # Packet handling shared by every public command.

    def _next_tag(self) -> int:
        """Advance the one-byte request tag, wrapping after 255 like the FPGA."""
        self._tag = (self._tag + 1) & 0xFF
        return self._tag

    def request(self, op: int, payload: bytes, timeout: float | None = None, resend: bool = True) -> bytes:
        """Send a command and return its reply data after checking the status.

        Corrupt or stale replies are skipped. A transport receive timeout
        starts a fresh attempt; the new tag distinguishes it from old replies.
        An error status is a completed command and is raised immediately.
        With resend=False the request goes out exactly once, for commands that
        must not run twice.
        """
        for _ in range(self.retries + 1 if resend else 1):
            # A retry gets a new tag even though its opcode/payload are the same.
            tag = self._next_tag()
            self.t.send(Frame(op, tag, payload).encode())
            while True:
                wire = self.t.recv_frame(timeout or self.timeout)
                if wire is None:
                    break  # timeout -> retry with a fresh tag
                try:
                    resp = Frame.decode(wire)
                except FrameError:
                    continue
                if resp.tag != tag or resp.op != (op | P.RESP_BIT):
                    continue  # stale reply from an earlier attempt
                if resp.status != Status.OK:
                    # BAD_ADDR and similar errors do not become successful
                    # just by resending the same request.
                    raise DeviceError(op, resp.status)
                return resp.data
        attempts = self.retries + 1 if resend else 1
        raise DeviceTimeout(f"no response to op 0x{op:02X} after {attempts} attempt(s)")

    # Public commands translate useful Python values into protocol payloads.

    def ping(self) -> P.PingInfo:
        """Read configuration and reject a device with the wrong identity magic."""
        info = P.parse_ping(self.request(*P.ping()))
        if info.magic != spec.MAGIC:
            raise RuntimeError(f"bad magic 0x{info.magic:08X}")
        return info

    def status(self) -> P.StatusInfo:
        """Read link errors and the last kernel cycle count without changing them."""
        return P.parse_status(self.request(*P.status()))

    def write_words(self, addr: int, words: np.ndarray | list[int]) -> None:
        """Write an array in legal packet-sized chunks, advancing word addresses."""
        data = np.asarray(words, dtype=np.uint32)
        step = P.MAX_WORDS_PER_FRAME
        for i in range(0, len(data), step):
            # Explicit little-endian bytes keep the wire format independent
            # of the host machine's native byte order.
            chunk = data[i:i + step].astype("<u4").tobytes()
            self.request(*P.write_mem(addr + i, chunk))

    def read_words(self, addr: int, count: int) -> np.ndarray:
        """Read several packet-sized spans into one unsigned 32-bit array."""
        out = np.empty(count, dtype=np.uint32)
        step = P.MAX_WORDS_PER_FRAME
        for i in range(0, count, step):
            n = min(step, count - i)
            out[i:i + n] = np.frombuffer(self.request(*P.read_mem(addr + i, n)), dtype="<u4")
        return out

    def write_f32(self, addr: int, values) -> None:
        """Store fp32 representations without numerically converting them to ints."""
        # view reinterprets the same bits. Casting a float to uint32 instead
        # would store its integer value and lose the IEEE-754 representation.
        self.write_words(addr, np.asarray(values, dtype=np.float32).view(np.uint32))

    def read_f32(self, addr: int, count: int) -> np.ndarray:
        """Interpret returned word bits as fp32 values, without changing the bits."""
        return self.read_words(addr, count).view(np.float32)

    def run(self, kernel: int, args: bytes = b"", timeout: float = 5.0, resend: bool = False) -> RunResult:
        """Wait for a kernel reply; kernels get a longer timeout than link commands.

        A kernel is only sent once unless the caller says a second run is
        harmless. See the note at the top of this file for why.
        """
        return RunResult(*P.parse_run(self.request(*P.run(kernel, args), timeout=timeout, resend=resend)))

    def nop(self) -> RunResult:
        """Run the no-operation kernel to check dispatch and cycle reporting."""
        return self.run(Kernel.NOP, resend=True)

    def fill(self, base: int, count: int, value: int) -> RunResult:
        """Write value to count words beginning at base, then return kernel time."""
        return self.run(Kernel.FILL, struct.pack("<III", base, count, value), resend=True)

    def fpu_kernel(self, fop: int, count: int, a: int, b: int, r: int, f: int,
                   timeout: float = 10.0) -> RunResult:
        """Run r[i], f[i] = fop(a[i], b[i]) over word arrays already in the scratchpad.

        a, b, r and f are word addresses. RunResult.aux holds the OR of all flags.
        Doing this twice only gives the same answer when r and f stay clear of
        a and b, so that is the only case that may be resent. The largest run
        the scratchpad can hold takes about 20 ms, so a timeout here means a
        lost frame and not a run still going.
        """
        clear = not any(x < y + count and y < x + count for x in (r, f) for y in (a, b))
        return self.run(Kernel.FPU, P.fpu_args(fop, count, a, b, r, f), timeout=timeout, resend=clear)

    def fpu(self, fop: int, a_bits, b_bits=None, base: int = 0) -> tuple[np.ndarray, np.ndarray, RunResult]:
        """Compute fop elementwise on raw fp32 bit arrays and return (results, flags, run).

        Uses four consecutive arrays starting at word `base` for a, b, r and f.
        For unary ops b may be omitted. Values are raw uint32 bit patterns, so
        NaN payloads and signed zeros pass through unchanged.
        """
        a = np.atleast_1d(np.asarray(a_bits, dtype=np.uint32))
        b = np.zeros_like(a) if b_bits is None else np.atleast_1d(np.asarray(b_bits, dtype=np.uint32))
        n = len(a)
        if len(b) != n:
            raise ValueError("a and b must have the same length")
        addr_a, addr_b, addr_r, addr_f = base, base + n, base + 2 * n, base + 3 * n
        self.write_words(addr_a, a)
        self.write_words(addr_b, b)
        run = self.fpu_kernel(fop, n, addr_a, addr_b, addr_r, addr_f)
        return self.read_words(addr_r, n), self.read_words(addr_f, n), run

    # The expression unit. The usual call is evu_run; evu_load and evu_start are
    # there for a host loop that changes a word or two between runs of the same
    # program, which is what root finding does.

    def _check_board_has_evu(self) -> None:
        """Fail clearly on an older bitstream, once per connection."""
        if self._evu_checked:
            return
        info = self.ping()
        if info.version < (0, 4):
            version = f"v{info.version[0]}.{info.version[1]}"
            raise RuntimeError(f"the board is at {version}, the EVU needs v0.4: reprogram it")
        self._evu_checked = True

    # I separate loading from starting so repeated evaluations can reuse the
    # same image. I mark its flag output before each start, then send RUN
    # once. Retrying a timed-out RUN could execute the program twice.
    def evu_load(self, run, base: int = 0):
        """Write a run's descriptor, program, constants and tables. Returns the Image."""
        from mathaccel.compiler.evu_isa import memory_image

        self._check_board_has_evu()
        image = memory_image(run, base)
        self.write_words(image.base, image.words)
        return image

    def evu_start(self, image, bounds: tuple[int, int]) -> RunResult:
        """Run a loaded image, sending RUN exactly once.

        bounds is the (low, high) pair from cycle_bounds. The timeout covers the
        high bound. If it still passes, I find out what happened and raise
        RunTimeout, and never send RUN a second time.
        """
        timeout = max(5.0, bounds[1] / spec.CLK_HZ * 1.2 * self.evu_time_scale + EVU_LINK_MARGIN)
        self.write_words(image.f, [EVU_NOT_RUN])
        try:
            return self.run(Kernel.EVU, P.evu_args(image.desc), timeout=timeout)
        except DeviceTimeout as e:
            raise self._after_timeout(image, timeout) from e

    def _after_timeout(self, image, waited: float) -> RunTimeout:
        """Find out what became of a run whose reply never came.

        Over UDP a request sent during a run waits in the board and is answered
        when the run ends. Over the UART it is dropped. Either way, no reply to
        STATUS means the core is still busy, and a reply means it is idle. STATUS
        changes nothing, so this can't restart anything.
        """
        try:
            self.request(*P.status(), timeout=self.timeout)
            flag = int(self.read_words(image.f, 1)[0])
        except DeviceTimeout:
            return RunTimeout(f"no reply after {waited:.1f} s and the board isn't answering, so the run is probably "
                              "still going. A run can't be cancelled: wait, or press the reset button.", "running")
        if flag == EVU_NOT_RUN:
            return RunTimeout(f"no reply after {waited:.1f} s, and the run never started: the request was lost. "
                              "I did not resend it.", "not started")
        return RunTimeout(f"no reply after {waited:.1f} s, but the run finished and only the reply was lost. Its "
                          f"outputs are at {image.r} and its flags at {image.f}. I did not run it again.", "finished")

    def evu_run(self, run, base: int = 0, max_seconds: float = EVU_MAX_SECONDS) -> EvuReply:
        """Write a run to the scratchpad, run it, and read back what it produced.

        max_seconds refuses a run whose low bound is longer than that, before
        anything is sent, because a started run can't be stopped. The low bound
        is the right one to test: the high bound is deliberately generous and
        would refuse runs that are really fast. Pass float("inf") to allow
        anything. Past CYCLE_COUNTER_SECONDS the reported cycles have wrapped.
        """
        from mathaccel.evu_model import cycle_bounds

        run.check()
        bounds = cycle_bounds(run)
        if bounds[0] / spec.CLK_HZ > max_seconds:
            raise RunTooLong(f"this run takes at least {bounds[0] / spec.CLK_HZ:.1f} s ({run.points:,} points, "
                             f"{len(run.program.words)} instructions) and can't be cancelled once started. The limit "
                             f"is {max_seconds:g} s: use fewer points or raise max_seconds.")
        t0 = time.perf_counter()
        image = self.evu_load(run, base)
        t1 = time.perf_counter()
        done = self.evu_start(image, bounds)
        t2 = time.perf_counter()
        count = 1 if run.reduce else run.points
        results = self.read_words(image.r, count)
        flags = self.read_words(image.f, count)
        t3 = time.perf_counter()
        return EvuReply(results, flags, done.cycles, done.aux, bounds, t1 - t0, t2 - t1, t3 - t2)

    def close(self) -> None:
        """Release resources owned by the transport, including a real serial port."""
        self.t.close()

    def __enter__(self):
        """Allow `with connect(...) as dev` to expose this command interface."""
        return self

    def __exit__(self, *exc):
        """Close the transport on normal exit or an exception from the with block."""
        self.close()


def connect(port: str | None = None, **kw) -> Device:
    """Select simulator, UDP or serial by port name and pass driver options through.

    None or "sim" needs no board. "udp:192.168.10.2" (optionally with ":port")
    talks to the board over Ethernet. "udpsim" starts the Python model behind a
    local UDP socket, which exercises UdpTransport without hardware. Any other
    name, such as "COM5" or a macOS /dev/cu.* device, is opened as a real
    serial port by UartTransport.
    """
    if port in (None, "sim"):
        return Device(SimTransport(), **kw)
    if port == "udpsim":
        from mathaccel.driver.udpsim import UdpSimServer

        server = UdpSimServer()
        device = Device(server.transport(), **kw)
        device.evu_time_scale = 100.0
        return device
    if port.startswith("udp:"):
        host, _, num = port[4:].partition(":")
        return Device(UdpTransport(host, int(num) if num else spec.UDP_PORT), **kw)
    return Device(UartTransport(port), **kw)
