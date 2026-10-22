"""Predict the board's Ethernet replies, frame by frame.

EthBoardModel is the golden model for the pyuvm Ethernet environment. It takes
one received frame (with its FCS) and returns the frames the FPGA must send
back, in order. Command frames inside UDP datagrams go to the same DeviceModel
the UART simulator uses, so memory contents and the STATUS counters are shared
between the two transports, as they are in the hardware.

What it does not model is timing. The RTL drops a frame when both receive
slots are full or when an earlier ping is still waiting to be answered; a test
that wants an exact prediction must not create those situations. The counters
here only cover frames that are dropped for reasons visible in the bytes.
"""

from __future__ import annotations

from mathaccel._generated import spec
from mathaccel.driver.model import DeviceModel
from mathaccel.net import frames as F


class EthBoardModel:
    """ARP responder, ICMP echo responder and UDP command port of the board."""

    def __init__(self, device: DeviceModel | None = None) -> None:
        self.device = device or DeviceModel()
        self.rx_errors = 0     # bad FCS, runts, RX_ER (LED5 in hardware)
        self.rx_drops = 0      # frames too long to store (LED6 in hardware)

    def handle_frame(self, frame: bytes, rx_er: bool = False) -> list[bytes]:
        """Return the reply frames (each with FCS) for one received frame.

        rx_er says the PHY flagged a receive error during this frame; the
        hardware then discards it exactly like a frame with a bad FCS.
        """
        # Same order as eth_rx.sv: a broken frame counts as an error even if
        # it is also too long.
        if rx_er or len(frame) < F.MIN_FRAME or not F.fcs_ok(frame):
            self.rx_errors += 1
            return []
        if len(frame) > F.MAX_FRAME:
            self.rx_drops += 1
            return []
        p = F.parse(frame)
        # Frames for other stations (or multicast, such as mDNS) are not ours.
        if p.dst not in (F.BOARD_MAC, F.BROADCAST):
            return []
        if p.kind == "arp":
            return self._arp(p)
        if p.ok_ip and p.ip_dst == F.BOARD_IP:
            if p.kind == "icmp":
                return self._icmp(p)
            if p.kind == "udp" and p.dport == spec.UDP_PORT:
                return self._udp(p)
        return []

    def _arp(self, p: F.Parsed) -> list[bytes]:
        """Answer "who has BOARD_IP" with the board's MAC; ignore everything else."""
        if p.arp_oper != 1 or p.tpa != F.BOARD_IP:
            return []
        body = F.arp(2, F.BOARD_MAC, F.BOARD_IP, p.sha, p.spa)
        return [F.ethernet(p.src, F.BOARD_MAC, F.ETH_TYPE_ARP, body)]

    def _icmp(self, p: F.Parsed) -> list[bytes]:
        """Echo request -> echo reply with the same identifier, sequence and data.

        Only the type changes (8 -> 0), so the reply checksum is recomputed
        over the new message. The request's own checksum is not checked; the
        FCS already protects the bytes.
        """
        msg = p.icmp
        if msg[0] != 8 or msg[1] != 0:
            return []
        reply = b"\x00\x00\x00\x00" + msg[4:]
        reply = reply[:2] + F.checksum(reply).to_bytes(2, "big") + reply[4:]
        ip = F.ipv4(F.BOARD_IP, p.ip_src, F.IP_PROTO_ICMP, reply)
        return [F.ethernet(p.src, F.BOARD_MAC, F.ETH_TYPE_IPV4, ip)]

    def _udp(self, p: F.Parsed) -> list[bytes]:
        """Feed the datagram to the command processor; one reply datagram per reply.

        The payload is a stream of COBS frames separated by 0x00. The hardware
        ends the stream with an extra 0x00 if the sender left it off, so an
        unterminated last frame is still processed.
        """
        out = []
        for chunk in p.udp_payload.split(b"\x00"):
            if not chunk:
                continue
            wire = self.device.handle_wire(chunk)
            if wire is not None:
                udp = F.udp(F.BOARD_IP, p.ip_src, spec.UDP_PORT, p.sport, wire)
                ip = F.ipv4(F.BOARD_IP, p.ip_src, F.IP_PROTO_UDP, udp)
                out.append(F.ethernet(p.src, F.BOARD_MAC, F.ETH_TYPE_IPV4, ip))
        return out
