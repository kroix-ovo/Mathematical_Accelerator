# Host software audit, before optimization

Baseline: `98be2be8e8b0fd9e1f600631fb54dae9638ce726`, pinned on 2026-11-09.
The earlier `533f5fe` is superseded by current main. All experimental branches
started at this exact HEAD; `worktrees/software-control` is a detached checkout.
The original main checkout and its untracked `clockInfo.txt` are untouched.

## Architecture

`sympy_to_evu.py` already performs SymPy CSE, memoized lowering, dead-code
elimination, register reuse and constant-pool deduplication. `evu_isa.py` encodes
and validates straight-line instructions, descriptors and scratchpad images.
`quadrature.py` builds fp32 tables and splits axes when hardware limits require
it. `problems.py` supplies 13 class workloads. Limits remain 256 instructions,
64 registers and 64 constants. No instruction scheduling or arithmetic rewrite
is planned, so emitted words and reduction order must remain identical.

`Device` chunks transfers at 255 words, matches one-byte tags and response
opcodes, and owns retries. `protocol.py` constructs little-endian headers and
checks lengths/CRC. `framing.py` performs bitwise CCITT-FALSE CRC and COBS.
`transport.py` moves bytes through UART, UDP or an in-process model;
`udpsim.py` puts the same model behind a local socket. EVU RUN is sent once;
STATUS and the `EVU_NOT_RUN` sentinel classify lost replies. Preserve that code.

`cref.py` compiles the existing DPI C references independently of this new
experiment. `evu_model.py` evaluates arrays through those references where
available, otherwise Python, with an exact final accumulator. These are
verification and simulation costs, not physical FPGA time. `spec/accel.toml`
and `tools/gen_spec.py` own the shared contract. CI checks generated files,
lint, software/UDP tests and separate RTL regressions. Existing `cpu_bench.py`
compares arithmetic engines; `evu_board.py` measures board upload/run/readback.
Their historical evidence is not a measurement of these language branches.

## Initial measurements

Unmodified control: **458 passed** on Windows, Python 3.11.9; one expected
warning for a deliberately refused long run. Full log is retained with evidence.
The initial mixed-workload cProfile recorded 2.968 s in its main thread:
CRC took 1.199 s self time, COBS encoding 0.283 s cumulative and decoding
0.139 s. Compilation took 0.790 s cumulative, including 0.216 s in CSE,
0.487 s in lowering and 0.293 s in fp32 constant conversion (nested times
overlap). See `evidence/software-language/windows-baseline-profile.profile.txt`.
These totals reflect this dataset, not universal component percentages; socket
server work runs on another thread and is absent from the main-thread profile.

## Selected experiment

First replace the Python CRC bit loop with a generated 256-entry table.
Accelerate exactly CRC and COBS in both native branches, preserving the same
algorithms and batching. Keep the compiler, NumPy, reference models, transport,
request matching and recovery in Python. Native CRC uses the identical table;
native COBS preserves the baseline's final empty block at exact 254-byte runs.
This offers a bounded comparison without changing any arithmetic semantics.

Do not add compiler caches, constant lookup caches, FIFO changes or receive
buffer changes without separate supporting measurements. Their likely benefit
is smaller than the measured framing bottleneck. Keep them as deferred
candidates, not claimed improvements. Deep backend research is not needed for
this bounded implementation; if future measurements justify it, tell the user
before undertaking it.

Physical UART/UDP board latency and FPGA cycles are **not measured** in this
experiment. Simulator-reported cycles only check consistency. Build a common
Linux toolchain for comparable native release benchmarks, and retain Windows
baseline validation separately. Do not combine cross-OS timings.
