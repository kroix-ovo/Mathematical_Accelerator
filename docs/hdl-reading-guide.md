# Reading the current HDL

This guide follows the implemented phase 1 design. The floating-point units,
CORDIC, expression engine, and physics kernels described in the project plan
are future work. Today the hardware accepts packets, reads and writes a
scratchpad, and runs NOP and FILL.

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

1. [`nexys_top.sv`](../hw/rtl/top/nexys_top.sv) connects the 100 MHz board
   clock, reset button, UART pins, and LEDs to `accel_core`. Simulation uses
   `accel_core` directly.
2. [`uart_rx.sv`](../hw/rtl/io/uart_rx.sv) samples serial bits and pulses
   `valid` for each complete byte.
3. [`cobs_decoder.sv`](../hw/rtl/io/cobs_decoder.sv) restores zeros inside a
   packet. A zero on the wire marks the packet end.
4. [`frame_rx.sv`](../hw/rtl/io/frame_rx.sv) stores bytes and checks length
   and CRC. Only a valid frame reaches the command processor.
5. [`cmd_proc.sv`](../hw/rtl/core/cmd_proc.sv) checks opcode and argument
   lengths. It can read or write [`scratch_mem.sv`](../hw/rtl/core/scratch_mem.sv),
   or start [`kernel_engine.sv`](../hw/rtl/core/kernel_engine.sv). The one-port
   memory is owned by the kernel while it runs.
6. The command processor writes reply payload bytes to a transmit buffer.
   [`frame_tx.sv`](../hw/rtl/io/frame_tx.sv) adds header and CRC;
   [`stream_reg.sv`](../hw/rtl/io/stream_reg.sv) holds each byte between
   stages; [`cobs_encoder.sv`](../hw/rtl/io/cobs_encoder.sv) removes wire
   zeros; [`uart_tx.sv`](../hw/rtl/io/uart_tx.sv) sends the serial bits.

For a concrete example, a FILL request contains a base word address, a count,
and a value. `cmd_proc` rejects an out-of-range span, then pulses `k_start`.
`kernel_engine` writes one word on each FILL clock. It pulses `done` after
the last write, and `cmd_proc` returns the measured cycle count.

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

No physical board result follows from these simulation checks. A bitstream,
timing report, and board-level UART test are separate evidence.
