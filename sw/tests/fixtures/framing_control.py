"""Turn packet bytes into a stream that can safely use zero as its delimiter.

I keep the two operations here separate: CRC detects changed bytes, while
COBS removes zeros inside a packet so UART can use a zero to mark its end.
The matching hardware is cobs_encoder/cobs_decoder and util_pkg.crc16_update.
These helpers do not know about command IDs, addresses, or serial ports.
"""

from __future__ import annotations


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """Update the 16-bit checksum with each input byte, starting at 0xFFFF.

    A caller can supply the previous CRC to continue across several chunks.
    The packet header and payload are covered; the CRC field is excluded.
    """
    for byte in data:
        # Mix this byte into the high end, then shift through its eight bits.
        # The polynomial XOR corresponds to the feedback wiring of a CRC.
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF  # Python integers grow; hardware keeps only 16 bits.
    return crc


class CobsError(ValueError):
    """The encoded bytes cannot describe a complete, zero-free COBS frame."""


def cobs_encode(data: bytes) -> bytes:
    """Encode blocks of nonzero bytes; the transport adds the final delimiter.

    A code byte counts itself plus the following data bytes. A raw zero ends
    a short block, so its position can be recovered without sending that zero.
    """
    out = bytearray()
    block = bytearray()
    for byte in data:
        if byte == 0:
            # The block's code replaces this raw zero. The next block starts
            # immediately, including an empty block for consecutive zeros.
            out.append(len(block) + 1)
            out += block
            block.clear()
        else:
            block.append(byte)
            if len(block) == 254:
                # Code 255 means 254 data bytes with no implied zero after
                # them. Longer nonzero runs continue in a fresh block.
                out.append(255)
                out += block
                block.clear()
    # Always close the last block, even if a zero or a full block left it empty.
    out.append(len(block) + 1)
    out += block
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Restore raw bytes from one encoded frame with its delimiter removed."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise CobsError("zero byte inside COBS frame")
        i += 1
        end = i + code - 1
        # A code may not ask for more bytes than the frame actually contains.
        if end > n:
            raise CobsError("COBS block overruns frame")
        block = data[i:end]
        if 0 in block:
            raise CobsError("zero byte inside COBS block")
        out += block
        i = end
        if code != 255 and i < n:
            # A short block implies a zero only between blocks. The final
            # block does not add a trailing raw zero of its own.
            out.append(0)
    return bytes(out)
