"""Protocol test vectors shared by the xsim and cocotb testbenches.

Each vector is (request_wire, expected_response_wire | None). Expected bytes
come from the Python reference model, so the RTL is checked against it
byte-for-byte, including COBS, CRC, status codes and kernel cycle counts.

    python hw/tb/vectors.py build/vectors.txt   # write the xsim vector file
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

from mathaccel._generated import spec
from mathaccel._generated.spec import Kernel, Op
from mathaccel.driver import protocol as P
from mathaccel.driver.model import DeviceModel
from mathaccel.driver.protocol import Frame


def build_vectors(seed: int = 0) -> list[tuple[bytes, bytes | None]]:
    rng = np.random.default_rng(seed)
    model = DeviceModel()
    vecs: list[tuple[bytes, bytes | None]] = []
    tag = 0

    def req(op: int, payload: bytes = b"") -> None:
        nonlocal tag
        tag = (tag + 1) & 0xFF
        wire = Frame(op, tag, payload).encode()
        resp = model.handle_wire(wire[:-1])
        vecs.append((wire, resp))

    def raw(wire: bytes) -> None:
        vecs.append((wire, model.handle_wire(wire[:-1])))

    req(*P.ping())
    req(*P.status())

    data = rng.integers(0, 2**32, 300, dtype=np.uint32)
    req(*P.write_mem(5, data[:255].tobytes()))           # max-size frame
    req(*P.write_mem(260, data[255:].tobytes()))
    req(*P.read_mem(5, 255))
    req(*P.read_mem(255, 50))
    req(*P.write_mem(1000, [0x01010101] * 255))          # long nonzero runs -> 0xFF COBS blocks
    req(*P.read_mem(1000, 255))
    req(*P.write_mem(2000, [0] * 16))                    # all-zero payload
    req(*P.read_mem(2000, 16))
    req(*P.write_mem(spec.SCRATCH_WORDS - 2, [7, 8]))    # top of memory
    req(*P.read_mem(spec.SCRATCH_WORDS - 2, 2))

    req(*P.run(Kernel.NOP))
    req(*P.run(Kernel.FILL, struct.pack("<III", 3000, 37, 0xDEADBEEF)))
    req(*P.read_mem(2999, 39))
    req(*P.run(Kernel.FILL, struct.pack("<III", 0, 0, 1)))  # empty fill
    req(*P.status())

    # error paths
    req(0x33)                                                 # BAD_OP
    req(Op.PING, b"\x00")                                     # BAD_LEN
    req(*P.read_mem(0, 0))                                    # BAD_LEN (count 0)
    req(*P.read_mem(0, 256))                                  # BAD_LEN (count > 255)
    req(*P.read_mem(spec.SCRATCH_WORDS - 1, 2))               # BAD_ADDR
    req(*P.write_mem(0xFFFFFFFF, [1]))                        # BAD_ADDR (wraps)
    req(Op.WRITE_MEM, b"\x00" * 6)                            # BAD_LEN
    req(*P.run(0x7E))                                         # BAD_KERNEL
    req(*P.run(Kernel.FILL, b"\x00" * 4))                     # BAD_LEN
    req(*P.run(Kernel.FILL, struct.pack("<III", spec.SCRATCH_WORDS - 1, 2, 0)))  # BAD_ADDR

    # link errors: no response, counters advance
    good = bytearray(Frame(Op.PING, 0x55).to_raw())
    good[-1] ^= 0x5A
    from mathaccel.driver.framing import cobs_encode
    raw(cobs_encode(bytes(good)) + b"\x00")                   # CRC error
    raw(cobs_encode(b"\x01\x02\x03") + b"\x00")               # too short
    raw(b"\x05\x01\x02\x00")                                  # COBS overrun
    raw(b"\x00")                                              # empty frame: ignored
    req(*P.status())
    req(*P.ping())
    return vecs


def write_vector_file(path: Path) -> int:
    vecs = build_vectors()
    with path.open("w") as f:
        for wire, resp in vecs:
            f.write(f"S {len(wire)} " + " ".join(f"{b:02x}" for b in wire) + "\n")
            if resp is None:
                f.write("N 0\n")
            else:
                f.write(f"E {len(resp)} " + " ".join(f"{b:02x}" for b in resp) + "\n")
    return len(vecs)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/vectors.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"wrote {write_vector_file(out)} vectors to {out}")
