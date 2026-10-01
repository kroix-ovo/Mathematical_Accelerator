"""Check wire encoding separately from the driver and FPGA command behavior.

I use known byte patterns as well as round trips: an encoder and decoder could
share the same mistake and still recover their own data. These tests exercise
zeros, full COBS blocks, checksum corruption, and invalid encoded lengths.
"""

import random

import pytest

from mathaccel.driver.framing import CobsError, cobs_decode, cobs_encode, crc16
from mathaccel.driver.protocol import Frame, FrameError


def test_crc16_check_value():
    """Check the CRC polynomial/settings against a fixed expected checksum."""
    # Standard check value for CRC-16/CCITT-FALSE.
    assert crc16(b"123456789") == 0x29B1


@pytest.mark.parametrize("data", [
    b"", b"\x00", b"\x00\x00", b"\x11\x22\x00\x33", bytes(range(1, 255)),
    bytes(range(1, 256)), bytes(254) , b"\x01" * 254, b"\x01" * 255, b"\x01" * 600,
])
def test_cobs_roundtrip_edge_cases(data):
    """Exercise empty/zero data and the 254-byte boundary that splits blocks."""
    enc = cobs_encode(data)
    # Only a transport delimiter may be zero on the wire, never encoded data.
    assert 0 not in enc
    assert cobs_decode(enc) == data


def test_cobs_roundtrip_random():
    """Check repeatable mixtures of zeros and nonzero bytes across packet sizes."""
    rng = random.Random(0)
    # Bias toward zeros so this covers many short blocks as well as long runs.
    # A fixed seed makes a failing input reproducible on another computer.
    for _ in range(2000):
        n = rng.randint(0, 1100)
        data = bytes(rng.choice([0, rng.randint(1, 255)]) for _ in range(n))
        assert cobs_decode(cobs_encode(data)) == data


def test_cobs_known_vector():
    """Assert exact encoded bytes without depending on our decoder for correctness."""
    # Two nonzero bytes give code 3; the raw zero then starts a code-2 block.
    assert cobs_encode(b"\x11\x22\x00\x33") == b"\x03\x11\x22\x02\x33"
    assert cobs_encode(b"\x00") == b"\x01\x01"


def test_cobs_rejects_overrun():
    """A code claiming four data bytes must fail when only one byte follows."""
    with pytest.raises(CobsError):
        cobs_decode(b"\x05\x11")


def test_frame_roundtrip_and_crc_detection():
    """Check delimiter placement, packet recovery, and a single-bit corruption."""
    f = Frame(0x02, 7, bytes(range(40)))
    wire = f.encode()
    assert wire.endswith(b"\x00") and 0 not in wire[:-1]
    assert Frame.decode(wire[:-1]) == f
    raw = bytearray(f.to_raw())
    # Change payload data without recomputing CRC. This is not malformed COBS,
    # so rejection must specifically come from the raw packet checksum check.
    raw[5] ^= 0x01
    with pytest.raises(FrameError, match="CRC"):
        Frame.from_raw(bytes(raw))
