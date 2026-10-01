import random

import pytest

from mathaccel.driver.framing import CobsError, cobs_decode, cobs_encode, crc16
from mathaccel.driver.protocol import Frame, FrameError


def test_crc16_check_value():
    # Standard check value for CRC-16/CCITT-FALSE.
    assert crc16(b"123456789") == 0x29B1


@pytest.mark.parametrize("data", [
    b"", b"\x00", b"\x00\x00", b"\x11\x22\x00\x33", bytes(range(1, 255)),
    bytes(range(1, 256)), bytes(254) , b"\x01" * 254, b"\x01" * 255, b"\x01" * 600,
])
def test_cobs_roundtrip_edge_cases(data):
    enc = cobs_encode(data)
    assert 0 not in enc
    assert cobs_decode(enc) == data


def test_cobs_roundtrip_random():
    rng = random.Random(0)
    for _ in range(2000):
        n = rng.randint(0, 1100)
        data = bytes(rng.choice([0, rng.randint(1, 255)]) for _ in range(n))
        assert cobs_decode(cobs_encode(data)) == data


def test_cobs_known_vector():
    # From the COBS paper / Wikipedia examples.
    assert cobs_encode(b"\x11\x22\x00\x33") == b"\x03\x11\x22\x02\x33"
    assert cobs_encode(b"\x00") == b"\x01\x01"


def test_cobs_rejects_overrun():
    with pytest.raises(CobsError):
        cobs_decode(b"\x05\x11")


def test_frame_roundtrip_and_crc_detection():
    f = Frame(0x02, 7, bytes(range(40)))
    wire = f.encode()
    assert wire.endswith(b"\x00") and 0 not in wire[:-1]
    assert Frame.decode(wire[:-1]) == f
    raw = bytearray(f.to_raw())
    raw[5] ^= 0x01
    with pytest.raises(FrameError, match="CRC"):
        Frame.from_raw(bytes(raw))
