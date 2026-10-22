"""Give callers a command interface to the board or its Python simulator.

I keep packet matching, retries, and transfer sizes here, so the CLI can call
read_words or fill without knowing how UART frames are built. Both transports
use this same path. Floating-point helpers only store and retrieve fp32 bit
patterns; the current hardware does not perform floating-point arithmetic.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from mathaccel._generated import spec
from mathaccel._generated.spec import Kernel, Status
from mathaccel.driver import protocol as P
from mathaccel.driver.protocol import DeviceError, Frame, FrameError
from mathaccel.driver.transport import SimTransport, Transport, UartTransport, UdpTransport


class DeviceTimeout(TimeoutError):
    """No matching reply arrived after all request attempts were exhausted."""


@dataclass(frozen=True)
class RunResult:
    """Kernel time measured in core clocks, excluding UART transfer time.

    aux is kernel specific: the OR of all element flags for the FPU kernel,
    zero for NOP and FILL.
    """
    cycles: int
    aux: int = 0

    @property
    def seconds(self) -> float:
        """Convert the reported clocks using the configured core frequency."""
        return self.cycles / spec.CLK_HZ


class Device:
    """Execute one request at a time through the supplied byte transport."""

    def __init__(self, transport: Transport, timeout: float = 0.5, retries: int = 3) -> None:
        # retries counts extra attempts after the first send, not total sends.
        self.t = transport
        self.timeout = timeout
        self.retries = retries
        self._tag = 0

    # Packet handling shared by every public command.

    def _next_tag(self) -> int:
        """Advance the one-byte request tag, wrapping after 255 like the FPGA."""
        self._tag = (self._tag + 1) & 0xFF
        return self._tag

    def request(self, op: int, payload: bytes, timeout: float | None = None) -> bytes:
        """Send a command and return its reply data after checking the status.

        Corrupt or stale replies are skipped. A transport receive timeout
        starts a fresh attempt; the new tag distinguishes it from old replies.
        An error status is a completed command and is raised immediately.
        """
        for _ in range(self.retries + 1):
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
        raise DeviceTimeout(f"no response to op 0x{op:02X} after {self.retries + 1} attempts")

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

    def run(self, kernel: int, args: bytes = b"", timeout: float = 5.0) -> RunResult:
        """Wait for a kernel reply; kernels get a longer timeout than link commands."""
        return RunResult(*P.parse_run(self.request(*P.run(kernel, args), timeout=timeout)))

    def nop(self) -> RunResult:
        """Run the no-operation kernel to check dispatch and cycle reporting."""
        return self.run(Kernel.NOP)

    def fill(self, base: int, count: int, value: int) -> RunResult:
        """Write value to count words beginning at base, then return kernel time."""
        return self.run(Kernel.FILL, struct.pack("<III", base, count, value))

    def fpu_kernel(self, fop: int, count: int, a: int, b: int, r: int, f: int,
                   timeout: float = 10.0) -> RunResult:
        """Run r[i], f[i] = fop(a[i], b[i]) over word arrays already in the scratchpad.

        a, b, r and f are word addresses. RunResult.aux holds the OR of all flags.
        """
        return self.run(Kernel.FPU, P.fpu_args(fop, count, a, b, r, f), timeout=timeout)

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
        return Device(server.transport(), **kw)
    if port.startswith("udp:"):
        host, _, num = port[4:].partition(":")
        return Device(UdpTransport(host, int(num) if num else spec.UDP_PORT), **kw)
    return Device(UartTransport(port), **kw)
