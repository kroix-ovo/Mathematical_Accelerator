"""AUTO-GENERATED from spec/accel.toml by tools/gen_spec.py -- do not edit."""

from enum import IntEnum

MAGIC = 0x4d414343
VERSION_MAJOR = 0
VERSION_MINOR = 1
CLK_HZ = 100000000
BAUD = 2000000
SCRATCH_ADDR_BITS = 16
MAX_PAYLOAD = 1024
SCRATCH_WORDS = 1 << SCRATCH_ADDR_BITS


class Op(IntEnum):
    PING = 0x01
    WRITE_MEM = 0x02
    READ_MEM = 0x03
    RUN = 0x04
    STATUS = 0x05


class Status(IntEnum):
    OK = 0x00
    BAD_OP = 0x01
    BAD_LEN = 0x02
    BAD_ADDR = 0x03
    BUSY = 0x04
    BAD_KERNEL = 0x05


class Kernel(IntEnum):
    NOP = 0x00
    FILL = 0x01
