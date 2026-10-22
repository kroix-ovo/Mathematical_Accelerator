"""Build and parse the Ethernet frames the board sends and receives.

I wrote this as a second, independent reading of the standards the RTL in
hw/rtl/eth implements, using plain loops instead of shift registers:

* Ethernet II: destination MAC, source MAC, EtherType, payload padded to
  46 bytes, then a CRC-32 frame check sequence (FCS). On the wire a frame is
  preceded by seven 0x55 preamble bytes and the 0xD5 start-of-frame delimiter;
  the helpers here stop at the FCS, and the RMII models add the preamble.
* ARP (RFC 826) for "who has 192.168.10.2?".
* IPv4 (RFC 791) with only the 20-byte header (no options).
* ICMP echo (RFC 792) so `ping` works, and UDP (RFC 768) for commands.

Header values the board chooses for its own packets are fixed here and in
eth_tx.sv together: IP identification 0 with Don't Fragment set (RFC 6864
allows any ID for such atomic datagrams), TTL 64, TOS 0.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from mathaccel._generated import spec

ETH_TYPE_IPV4 = 0x0800
ETH_TYPE_ARP = 0x0806
IP_PROTO_ICMP = 1
IP_PROTO_UDP = 17
BROADCAST = b"\xff" * 6
MIN_FRAME = 64          # bytes including FCS; shorter frames are runts
MAX_FRAME = 1522        # bytes including FCS (1518 plus room for one VLAN tag)
CRC32_RESIDUE = 0xDEBB20E3


def mac_bytes(text: str) -> bytes:
    """"02:4D:41:43:43:01" -> six bytes in wire order."""
    return bytes(int(x, 16) for x in text.split(":"))


def ip_bytes(text: str) -> bytes:
    """"192.168.10.2" -> four bytes in wire order."""
    return bytes(int(x) for x in text.split("."))


BOARD_MAC = mac_bytes(spec.ETH_MAC)
BOARD_IP = ip_bytes(spec.ETH_IP)
HOST_IP = ip_bytes(spec.HOST_IP)


# ---------------------------------------------------------------------------
# Checks: CRC-32 and the Internet checksum
# ---------------------------------------------------------------------------

def crc32_update(crc: int, data: bytes) -> int:
    """Run the Ethernet CRC register over data, one bit at a time.

    Ethernet sends each byte least significant bit first, so the register is
    "reflected": it shifts right and the polynomial 0x04C11DB7 appears bit
    reversed as 0xEDB88320. The register starts at 0xFFFFFFFF. This returns
    the raw register; fcs() inverts it to get the transmitted value.
    """
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc


def fcs(frame: bytes) -> bytes:
    """The four FCS bytes for a frame (inverted CRC, low byte first on the wire)."""
    return struct.pack("<I", crc32_update(0xFFFFFFFF, frame) ^ 0xFFFFFFFF)


def fcs_ok(frame_with_fcs: bytes) -> bool:
    """A receiver runs the register over the FCS too; a good frame always
    leaves the same constant (the residue) behind. eth_rx.sv uses that trick."""
    return crc32_update(0xFFFFFFFF, frame_with_fcs) == CRC32_RESIDUE


def ones_sum(data: bytes, start: int = 0) -> int:
    """Add big-endian 16-bit words with end-around carry (RFC 1071).

    An odd final byte counts as the high half of a word whose low half is 0.
    The result is folded to 16 bits but not inverted.
    """
    if len(data) % 2:
        data = data + b"\x00"
    s = start
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return s


def checksum(data: bytes, start: int = 0) -> int:
    """The value to put in a checksum field: the inverted ones'-complement sum."""
    return ~ones_sum(data, start) & 0xFFFF


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def ethernet(dst: bytes, src: bytes, ethertype: int, payload: bytes, with_fcs: bool = True) -> bytes:
    """Header + payload padded with zeros to the 60-byte minimum, then the FCS."""
    body = dst + src + struct.pack(">H", ethertype) + payload
    body += b"\x00" * max(0, 60 - len(body))
    return body + fcs(body) if with_fcs else body


def arp(oper: int, sha: bytes, spa: bytes, tha: bytes, tpa: bytes) -> bytes:
    """ARP body for Ethernet/IPv4: hardware type 1, protocol 0x0800, sizes 6 and 4."""
    return struct.pack(">HHBBH", 1, ETH_TYPE_IPV4, 6, 4, oper) + sha + spa + tha + tpa


def ipv4(src: bytes, dst: bytes, proto: int, payload: bytes, ident: int = 0, flags_frag: int = 0x4000,
         ttl: int = 64, tos: int = 0) -> bytes:
    """20-byte IPv4 header with a correct header checksum, followed by payload."""
    hdr = struct.pack(">BBHHHBBH", 0x45, tos, 20 + len(payload), ident, flags_frag, ttl, proto, 0) + src + dst
    hdr = hdr[:10] + struct.pack(">H", checksum(hdr)) + hdr[12:]
    return hdr + payload


def udp(src_ip: bytes, dst_ip: bytes, sport: int, dport: int, payload: bytes) -> bytes:
    """UDP header and payload. The checksum covers a pseudo-header (both IP
    addresses, protocol 17 and the UDP length) plus the header and payload.
    A computed 0 is sent as 0xFFFF, because 0 means "no checksum"."""
    length = 8 + len(payload)
    pseudo = src_ip + dst_ip + struct.pack(">BBH", 0, IP_PROTO_UDP, length)
    hdr = struct.pack(">HHHH", sport, dport, length, 0)
    c = checksum(pseudo + hdr + payload) or 0xFFFF
    return struct.pack(">HHHH", sport, dport, length, c) + payload


def icmp_echo(typ: int, ident: int, seq: int, data: bytes) -> bytes:
    """ICMP echo request (type 8) or reply (type 0) with its checksum."""
    msg = struct.pack(">BBHHH", typ, 0, 0, ident, seq) + data
    return msg[:2] + struct.pack(">H", checksum(msg)) + msg[4:]


def udp_frame(payload: bytes, src_mac: bytes, src_ip: bytes, sport: int,
              dst_mac: bytes = BOARD_MAC, dst_ip: bytes = BOARD_IP, dport: int = spec.UDP_PORT) -> bytes:
    """A complete host -> board UDP frame (what the laptop's network stack sends)."""
    return ethernet(dst_mac, src_mac, ETH_TYPE_IPV4,
                    ipv4(src_ip, dst_ip, IP_PROTO_UDP, udp(src_ip, dst_ip, sport, dport, payload)))


def arp_request_frame(src_mac: bytes, src_ip: bytes, target_ip: bytes = BOARD_IP) -> bytes:
    """A broadcast "who has target_ip? tell src_ip"."""
    return ethernet(BROADCAST, src_mac, ETH_TYPE_ARP, arp(1, src_mac, src_ip, b"\x00" * 6, target_ip))


def ping_frame(src_mac: bytes, src_ip: bytes, ident: int, seq: int, data: bytes,
               dst_ip: bytes = BOARD_IP) -> bytes:
    """An ICMP echo request addressed to the board."""
    return ethernet(BOARD_MAC, src_mac, ETH_TYPE_IPV4,
                    ipv4(src_ip, dst_ip, IP_PROTO_ICMP, icmp_echo(8, ident, seq, data)))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

@dataclass
class Parsed:
    """The fields of one frame the board model and the tests care about.

    kind is "arp", "icmp", "udp" or "other"; fields that do not apply stay
    at their defaults. ok_ip is False when the IPv4 header is malformed.
    """
    dst: bytes
    src: bytes
    ethertype: int
    kind: str = "other"
    arp_oper: int = 0
    sha: bytes = b""
    spa: bytes = b""
    tpa: bytes = b""
    ok_ip: bool = False
    ip_src: bytes = b""
    ip_dst: bytes = b""
    ip_total: int = 0
    proto: int = 0
    icmp: bytes = b""       # the whole ICMP message (header and data)
    sport: int = 0
    dport: int = 0
    udp_payload: bytes = b""


def parse(frame_with_fcs: bytes) -> Parsed:
    """Split a received frame into fields, applying the board's acceptance rules.

    The rules match eth_rx.sv: an ARP body must be Ethernet/IPv4 with sizes
    6/4; IPv4 needs version 4, a 20-byte header, a valid header checksum, no
    fragmentation, and a total length that fits in the frame; UDP needs a
    length of at least 8 that fits inside the IP datagram. Bytes after the IP
    total length (Ethernet padding) are ignored.
    """
    f = frame_with_fcs[:-4]
    p = Parsed(dst=f[0:6], src=f[6:12], ethertype=(f[12] << 8) | f[13])
    if p.ethertype == ETH_TYPE_ARP and len(f) >= 42:
        htype, ptype, hlen, plen, oper = struct.unpack(">HHBBH", f[14:22])
        if (htype, ptype, hlen, plen) == (1, ETH_TYPE_IPV4, 6, 4):
            p.kind, p.arp_oper = "arp", oper
            p.sha, p.spa, p.tpa = f[22:28], f[28:32], f[38:42]
        return p
    if p.ethertype != ETH_TYPE_IPV4 or len(f) < 34:
        return p
    hdr = f[14:34]
    total = (hdr[2] << 8) | hdr[3]
    flags_frag = (hdr[6] << 8) | hdr[7]
    if hdr[0] != 0x45 or ones_sum(hdr) != 0xFFFF or flags_frag & 0x3FFF or total < 20 or 14 + total > len(f):
        return p
    p.ok_ip = True
    p.ip_total, p.proto = total, hdr[9]
    p.ip_src, p.ip_dst = hdr[12:16], hdr[16:20]
    body = f[34:14 + total]
    if p.proto == IP_PROTO_ICMP and len(body) >= 8:
        p.kind, p.icmp = "icmp", body
    elif p.proto == IP_PROTO_UDP and len(body) >= 8:
        sport, dport, length = struct.unpack(">HHH", body[:6])
        if 8 <= length <= len(body):
            p.kind, p.sport, p.dport = "udp", sport, dport
            p.udp_payload = body[8:length]
    return p
