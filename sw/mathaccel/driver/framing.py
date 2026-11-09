"""Turn packet bytes into a stream that can safely use zero as its delimiter.

I keep the two operations here separate: CRC detects changed bytes, while
COBS removes zeros inside a packet so UART can use a zero to mark its end.
The matching hardware is cobs_encoder/cobs_decoder and util_pkg.crc16_update.
These helpers do not know about command IDs, addresses, or serial ports.
"""

from __future__ import annotations

import os
from binascii import crc_hqx


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """Update the 16-bit checksum with each input byte, starting at 0xFFFF.

    A caller can supply the previous CRC to continue across several chunks.
    The packet header and payload are covered; the CRC field is excluded.
    """
    return crc_hqx(data, crc & 0xFFFF) if data else crc


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


# All three experiments carry this exact selector. Native code is optional;
# request it explicitly to make a missing build fail rather than skew a run.
BACKEND = "python"
_python_crc16 = crc16
_python_cobs_encode = cobs_encode
_python_cobs_decode = cobs_decode


def _select_backend():
    global BACKEND, crc16, cobs_encode, cobs_decode
    selection = os.environ.get("MATHACCEL_FRAMING", "auto")
    if selection not in ("auto", "python", "native"):
        raise ValueError("MATHACCEL_FRAMING must be auto, python or native")
    if selection == "python":
        return
    try:
        from mathaccel import _framing_native as native
    except ImportError:
        if selection == "native":
            raise
        return

    def native_crc(data: bytes, crc: int = 0xFFFF) -> int:
        # Preserve the original empty-input and arbitrary-integer seed behavior.
        return native.crc16(bytes(data), crc & 0xFFFF) if data else crc

    def native_encode(data: bytes) -> bytes:
        return native.cobs_encode(bytes(data))

    def native_decode(data: bytes) -> bytes:
        try:
            return native.cobs_decode(bytes(data))
        except ValueError as e:
            raise CobsError(str(e)) from e

    crc16 = native_crc
    cobs_encode = native_encode
    cobs_decode = native_decode
    BACKEND = native.backend



_select_backend()
