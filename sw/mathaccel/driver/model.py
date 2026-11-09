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

from mathaccel import fp32ref
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp, Kernel, Op, Status
from mathaccel.driver.protocol import RESP_BIT, Frame, FrameError


class DeviceModel:
    """Scratchpad, command responses and kernel timing through phase 4.

    The EVU kernel's clock count isn't predicted exactly (see cycle_bounds in
    evu_model.py), so a reply from it reports the low bound, and evu_bounds keeps
    both bounds for a scoreboard to check the real count against.
    """

    def __init__(self) -> None:
        # Like configured FPGA block RAM, a new model starts with zero words.
        # uint32 stores word bits regardless of how a caller interprets them.
        self.mem = np.zeros(spec.SCRATCH_WORDS, dtype=np.uint32)
        self.crc_errors = 0
        self.frame_errors = 0
        self.last_cycles = 0
        self.evu_bounds: tuple[int, int] | None = None

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
        """Apply a supported kernel; reply with busy cycles and the aux word."""
        if not p:
            return Status.BAD_LEN, b""
        kernel, args = p[0], p[1:]
        aux = 0
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
        elif kernel == Kernel.FPU:
            # Same check order as cmd_proc.sv: length, op code, then ranges.
            if len(args) != 21:
                return Status.BAD_LEN, b""
            fop, count, a, b, r, f = struct.unpack("<BIIIII", args)
            if fop >= spec.FPU_OP_COUNT:
                return Status.BAD_ARG, b""
            if not all(self._in_range(base, count) for base in (a, b, r, f)):
                return Status.BAD_ADDR, b""
            # Read every operand before writing any result. The hardware reads
            # a[i], b[i] before r[i], f[i] are written, so identical or
            # disjoint arrays give the same answer either way; partially
            # overlapping arrays are documented as undefined.
            av = self.mem[a:a + count].copy()
            bv = self.mem[b:b + count].copy()
            for k in range(count):
                res, fl = fpu_evaluate(fop, int(av[k]), int(bv[k]))
                self.mem[r + k] = res
                self.mem[f + k] = fl
                aux |= fl
            cycles = fpu_kernel_cycles(fop, count)
        elif kernel == Kernel.EVU:
            status, cycles, aux = self._run_evu(args)
            if status != Status.OK:
                return status, b""
        else:
            return Status.BAD_KERNEL, b""
        self.last_cycles = cycles
        return Status.OK, struct.pack("<II", cycles, aux)


    # I validate before executing or updating memory, just as the EVU does.
    # The simulator reports a lower cycle bound; it is a numerical oracle,
    # not a clock-accurate replacement for the ready/retry queues in RTL.
    def _run_evu(self, args: bytes) -> tuple[int, int, int]:
        """Check and run a descriptor. Returns (status, cycles, aux)."""
        # Imported here because evu_model imports this module.
        from mathaccel import evu_model
        from mathaccel.compiler import evu_isa

        if len(args) != 4:
            return Status.BAD_LEN, 0, 0
        (desc,) = struct.unpack("<I", args)
        if not self._in_range(desc, spec.EVU_DESC_WORDS):
            return Status.BAD_ADDR, 0, 0

        def read(address: int) -> int:
            return int(self.mem[address])

        status = evu_isa.check_descriptor(read, desc)
        if status != Status.OK:
            return status, 0, 0
        run, r, f = evu_isa.read_descriptor(read, desc)
        result = evu_model.execute(run)
        if run.reduce:
            self.mem[r] = result.sum
            self.mem[f] = result.sum_flags
        else:
            self.mem[r:r + run.points] = result.results
            self.mem[f:f + run.points] = result.flags
        self.evu_bounds = evu_model.cycle_bounds(run)
        return Status.OK, self.evu_bounds[0], result.aux


CORDIC_OPS = (FpuOp.SIN, FpuOp.COS, FpuOp.ATAN2, FpuOp.HYPOT, FpuOp.EXP, FpuOp.LN)


def fpu_evaluate(fop: int, a: int, b: int = 0) -> tuple[int, int]:
    """What the FPU returns for one element: (result_bits, flags).

    Ops 0-11 follow the exact IEEE rules in fp32ref. The CORDIC ops 12-17
    are not correctly rounded, so for those I use the bit-accurate model of
    fp_cordic.sv instead, which matches the hardware exactly (its accuracy
    against mpmath is measured separately by tools/measure_ulp.py). It is
    imported only when needed, because building its tables takes a moment.
    """
    if fop in CORDIC_OPS:
        from mathaccel import cordic_model
        return cordic_model.evaluate(fop, a, b)
    return fp32ref.evaluate(fop, a, b)


# Clock counts of the FPU kernel, measured from the RTL (kernel_engine.sv)
# and checked against it by the xsim/cocotb vector tests. Each element costs
# FETCH clocks (RA, RB, RC, ISSUE) when the FPU is ready at once; divide and
# square root are limited by their own unit's period instead. The CORDIC ops
# issue at the same rate as the 6-clock ops and only have a longer tail: the
# FPU returns them 54 clocks after issue instead of 7.
FPU_FETCH = 4          # RA, RB, RC, ISSUE
FPU_PIPE_TAIL = 9      # last issue -> 7-clock FPU latency -> flag write -> FIN
CORDIC_TAIL = FPU_PIPE_TAIL + (54 - 7)
# Division and square root are separate units (fp_div, fp_sqrt), so each has
# its own three numbers:
#   LANES     how many divides (or roots) the unit can hold at once in
#             independent copies; 1 for an ordinary unit
#   INTERVAL  the fewest clocks from one request a lane accepts to the next
#             one it can accept: 33 for the baseline (capture, prep, 27
#             iterate, pack, 2 round, output), 1 for a fully pipelined unit
#   LAST      FPU kernel clocks from accepting the last element to the end of
#             the run (its result, the flag write and FIN)
# The FPU kernel offers an element at most every FPU_FETCH clocks and waits in
# ISSUE when no lane is free, so element k is accepted at
#   a[k] = max(a[k-1] + FPU_FETCH, a[k-LANES] + INTERVAL),   a[0] = 0,
# and the run lasts a[count-1] + LAST. For the baseline that is 33 * count + 6.
# INTERVAL / LANES also bounds an EVU run from below (evu_model.cycle_bounds).
# Every divider experiment changes the DIV_ numbers here, in
# hw/tb/uvm/dpi/evu_ref.c and in hw/tb/uvm/top/top_scoreboard.svh together.
DIV_LANES = 1
DIV_INTERVAL = 33
DIV_LAST = 39
SQRT_LANES = 1
SQRT_INTERVAL = 33
SQRT_LAST = 39


def unit_kernel_cycles(count: int, lanes: int, interval: int, last: int) -> int:
    """FPU kernel clocks for count elements on a unit with the given lanes and interval."""
    if lanes == 1:
        return max(FPU_FETCH, interval) * (count - 1) + last
    accepted = [0]
    for k in range(1, count):
        free = accepted[k - lanes] + interval if k >= lanes else 0
        accepted.append(max(accepted[k - 1] + FPU_FETCH, free))
    return accepted[-1] + last


def fpu_kernel_cycles(fop: int, count: int) -> int:
    """Predict reported clocks from the fetch cadence, unit latency and final write-back."""
    if count == 0:
        return 1
    if fop == FpuOp.DIV:
        return unit_kernel_cycles(count, DIV_LANES, DIV_INTERVAL, DIV_LAST)
    if fop == FpuOp.SQRT:
        return unit_kernel_cycles(count, SQRT_LANES, SQRT_INTERVAL, SQRT_LAST)
    if fop in CORDIC_OPS:
        return FPU_FETCH * count + CORDIC_TAIL
    return FPU_FETCH * count + FPU_PIPE_TAIL
