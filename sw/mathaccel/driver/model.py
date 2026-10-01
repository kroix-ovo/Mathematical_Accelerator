"""Reference model of the FPGA command processor.

Implements docs/protocol.md at the frame level, including the kernel cycle
counts, so it can stand in for the board (SimTransport) and act as the golden
model in the RTL testbenches.
"""

from __future__ import annotations

import struct

import numpy as np

from mathaccel._generated import spec
from mathaccel._generated.spec import Kernel, Op, Status
from mathaccel.driver.protocol import RESP_BIT, Frame, FrameError


class DeviceModel:
    def __init__(self) -> None:
        self.mem = np.zeros(spec.SCRATCH_WORDS, dtype=np.uint32)
        self.crc_errors = 0
        self.frame_errors = 0
        self.last_cycles = 0

    # -- frame level --------------------------------------------------------

    def handle_wire(self, wire: bytes) -> bytes | None:
        """Process one COBS frame (no delimiter). Returns response wire bytes or None."""
        if not wire:
            return None  # back-to-back delimiters: ignored, not an error
        try:
            req = Frame.decode(wire)
        except FrameError as e:
            if "CRC" in str(e):
                self.crc_errors = (self.crc_errors + 1) & 0xFFFF
            else:
                self.frame_errors = (self.frame_errors + 1) & 0xFFFF
            return None
        status, data = self.execute(req.op, req.payload)
        return Frame(req.op | RESP_BIT, req.tag, bytes([status]) + data).encode()

    # -- command level ------------------------------------------------------

    def execute(self, op: int, p: bytes) -> tuple[int, bytes]:
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
        return addr + count <= spec.SCRATCH_WORDS

    def _ping(self, p: bytes) -> tuple[int, bytes]:
        if p:
            return Status.BAD_LEN, b""
        return Status.OK, struct.pack("<IBBBBI", spec.MAGIC, spec.VERSION_MAJOR,
                                      spec.VERSION_MINOR, spec.SCRATCH_ADDR_BITS, 0, spec.CLK_HZ)

    def _write_mem(self, p: bytes) -> tuple[int, bytes]:
        if len(p) < 8 or len(p) % 4:
            return Status.BAD_LEN, b""
        (addr,) = struct.unpack_from("<I", p)
        words = np.frombuffer(p[4:], dtype="<u4")
        if not self._in_range(addr, len(words)):
            return Status.BAD_ADDR, b""
        self.mem[addr:addr + len(words)] = words
        return Status.OK, b""

    def _read_mem(self, p: bytes) -> tuple[int, bytes]:
        if len(p) != 6:
            return Status.BAD_LEN, b""
        addr, count = struct.unpack("<IH", p)
        if not 1 <= count <= 255:
            return Status.BAD_LEN, b""
        if not self._in_range(addr, count):
            return Status.BAD_ADDR, b""
        return Status.OK, self.mem[addr:addr + count].astype("<u4").tobytes()

    def _status(self, p: bytes) -> tuple[int, bytes]:
        if p:
            return Status.BAD_LEN, b""
        return Status.OK, struct.pack("<HHI", self.crc_errors, self.frame_errors, self.last_cycles)

    def _run(self, p: bytes) -> tuple[int, bytes]:
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
            cycles = count + 1
        else:
            return Status.BAD_KERNEL, b""
        self.last_cycles = cycles
        return Status.OK, struct.pack("<I", cycles)
