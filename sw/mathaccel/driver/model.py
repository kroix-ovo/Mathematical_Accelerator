"""Reference model of the FPGA command processor.

Implements docs/protocol.md at the frame level, including the kernel cycle
counts, so it can stand in for the board (SimTransport) and act as the golden
model in the RTL testbenches.

I model command results and reported cycles here, not the clock-by-clock HDL
implementation. Persistent memory lets a write vector affect a later read.
Tests still need independent expectations for the protocol and math rules;
matching this model alone cannot establish that the model itself is correct.
"""

from __future__ import annotations

import struct

import numpy as np

from mathaccel._generated import spec
from mathaccel._generated.spec import Kernel, Op, Status
from mathaccel.driver.protocol import RESP_BIT, Frame, FrameError


class DeviceModel:
    """A scratchpad and command handlers with the FPGA's phase 1 behavior."""

    def __init__(self) -> None:
        # Like configured FPGA block RAM, a new model starts with zero words.
        # uint32 stores word bits regardless of how a caller interprets them.
        self.mem = np.zeros(spec.SCRATCH_WORDS, dtype=np.uint32)
        self.crc_errors = 0
        self.frame_errors = 0
        self.last_cycles = 0

    # Validate link packets before any command is allowed to touch memory.

    def handle_wire(self, wire: bytes) -> bytes | None:
        """Process one COBS frame (no delimiter). Returns response wire bytes or None."""
        if not wire:
            return None  # back-to-back delimiters: ignored, not an error
        try:
            req = Frame.decode(wire)
        except FrameError as e:
            # Masking preserves the HDL's 16-bit wrapping counter behavior.
            if "CRC" in str(e):
                self.crc_errors = (self.crc_errors + 1) & 0xFFFF
            else:
                self.frame_errors = (self.frame_errors + 1) & 0xFFFF
            return None
        status, data = self.execute(req.op, req.payload)
        # Every reply begins with a status byte and echoes the request tag.
        return Frame(req.op | RESP_BIT, req.tag, bytes([status]) + data).encode()

    # Command handlers see payload bytes after framing/CRC validation.

    def execute(self, op: int, p: bytes) -> tuple[int, bytes]:
        """Select a command handler and return (status, data after status)."""
        handler = {
            Op.PING: self._ping,
            Op.WRITE_MEM: self._write_mem,
            Op.READ_MEM: self._read_mem,
            Op.RUN: self._run,
            Op.STATUS: self._status,
        }.get(op)
        if handler is None:
            return Status.BAD_OP, b""
        return handler(p)

    def _in_range(self, addr: int, count: int) -> bool:
        """Check the exclusive end of a span against the number of memory words."""
        # Wire decoding supplies unsigned arguments. Python addition cannot
        # wrap like a 32-bit hardware adder, so oversized spans stay oversized.
        return addr + count <= spec.SCRATCH_WORDS

    def _ping(self, p: bytes) -> tuple[int, bytes]:
        """Return configuration only for an argument-free PING request."""
        if p:
            return Status.BAD_LEN, b""
        return Status.OK, struct.pack("<IBBBBI", spec.MAGIC, spec.VERSION_MAJOR,
                                      spec.VERSION_MINOR, spec.SCRATCH_ADDR_BITS, 0, spec.CLK_HZ)

    def _write_mem(self, p: bytes) -> tuple[int, bytes]:
        """Decode the address and whole words, then update just that memory span."""
        # At least one word follows the four-byte address. Any partial word
        # rejects the command before a memory write occurs.
        if len(p) < 8 or len(p) % 4:
            return Status.BAD_LEN, b""
        (addr,) = struct.unpack_from("<I", p)
        words = np.frombuffer(p[4:], dtype="<u4")
        if not self._in_range(addr, len(words)):
            return Status.BAD_ADDR, b""
        self.mem[addr:addr + len(words)] = words
        return Status.OK, b""

    def _read_mem(self, p: bytes) -> tuple[int, bytes]:
        """Return a legal word span in wire byte order after checking its bounds."""
        if len(p) != 6:
            return Status.BAD_LEN, b""
        addr, count = struct.unpack("<IH", p)
        if not 1 <= count <= 255:
            return Status.BAD_LEN, b""
        if not self._in_range(addr, count):
            return Status.BAD_ADDR, b""
        return Status.OK, self.mem[addr:addr + count].astype("<u4").tobytes()

    def _status(self, p: bytes) -> tuple[int, bytes]:
        """Read error counters and last_cycles; STATUS does not clear them."""
        if p:
            return Status.BAD_LEN, b""
        return Status.OK, struct.pack("<HHI", self.crc_errors, self.frame_errors, self.last_cycles)

    def _run(self, p: bytes) -> tuple[int, bytes]:
        """Apply a supported kernel and report the busy cycles defined for the RTL."""
        if not p:
            return Status.BAD_LEN, b""
        kernel, args = p[0], p[1:]
        if kernel == Kernel.NOP:
            if args:
                return Status.BAD_LEN, b""
            cycles = 1
        elif kernel == Kernel.FILL:
            if len(args) != 12:
                return Status.BAD_LEN, b""
            base, count, value = struct.unpack("<III", args)
            if not self._in_range(base, count):
                return Status.BAD_ADDR, b""
            self.mem[base:base + count] = value
            # The RTL takes one write clock per word, then one FIN clock.
            # An empty fill still takes the single completion clock.
            cycles = count + 1
        else:
            return Status.BAD_KERNEL, b""
        self.last_cycles = cycles
        return Status.OK, struct.pack("<I", cycles)
