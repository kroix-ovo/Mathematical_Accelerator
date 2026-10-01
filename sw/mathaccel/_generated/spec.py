"""AUTO-GENERATED from spec/accel.toml by tools/gen_spec.py -- do not edit.

Device settings and packet IDs shared with hw/rtl/pkg/accel_pkg.sv.
I generate both copies together; edit the TOML and regenerate them.
"""

from enum import IntEnum

# Device identity, version, clock, baud, and packet/memory sizes.
MAGIC = 0x4d414343
VERSION_MAJOR = 0
VERSION_MINOR = 1
CLK_HZ = 100000000
BAUD = 2000000
SCRATCH_ADDR_BITS = 16
MAX_PAYLOAD = 1024
# N address bits select 2**N words, each containing 32 data bits.
SCRATCH_WORDS = 1 << SCRATCH_ADDR_BITS


class Op(IntEnum):
    """Request IDs; replies use these values with bit 7 set."""

    PING = 0x01
    WRITE_MEM = 0x02
    READ_MEM = 0x03
    RUN = 0x04
    STATUS = 0x05


class Status(IntEnum):
    """First reply payload byte; OK is the successful result."""

    OK = 0x00
    BAD_OP = 0x01
    BAD_LEN = 0x02
    BAD_ADDR = 0x03
    BUSY = 0x04
    BAD_KERNEL = 0x05


class Kernel(IntEnum):
    """RUN payload IDs selecting a hardware kernel."""

    NOP = 0x00
    FILL = 0x01
