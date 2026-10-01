"""High-level device API used by the tools and the agent."""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from mathaccel._generated import spec
from mathaccel._generated.spec import Kernel, Status
from mathaccel.driver import protocol as P
from mathaccel.driver.protocol import DeviceError, Frame, FrameError
from mathaccel.driver.transport import SimTransport, Transport, UartTransport


class DeviceTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class RunResult:
    cycles: int

    @property
    def seconds(self) -> float:
        return self.cycles / spec.CLK_HZ


class Device:
    def __init__(self, transport: Transport, timeout: float = 0.5, retries: int = 3) -> None:
        self.t = transport
        self.timeout = timeout
        self.retries = retries
        self._tag = 0

    # -- plumbing -----------------------------------------------------------

    def _next_tag(self) -> int:
        self._tag = (self._tag + 1) & 0xFF
        return self._tag

    def request(self, op: int, payload: bytes, timeout: float | None = None) -> bytes:
        """Send a request, wait for the matching response, return data after the status byte."""
        for _ in range(self.retries + 1):
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
                    raise DeviceError(op, resp.status)
                return resp.data
        raise DeviceTimeout(f"no response to op 0x{op:02X} after {self.retries + 1} attempts")

    # -- commands -----------------------------------------------------------

    def ping(self) -> P.PingInfo:
        info = P.parse_ping(self.request(*P.ping()))
        if info.magic != spec.MAGIC:
            raise RuntimeError(f"bad magic 0x{info.magic:08X}")
        return info

    def status(self) -> P.StatusInfo:
        return P.parse_status(self.request(*P.status()))

    def write_words(self, addr: int, words: np.ndarray | list[int]) -> None:
        data = np.asarray(words, dtype=np.uint32)
        step = P.MAX_WORDS_PER_FRAME
        for i in range(0, len(data), step):
            chunk = data[i:i + step].astype("<u4").tobytes()
            self.request(*P.write_mem(addr + i, chunk))

    def read_words(self, addr: int, count: int) -> np.ndarray:
        out = np.empty(count, dtype=np.uint32)
        step = P.MAX_WORDS_PER_FRAME
        for i in range(0, count, step):
            n = min(step, count - i)
            out[i:i + n] = np.frombuffer(self.request(*P.read_mem(addr + i, n)), dtype="<u4")
        return out

    def write_f32(self, addr: int, values) -> None:
        self.write_words(addr, np.asarray(values, dtype=np.float32).view(np.uint32))

    def read_f32(self, addr: int, count: int) -> np.ndarray:
        return self.read_words(addr, count).view(np.float32)

    def run(self, kernel: int, args: bytes = b"", timeout: float = 5.0) -> RunResult:
        return RunResult(P.parse_cycles(self.request(*P.run(kernel, args), timeout=timeout)))

    def nop(self) -> RunResult:
        return self.run(Kernel.NOP)

    def fill(self, base: int, count: int, value: int) -> RunResult:
        return self.run(Kernel.FILL, struct.pack("<III", base, count, value))

    def close(self) -> None:
        self.t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def connect(port: str | None = None, **kw) -> Device:
    """Open the board on `port` (e.g. "COM5"), or the simulator when port is None or "sim"."""
    if port in (None, "sim"):
        return Device(SimTransport(), **kw)
    return Device(UartTransport(port), **kw)
