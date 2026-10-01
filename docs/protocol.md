# Host ↔ FPGA protocol

Transport-agnostic. It's UART today (2 Mbaud 8N1, exactly 50 clocks per bit at 100 MHz) and UDP later. The constants live in `spec/accel.toml`.

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
| COBS error, frame < 6 bytes, `len` ≠ actual length, `len` > max | frame dropped, `frame_errors` +1, no response |
| Unknown `op` | response with status `BAD_OP` |
| Bad payload length for op | status `BAD_LEN` |
| Address range outside scratchpad | status `BAD_ADDR` |
| Unknown kernel | status `BAD_KERNEL` |

The host treats a missing response as a timeout and retries with a new tag.

## Commands

| op | name | request payload | response payload (after status) |
|---|---|---|---|
| 0x01 | PING | — | magic:u32 ("MACC"), ver_major:u8, ver_minor:u8, scratch_addr_bits:u8, rsvd:u8, clk_hz:u32 |
| 0x02 | WRITE_MEM | addr:u32, word:u32 × n (1 ≤ n ≤ 255) | — |
| 0x03 | READ_MEM | addr:u32, count:u16 (1 ≤ count ≤ 255) | word:u32 × count |
| 0x04 | RUN | kernel:u8, args… | cycles:u32 |
| 0x05 | STATUS | — | crc_errors:u16, frame_errors:u16, last_cycles:u32 |

Addresses and counts are in **32-bit words**. RUN is synchronous: the response is sent when the kernel finishes, and `cycles` is the number of core clock cycles the kernel was busy.

## Kernels

| id | name | args | cycles |
|---|---|---|---|
| 0x00 | NOP | — | 1 |
| 0x01 | FILL | base:u32, count:u32, value:u32 | count + 1 |

The Python reference model (`sw/mathaccel/driver/model.py`) implements this document exactly. It serves as the simulator backend and as the golden model for the RTL testbenches.
