"""Device API tests. Run against the simulator by default, or the board with `-m hw --port COMx`."""

import struct

import numpy as np
import pytest

from mathaccel._generated import spec
from mathaccel._generated.spec import Kernel, Op, Status
from mathaccel.driver.device import Device
from mathaccel.driver.model import DeviceModel
from mathaccel.driver.protocol import DeviceError, Frame
from mathaccel.driver.transport import SimTransport


def test_ping(dev):
    info = dev.ping()
    assert info.version == (spec.VERSION_MAJOR, spec.VERSION_MINOR)
    assert info.clk_hz == spec.CLK_HZ


def test_memory_roundtrip_multi_frame(dev):
    data = np.random.default_rng(3).integers(0, 2**32, 1000, dtype=np.uint32)
    dev.write_words(100, data)
    np.testing.assert_array_equal(dev.read_words(100, 1000), data)


def test_f32_roundtrip(dev):
    vals = np.array([0.0, -1.5, 3.14159, 1e30, -1e-30], dtype=np.float32)
    dev.write_f32(0, vals)
    np.testing.assert_array_equal(dev.read_f32(0, len(vals)), vals)


def test_top_of_memory(dev):
    top = spec.SCRATCH_WORDS - 3
    dev.write_words(top, [1, 2, 3])
    assert list(dev.read_words(top, 3)) == [1, 2, 3]
    with pytest.raises(DeviceError, match="BAD_ADDR"):
        dev.write_words(top, [1, 2, 3, 4])
    with pytest.raises(DeviceError, match="BAD_ADDR"):
        dev.read_words(spec.SCRATCH_WORDS - 1, 2)


def test_fill_and_cycles(dev):
    r = dev.fill(10, 20, 0xCAFEF00D)
    assert r.cycles == 21
    words = dev.read_words(9, 22)
    assert all(w == 0xCAFEF00D for w in words[1:21])
    assert dev.nop().cycles == 1
    assert dev.status().last_cycles == 1


def test_bad_kernel_and_len(dev):
    with pytest.raises(DeviceError, match="BAD_KERNEL"):
        dev.run(0x7E)
    with pytest.raises(DeviceError, match="BAD_LEN"):
        dev.run(Kernel.FILL, b"\x00" * 4)
    with pytest.raises(DeviceError, match="BAD_LEN"):
        dev.request(Op.READ_MEM, struct.pack("<IH", 0, 0))


def test_unknown_opcode(dev):
    with pytest.raises(DeviceError, match="BAD_OP"):
        dev.request(0x33, b"")


# ---- simulator-only tests (inspect the model's error counters) -------------

def test_corrupt_frames_counted_and_dropped():
    model = DeviceModel()
    t = SimTransport(model)
    dev = Device(t, retries=0)
    raw = bytearray(Frame(Op.PING, 1).to_raw())
    raw[-1] ^= 0xFF
    t.send(bytes(raw).replace(b"\x00", b"\x01") + b"\x00")  # garbage, not valid COBS of a good frame
    assert t.recv_frame(0) is None
    assert model.crc_errors + model.frame_errors == 1
    dev.ping()  # still alive afterwards


def test_neighbour_words_untouched():
    dev = Device(SimTransport())
    dev.write_words(0, [7] * 40)
    dev.fill(10, 20, 1)
    w = dev.read_words(0, 40)
    assert list(w[:10]) == [7] * 10 and list(w[30:]) == [7] * 10


def test_status_payload_len_checked():
    dev = Device(SimTransport())
    with pytest.raises(DeviceError) as e:
        dev.request(Op.PING, b"\x01")
    assert e.value.status == Status.BAD_LEN
