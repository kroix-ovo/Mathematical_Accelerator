# Reading the current HDL

This guide follows the implemented design through phase 2. The hardware
accepts packets, reads and writes a scratchpad, and runs three kernels: NOP,
FILL, and FPU (elementwise fp32 arithmetic on scratchpad arrays). CORDIC, the
expression engine, and the physics kernels in the project plan are future work.

## The few SystemVerilog ideas you need

- `logic [7:0]` is an eight-bit signal. `[31:0]` is a 32-bit word.
- `assign` and `always_comb` describe combinational logic: outputs follow
  inputs without waiting for a clock edge.
- `always_ff @(posedge clk)` describes registers. An assignment with `<=`
  takes effect after that rising edge; expressions on the right use the old
  register values. This is why a state machine can update a counter and test
  its previous value in the same block.
- A `valid` pulse says a byte or result is available. With a `ready` signal,
  the transfer occurs on a rising edge when both are high.
- `rst` returns control state machines to idle. Scratchpad contents are set
  to zero when the FPGA is configured; a later reset does not erase them.

## Follow a request

1. [`nexys_top.sv`](../hw/rtl/top/nexys_top.sv) makes the 100 MHz core clock
   and the 50 MHz Ethernet clocks with an MMCM, and connects the reset
   button, UART pins, Ethernet PHY pins and LEDs to `accel_core`. Most
   simulations use `accel_core` directly.
2. [`uart_rx.sv`](../hw/rtl/io/uart_rx.sv) samples serial bits and pulses
   `valid` for each complete byte.
3. [`cobs_decoder.sv`](../hw/rtl/io/cobs_decoder.sv) restores zeros inside a
   packet. A zero on the wire marks the packet end.
4. [`frame_rx.sv`](../hw/rtl/io/frame_rx.sv) stores bytes and checks length
   and CRC. Only a valid frame reaches the command processor.
5. [`cmd_proc.sv`](../hw/rtl/core/cmd_proc.sv) checks opcode and argument
   lengths. It can read or write [`scratch_mem.sv`](../hw/rtl/core/scratch_mem.sv),
   or start [`kernel_engine.sv`](../hw/rtl/core/kernel_engine.sv). The kernel
   owns the memory's first port while it runs; the FPU kernel also writes
   results through the second port.
6. The command processor writes reply payload bytes to a transmit buffer.
   [`frame_tx.sv`](../hw/rtl/io/frame_tx.sv) adds header and CRC;
   [`stream_reg.sv`](../hw/rtl/io/stream_reg.sv) holds each byte between
   stages; [`cobs_encoder.sv`](../hw/rtl/io/cobs_encoder.sv) removes wire
   zeros; [`uart_tx.sv`](../hw/rtl/io/uart_tx.sv) sends the serial bits.

A request over Ethernet takes a detour at the start and the end. In
[`eth_stack.sv`](../hw/rtl/eth/eth_stack.sv),
[`rmii_rx.sv`](../hw/rtl/eth/rmii_rx.sv) turns the PHY's two-bit RMII
signals into bytes, and [`eth_rx.sv`](../hw/rtl/eth/eth_rx.sv) checks the
Ethernet CRC, stores the frame and reads its ARP, IPv4, ICMP and UDP headers.
The UDP payload is then handed, one byte per pulse, to a second
`cobs_decoder` and `frame_rx` in `accel_core`, and from there it follows steps
3 to 6 like a UART request. A small arbiter in `accel_core` picks which
`frame_rx` feeds `cmd_proc` and remembers it, so the encoded reply goes to
[`eth_tx.sv`](../hw/rtl/eth/eth_tx.sv) instead of `uart_tx`. `eth_tx` wraps
the reply in UDP, IPv4 and Ethernet headers, and
[`rmii_tx.sv`](../hw/rtl/eth/rmii_tx.sv) sends it to the PHY.
[`protocol.md`](protocol.md#udp-transport) describes what the board accepts.

For a concrete example, a FILL request contains a base word address, a count,
and a value. `cmd_proc` rejects an out-of-range span, then pulses `k_start`.
`kernel_engine` writes one word on each FILL clock. It pulses `done` after
the last write, and `cmd_proc` returns the measured cycle count.

## The FPU

The FPU kernel streams pairs of words from the scratchpad into
[`fpu.sv`](../hw/rtl/fp/fpu.sv) and writes each result and its flags back.
The FPU has one request port (valid/ready handshake) and one result port
(valid only), and sends every request to one of four units:

- [`fp_addsub.sv`](../hw/rtl/fp/fp_addsub.sv): addition and subtraction. Line up
  the exponents, add the significands, renormalize. Six pipeline stages.
- [`fp_mul.sv`](../hw/rtl/fp/fp_mul.sv): multiply the significands on the
  FPGA's DSP48 multipliers and add the exponents. Six stages.
- [`fp_misc.sv`](../hw/rtl/fp/fp_misc.sv): integer conversions, min/max and
  compares, also six stages so all three pipelines finish in step.
- [`fp_divsqrt.sv`](../hw/rtl/fp/fp_divsqrt.sv): binary long division and
  digit-by-digit square root, one result bit per clock, one operation at a time.

Every unit hands its exact (or nearly exact) answer to `round_pre` and
`round_post` in [`fp_pkg.sv`](../hw/rtl/fp/fp_pkg.sv), the only rounding logic
in the design. Read `fp_pkg.sv` first: its header explains the fp32 bit
layout, subnormal numbers, NaNs, and the guard and sticky bits that rounding
uses. A pipeline "stage" is the logic between two registers; each
`always_ff` block in those files is one stage and has a comment saying what
it does.

The precise arithmetic rules are in [`numerics.md`](numerics.md). The
testbenches are described in [`verification.md`](verification.md).

## Where to change things

- Change protocol constants in [`spec/accel.toml`](../spec/accel.toml), then
  run `python tools/gen_spec.py`. The generated HDL package and Python module
  must stay together.
- Read [`docs/protocol.md`](protocol.md) for exact packet layouts and errors.
- Add kernel behavior to the hardware and to
  [`model.py`](../sw/mathaccel/driver/model.py), then add request/response
  vectors in [`vectors.py`](../hw/tb/vectors.py). The model is a reference,
  so matching it checks consistency; it does not alone prove the specification
  is correct.
- Run `python hw/scripts/run_xsim.py` from the repository root for the
  UART-to-UART simulation. Run `pytest -q` after installing the Python package.
  The UVM environments and their run commands are in
  [`verification.md`](verification.md).

No physical board result follows from these simulation checks. A bitstream,
timing report, and board-level UART test are separate evidence.
