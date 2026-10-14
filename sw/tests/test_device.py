"""Check phase 1 commands through the public Device API.

Tests taking the dev fixture use a fresh simulator unless --port is supplied.
The lower tests create their own model to inspect state directly. I check
returned data and error codes here; UART bit timing belongs to the HDL tests.
Writes/FILL overwrite the selected memory span when run on a physical board.
"""

import struct

import numpy as np
import pytest

from mathaccel import fp32ref
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp, Kernel, Op, Status
from mathaccel.driver.device import Device
from mathaccel.driver.model import DeviceModel
from mathaccel.driver.protocol import DeviceError, Frame
from mathaccel.driver.transport import SimTransport
from mathaccel.fpgen import stream


def test_ping(dev):
    """Verify the driver can identify the configured protocol version and clock."""
    info = dev.ping()
    assert info.version == (spec.VERSION_MAJOR, spec.VERSION_MINOR)
    assert info.clk_hz == spec.CLK_HZ


def test_memory_roundtrip_multi_frame(dev):
    """Transfer more than 255 words to exercise driver chunking and address steps."""
    data = np.random.default_rng(3).integers(0, 2**32, 1000, dtype=np.uint32)
    dev.write_words(100, data)
    np.testing.assert_array_equal(dev.read_words(100, 1000), data)


def test_f32_roundtrip(dev):
    """Store and recover float representations; this does not test fp32 arithmetic."""
    vals = np.array([0.0, -1.5, 3.14159, 1e30, -1e-30], dtype=np.float32)
    dev.write_f32(0, vals)
    np.testing.assert_array_equal(dev.read_f32(0, len(vals)), vals)


def test_top_of_memory(dev):
    """Accept a write ending at the last word, and reject spans crossing that end."""
    top = spec.SCRATCH_WORDS - 3
    dev.write_words(top, [1, 2, 3])
    assert list(dev.read_words(top, 3)) == [1, 2, 3]
    with pytest.raises(DeviceError, match="BAD_ADDR"):
        dev.write_words(top, [1, 2, 3, 4])
    with pytest.raises(DeviceError, match="BAD_ADDR"):
        dev.read_words(spec.SCRATCH_WORDS - 1, 2)


def test_fill_and_cycles(dev):
    """Check FILL data and its completion clock, then verify NOP updates STATUS."""
    r = dev.fill(10, 20, 0xCAFEF00D)
    assert r.cycles == 21
    words = dev.read_words(9, 22)
    # The interior slice is exactly the requested fill span. A later test
    # separately checks that neighboring words retain known sentinel values.
    assert all(w == 0xCAFEF00D for w in words[1:21])
    assert dev.nop().cycles == 1
    assert dev.status().last_cycles == 1


def test_bad_kernel_and_len(dev):
    """Distinguish an unsupported kernel from incomplete arguments or zero reads."""
    with pytest.raises(DeviceError, match="BAD_KERNEL"):
        dev.run(0x7E)
    with pytest.raises(DeviceError, match="BAD_LEN"):
        dev.run(Kernel.FILL, b"\x00" * 4)
    with pytest.raises(DeviceError, match="BAD_LEN"):
        dev.request(Op.READ_MEM, struct.pack("<IH", 0, 0))


def test_unknown_opcode(dev):
    """Reject a packet with a valid layout but an unrecognized command ID."""
    with pytest.raises(DeviceError, match="BAD_OP"):
        dev.request(0x33, b"")


# These tests create their own backend so they can inspect model state without
# relying on a board's earlier commands or accessing hardware internals.

def test_corrupt_frames_counted_and_dropped():
    """Drop malformed wire data, count one link error, and accept the next PING."""
    model = DeviceModel()
    t = SimTransport(model)
    dev = Device(t, retries=0)
    raw = bytearray(Frame(Op.PING, 1).to_raw())
    raw[-1] ^= 0xFF
    # This intentionally sends garbage encoding. It can fail at framing or CRC,
    # so the test checks the combined counter rather than claiming a CRC-only error.
    t.send(bytes(raw).replace(b"\x00", b"\x01") + b"\x00")  # garbage, not valid COBS of a good frame
    assert t.recv_frame(0) is None
    assert model.crc_errors + model.frame_errors == 1
    dev.ping()  # still alive afterwards


def test_neighbour_words_untouched():
    """Use sentinel values to detect FILL writes before or after its requested span."""
    dev = Device(SimTransport())
    dev.write_words(0, [7] * 40)
    dev.fill(10, 20, 1)
    w = dev.read_words(0, 40)
    assert list(w[:10]) == [7] * 10 and list(w[30:]) == [7] * 10


def test_status_payload_len_checked():
    """Verify BAD_LEN arrives as a DeviceError with its numeric status retained."""
    dev = Device(SimTransport())
    with pytest.raises(DeviceError) as e:
        dev.request(Op.PING, b"\x01")
    assert e.value.status == Status.BAD_LEN


# ---- FPU kernel (simulator by default, the board with --port) --------------

@pytest.mark.parametrize("fop", list(FpuOp))
def test_fpu_kernel_matches_reference(dev, fop):
    """Run seeded vectors through the FPU kernel and compare bit patterns and flags."""
    vecs = list(stream(0xF00D + fop, 64, fop))
    a = [v[1] for v in vecs]
    b = [v[2] for v in vecs]
    res, fl, run = dev.fpu(fop, a, b, base=1000)
    for k, (x, y) in enumerate(zip(a, b, strict=True)):
        assert (int(res[k]), int(fl[k])) == fp32ref.evaluate(fop, x, y), f"{FpuOp(fop).name} {x:08x} {y:08x}"
    want_aux = 0
    for f in fl:
        want_aux |= int(f)
    assert run.aux == want_aux


def test_fpu_kernel_errors(dev):
    with pytest.raises(DeviceError, match="BAD_ARG"):
        dev.fpu_kernel(12, 1, 0, 0, 0, 0)
    with pytest.raises(DeviceError, match="BAD_ADDR"):
        dev.fpu_kernel(0, 2, 0, 0, 0, spec.SCRATCH_WORDS - 1)
    assert dev.fpu_kernel(0, 0, 0, 0, 0, 0).cycles == 1
