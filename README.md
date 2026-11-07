# Mathematical Accelerator

I started this project to help with quick algebra and calculus calculations in my semiconductor physics, electromagnetics, and RF classes. I want a tool that is faster to use than a calculator and lets me check numerical results while I work through the problem myself. Using generative AI to solve those problems would take over work I am supposed to do. My goal here is to build a calculation tool I understand and can use within my classes' rules.

The project combines SystemVerilog hardware for a Digilent Nexys A7-100T FPGA with a Python driver. The FPGA is intended to perform the numerical operations, while the host provides a way to enter calculations, move data, and read results. The calculation workflow will use explicit operations rather than an AI model generating solutions.

## What works now

Phases 1 and 2 implement the communication and memory system and a hand-written IEEE-754 single-precision (`fp32`) floating-point unit. Phase 3 adds six CORDIC functions to that FPU and protocol version 0.3. Phase 4 adds an expression unit that runs a compiled program over thousands of points on the FPGA, with an exact accumulator, so the board can do double, triple, line and flux integrals and sample gradient, divergence and curl (protocol version 0.4). All four phases have been tested on the board. The host can send commands to the FPGA design, read and write its scratchpad, and run four kernels:

- `NOP` performs no memory operation and reports one kernel clock cycle.
- `FILL` writes a chosen 32-bit value to a range of scratchpad words and reports the number of kernel clock cycles used.
- `FPU` applies one operation to every element of two arrays in the scratchpad and writes the results and the IEEE exception flags for each element. The operations are add, subtract, multiply, divide, square root, integer-to-float and float-to-integer conversion, min, max, three compares, sin, cos, atan2, hypot, exp and ln.
- `EVU` runs a small bytecode program once per point of a grid, using the same FPU, and either adds every point's result into one exactly rounded sum or writes one result per point. See [Phase 4](#phase-4-expression-unit-and-integrals).

The FPU rounds to nearest even, handles subnormal numbers without flushing them to zero, returns one canonical NaN, and reports the five IEEE flags (invalid, divide by zero, overflow, underflow, inexact). [Numerics](docs/numerics.md) defines every rule. Add, multiply, conversions and compares are pipelined and accept one operation per clock; divide and square root compute one bit per clock and take 33 clocks each.

The design has a 256 KiB scratchpad, a command processor, a UART link configured for 2 Mbaud at a 100 MHz clock, and a second link over the board's Ethernet port (described below). Packets use COBS framing and CRC-16 checks. Commands with invalid lengths or addresses receive an error response; corrupted packets are dropped and counted. Replies include a tag so the host can match them to requests.

The Python driver provides the same command interface through either a serial connection or a software simulator. Two reference models check the arithmetic: an exact Python model that uses rational numbers, and a C model that uses the PC's own floating-point hardware. A third comparison against NumPy checks the Python model itself.

I now check the design with nine UVM environments in SystemVerilog UVM 1.2 (Vivado xsim) and pyuvm (cocotb with Verilator and Icarus). I test the FPU block, the whole design through its UART and Ethernet pins, and the CORDIC unit, exact accumulator and expression unit on their own. I use sequencers, layered command-to-byte sequences, virtual sequences, scoreboards and coverage to follow what actually happens. Each of the 12 phase 2 FPU operations passed one million generated simulation vectors, with result bits and flags compared exactly against the C model.

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
                 kernel engine, fp32 units and the exact accumulator
                 (hw/rtl/fp), expression unit (hw/rtl/evu), Ethernet/UDP
                 transport (hw/rtl/eth), core and board tops
hw/constraints/ Nexys A7-100T pin and clock constraints
hw/scripts/     Vivado build and programming, xsim, UVM and pyuvm runners
hw/tb/          Reference vectors, SV and pyuvm UVM environments, RMII PHY
                model, board-level Ethernet test, cocotb tests
sw/mathaccel/   Python driver, UART/UDP/simulator transports, fp32,
                CORDIC, EVU and Ethernet reference models, SymPy
                compiler (compiler/), calculus tools (calc.py), CLI
sw/tests/       Driver, framing, Ethernet, reference-model, compiler,
                calc and CPU-benchmark tests
spec/accel.toml Protocol constants shared by hardware and software
tools/          Spec and CORDIC table generators, ULP measurement, board
                comparison, CPU benchmark and report generator
docs/           Protocol, numerics, verification, phase plans, CPU
                comparison, errors log, and HDL reading guide
.github/        Software and RTL simulation workflow
```

I have added comments throughout the HDL to explain the data flow, state machines, and clock timing for someone who knows basic digital logic. Start with the [HDL reading guide](docs/hdl-reading-guide.md), then follow a request through `accel_core` and `cmd_proc`. The [protocol document](docs/protocol.md) gives the exact packet and command formats.

## Phase 3 functions

Phase 3 adds `SIN`, `COS`, `ATAN2(y, x)`, `HYPOT`, `EXP` and `LN` as operation codes 12–17. I use a shared CORDIC pipeline: 42 stages of shifts and additions, with input reduction and output rounding for a total of 52 clocks inside the unit. It accepts one operation per clock. The FPU buffers CORDIC results when shorter operations need the result port, reserves space for work already in flight, and pauses new requests when an older queued result needs to leave.

These functions have a target error below 1 ULP, where one ULP is the spacing between nearby fp32 values. The Python integer model and its C port describe the datapath bit for bit; mpmath at 256-bit precision checks its mathematical accuracy. The tests also check flags, tags, latency, and mixed operations with a smaller 16-entry FIFO. [Numerics](docs/numerics.md#phase-3-functions) explains the rules and [Verification](docs/verification.md#phase-3-implementation-checks) records the simulation checks.

On 2026-11-03 I tested the six functions on the Nexys A7-100T over Ethernet: one million vectors per function, half of them aimed at hard cases such as inputs near multiples of π/2. Every result and flag word matched the bit-accurate model and every kernel cycle count was correct. The largest error was 0.599 ULP (atan2), and across all sample runs the largest I have measured is 0.629 ULP. These are samples, not a bound over all inputs. The full design meets 100 MHz with +0.154 ns of slack. The [measured accuracy table](docs/numerics.md#measured-accuracy) and [board results](docs/verification.md#phase-3-board-results) have the details, including what the board run doesn't cover.

The CLI's `fp` and `fptest` cover all eighteen operations; `fptest --op phase3` checks the six functions bit for bit against the CORDIC model and measures their ULP error against mpmath. The [phase 3 plan](docs/phase3-plan.md) records the original design goals. For a guided explanation of the implemented stages, start with [Follow one CORDIC operation](docs/hdl-reading-guide.md#follow-one-cordic-operation).

## Phase 4: expression unit and integrals

I got to the point where one FPU operation per array wasn't enough. For something like `x^2*sin(x)`, I had to start several kernels and keep the intermediate arrays in scratchpad. So in Phase 4 I added a small expression unit, or EVU. I compile the expression with SymPy on the laptop, upload its bytecode and tables once, and let the board work through every grid point. I can ask for one result per point, or one exact sum at the end. The arrays don't have to come back to the laptop between instructions. I wrote a [walk through one Phase 4 calculation](docs/phase4-reading-guide.md) to connect the host setup to the RTL stages.

- **31 points in flight.** I give each point a context with its own registers. A ready queue hands the FPU one instruction per clock from whichever point is ready, so while one point waits 52 clocks for a sine, the others keep the FPU busy.
- **An exact accumulator.** I add every point's result into a 320-bit fixed-point register with no rounding until the end, one term per clock. The answer doesn't depend on the order the points finish in, which matters because they finish out of order.
- **Host tools.** I put the calculation entry points in `sw/mathaccel/calc.py`: `integrate_nd`, `line_integral`, `surface_flux`, `grad`, `div`, `curl_at` and `root_find`, and `mathaccel calc` puts them on the command line. By default I run each integral again on a finer rule and report `NOT CONVERGED` if the answers disagree beyond tolerance. If that finer run cannot fit, I report that I could not check convergence. NaN, infinity and invalid operations are reported, never hidden in a number.

The part I was most curious about was whether the whole thing would still agree bit for bit once I mixed instructions and let points finish out of order. On 2026-11-05 I ran it on the board over Ethernet and the UART. All 22 integration rules of my 13 class problems, up to 2,097,152 points, matched the bit-accurate model exactly, with 0 mismatches in any result, flag or `aux` word. On every converged rule the answer was within 1.38 ULP of a 40-digit reference. 100 random programs, 62,000 map-mode results and 6 root-finding cases matched too, and every cycle count came out the same over both links. The full design meets 100 MHz with +0.287 ns of slack in 30.8% of the FPGA's LUTs. [Verification](docs/verification.md#phase-4-board-results) has the details, and the [phase 4 plan](docs/phase4-plan.md) has each milestone's gate.

### Against my laptop's CPU

I always knew the laptop would be faster in real time, and it is, on every rule. What I found more interesting was how much work I got out of each clock. I use that as an architecture comparison, with the precision and summing differences alongside it; wall time is still what tells me how long I'll wait. Its Intel i7-11800H ran at a measured 3.9 to 4.3 GHz during the tests, so it gets **39x to 43x more clock cycles per second** than my FPU's 100 MHz, and that clock gap already matters in the single-thread comparison. I measured threaded cases separately too; AVX-512 support alone does not tell me which instructions each case used.

Clock for clock, on the rules of 4,096 points or more:

| program | my FPU, clocks per point | NumPy FP64 | compiled Numba, one thread |
|---|---|---|---|
| `sin(x)` | **4.0** | 23.5 | 17.6 |
| `sin(50x)^2` | **10.0** | 29.9 | 22.2 |
| `exp(-x)` | **7.9** | 21.9 | 12.5 |
| `exp(-x^2-y^2)`, 1M points | **12.1** | 67.2 | 13.7 |
| `r^3 sin(θ) cos^2(θ)` | 16.1 | 144.6 | **4.4** |
| `(x+y+z)^2`, 2M points | 12.0 | 38.5 | **3.8** |
| `1/sqrt(x^2+y^2+c)`, 1M points | 66.6 | 63.2 | **6.1** |

I put the lowest clock count in each row in bold.

- **My FPU uses fewer clocks per point than the NumPy FP64 baseline on 8 of 10 large rules,** and fewer than compiled Numba on every program built around a sine or an exponential: 4.4x fewer on `sin`.
- **Why:** the CORDIC unit is fully pipelined, so a sine costs one issue slot, the same as an add, while the CPU runs a library routine for each one. 31 contexts hide the 52-clock latency, so the FPU issues on 88% to 100% of clocks on divide-free programs. I can feed the exact sum one term per clock, then pay a fixed 29 clocks to finish it. Grid points come from counters and tables sit in block RAM, so nothing ever waits on a cache.
- **Where the CPU wins clock for clock:** divides and square roots, because my divider takes 33 clocks and handles one at a time, and work the compiler can move out of the inner loop, because the EVU runs every instruction at every point.
- **Accuracy:** the board's answer is the same on every run and in any order. A plain fp32 loop on the CPU drifted up to 16,379 ULP on the same rules and changed with the thread count. NumPy's pairwise sum stays within 2 ULP, close to the board.
- **Real time:** repeat runs on the board were 14x to 495x slower than single-threaded Numba. The clock gap explains most of it: on `sin` a 41x clock gap shrinks to 9.3x in kernel time, because the FPU needs about a quarter of the clocks per point.

I measured the CPU's package energy too. The board's needs an external power meter, since the Nexys A7 has no sensor the design can read. [The CPU comparison](docs/phase4-cpu-comparison.md) has every table, the method, the energy, and a clock-by-clock profile of where the EVU spends its time. I kept the [measurement inputs](docs/evidence/phase4/README.md) with the source so I can rebuild those tables later. I kept the faults, fixes and still-open Ethernet issue in my [errors log](docs/errors_log.md).

## Where I want to take it

With the arithmetic units, functions and integrals working, I want to make the calculation interface quicker to use day to day. Later hardware could add operations that come up in my classes: phasor calculations, field sums, Laplace and Poisson problems, and semiconductor carrier calculations.

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
python hw/scripts/run_pyuvm.py cordic --test CordicRegressTest --n 5000
python hw/scripts/run_pyuvm.py fpu16 --test FpuRegressTest --n 3000
python hw/scripts/run_pyuvm.py accum --test AccumRegressTest
python hw/scripts/run_pyuvm.py evu --test EvuRegressTest --n 120
```

The calculus tools work on the simulator or the board:

```bash
mathaccel --port sim calc --help
python tools/evu_board.py --port udpsim --random 60
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
mathaccel --port udp:192.168.10.2 fp sin 0.5
mathaccel --port udp:192.168.10.2 fptest --op phase3 --n 100000
pytest --port udp:192.168.10.2
```

`--port udpsim` runs the Python model behind a local UDP socket, which tests the UDP code path without a board.

`fp` runs one operation on the board and prints the result, its flags, and the kernel's clock count. `fptest` runs seeded test vectors for every operation on the board and compares each result, flag word and kernel cycle count with the Python reference model.

Replace `COM5` with the board's serial port. The build writes its output to `build/vivado/`. LED 0 shows the heartbeat, LED 1 shows command activity, LED 2 records link errors, and LED 15 shows UART receive activity. For Ethernet, LED 3 and LED 4 flash for each received and sent frame, LED 5 stays on after a bad frame (RX error, bad CRC, or runt), LED 6 stays on after a dropped frame, and LED 7 shows that the PHY is out of reset. LEDs 8 to 14 are Ethernet debug indicators, described in [Verification](docs/verification.md#still-open).
