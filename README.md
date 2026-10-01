# Mathematical Accelerator

A hardware/software co-design project: a custom **fp32 math coprocessor** on a Digilent **Nexys A7-100T** (Artix-7), driven by a from-scratch Python **math agent** running on a local Ollama model. It's built to answer EM/RF and semiconductor-physics questions: vector calculus, field sums, phasors, Laplace/Poisson problems and carrier statistics.

```
you ─► REPL ─► agent loop (Ollama, tool calls) ─► SymPy (symbolic setup, show work)
                                               └► mathaccel driver ─► UART ─► FPGA coprocessor
```

* **FPGA does the numbers:** hand-written IEEE-754 fp32 units, CORDIC transcendentals, an expression-evaluation unit that runs host-compiled bytecode over quadrature grids, plus field, stencil and tridiagonal engines.
* **The host does the language and the symbols:** the agent turns a question into tool calls, SymPy sets up the math, and the driver ships data and bytecode to the board.
* **Every kernel is verified against a Python golden model:** the same reference model powers the simulator backend, the cocotb/xsim testbenches and the hardware-in-the-loop tests.

An honest note on speed: over a 2 Mbaud UART, numpy on a laptop wins most small problems. The FPGA's value here is in the architecture, bit-exact verification and cycle-count benchmarks, and in compute-heavy kernels (N-body field sums, stencil relaxation, batched expression evaluation) where the data moved is small compared with the work done.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold, toolchain, CI | ✅ |
| 1 | UART link, COBS/CRC framing, command processor, 256 KiB scratchpad, NOP/FILL kernels, Python driver + simulator | ✅ verified in xsim against the golden model |
| 2 | fp32 add/mul/div/sqrt/cvt, complex unit | next |
| 3 | CORDIC sin/cos/atan2/exp/ln | |
| 4 | Expression evaluation unit + SymPy→bytecode compiler + quadrature (Calc 3) | |
| 5 | Ollama agent + REPL | |
| 6 | EM/RF kernels: Coulomb/Biot-Savart field engine, Laplace/Poisson stencil, transmission lines | |
| 7 | Semiconductor kernels: tridiagonal solver, pn-junction Poisson, Fermi–Dirac | |
| 8 | Stretch: Ethernet/UDP, DDR2, web UI, systolic linear algebra | |

## Layout

```
spec/accel.toml          single source of truth for opcodes, status codes, kernels
tools/gen_spec.py        generates hw/rtl/pkg/accel_pkg.sv and sw/mathaccel/_generated/spec.py
hw/rtl/                  SystemVerilog (io/, core/, top/, pkg/; fp/, cordic/, kernels/ to come)
hw/constraints/          Nexys A7-100T XDC
hw/scripts/              build.tcl (Vivado batch), program.tcl, run_xsim.py
hw/tb/                   vectors.py (golden vectors), sv/ (xsim), cocotb/ (Verilator/Icarus)
sw/mathaccel/            driver/ (framing, protocol, model, transports, Device API), cli.py
sw/tests/                pytest, sim by default, `-m hw --port COMx` for the board
docs/                    protocol.md, …
```

For a beginner-friendly path through the current SystemVerilog modules, start
with [the HDL reading guide](docs/hdl-reading-guide.md).

## Quick start

```bash
python -m venv .venv
# Activate it: .venv\Scripts\Activate.ps1 on Windows PowerShell,
# or source .venv/bin/activate on macOS/Linux.
pip install -e ".[dev]"
pytest                                               # software tests on the simulator
mathaccel --port sim bench                           # driver CLI against the model
```

RTL simulation:

```bash
python hw/scripts/run_xsim.py                        # Vivado xsim, golden vectors
python hw/tb/cocotb/run.py --sim verilator           # cocotb (needs Verilator >= 5.036)
```

Build and program the board (Vivado 2023.2):

```bash
vivado -mode batch -source hw/scripts/build.tcl      # bitstream + timing/utilisation in build/vivado/
vivado -mode batch -source hw/scripts/program.tcl
mathaccel --port COM5 ping                           # use your board's COM port
mathaccel --port COM5 memtest
pytest -m hw --port COM5
```

LEDs: LD0 heartbeat, LD1 command busy, LD2 link error seen, LD15 host RX activity.

## Protocol

COBS-framed packets with a CRC-16 over UART at 2 Mbaud. See [docs/protocol.md](docs/protocol.md).
