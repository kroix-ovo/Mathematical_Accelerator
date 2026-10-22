# Host ↔ FPGA protocol

Transport-agnostic. The same frames travel over the UART (2 Mbaud 8N1, exactly 50 clocks per bit at 100 MHz) and over UDP on the board's Ethernet port; [UDP transport](#udp-transport) below describes the second one. The constants live in `spec/accel.toml`.

## Framing

```
raw frame  = op:u8 | tag:u8 | len:u16 | payload[len] | crc:u16
wire frame = COBS(raw frame) | 0x00
```

* All multi-byte fields are **little-endian**.
* `crc` is CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no xorout) over `op..payload`.
* COBS removes every 0x00 from the frame, so 0x00 always marks the end of a frame. A receiver can resynchronise on any 0x00. Empty frames (back-to-back delimiters) are ignored.
* `len` ≤ `max_payload` (1024).
* `tag` is chosen by the host and echoed in the response so stale replies can be matched or discarded.

A response has `op | 0x80`, the same `tag`, and a payload whose **first byte is a status code**.

### Error handling

| Condition | Device behaviour |
|---|---|
| CRC mismatch | frame dropped, `crc_errors` counter +1, no response |
| Frame starts arriving while a command is running | frame dropped, `frame_errors` +1, no response |
| COBS error, frame < 6 bytes, `len` ≠ actual length, `len` > max | frame dropped, `frame_errors` +1, no response |
| Unknown `op` | response with status `BAD_OP` |
| Bad payload length for op | status `BAD_LEN` |
| Address range outside scratchpad | status `BAD_ADDR` |
| Unknown kernel | status `BAD_KERNEL` |
| Kernel argument out of range (FPU op code > 11) | status `BAD_ARG` |

The host treats a missing response as a timeout and retries with a new tag.

## Commands

| op | name | request payload | response payload (after status) |
|---|---|---|---|
| 0x01 | PING | — | magic:u32 ("MACC"), ver_major:u8, ver_minor:u8, scratch_addr_bits:u8, rsvd:u8, clk_hz:u32 |
| 0x02 | WRITE_MEM | addr:u32, word:u32 × n (1 ≤ n ≤ 255) | — |
| 0x03 | READ_MEM | addr:u32, count:u16 (1 ≤ count ≤ 255) | word:u32 × count |
| 0x04 | RUN | kernel:u8, args… | cycles:u32, aux:u32 |
| 0x05 | STATUS | — | crc_errors:u16, frame_errors:u16, last_cycles:u32 |

Addresses and counts are in **32-bit words**. RUN is synchronous: the response is sent when the kernel finishes, and `cycles` is the number of core clock cycles the kernel was busy. `aux` is a kernel-specific result word: the OR of every element's exception flags for the FPU kernel, and 0 for NOP and FILL.

Version 0.2 added the FPU kernel, the `aux` word in RUN replies, and the `BAD_ARG` status.

## Kernels

| id | name | args | cycles |
|---|---|---|---|
| 0x00 | NOP | — | 1 |
| 0x01 | FILL | base:u32, count:u32, value:u32 | count + 1 |
| 0x02 | FPU | fop:u8, count:u32, a:u32, b:u32, r:u32, f:u32 | 4·count + 9 (DIV, SQRT: 33·count + 6); 1 when count = 0 |

### FPU kernel

For `i` in `0 .. count-1`: `r[i], f[i] = fop(a[i], b[i])`. The arguments `a`, `b`, `r` and `f` are word addresses of four arrays in the scratchpad. `r[i]` receives the 32-bit result pattern and `f[i]` receives that element's flags in bits 4..0 (NV DZ OF UF NX). Unary operations ignore `b`, but its range is still checked. The arrays must be identical or disjoint. Partially overlapping arrays give undefined results.

Checks happen in this order: payload length (22 bytes including the kernel byte), then `fop ≤ 11` (`BAD_ARG`), then each array inside the scratchpad (`BAD_ADDR`).

| fop | op | | fop | op |
|---|---|---|---|---|
| 0 | ADD | | 6 | F2I (fp32 → int32) |
| 1 | SUB | | 7 | MIN |
| 2 | MUL | | 8 | MAX |
| 3 | DIV | | 9 | EQ |
| 4 | SQRT | | 10 | LT |
| 5 | I2F (int32 → fp32) | | 11 | LE |

The exact arithmetic rules (rounding, subnormals, NaN results, flags) are in [numerics.md](numerics.md).

The Python reference model (`sw/mathaccel/driver/model.py`) implements this document exactly. It serves as the simulator backend and as the golden model for the RTL testbenches.

## UDP transport

The board also accepts command frames as UDP datagrams over its 10/100 Ethernet port (LAN8720A PHY, RMII, 100 Mbit/s). The frames inside are the same wire frames as on the UART, COBS encoding and the 0x00 delimiter included, so nothing above this section changes. The addresses are in the `[ethernet]` section of `spec/accel.toml`:

| | value |
|---|---|
| board MAC | 02:4D:41:43:43:01 (locally administered; 4D 41 43 43 is "MACC") |
| board IPv4 | 192.168.10.2 |
| laptop IPv4 (static, direct cable, no router) | 192.168.10.1, netmask 255.255.255.0 |
| command port | UDP 50000 |

The host sends one or more wire frames per datagram to 192.168.10.2:50000. The board processes the frames in order and sends each reply as its own datagram, from port 50000 back to the address and port the request came from. The Python driver sends one frame per datagram (`mathaccel --port udp:192.168.10.2`). If the last frame in a datagram is missing its 0x00, the board adds one, so a frame never continues into the next datagram. The largest reply (READ_MEM of 255 words) is about 1040 bytes, so every reply fits in one datagram without IP fragmentation.

The board answers two more kinds of packets so that ordinary network tools work:

* ARP requests for 192.168.10.2, with its MAC address. The board never sends ARP requests itself; it replies to the MAC address the request came from.
* ICMP echo requests (`ping 192.168.10.2`), with the same identifier, sequence number and data, up to the standard 1472 data bytes.

Packets the board sends use IPv4 with a 20-byte header, TOS 0, identification 0 with Don't Fragment set, and TTL 64. The UDP checksum is always filled in.

What the board does with each received frame, in the order it checks:

| frame | action |
|---|---|
| PHY receive error (RX_ER), bad Ethernet FCS, or shorter than 64 bytes | discarded; LED5 lights |
| longer than 1522 bytes, or no free receive buffer | discarded; LED6 lights |
| destination MAC neither the board's nor broadcast | ignored (this includes multicast such as mDNS) |
| ARP request for 192.168.10.2 | ARP reply |
| IPv4 to 192.168.10.2 with a 20-byte header, valid header checksum, not fragmented: ICMP echo request | echo reply |
| the same, UDP to port 50000 with a payload | frames go to the command processor |
| anything else (IPv6, other ports, other addresses, IP options) | ignored |

The board does not check the UDP checksum of received datagrams; the Ethernet FCS and the CRC-16 inside every frame already cover those bytes.

Two received frames can wait in the board at once. The board passes a datagram's frames to the command processor one at a time and waits for each reply to be sent before passing on the next frame. A host that sends several requests without waiting therefore gets every reply as long as no more than two datagrams are waiting. Only one ping waits at a time; another echo request that arrives meanwhile is dropped.

Both transports are always active and share one command processor, scratchpad and set of kernels. Memory written over UDP can be read over the UART. STATUS reports `crc_errors` and `frame_errors` summed over both transports. If frames finish arriving on both transports on the same clock cycle, the UDP frame is processed and the UART frame is dropped without being counted. A frame on one transport that completes while a command from the other is running is dropped the same way. The host's timeout and retry covers both cases. In practice they only happen when two programs use the two transports at the same moment.
