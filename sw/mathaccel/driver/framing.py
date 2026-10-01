"""COBS framing and CRC-16/CCITT-FALSE, matching hw/rtl/io/{cobs_*,crc16}.sv."""

from __future__ import annotations


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


class CobsError(ValueError):
    pass


def cobs_encode(data: bytes) -> bytes:
    """COBS-encode `data`. The result contains no zero bytes (delimiter not appended)."""
    out = bytearray()
    block = bytearray()
    for byte in data:
        if byte == 0:
            out.append(len(block) + 1)
            out += block
            block.clear()
        else:
            block.append(byte)
            if len(block) == 254:
                out.append(255)
                out += block
                block.clear()
    out.append(len(block) + 1)
    out += block
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise CobsError("zero byte inside COBS frame")
        i += 1
        end = i + code - 1
        if end > n:
            raise CobsError("COBS block overruns frame")
        block = data[i:end]
        if 0 in block:
            raise CobsError("zero byte inside COBS block")
        out += block
        i = end
        if code != 255 and i < n:
            out.append(0)
    return bytes(out)
