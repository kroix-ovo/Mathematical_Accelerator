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
| UART frame starts arriving while a command is running | frame dropped, `frame_errors` +1, no response |
| UDP datagram arrives while a command is running | one datagram is held and run afterwards; any more are dropped with no response and no counter (LED6 lights). See [UDP transport](#udp-transport) |
| COBS error, frame < 6 bytes, `len` ≠ actual length, `len` > max | frame dropped, `frame_errors` +1, no response |
| Unknown `op` | response with status `BAD_OP` |
| Bad payload length for op | status `BAD_LEN` |
| Address range outside scratchpad | status `BAD_ADDR` |
| Unknown kernel | status `BAD_KERNEL` |
| Kernel argument out of range (FPU op code ≥ 18, or a bad EVU descriptor or program) | status `BAD_ARG` |

The host treats a missing response as a timeout. For a command that's safe to repeat it retries with a new tag. An EVU RUN is never resent, because over UDP a resend is held and runs a second time: the driver asks STATUS instead to find out whether the run is still going, finished, or never started.

## Commands

| op | name | request payload | response payload (after status) |
|---|---|---|---|
| 0x01 | PING | — | magic:u32 ("MACC"), ver_major:u8, ver_minor:u8, scratch_addr_bits:u8, rsvd:u8, clk_hz:u32 |
| 0x02 | WRITE_MEM | addr:u32, word:u32 × n (1 ≤ n ≤ 255) | — |
| 0x03 | READ_MEM | addr:u32, count:u16 (1 ≤ count ≤ 255) | word:u32 × count |
| 0x04 | RUN | kernel:u8, args… | cycles:u32, aux:u32 |
| 0x05 | STATUS | — | crc_errors:u16, frame_errors:u16, last_cycles:u32 |

Addresses and counts are in **32-bit words**. RUN is synchronous: the response is sent when the kernel finishes, and `cycles` is the number of core clock cycles the kernel was busy. `aux` is a kernel-specific result word: the OR of every element's exception flags for the FPU kernel, the OR of every point's flags for the EVU kernel, and 0 for NOP and FILL.

Version 0.2 added the FPU kernel, the `aux` word in RUN replies, and the `BAD_ARG` status. Version 0.3 added FPU op codes 12–17 (the phase 3 functions); nothing else changed. Version 0.4 added the EVU kernel; nothing else changed.

## Kernels

| id | name | args | cycles |
|---|---|---|---|
| 0x00 | NOP | — | 1 |
| 0x01 | FILL | base:u32, count:u32, value:u32 | count + 1 |
| 0x02 | FPU | fop:u8, count:u32, a:u32, b:u32, r:u32, f:u32 | 4·count + 9 (DIV, SQRT: 33·count + 6; SIN … LN: 4·count + 56); 1 when count = 0 |
| 0x03 | EVU | desc:u32 | depends on the run, see [EVU kernel](#evu-kernel) |

### FPU kernel

For `i` in `0 .. count-1`: `r[i], f[i] = fop(a[i], b[i])`. The arguments `a`, `b`, `r` and `f` are word addresses of four arrays in the scratchpad. `r[i]` receives the 32-bit result pattern and `f[i]` receives that element's flags in bits 4..0 (NV DZ OF UF NX). Unary operations ignore `b`, but its range is still checked. The arrays must be identical or disjoint. Partially overlapping arrays give undefined results.

Checks happen in this order: payload length (22 bytes including the kernel byte), then `fop ≤ 17` (`BAD_ARG`; the limit is `fpu_op_count` in `spec/accel.toml`), then each array inside the scratchpad (`BAD_ADDR`).

| fop | op | | fop | op | | fop | op |
|---|---|---|---|---|---|---|---|
| 0 | ADD | | 6 | F2I (fp32 → int32) | | 12 | SIN |
| 1 | SUB | | 7 | MIN | | 13 | COS |
| 2 | MUL | | 8 | MAX | | 14 | ATAN2 (a = y, b = x) |
| 3 | DIV | | 9 | EQ | | 15 | HYPOT |
| 4 | SQRT | | 10 | LT | | 16 | EXP |
| 5 | I2F (int32 → fp32) | | 11 | LE | | 17 | LN |

Codes 12–17 are computed by the CORDIC unit with a target error below 1 ULP of the high-precision value; they do not promise a correctly rounded result for every input. The largest error measured so far is 0.629 ULP (atan2), on samples of a million inputs per function; [numerics.md](numerics.md#measured-accuracy) has the table. The flags follow the phase 3 rule (NX on every ordinary result except a listed set of exact cases), described in [numerics.md](numerics.md#phase-3-functions).

The exact arithmetic rules (rounding, subnormals, NaN results, flags) are in [numerics.md](numerics.md).

### EVU kernel

The expression unit runs a small program once per point of a grid. `desc` is the word address of a 16-word descriptor in the scratchpad, which names everything else the run needs. The host writes these with WRITE_MEM first, and can run the same descriptor as many times as it likes.

| word | field | meaning |
|---|---|---|
| 0 | mode | 1: reduce, add every point's output into one sum. 0: map, one result per point. |
| 1 | prog | address of the instruction words |
| 2 | prog_len | how many instruction words, 1 to 256 |
| 3 | const | address of the constants |
| 4 | const_len | how many constants, 0 to 64 |
| 5, 6, 7 | n0, n1, n2 | grid size, each 1 to 65,535. An unused dimension holds 1. At most 2^32 points in all. |
| 8 | r | where results go |
| 9 | f | where flags go |
| 10–15 | tables | six node tables, one word each: base address in bits 15..0, the dimension that indexes it in bits 17..16. A word of 0 is fine for an unused table. |

One instruction is one 32-bit word:

| bits | field | meaning |
|---|---|---|
| 31..27 | op | an FPU op code 0–17, or 31 for LOAD |
| 26 | last | this is the final instruction, and its result is the point's output |
| 25..20 | dst | destination register, 0 to 63 |
| 19..13 | a | first source. Bit 6 set: constant, low bits its number. Clear: register. For LOAD this field is the table number instead. |
| 12..6 | b | second source, coded the same way |
| 5..0 | | must be zero |

An FPU op does `R[dst] = op(a, b)`. LOAD does `R[dst] = table[a][i]`, where `i` is this point's index along the table's dimension. Points are numbered in grid order with the last dimension fastest, so point `p` is `n2·(n1·i0 + i1) + i2`. A program runs from word 0 to the first word marked last. If no word is marked last, the word at `prog_len − 1` counts as last.

In map mode `r[p]` receives the output of point `p` and `f[p]` the OR of the flags of all its instructions. In reduce mode the outputs are added exactly, with no rounding until the end, so the order points finish in can't change the answer. `r[0]` then receives the sum, rounded once to fp32, and `f[0]` the flags of that sum: NX and OF from the rounding, NV for an sNaN or +∞ and −∞ together, the same as repeated ADD would give. `aux` is the OR of the flags of every point, in both modes. Reading the results takes a READ_MEM.

Checks happen in this order, and a run that fails any of them writes nothing and changes neither `last_cycles` nor the unit's readiness for the next run:

1. Payload length: 5 bytes including the kernel byte (`BAD_LEN`).
2. All 16 descriptor words inside the scratchpad (`BAD_ADDR`).
3. Every field (`BAD_ARG`): mode 0 or 1, prog_len, const_len and each grid size in the ranges above, no table with dimension 3 or anything set above bit 17, and at most 2^32 points.
4. Every address (`BAD_ADDR`): prog, const, r and f are below 65,536, the program and constants lie inside the scratchpad, every table does, used or not, using the grid size of its dimension, and in map mode so do `r[0..points-1]` and `f[0..points-1]`.
5. Every word up to prog_len, including any after the first last bit (`BAD_ARG`): a known op, a table number below 6 for LOAD, every constant source below const_len, and bits 5..0 zero.

The first failed step decides the status. Step 5 runs while the program is copied in, so it takes a few more clocks than the others.

A program can't hang the board: it has no branches, so every point finishes. A large grid can still take a long time, and the board answers nothing else until it finishes. Two things are the host's job, because the hardware doesn't check them, and doing either gives undefined results:

* Reading a register no earlier instruction of the same point wrote. The value is whatever an earlier point left there.
* Letting `r` or `f` overlap the descriptor, program, constants or tables, or letting map mode's `r` and `f` overlap each other. Points finish out of order, so the order of those writes isn't fixed.

`cycles` isn't a fixed formula here, because it depends on how the points line up in the FPU. A run takes about 70 clocks plus `prog_len + const_len`, plus 29 in reduce mode, plus the points. With no divide or square root and a few hundred points or more, a point takes close to one clock per instruction; `docs/phase4-plan.md` has measurements. Each divide or square root in a program adds about 33 clocks per point, because that unit takes one at a time.

The Python reference model (`sw/mathaccel/driver/model.py`) implements this document exactly. It serves as the simulator backend and as the golden model for the RTL testbenches. The one thing it doesn't predict exactly is `cycles` of an EVU run: it gives a low and a high bound, and the testbenches check the real count against them.

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

The board has two receive slots. It passes a datagram's frames to the command processor one at a time and waits for each reply to be sent before passing on the next frame, and a datagram keeps its slot until its own last command has finished. So while the core is idle two datagrams can wait, but while a command is running one slot is taken by that command and only one more datagram fits. That one is held and runs when the command finishes. Anything after it is dropped with no reply and no counter, and only LED6 shows it. I measured this on the board in phase 4: during a 1.46 s EVU run a second RUN was held and ran again afterwards, and two PINGs sent after it got no reply. A host that sends several requests without waiting gets every reply as long as no more than two datagrams are waiting and nothing long is running. Only one ping waits at a time; another echo request that arrives meanwhile is dropped.

Both transports are always active and share one command processor, scratchpad and set of kernels. Memory written over UDP can be read over the UART. STATUS reports `crc_errors` and `frame_errors` summed over both transports. If frames finish arriving on both transports on the same clock cycle, the UDP frame is processed and the UART frame is dropped without being counted. A UART frame that completes while a UDP command is running is dropped the same way; a UDP datagram that arrives while a UART command is running waits in a receive slot as described above. The host's timeout and retry covers both cases. In practice they only happen when two programs use the two transports at the same moment.
