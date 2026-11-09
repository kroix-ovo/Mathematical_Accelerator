"""Differential framing against the pinned control, plus transport boundaries."""
from __future__ import annotations

import random
import runpy
import subprocess
import types
from collections import deque
from pathlib import Path

import numpy as np
import pytest

from mathaccel._generated import spec
from mathaccel.driver import framing as f
from mathaccel.driver.device import Device, DeviceTimeout, connect
from mathaccel.driver.protocol import Frame, FrameError
from mathaccel.driver.transport import UartTransport, UdpTransport

BASELINE = "98be2be8e8b0fd9e1f600631fb54dae9638ce726"


@pytest.fixture(scope="module")
def control():
    root = Path(__file__).resolve().parents[2]
    git_env = runpy.run_path(str(root / "tools/language_git.py"))["git_env"]
    source = subprocess.check_output(
        ["git", "-C", str(root), "show", f"{BASELINE}:sw/mathaccel/driver/framing.py"],
        env=git_env(root), text=True)
    module = types.ModuleType("control_framing")
    exec(compile(source, "control_framing.py", "exec"), module.__dict__)
    return module


def test_seeded_framing_differential(control):
    rng = random.Random(20261109)
    for n in [0, 1, 253, 254, 255, 256, 1024, 1030] * 20:
        data = bytes(rng.randrange(256) for _ in range(n))
        assert f.cobs_encode(data) == control.cobs_encode(data)
        assert f.cobs_decode(control.cobs_encode(data)) == data
        for seed in (0, 1, 0xffff, -1, 1 << 80):
            assert f.crc16(data, seed) == control.crc16(data, seed)
        cut = n // 2
        assert f.crc16(data[cut:], f.crc16(data[:cut])) == control.crc16(data)
    # Arbitrary encoded bytes compare both rejection and error messages.
    for _ in range(2000):
        data = rng.randbytes(rng.randrange(300))
        try:
            expected = control.cobs_decode(data)
        except ValueError as e:
            with pytest.raises(f.CobsError) as got:
                f.cobs_decode(data)
            assert str(got.value) == str(e)
        else:
            assert f.cobs_decode(data) == expected


@pytest.mark.parametrize("n", [0, 1, 254, 255, 508, 509])
def test_independent_cobs_vectors(n):
    data = b"\x55" * n
    blocks, tail = divmod(n, 254)
    expected = (b"\xff" + b"\x55" * 254) * blocks + bytes([tail + 1]) + b"\x55" * tail
    assert f.cobs_encode(data) == expected
    assert f.cobs_decode(expected) == data
    assert f.cobs_encode(bytes(n)) == b"\x01" * (n + 1)


def test_frame_limits_and_truncation():
    frame = Frame(2, 255, bytes(spec.MAX_PAYLOAD))
    assert Frame.decode(frame.encode()[:-1]) == frame
    with pytest.raises(ValueError):
        Frame(2, 0, bytes(spec.MAX_PAYLOAD + 1)).encode()
    raw = frame.to_raw()
    for cut in (0, 1, 5, len(raw) - 1):
        with pytest.raises(FrameError):
            Frame.from_raw(raw[:cut])


@pytest.mark.parametrize("n", [1, 255, 256, 510, 511, 4096])
def test_word_patterns_and_strided_arrays(n):
    patterns = np.array([0, 0x80000000, 0x7f800000, 0xff800000, 0x7fc12345,
                         0x7f812345, 1, 0xffffffff], dtype=np.uint32)
    words = np.resize(patterns, 2 * n)[::2]
    with connect("sim") as dev:
        dev.write_words(spec.SCRATCH_WORDS - n, words)
        np.testing.assert_array_equal(dev.read_words(spec.SCRATCH_WORDS - n, n), words)


def test_uart_partial_and_buffered_frames():
    class Serial:
        in_waiting = 0
        parts = deque([b"\x00\x02", b"a\x00\x02b\x00"])

        def read(self, n):
            return self.parts.popleft() if self.parts else b""

    uart = UartTransport.__new__(UartTransport)
    uart.ser = Serial()
    uart._buf = bytearray()
    assert uart.recv_frame(0.1) == b"\x02a"
    assert uart.recv_frame(0.1) == b"\x02b"
    assert uart.recv_frame(0) is None


def test_udp_multiple_frames_in_one_datagram():
    class Socket:
        calls = 0

        def settimeout(self, timeout):
            pass

        def recv(self, n):
            self.calls += 1
            return b"\x00\x02a\x00\x02b\x00"

    udp = UdpTransport.__new__(UdpTransport)
    udp.sock = Socket()
    udp._pending = []
    assert udp.recv_frame(1) == b"\x02a"
    assert udp.recv_frame(1) == b"\x02b"
    assert udp.sock.calls == 1


def test_stale_tags_opcodes_corruption_and_retry():
    class Transport:
        def __init__(self):
            self.sent = []
            self.pending = deque()

        def send(self, wire):
            req = Frame.decode(wire[:-1])
            self.sent.append(req)
            if len(self.sent) == 2:
                self.pending.extend([
                    b"\x00",  # malformed
                    Frame(req.op | 128, req.tag - 1, b"\x00old").encode()[:-1],
                    Frame(0xff, req.tag, b"\x00wrong").encode()[:-1],
                    Frame(req.op | 128, req.tag, b"\x00ok").encode()[:-1],
                ])

        def recv_frame(self, timeout):
            return self.pending.popleft() if self.pending else None

    transport = Transport()
    dev = Device(transport, retries=2)
    assert dev.request(spec.Op.STATUS, b"") == b"ok"
    assert [r.tag for r in transport.sent] == [1, 2]
    silent = Transport()
    with pytest.raises(DeviceTimeout):
        Device(silent, retries=9).request(spec.Op.RUN, b"\x03", resend=False)
    assert len(silent.sent) == 1
