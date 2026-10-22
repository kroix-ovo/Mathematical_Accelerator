"""Check the Ethernet reference code and the UDP transport.

The first group checks frames.py against independent sources: zlib's CRC-32,
the worked checksum example in RFC 1071, and Python's own socket code for the
UDP path. The second group pins down what the board model answers and what it
ignores, since the pyuvm Ethernet environment uses it as the golden model.
The last group runs Device commands through UdpTransport and a local socket.
"""

import socket
import struct
import zlib

import numpy as np
import pytest

from mathaccel._generated import spec
from mathaccel._generated.spec import Kernel, Op
from mathaccel.driver import protocol as P
from mathaccel.driver.device import connect
from mathaccel.driver.protocol import Frame
from mathaccel.net import frames as F
from mathaccel.net.board_model import EthBoardModel

HOST_MAC = bytes.fromhex("D8BBC1000001")
HOST_IP = F.HOST_IP


def test_crc32_matches_zlib():
    """My bit-by-bit register and zlib agree, and good frames leave the residue."""
    rng = np.random.default_rng(5)
    for n in (0, 1, 14, 60, 1514):
        data = rng.integers(0, 256, n, dtype=np.uint8).tobytes()
        assert F.crc32_update(0xFFFFFFFF, data) ^ 0xFFFFFFFF == zlib.crc32(data)
        assert F.fcs_ok(data + F.fcs(data))
        assert not F.fcs_ok(data + bytes([F.fcs(data)[0] ^ 1]) + F.fcs(data)[1:])


def test_ones_sum_rfc1071_example():
    """RFC 1071 section 3 adds 00 01 f2 03 f4 f5 f6 f7 to get ddf2."""
    assert F.ones_sum(bytes.fromhex("0001f203f4f5f6f7")) == 0xDDF2
    assert F.checksum(bytes.fromhex("0001f203f4f5f6f7")) == 0x220D


def test_ipv4_header_checksum_verifies():
    """Summing a header that contains its own checksum gives FFFF."""
    ip = F.ipv4(HOST_IP, F.BOARD_IP, F.IP_PROTO_UDP, b"x" * 13)
    assert F.ones_sum(ip[:20]) == 0xFFFF
    assert ip[:2] == b"\x45\x00" and ip[6:8] == b"\x40\x00" and ip[8] == 64


def test_udp_checksum_verifies_with_pseudo_header():
    """A receiver checks UDP by summing pseudo-header + datagram to FFFF."""
    for payload in (b"", b"a", b"ab", bytes(range(1, 200))):
        d = F.udp(HOST_IP, F.BOARD_IP, 51234, spec.UDP_PORT, payload)
        pseudo = HOST_IP + F.BOARD_IP + struct.pack(">BBH", 0, 17, len(d))
        assert F.ones_sum(pseudo + d) == 0xFFFF


def test_minimum_frame_is_padded():
    f = F.arp_request_frame(HOST_MAC, HOST_IP)
    assert len(f) == 64 and F.fcs_ok(f)


# ---------------------------------------------------------------------------
# Board model
# ---------------------------------------------------------------------------

def test_arp_reply():
    m = EthBoardModel()
    (r,) = m.handle_frame(F.arp_request_frame(HOST_MAC, HOST_IP))
    p = F.parse(r)
    assert F.fcs_ok(r) and len(r) == 64
    assert (p.dst, p.src, p.kind, p.arp_oper) == (HOST_MAC, F.BOARD_MAC, "arp", 2)
    assert (p.sha, p.spa, p.tpa) == (F.BOARD_MAC, F.BOARD_IP, HOST_IP)
    # Requests for other addresses are not ours to answer.
    assert m.handle_frame(F.arp_request_frame(HOST_MAC, HOST_IP, F.ip_bytes("192.168.10.7"))) == []


@pytest.mark.parametrize("size", [0, 1, 32, 1472])
def test_ping_reply(size):
    m = EthBoardModel()
    data = bytes((i * 7) & 0xFF for i in range(size))
    (r,) = m.handle_frame(F.ping_frame(HOST_MAC, HOST_IP, 0x0001, 0x0042, data))
    p = F.parse(r)
    assert p.kind == "icmp" and p.ip_src == F.BOARD_IP and p.ip_dst == HOST_IP
    assert p.icmp == F.icmp_echo(0, 0x0001, 0x0042, data)
    assert F.ones_sum(p.icmp) == 0xFFFF


def test_udp_command_reply_matches_device_model():
    m = EthBoardModel()
    wire = Frame(Op.PING, 7, b"").encode()
    (r,) = m.handle_frame(F.udp_frame(wire, HOST_MAC, HOST_IP, 51000))
    p = F.parse(r)
    assert (p.kind, p.sport, p.dport, p.ip_dst, p.dst) == ("udp", spec.UDP_PORT, 51000, HOST_IP, HOST_MAC)
    assert Frame.decode(p.udp_payload[:-1]).tag == 7


def test_udp_several_frames_and_missing_delimiter():
    """Two frames in one datagram get two replies; a missing final 0x00 is added."""
    m = EthBoardModel()
    a = Frame(Op.PING, 1, b"").encode()
    b = Frame(Op.RUN, 2, bytes([Kernel.NOP])).encode()[:-1]      # no trailing 0x00
    out = m.handle_frame(F.udp_frame(a + b, HOST_MAC, HOST_IP, 51000))
    assert [Frame.decode(F.parse(r).udp_payload[:-1]).tag for r in out] == [1, 2]


def test_ignored_and_dropped_frames():
    m = EthBoardModel()
    wire = Frame(Op.PING, 1, b"").encode()
    good = F.udp_frame(wire, HOST_MAC, HOST_IP, 51000)
    other_mac = F.udp_frame(wire, HOST_MAC, HOST_IP, 51000, dst_mac=bytes.fromhex("020000000099"))
    other_ip = F.udp_frame(wire, HOST_MAC, HOST_IP, 51000, dst_ip=F.ip_bytes("192.168.10.9"))
    other_port = F.udp_frame(wire, HOST_MAC, HOST_IP, 51000, dport=50001)
    mdns = F.ethernet(bytes.fromhex("01005E0000FB"), HOST_MAC, F.ETH_TYPE_IPV4,
                      F.ipv4(HOST_IP, F.ip_bytes("224.0.0.251"), 17, F.udp(HOST_IP, F.ip_bytes("224.0.0.251"),
                                                                             5353, 5353, b"q" * 30)))
    ipv6 = F.ethernet(bytes.fromhex("333300000001"), HOST_MAC, 0x86DD, b"\x60" + b"\x00" * 50)
    for f in (other_mac, other_ip, other_port, mdns, ipv6):
        assert m.handle_frame(f) == []
    assert (m.rx_errors, m.rx_drops) == (0, 0)
    bad_fcs = good[:-1] + bytes([good[-1] ^ 0x80])
    assert m.handle_frame(bad_fcs) == [] and m.rx_errors == 1
    assert m.handle_frame(good, rx_er=True) == [] and m.rx_errors == 2
    assert m.handle_frame(good[:40] + F.fcs(good[:40])) == [] and m.rx_errors == 3     # runt
    big = F.ethernet(F.BOARD_MAC, HOST_MAC, 0x0800, bytes(1600))
    assert m.handle_frame(big) == [] and m.rx_drops == 1
    # A broken IPv4 header checksum is ignored like any other non-matching frame.
    broken = bytearray(good)
    broken[24] ^= 0xFF
    broken = bytes(broken[:-4]) + F.fcs(bytes(broken[:-4]))
    assert m.handle_frame(broken) == []
    # The model still answers afterwards.
    assert len(m.handle_frame(good)) == 1


def test_udp_and_uart_share_one_device_model():
    """Memory written through UDP is visible to the same DeviceModel the UART uses."""
    m = EthBoardModel()
    op, payload = P.write_mem(10, [0x11223344])
    w = Frame(op, 3, payload).encode()
    m.handle_frame(F.udp_frame(w, HOST_MAC, HOST_IP, 51000))
    assert int(m.device.mem[10]) == 0x11223344


# ---------------------------------------------------------------------------
# UdpTransport through a local socket
# ---------------------------------------------------------------------------

def test_device_over_udpsim():
    with connect("udpsim") as dev:
        info = dev.ping()
        assert info.clk_hz == spec.CLK_HZ
        data = np.arange(600, dtype=np.uint32) * 0x01010101
        dev.write_words(5, data)
        np.testing.assert_array_equal(dev.read_words(5, 600), data)
        assert dev.fill(0, 100, 7).cycles == 101


def test_udp_transport_ignores_other_senders():
    """A connected UDP socket only delivers datagrams from the board's address."""
    with connect("udpsim") as dev:
        local = dev.t.sock.getsockname()
        intruder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        intruder.sendto(Frame(0x81, 0, b"\x00").encode(), local)
        intruder.close()
        assert dev.ping().magic == spec.MAGIC


def test_connect_parses_udp_port():
    with connect("udp:127.0.0.1:50999") as dev:
        assert dev.t.sock.getpeername() == ("127.0.0.1", 50999)
    with connect("udp:127.0.0.1") as dev:
        assert dev.t.sock.getpeername()[1] == spec.UDP_PORT
