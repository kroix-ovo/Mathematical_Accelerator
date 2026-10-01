"""Move encoded packets through either a serial cable or the Python model.

I keep byte movement separate from command interpretation: a transport knows
where a packet ends, while protocol.py checks its contents. Sends include the
zero delimiter; receives strip it so both backends present the same interface.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from mathaccel._generated.spec import BAUD
from mathaccel.driver.model import DeviceModel


class Transport(ABC):
    """The small interface Device needs, independent of a physical serial port."""
    @abstractmethod
    def send(self, wire: bytes) -> None:
        """Send one encoded frame (including the 0x00 delimiter)."""

    @abstractmethod
    def recv_frame(self, timeout: float) -> bytes | None:
        """Return the next COBS frame without its delimiter, or None on timeout."""

    def close(self) -> None:
        """Default cleanup for backends, such as the model, with no open handle."""
        pass

    def __enter__(self):
        """Return the transport when used in a with block."""
        return self

    def __exit__(self, *exc):
        """Run backend cleanup even if the with block raises an exception."""
        self.close()


class SimTransport(Transport):
    """Route frames into the reference model and queue the generated replies.

    This is a functional simulator: it has no clock, UART bit timing, or HDL.
    Those details are exercised separately by the RTL testbenches.
    """

    def __init__(self, model: DeviceModel | None = None) -> None:
        # Supplying a model lets tests inspect the same memory/error counters
        # that requests through this transport will change.
        self.model = model or DeviceModel()
        self._pending: list[bytes] = []

    def send(self, wire: bytes) -> None:
        """Process complete zero-terminated frames and enqueue nonempty replies."""
        # The final split item is after the last delimiter, so it is excluded.
        # Unlike UartTransport, this backend expects complete frames per send.
        for chunk in wire.split(b"\x00")[:-1]:
            if chunk:
                resp = self.model.handle_wire(chunk)
                if resp is not None:
                    # The model returns a delimiter; Device expects it stripped.
                    self._pending.append(resp[:-1])

    def recv_frame(self, timeout: float) -> bytes | None:
        """Return the oldest reply immediately; the model does not emulate waiting."""
        return self._pending.pop(0) if self._pending else None


class UartTransport(Transport):
    """Collect serial chunks until a zero delimiter completes one wire frame."""

    def __init__(self, port: str, baud: int = BAUD) -> None:
        import serial  # imported lazily so the sim path has no pyserial dependency

        # A short serial read timeout lets recv_frame regularly check its
        # overall deadline, even when the board sends no bytes.
        self.ser = serial.Serial(port, baud, timeout=0.01)
        self.ser.reset_input_buffer()
        self._buf = bytearray()

    def send(self, wire: bytes) -> None:
        """Write bytes already framed by Device; serial handles bit transmission."""
        self.ser.write(wire)

    def recv_frame(self, timeout: float) -> bytes | None:
        """Read one complete packet, keeping partial and later packets buffered."""
        # monotonic time is unaffected by changes to the wall clock.
        deadline = time.monotonic() + timeout
        while True:
            idx = self._buf.find(0)
            if idx >= 0:
                # Consume this delimiter along with its frame, leaving any
                # following packet bytes ready for the next recv_frame call.
                frame = bytes(self._buf[:idx])
                del self._buf[:idx + 1]
                if frame:
                    return frame
                # Empty frames are harmless consecutive delimiters.
                continue
            if time.monotonic() > deadline:
                return None
            # A serial read is a chunk of bytes, not necessarily a whole frame.
            self._buf += self.ser.read(max(1, self.ser.in_waiting))

    def close(self) -> None:
        """Release the serial handle so another program can open the port."""
        self.ser.close()
