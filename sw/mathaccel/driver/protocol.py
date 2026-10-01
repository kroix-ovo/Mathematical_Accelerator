"""Describe the packet shared by the Python driver and the FPGA.

I keep byte layouts here so callers can ask for commands without assembling
UART packets themselves. Frame adds a header, CRC, and COBS encoding; the
small builders below it only assemble command payloads. See docs/protocol.md
for the same layouts as tables. No serial I/O happens in this module.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from mathaccel._generated.spec import MAX_PAYLOAD, Op, Status
from mathaccel.driver.framing import CobsError, cobs_decode, cobs_encode, crc16

RESP_BIT = 0x80  # Replies echo the request opcode with its high bit set.
# '<' means little-endian, B is one byte, and H is a two-byte unsigned value.
HEADER = struct.Struct("<BBH")  # opcode, request tag, payload byte count
MIN_RAW = HEADER.size + 2  # Even an empty payload has a header and 16-bit CRC.
MAX_WORDS_PER_FRAME = 255  # Address/status overhead leaves room for 255 words.


class FrameError(ValueError):
    """Frame failed COBS, length or CRC checks."""


class DeviceError(RuntimeError):
    """Device returned a non-OK status."""

    def __init__(self, op: int, status: int):
        # Retain the numeric values for programmatic checks, but show names
        # in error messages when they exist in the generated specification.
        op_name = Op(op).name if op in Op._value2member_map_ else f"op 0x{op:02X}"
        st_name = Status(status).name if status in Status._value2member_map_ else f"0x{status:02X}"
        super().__init__(f"{op_name} failed: {st_name}")
        self.op = op
        self.status = status


@dataclass(frozen=True)
class Frame:
    """One immutable packet before wire encoding, with bytes as its payload.

    The same representation is used for requests and replies. status and data
    interpret the reply payload; they are not fields of a request header.
    """
    op: int
    tag: int
    payload: bytes = b""

    def to_raw(self) -> bytes:
        """Build header | payload | CRC, before COBS removes packet zeros."""
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(f"payload {len(self.payload)} > {MAX_PAYLOAD}")
        body = HEADER.pack(self.op, self.tag, len(self.payload)) + self.payload
        return body + struct.pack("<H", crc16(body))

    def encode(self) -> bytes:
        """Wire bytes including the trailing 0x00 delimiter."""
        return cobs_encode(self.to_raw()) + b"\x00"

    @classmethod
    def from_raw(cls, raw: bytes) -> Frame:
        """Check size and CRC before exposing the packet's payload to callers."""
        if len(raw) < MIN_RAW:
            raise FrameError("frame too short")
        op, tag, length = HEADER.unpack_from(raw)
        if length > MAX_PAYLOAD or len(raw) != MIN_RAW + length:
            raise FrameError("length field mismatch")
        # The last two bytes store the sender's CRC. Recompute it over all
        # earlier bytes, never over the checksum itself.
        (crc,) = struct.unpack_from("<H", raw, len(raw) - 2)
        if crc != crc16(raw[:-2]):
            raise FrameError("bad CRC")
        return cls(op, tag, bytes(raw[HEADER.size:-2]))

    @classmethod
    def decode(cls, wire: bytes) -> Frame:
        """Decode one COBS frame (delimiter already stripped)."""
        try:
            return cls.from_raw(cobs_decode(wire))
        except CobsError as e:
            # Present one packet-error type to the driver regardless of which
            # layer rejected the bytes. Preserve the original cause as well.
            raise FrameError(str(e)) from e

    @property
    def status(self) -> int:
        """Read the first reply byte; -1 makes a missing status fail validation."""
        return self.payload[0] if self.payload else -1

    @property
    def data(self) -> bytes:
        """Return reply data after its status byte for the command parsers."""
        return self.payload[1:]


# Request builders return (opcode, payload), ready for Device.request(*...).
# Addresses/counts are in 32-bit words, but payload lengths count bytes.

def ping() -> tuple[int, bytes]:
    """Ask for device identity and configuration without sending arguments."""
    return Op.PING, b""


def write_mem(addr: int, words: list[int] | bytes) -> tuple[int, bytes]:
    """Place a word address before little-endian data; callers split long writes."""
    data = words if isinstance(words, bytes) else struct.pack(f"<{len(words)}I", *words)
    return Op.WRITE_MEM, struct.pack("<I", addr) + data


def read_mem(addr: int, count: int) -> tuple[int, bytes]:
    """Request count words from a 32-bit address, with a 16-bit count field."""
    return Op.READ_MEM, struct.pack("<IH", addr, count)


def run(kernel: int, args: bytes = b"") -> tuple[int, bytes]:
    """Put the kernel ID first, followed by that kernel's argument bytes."""
    return Op.RUN, bytes([kernel]) + args


def status() -> tuple[int, bytes]:
    """Ask for link error counters and the most recent kernel cycle count."""
    return Op.STATUS, b""


# These parsers receive data after Device has checked the reply's status.
# struct.unpack requires exactly the expected number of bytes.

@dataclass(frozen=True)
class PingInfo:
    """Identity and configuration returned by a successful PING command."""
    magic: int
    version: tuple[int, int]
    scratch_addr_bits: int
    clk_hz: int


@dataclass(frozen=True)
class StatusInfo:
    """Link error counts and the last kernel duration, as reported by the device."""
    crc_errors: int
    frame_errors: int
    last_cycles: int


def parse_ping(data: bytes) -> PingInfo:
    """Unpack identity, version bytes, address width, reserved byte, and clock."""
    magic, vmaj, vmin, aw, _rsvd, clk = struct.unpack("<IBBBBI", data)
    return PingInfo(magic, (vmaj, vmin), aw, clk)


def parse_status(data: bytes) -> StatusInfo:
    """Unpack two 16-bit counters followed by one 32-bit cycle count."""
    return StatusInfo(*struct.unpack("<HHI", data))


def parse_cycles(data: bytes) -> int:
    """Read the 32-bit busy-cycle count returned by RUN."""
    return struct.unpack("<I", data)[0]
