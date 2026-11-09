# Reading the current HDL

This guide follows the implemented design through phase 3. The hardware
accepts packets, reads and writes a scratchpad, and runs three kernels: NOP,
FILL, and FPU (elementwise fp32 arithmetic on scratchpad arrays). The FPU now
includes CORDIC for sin, cos, atan2, hypot, exp and ln. The expression engine
and physics kernels remain future work. Phase 3 simulation results are
separate from the earlier physical board results in [verification.md](verification.md).

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

I read HDL as a description of connected circuits. Two module instances
exist at the same time, and two clocked blocks respond to the same edge.
Their order in the file does not make one run before the other. Within a
clocked block, `next <= current` means a register captures the value that
was present before the edge. A local `=` assignment calculates a temporary
value for that block; it does not add another clock edge.

Some notation looks unfamiliar even when the circuit is straightforward:

| Syntax | What to picture |
|---|---|
| `word[30:23]` | Wires carrying bits 30 down to 23 of a bus |
| `args[8*k +: 8]` | Eight adjacent bits starting at bit `8*k`, selected as one byte |
| `{high, low}` | Two buses joined end to end, with `high` in the upper positions |
| `24'(value)` | A value resized to 24 bits; bits above that width are discarded |
| `'0`, `1'b0`, `8'hFF` | All zeros at the destination width, one zero bit, and eight one bits |
| `cond ? a : b` | A multiplexer selecting `a` when `cond` is true |
| `logic signed`, `>>>` | A two's-complement bus and a right shift that preserves its sign |
| `typedef struct packed` | One bus divided into named fields, such as result, flags and tag |
| `logic [31:0] mem [64]` | 64 separate words, each 32 bits wide |
| `for (genvar i = ...)` | Repeated hardware built before execution; here it creates pipeline stages |
| `function automatic` | A reusable calculation; in RTL its logic feeds the caller's circuit |

A procedural `for` loop is not automatically one iteration per clock either.
For example, a checksum helper's loop expands combinational XOR logic.
An iterative divider needs an explicit counter and registers to spread its
iterations across clocks. The distinction comes from the surrounding circuit,
not from the word `for`.

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
(valid only), and sends every request to one of six units:

- [`fp_addsub.sv`](../hw/rtl/fp/fp_addsub.sv): addition and subtraction. Line up
  the exponents, add the significands, renormalize. Six pipeline stages.
- [`fp_mul.sv`](../hw/rtl/fp/fp_mul.sv): multiply the significands on the
  FPGA's DSP48 multipliers and add the exponents. Six stages.
- [`fp_misc.sv`](../hw/rtl/fp/fp_misc.sv): integer conversions, min/max and
  compares, also six stages so all three pipelines finish in step.
- [`fp_div.sv`](../hw/rtl/fp/fp_div.sv): binary long division, one quotient
  bit per clock, one divide at a time.
- [`fp_sqrt.sv`](../hw/rtl/fp/fp_sqrt.sv): digit-by-digit square root, one root
  bit per clock, one at a time. It is separate from `fp_div`, so a square root
  can run while a divide is in progress.
- [`fp_cordic.sv`](../hw/rtl/fp/fp_cordic.sv): the six phase 3 functions,
  using a pipeline of shifts and additions with argument reduction at the input.

Every unit hands its exact (or nearly exact) answer to `round_pre` and
`round_post` in [`fp_pkg.sv`](../hw/rtl/fp/fp_pkg.sv), the only rounding logic
in the design. Read `fp_pkg.sv` first: its header explains the fp32 bit
layout, subnormal numbers, NaNs, and the guard and sticky bits that rounding
uses. A pipeline "stage" is the logic between two registers; each
`always_ff` block in those files is one stage and has a comment saying what
it does.

The precise arithmetic rules are in [`numerics.md`](numerics.md). The
testbenches are described in [`verification.md`](verification.md).

## Follow one CORDIC operation

Start with the header of `fp_cordic.sv`, which maps each function to its
datapath. I use the same stages for several functions, but carry a mode with
each request so adjacent requests can use different kinds of step.

1. **Unpack and classify.** Stage 1 reads the fp32 sign, exponent and
   significand. It handles zeros, infinities, NaNs and selected tiny inputs
   by explicit rules. A precomputed answer travels beside the arithmetic
   until the usual output clock; special inputs do not finish early.
2. **Reduce and scale.** Stages 2–5 put the input into a range the adders can
   handle accurately. Sin/cos multiply a 24-bit significand by a 96-bit
   window of `2/pi`. Whole turns can be discarded, leaving the quadrant and
   a fraction of a quarter turn. Exp uses the same multiplier with `1/ln2`.
   Atan2/hypot normalize and order the two magnitudes. Ln separates the
   exponent contribution and treats inputs close to 1 separately.
3. **Load the vector.** Stage 6 chooses starting `x`, `y`, `z`, the step
   mode and the eventual result sign. A fixed-point integer with 44 fraction
   bits represents the stored integer divided by `2^44`. For example,
   `1 << 43` represents 0.5. The binary point is an agreement between the
   stages, rather than a separate signal.
4. **Take 42 small steps.** Each generated stage has its own adders and
   registers. Circular rotation drives the angle remainder toward zero;
   vectoring drives `y` toward zero and accumulates the angle. Hyperbolic
   steps serve exp/ln and repeat selected shift amounts to converge. Linear
   steps perform division for small atan2 results and ln near 1.
5. **Normalize and round.** The last four stages select `x`, `y`, `z`, or
   the parallel small-angle result, find its leading one, restore its
   exponent and apply the shared rounding logic. Guard and sticky bits
   retain enough information about discarded bits to decide rounding.

The unit accepts one request per clock and has 52 clocks of latency. Those
are different properties: while one request is near the output, newer ones
can occupy every earlier stage. A valid bit moves with each request. An
empty stage still contains bits, but those bits do not describe an answer.

The surrounding FPU adds buffering and output arbitration. With an otherwise
free result port, a CORDIC result appears after 54 clocks. Short operations
can finish while older CORDIC operations are still running, so tags identify
which request produced each result. I reserve a FIFO entry when accepting
CORDIC work, before its result exists. Otherwise many in-flight requests
could all finish after the FIFO had filled. The age rule temporarily stops
new requests to give waiting results access to the shared output.

The kernel uses a single operation for an entire array and fetches a new
pair every four clocks. Its CORDIC cycle formula is therefore `4*count + 56`
for a nonempty array, including pipeline drain and final writes. It does
not mean each element needs 56 separate clocks of exclusive execution.

## Read the software and tests beside the hardware

The Python driver in [`device.py`](../sw/mathaccel/driver/device.py) turns
array operations into memory commands and a RUN request. The transport
moves bytes; [`protocol.py`](../sw/mathaccel/driver/protocol.py) checks packet
framing and CRC. [`model.py`](../sw/mathaccel/driver/model.py) predicts the
command's memory effects and reported cycles without simulating each clock.

For phase 3 there are two questions to test. First, does the RTL implement
the intended finite-width algorithm? The integer model in
[`cordic_model.py`](../sw/mathaccel/cordic_model.py) reproduces its truncations,
tables and shifts. The C port in
[`cordic_ref.c`](../hw/tb/uvm/dpi/cordic_ref.c) provides that same check for SV
tests. They should match result bits and flags exactly.

Second, how close is that algorithm to the mathematical function?
[`mpref.py`](../sw/mathaccel/mpref.py) evaluates with mpmath at 256-bit
precision, and [`ulp.py`](../sw/mathaccel/ulp.py) measures the difference in
fp32 spacing. The C/libm reference is a faster second opinion. Sharing a
constant table establishes consistency between implementations; it does not
establish accuracy against the mathematical function.

The testbench classes are simulation software. A sequence chooses inputs,
a driver applies them to pins, monitors record accepted requests and valid
results, and a scoreboard compares the observations with predictions.
Coverage records which cases were exercised. It is useful evidence of test
scope, but a covered case still needs its result checked.

The CLI's `fp` and `fptest` cover all eighteen operations. `fptest --op phase3`
checks the six functions against the bit-accurate model and measures their
ULP error, so the same check runs on the board and on the simulator.

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
- Change CORDIC widths in `CordicParams` in `cordic_model.py`, then run
  `python tools/gen_cordic.py`. This regenerates both `cordic_pkg.sv` and
  `cordic_tables.h`. Remeasure accuracy and timing after changing widths;
  editing a generated table alone makes the models disagree.

I keep comments focused on data movement, timing and reasons for a design
choice. First person marks a choice; the surrounding explanation describes
what the circuit does. The wording follows the clear, conversational approach
in [Google's developer writing guidance](https://developers.google.com/style/tone).

No physical board result follows from these simulation checks. A bitstream,
timing report, and board-level UART test are separate evidence.
