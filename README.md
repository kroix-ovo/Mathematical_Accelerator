# Mathematical Accelerator

I started this project to help with quick algebra and calculus calculations in my semiconductor physics, electromagnetics, and RF classes. I want a tool that is faster to use than a calculator and lets me check numerical results while I work through the problem myself. Using generative AI to solve those problems would take over work I am supposed to do. My goal here is to build a calculation tool I understand and can use within my classes' rules.

The project combines SystemVerilog hardware for a Digilent Nexys A7-100T FPGA with a Python driver. The FPGA is intended to perform the numerical operations, while the host provides a way to enter calculations, move data, and read results. The calculation workflow will use explicit operations rather than an AI model generating solutions.

## What works now

Phases 1 and 2 are implemented: the communication and memory system, and a hand-written IEEE-754 single-precision (`fp32`) floating-point unit. The host can send commands to the FPGA design, read and write its scratchpad, and run three kernels:

- `NOP` performs no memory operation and reports one kernel clock cycle.
- `FILL` writes a chosen 32-bit value to a range of scratchpad words and reports the number of kernel clock cycles used.
- `FPU` applies one operation to every element of two arrays in the scratchpad and writes the results and the IEEE exception flags for each element. The operations are add, subtract, multiply, divide, square root, integer-to-float and float-to-integer conversion, min, max, and three compares.

The FPU rounds to nearest even, handles subnormal numbers without flushing them to zero, returns one canonical NaN, and reports the five IEEE flags (invalid, divide by zero, overflow, underflow, inexact). [Numerics](docs/numerics.md) defines every rule. Add, multiply, conversions and compares are pipelined and accept one operation per clock; divide and square root compute one bit per clock and take 33 clocks each.

The design has a 256 KiB scratchpad, a command processor, a UART link configured for 2 Mbaud at a 100 MHz clock, and a second link over the board's Ethernet port (described below). Packets use COBS framing and CRC-16 checks. Commands with invalid lengths or addresses receive an error response; corrupted packets are dropped and counted. Replies include a tag so the host can match them to requests.

The Python driver provides the same command interface through either a serial connection or a software simulator. Two reference models check the arithmetic: an exact Python model that uses rational numbers, and a C model that uses the PC's own floating-point hardware. A third comparison against NumPy checks the Python model itself.

Verification uses four UVM environments, one per combination of SystemVerilog UVM 1.2 (Vivado xsim) or pyuvm (cocotb with Verilator and Icarus), and the FPU block alone or the whole design through its UART pins. They include sequencers, layered command-to-byte sequences, virtual sequences, scoreboards, and coverage. Every FPU operation passed one million generated simulation vectors, with result bits and flags compared exactly against the C model.

On 2026-10-13 I tested phase 2 on the Nexys A7-100T through COM3. All 65,536 scratchpad words read back correctly, a 4,096-word FILL took exactly 4,097 cycles, and all 9 calculations checked by hand returned the expected answers. The board also passed 5,000 vectors for each of the 12 FPU operations, 60,000 total, with no result or flag mismatches against the Python model. The device test run passed 23 tests: 20 used the board and 3 used their own simulator. No CRC or framing errors remained afterwards.

I then changed the FTDI USB serial driver's latency timer from 16 ms to 1 ms and repeated the checks. Correctness stayed the same. The measured NOP round trip fell from 15.976 ms to 0.994 ms, about 16 times faster; memory transfer rose from 53 to 143 kB/s, about 2.7 times faster; and the FPU vector checks ran roughly 3 times faster. These are host/link measurements. The kernel still used 4 clocks per element for the pipelined operations and 33 for divide and square root. [Verification](docs/verification.md#board-results) records the board results and the before/after comparison. Being faster to use than a calculator is still a goal rather than a demonstrated result.

### Ethernet transport

The UART tops out at about 1 ms per request and 143 kB/s, so I added a second transport over the board's 10/100 Ethernet port. The FPGA drives the LAN8720A PHY over RMII at 100 Mbit/s and answers ARP and ping. Command frames arrive as UDP datagrams to 192.168.10.2, port 50000, on a direct cable to the laptop at 192.168.10.1. The frames inside each datagram are the same COBS/CRC frames the UART carries. The command processor, kernels and FPU did not change, and the UART still works next to the Ethernet path. Replies go back on the transport their request used, and STATUS adds up the link errors from both. [Protocol](docs/protocol.md#udp-transport) has the details.

The hardware side is in `hw/rtl/eth`. The FPGA makes the PHY's 50 MHz reference clock with its MMCM and sends it out phase-shifted, so its own sampling edge sits inside the PHY's data window. The design keeps two received frames, checks the Ethernet CRC-32 and IPv4 header checksum, and builds its replies with the IPv4 and UDP checksums filled in. On the host, `mathaccel --port udp:192.168.10.2 ...` uses a UDP socket instead of the serial port; every command and test works with either.

Before the board test, two new UVM environments (SystemVerilog in xsim and pyuvm in Verilator/Icarus) drove the accelerator through its RMII pins with a PHY model. Each compared every reply frame byte for byte against its own reference model, Python on one side and SystemVerilog on the other. A board-level xsim test also ran the real clocking primitives with modeled pad delays.

On 2026-10-21 I tested it on the board over a direct cable to the laptop. Ping, ARP, the full 256 KiB memory test, and 60,000 FPU vectors over UDP all passed with no mismatches and no link errors. The median NOP round trip measured 97 µs over UDP and 998 µs over the UART in the same run. Memory moved at 873 kB/s over UDP, against 143 kB/s over the UART. Some replies at first took seconds to arrive. Debug LEDs in the FPGA showed that the board had sent them on time and the laptop's network adapter was holding them. Disabling the adapter's Receive Side Scaling and updating its Realtek driver fixed that: 0 late replies in 10,000 requests. [Verification](docs/verification.md#ethernet-board-results) has the measurements and the steps that found the cause. One issue is left: after the laptop's adapter resets, the link only comes back when the cable is replugged, because the FPGA does not watch the PHY's link status.

## What the repository contains

```text
hw/rtl/          UART, packet framing, command processor, scratchpad,
                 kernel engine, fp32 units (hw/rtl/fp), Ethernet/UDP
                 transport (hw/rtl/eth), core and board tops
hw/constraints/ Nexys A7-100T pin and clock constraints
hw/scripts/     Vivado build and programming, xsim, UVM and pyuvm runners
hw/tb/          Reference vectors, SV and pyuvm UVM environments, RMII PHY
                model, board-level Ethernet test, cocotb tests
sw/mathaccel/   Python driver, UART/UDP/simulator transports, fp32 and
                Ethernet reference models, CLI
sw/tests/       Driver, framing, Ethernet, and reference-model tests
spec/accel.toml Protocol constants shared by hardware and software
tools/          Generator for the HDL and Python protocol constants
docs/           Protocol, numerics, verification, and HDL reading guide
.github/        Software and RTL simulation workflow
```

I have added comments throughout the HDL to explain the data flow, state machines, and clock timing for someone who knows basic digital logic. Start with the [HDL reading guide](docs/hdl-reading-guide.md), then follow a request through `accel_core` and `cmd_proc`. The [protocol document](docs/protocol.md) gives the exact packet and command formats.

## Next step: phase 3 functions

Phase 3 will add sine, cosine, arctangent, exponential and logarithm using CORDIC, a method that computes these functions with shifts and additions. Unlike the phase 2 operations, these results will not be bit-exact against every reference, so I need to define an error limit (in units in the last place) and measure the worst case.

## Where I want to take it

With the arithmetic units working, I want to build a direct calculation interface for algebra and calculus, including expression evaluation and numerical integration. Later hardware could add functions such as sine, cosine, exponential, and logarithm, followed by operations that come up in my classes: phasor calculations, field sums, Laplace and Poisson problems, and semiconductor carrier calculations.

The interface should make the requested operation and its inputs clear. I would like to type requests in plain text, such as "5 divided by 9", and have a small text model translate them into the explicit operations the FPGA runs. That model would only pick the operation and operands; it would not solve problems. I still need to choose the equations, set up the problem, and understand the result. I also want to measure the time spent transferring data and computing it, so any claim about speed comes from an actual calculation benchmark.

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
python hw/scripts/run_uvm.py fpu            # SystemVerilog UVM, Vivado xsim
python hw/scripts/run_uvm.py top
python hw/scripts/run_pyuvm.py fpu          # pyuvm in WSL (see docs/verification.md)
python hw/scripts/run_pyuvm.py top
python hw/scripts/run_uvm.py eth            # Ethernet, SystemVerilog UVM
python hw/scripts/run_uvm.py board          # nexys_top with the MMCM/ODDR models
python hw/scripts/run_pyuvm.py eth          # Ethernet, pyuvm
```

To build and program the Nexys A7-100T with Vivado 2023.2:

```bash
vivado -mode batch -source hw/scripts/build.tcl
vivado -mode batch -source hw/scripts/program.tcl
mathaccel --port COM5 ping
mathaccel --port COM5 memtest
mathaccel --port COM5 fp div 1 3
mathaccel --port COM5 fptest --n 5000
pytest --port COM5
```

With the Ethernet cable connected and the laptop's port set to 192.168.10.1 (netmask 255.255.255.0, no gateway), the same commands work over UDP:

```bash
ping 192.168.10.2
mathaccel --port udp:192.168.10.2 ping
mathaccel --port udp:192.168.10.2 bench
pytest --port udp:192.168.10.2
```

`--port udpsim` runs the Python model behind a local UDP socket, which tests the UDP code path without a board.

`fp` runs one operation on the board and prints the result, its flags, and the kernel's clock count. `fptest` runs seeded test vectors for every operation on the board and compares each result and flag word with the Python reference model.

Replace `COM5` with the board's serial port. The build writes its output to `build/vivado/`. LED 0 shows the heartbeat, LED 1 shows command activity, LED 2 records link errors, and LED 15 shows UART receive activity. For Ethernet, LED 3 and LED 4 flash for each received and sent frame, LED 5 stays on after a bad frame (RX error, bad CRC, or runt), LED 6 stays on after a dropped frame, and LED 7 shows that the PHY is out of reset. LEDs 8 to 14 are Ethernet debug indicators, described in [Verification](docs/verification.md#still-open).
