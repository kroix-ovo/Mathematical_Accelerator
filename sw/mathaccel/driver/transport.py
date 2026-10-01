"""Byte transports. A transport moves whole COBS frames; framing/CRC live in protocol.py."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from mathaccel._generated.spec import BAUD
from mathaccel.driver.model import DeviceModel


class Transport(ABC):
    @abstractmethod
    def send(self, wire: bytes) -> None:
        """Send one encoded frame (including the 0x00 delimiter)."""

    @abstractmethod
    def recv_frame(self, timeout: float) -> bytes | None:
        """Return the next COBS frame without its delimiter, or None on timeout."""

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class SimTransport(Transport):
    """Routes frames into the Python reference model instead of hardware."""

    def __init__(self, model: DeviceModel | None = None) -> None:
        self.model = model or DeviceModel()
        self._pending: list[bytes] = []

    def send(self, wire: bytes) -> None:
        for chunk in wire.split(b"\x00")[:-1]:
            if chunk:
                resp = self.model.handle_wire(chunk)
                if resp is not None:
                    self._pending.append(resp[:-1])

    def recv_frame(self, timeout: float) -> bytes | None:
        return self._pending.pop(0) if self._pending else None


class UartTransport(Transport):
    def __init__(self, port: str, baud: int = BAUD) -> None:
        import serial  # imported lazily so the sim path has no pyserial dependency

        self.ser = serial.Serial(port, baud, timeout=0.01)
        self.ser.reset_input_buffer()
        self._buf = bytearray()

    def send(self, wire: bytes) -> None:
        self.ser.write(wire)

    def recv_frame(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while True:
            idx = self._buf.find(0)
            if idx >= 0:
                frame = bytes(self._buf[:idx])
                del self._buf[:idx + 1]
                if frame:
                    return frame
                continue
            if time.monotonic() > deadline:
                return None
            self._buf += self.ser.read(max(1, self.ser.in_waiting))

    def close(self) -> None:
        self.ser.close()
