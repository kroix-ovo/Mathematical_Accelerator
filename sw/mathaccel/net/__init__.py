"""Ethernet, ARP, IPv4, ICMP and UDP reference code for the UDP transport.

frames.py builds and parses the packets byte by byte. board_model.py uses it
to predict what the FPGA's Ethernet block sends back for each received frame.
The host itself talks to the board through ordinary operating-system sockets
(UdpTransport); this package is for the simulators and tests.
"""
