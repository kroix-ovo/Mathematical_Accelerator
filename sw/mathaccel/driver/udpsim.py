"""Serve the Python device model on a local UDP socket.

UdpSimServer behaves like the board's UDP command port: every datagram it
receives is split at 0x00 delimiters, each frame goes through DeviceModel, and
each reply goes back as its own datagram to the sender. It runs in a
background thread on 127.0.0.1 with a port the operating system picks, so the
tests and `mathaccel --port udpsim` can exercise UdpTransport and the socket
code without a board. ARP, IP and Ethernet are not involved here; the RTL
testbenches cover those against sw/mathaccel/net/board_model.py.
"""

from __future__ import annotations

import socket
import threading

from mathaccel.driver.model import DeviceModel
from mathaccel.driver.transport import UdpTransport


class UdpSimServer:
    """A DeviceModel behind a UDP socket, answering from a daemon thread."""

    def __init__(self, model: DeviceModel | None = None) -> None:
        self.model = model or DeviceModel()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        # daemon=True: the thread never keeps a finished test run alive.
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                return              # socket closed by close()
            for chunk in data.split(b"\x00"):
                if chunk:
                    reply = self.model.handle_wire(chunk)
                    if reply is not None:
                        self.sock.sendto(reply, addr)

    def transport(self) -> UdpTransport:
        """A client transport for this server that also stops it when closed."""
        server = self

        class _Client(UdpTransport):
            def close(self) -> None:
                super().close()
                server.close()

        return _Client("127.0.0.1", self.port)

    def close(self) -> None:
        self.sock.close()
