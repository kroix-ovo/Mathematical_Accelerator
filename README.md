# Mathematical Accelerator

I started this project to help with quick algebra and calculus calculations in my semiconductor physics, electromagnetics, and RF classes. I want a tool that is faster to use than a calculator and lets me check numerical results while I work through the problem myself. Using generative AI to solve those problems would take over work I am supposed to do. My goal here is to build a calculation tool I understand and can use within my classes' rules.

The project combines SystemVerilog hardware for a Digilent Nexys A7-100T FPGA with a Python driver. The FPGA is intended to perform the numerical operations, while the host provides a way to enter calculations, move data, and read results. The calculation workflow will use explicit operations rather than an AI model generating solutions.

## What works now

The current implementation is phase 1: the communication and memory system that later arithmetic units will use. It does not yet perform floating-point algebra or calculus. The host can send commands to the FPGA design, read and write its scratchpad, and run two small kernels:

- `NOP` performs no memory operation and reports one kernel clock cycle.
- `FILL` writes a chosen 32-bit value to a range of scratchpad words and reports the number of kernel clock cycles used.

The design has a 256 KiB scratchpad, a command processor, and a UART link configured for 2 Mbaud at a 100 MHz clock. Packets use COBS framing and CRC-16 checks. Commands with invalid lengths or addresses receive an error response; corrupted packets are dropped and counted. Replies include a tag so the host can match them to requests.

The Python driver provides the same command interface through either a serial connection or a software simulator. I use a Python reference model to generate expected replies for the RTL testbenches. The existing checks cover memory transfers, NOP and FILL, malformed requests, corrupted packets, and recovery after link errors.

The phase 1 checks have passed 25 Python tests and 33 request vectors in Vivado xsim. GitHub Actions also runs the software checks and the RTL vectors through cocotb with Verilator. These results verify the simulated design; physical board operation and calculation speed still need their own measurements. Being faster to use than a calculator is a goal, not a result demonstrated by the current version.

## What the repository contains

```text
hw/rtl/          UART, packet framing, command processor, scratchpad,
                 NOP/FILL engine, and core and board top modules
hw/constraints/ Nexys A7-100T pin and clock constraints
hw/scripts/     Vivado build, programming, and xsim scripts
hw/tb/          Shared reference vectors and SystemVerilog/cocotb testbenches
sw/mathaccel/   Python driver, serial and simulator transports, and CLI
sw/tests/       Driver and framing tests
spec/accel.toml Protocol constants shared by hardware and software
tools/          Generator for the HDL and Python protocol constants
docs/           Protocol description and HDL reading guide
.github/        Software and RTL simulation workflow
```

I have added comments throughout the HDL to explain the data flow, state machines, and clock timing for someone who knows basic digital logic. Start with the [HDL reading guide](docs/hdl-reading-guide.md), then follow a request through `accel_core` and `cmd_proc`. The [protocol document](docs/protocol.md) gives the exact packet and command formats.

## Next step: phase 2 arithmetic

Phase 2 will add hand-written IEEE-754 single-precision (`fp32`) addition, multiplication, division, square root, and conversion units. These units are not implemented yet.

My verification target is more than one million random test vectors for each unit, checked against NumPy for bit-exact results. That means comparing the 32-bit result patterns, rather than accepting values because they are numerically close. Directed tests will also cover rounding, signed zero, subnormal values, overflow, infinities, and NaNs. The conversion formats and expected special-case behavior need to be defined as part of that work.

## Where I want to take it

Once the arithmetic units work, I want to build a direct calculation interface for algebra and calculus, including expression evaluation and numerical integration. Later hardware could add functions such as sine, cosine, exponential, and logarithm, followed by operations that come up in my classes: phasor calculations, field sums, Laplace and Poisson problems, and semiconductor carrier calculations.

The interface should make the requested operation and its inputs clear. I still need to choose the equations, set up the problem, and understand the result. I also want to measure the time spent transferring data and computing it, so any claim about speed comes from an actual calculation benchmark.

## Run the current version

Use Python 3.11 or newer. On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest -q
mathaccel --port sim bench
```

On Windows PowerShell, create the environment with `python -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1`, then run the same install, test, and CLI commands.

For RTL simulation, use one of the included runners:

```bash
python hw/scripts/run_xsim.py              # Requires Vivado xsim
python hw/tb/cocotb/run.py --sim verilator  # Requires Verilator >= 5.036
```

To build and program the Nexys A7-100T with Vivado 2023.2:

```bash
vivado -mode batch -source hw/scripts/build.tcl
vivado -mode batch -source hw/scripts/program.tcl
mathaccel --port COM5 ping
mathaccel --port COM5 memtest
pytest -m hw --port COM5
```

Replace `COM5` with the board's serial port. The build writes its output to `build/vivado/`. LED 0 shows the heartbeat, LED 1 shows command activity, LED 2 records link errors, and LED 15 shows host receive activity.
