# Software language experiment

Measured 2026-11-09. The optimized Python baseline is the default recommendation for occasional use. Native framing is an optional measured optimization for sustained packet work. This first study did not measure physical-board performance. The subsequent [physical FPGA comparison](software-language-board-experiment.md) measures sustained transfers and gives the recommendation for that workload.

## Pinned implementations

Control: `98be2be8e8b0fd9e1f600631fb54dae9638ce726`. This was main at task start. Other divider work advanced main during the experiment; it was deliberately kept outside this comparison. The detached control's tracked sources were never edited. All three experimental worktrees are siblings of the existing divider worktrees under `worktrees/`, with branch names `experiment/python-optimized`, `experiment/rust-hybrid` and `experiment/c-hybrid`.

Evaluated commits (subsequent report/evidence commits do not change the measured implementation):

- software-control: `98be2be8e8b0fd9e1f600631fb54dae9638ce726`
- python-optimized: `4562e49b38670b6de129a49ee01a38f2c506f028`
- rust-hybrid: `581f3b484c0b4a991c05ec997e2b2adc9c63d0e3`
- c-hybrid: `629587f4de549ca9da4be766a41a6e6702735db9`

Environment: 11th Gen Intel(R) Core(TM) i7-11800H @ 2.30GHz; 16 logical CPUs; Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.35; Python 3.11.18 (main, Oct 26 2026, 14:05:07) [Clang 23.1.3 ]; rustc 1.99.1 (58cf8f9c3 2026-10-13); gcc (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0. Dependencies: numpy 2.4.4, sympy 1.14.0, mpmath 1.3.0, pyserial 3.5.

The shared Python series contains the table-CRC candidate (`f4516ed`), API preservation (`2bade95`) and selection of standard-library CRC (`a485949`). Both native branches merge these exact commits; they do not independently port Python optimizations. The runner verifies identical experimental `framing.py` hashes and checks that all other production Python files match the pinned control. No RTL, numerical model, compiler transformation or protocol layout changed.

## Audit and optimization decisions

The pre-edit [audit](software-language-audit.md) records architecture and initial cProfile evidence. In that Windows mixed workload, CRC accounted for 1.199 seconds of main-thread self time; COBS encode and decode took 0.283 and 0.139 seconds cumulative. Compilation took 0.790 seconds cumulative, with SymPy lowering, CSE and fp32 constant conversion already present. Nested profile times overlap, and server-thread work is not included in that profile.

A Python table CRC passed correctness tests but lost to `binascii.crc_hqx` in an exploratory 1 KiB Windows check: median 1000-call times were 0.1594 versus 0.00288 seconds. The final optimized Python implementation therefore uses the standard library, with explicit initial CRC and empty-input behavior. This is ordinary Python application optimization that already benefits from CPython's native library; the experiment does not pretend Python's ecosystem is entirely interpreted. [Python documents the polynomial and seed](https://docs.python.org/3.11/library/binascii.html#binascii.crc_hqx).

Rust and C replace exactly CRC, COBS encoding and COBS decoding. They use equivalent table CRC and marker-based COBS loops, identical per-call batching, immutable borrowed input, a temporary output buffer and a copy into Python-owned bytes. Both hold the GIL. The common Python adapter preserves keyword arguments, seeds and CobsError. Rust uses safe slices, Vec, typed DecodeError and PyO3 0.27.2; C uses fixed-width unsigned types, checked capacities and the CPython API.

SymPy, quadrature, lowering, CSE, register allocation, constant-pool logic, NumPy, CLI, packet layouts, device/request orchestration, UDP/UART, EVU timeout recovery and the existing DPI-C models remain as before. In particular, EVU RUN is still sent once, with STATUS and EVU_NOT_RUN recovery. No arithmetic order, NaN handling, rounding or accumulator changes were introduced. Compiler caches, FIFO conversion, constant-index dictionaries, NumPy copy removal and receive-buffer changes were deferred: this experiment does not have evidence that they would improve the selected workload enough to justify extra changes.

## Correctness and validation

| Variant | Full software suite | Additional UDP device suite |
|---|---|---|
| software-control | 458 passed, 2 warnings in 51.44s | 30 passed in 2.47s |
| python-optimized | 475 passed, 2 warnings in 66.93s (0:01:06) | 30 passed in 1.84s |
| rust-hybrid | 475 passed, 2 warnings in 58.12s | 30 passed in 1.22s |
| c-hybrid | 475 passed, 2 warnings in 44.81s | 30 passed in 1.54s |

Rust: three native unit tests passed. C: known vectors, bounds checks and 2,049 seeded input lengths passed under GCC AddressSanitizer plus UndefinedBehaviorSanitizer. The sanitizer run covers the C codec core, not a sanitizer build of CPython. Both native extensions also pass the full Python suite and differential tests. The unchanged control also passed the 17 new common tests in a separate run against its own source. Windows control passed 458 tests; final optimized Python's targeted compiler/driver recovery and new differential run passed 143 tests. A table-candidate full run passed 475 tests. Final four-way software results above are all from the same Linux environment.

Added tests compare against framing source read directly from the immutable Git commit: seeded CRCs, arbitrary initial seeds, incremental updates, exact COBS vectors and malformed-frame rejection messages. They cover 254/255-byte blocks, maximum/truncated packets, strided raw-word arrays, 255/256-word frame boundaries, UART partial reads, several frames per UDP datagram, stale tags/opcodes, and retry rules. Existing tests retain all three RUN recovery outcomes, invalid descriptors, cycle bounds, map/reduce, signed zero, NaNs, infinities and reference-model checks.

All 16 timed processes produced identical fingerprint dictionaries: wire bytes, accepted and rejected compiler cases, instruction words, constant pools, memory images, class-workload results/flags and simulator cycle counts. This is strong regression evidence for this dataset, not exhaustive proof of all possible programs. Existing full repository lint and generated-spec/table checks passed. RTL simulation and physical-board tests were not rerun because the measured change is confined to host framing.

The first Linux attempt had five missing-cocotb dependency failures (453 passed); after installing the declared development dependencies, the full matrix was rerun successfully. The failed attempt is retained as evidence. The expected long-run warning remains; any Numba threading warning is captured in the raw logs.

## Comparable runtime measurements

Four independent subprocess rounds, rotated order so each variant occupies every position. Same interpreter, dependencies, seed 20261109, hash seed 0 and single-thread NumPy/BLAS settings. Each case has three warmups. Protocol microbenchmarks have 1001 samples per process, driver cases 101, compiler and class EVU cases 11. Values below are medians of the four per-round medians. Raw samples, p95, supported p99, process CPU utilization, throughput and simulator upload/run/readback phase timings are retained in JSON. Cold compilation clears SymPy caches inside the timed call; imports and process startup are excluded. Warm compilation does not add a new compiled-program cache.

| Metric | Original Python | Optimized Python | Python + Rust | Python + C |
|---|---|---|---|---|
| Correctness | Pass | Pass | Pass | Pass |
| Warm compile, near instruction limit | 11.128 ms | 11.215 ms | 10.046 ms | 11.310 ms |
| Cold compile, near instruction limit | 75.210 ms | 71.122 ms | 66.024 ms | 81.716 ms |
| Class compile, Gaussian | 1.602 ms | 2.051 ms | 1.522 ms | 1.922 ms |
| CRC, 1024 bytes | 1104.611 us | 2.800 us | 3.123 us | 3.188 us |
| COBS encode, 1024 bytes | 55.908 us | 52.977 us | 1.418 us | 1.933 us |
| COBS decode, 1024 bytes | 2.646 us | 2.578 us | 0.632 us | 0.875 us |
| Packet encode, 1024-byte payload | 1338.691 us | 55.903 us | 5.553 us | 7.334 us |
| Packet decode, 1024-byte payload | 1007.617 us | 6.631 us | 4.771 us | 6.564 us |
| Small request, UDP simulator PING | 183.317 us | 131.362 us | 98.654 us | 103.537 us |
| 4096-word write + read, UDP simulator | 85.961 ms | 12.514 ms | 5.141 ms | 5.233 ms |
| Full EVU, 16-point CORDIC, UDP simulator | 2.020 ms | 1.212 ms | 1.232 ms | 1.226 ms |
| Loaded EVU, 16-point CORDIC, UDP simulator | 0.815 ms | 0.671 ms | 0.639 ms | 0.661 ms |
| p99, packet encode 1024 bytes | 2613.436 us | 137.606 us | 14.060 us | 21.490 us |
| Peak process RSS | 84.4 MiB | 84.3 MiB | 84.4 MiB | 84.2 MiB |
| UDP-sim useful read+write payload throughput | 0.36 MB/s | 2.57 MB/s | 6.25 MB/s | 6.13 MB/s |
| Process CPU, UDP-sim 4096-word transfer | 99.2% | 98.5% | 97.6% | 97.7% |
| Implementation effort | Existing | Small stdlib adapter | New Rust crate, bindings, build/test tools | New C core, bindings, build/test tools |
| Maintenance complexity | Existing | Low incremental | Toolchain + Cargo/PyO3 and FFI | Toolchain + manual bounds/ownership and FFI |

These are software-only results. The UDP server is a functional Python model in the same process; its framing is accelerated too, so its gains cannot be projected onto a physical FPGA link. The in-process simulator has no physical transport latency. Reported model cycles are consistency checks, not measured FPGA time; they match across languages because bytecode and hardware were unchanged. For physical-board large-transfer throughput, end-to-end latency, p99 latency, kernel time and energy: **not measured**. No fixed bitstream and physical transport were configured for this matrix.

The per-process p99 is descriptive (about ten tail observations per 1001-sample protocol case). Driver and compiler sample counts do not support p99; no p99.9 claims are made. Process peak RSS includes imports, reference-library initialization and all workloads; it is not per-operation memory. Process CPU percentages include the local simulation server thread. The laptop was not isolated or affinity-pinned, and WSL scheduling/background work can affect small differences. No universal compiler speedup should be inferred: production compiler source is identical.

## Native development and total cost

Measured release-build command wall times were 20.784 s for Rust and 1.602 s for C, excluding toolchain installation and dependency downloads. These are single builds, not stable comparative build benchmarks. Rust uses locked PyO3 dependencies and maturin; C uses setuptools and the CPython C API. Neither adds a new Python runtime dependency to the original application. Rust avoids unsafe code in the local crate; C requires explicit bounds and buffer cleanup. Both still require FFI and platform-build maintenance. The source trees and test/build scripts are small enough to review directly; lines of code are a complexity proxy, not a winner criterion. No actual human engineering or debugging hours were measured.

For J = wd Td + wb Tb + wm Tm + we N Te, let delta K be the weighted incremental native engineering/debugging/maintenance cost, expressed in seconds, and delta t the measured saving per operation against optimized Python. Break-even N = delta K / (we delta t), only when delta t > 0. The following sensitivity analysis assumes we=1 and hypothetical aggregate delta K of 1, 4 or 16 engineer-hours, valuing one engineering second as one runtime second. These are explicit scenarios, not estimates of this task's development effort. Higher engineering weights raise N proportionally.

| Native variant | Operation | Saving vs optimized Python | N at 1 h | N at 4 h | N at 16 h |
|---|---|---|---|---|---|
| rust-hybrid | packet_encode/1024 | 50.350 us | 71,499,503 | 285,998,014 | 1,143,992,056 |
| rust-hybrid | udpsim/transfer/4096 | 7373.228 us | 488,253 | 1,953,012 | 7,812,047 |
| c-hybrid | packet_encode/1024 | 48.569 us | 74,122,116 | 296,488,465 | 1,185,953,859 |
| c-hybrid | udpsim/transfer/4096 | 7281.786 us | 494,384 | 1,977,537 | 7,910,147 |

The UDP-simulator break-even rows apply only to that simulator workload; physical transport savings are unknown. Runtime CPU/energy prices can be substituted for we, but there is no measured board energy or maintenance-history dataset here.

New native source (including comments, blanks and unit tests): Rust 142 lines, C 162 lines. This excludes generated tables, lockfiles and Python build/adapter code. Cargo.lock contains 19 external Rust crates (including build-time dependencies); the C core links no third-party codec library and uses the existing Python headers. Build tools are separate from runtime dependencies.

For occasional interactive computations, use optimized Python: the stdlib CRC removes substantial measured overhead with little build burden. For repeated large batches, native framing is useful when framing is actually on the critical path and execution counts justify the cost above; keep symbolic compilation and mathematical workflows in Python. For continuous host-to-FPGA traffic, retain both native options for a physical-board follow-up under one fixed bitstream and link configuration. Select by measured link workload and team/toolchain preference; these simulations cannot establish a physical-board or universal language winner.

The lowest measured median for 1024-byte packet encoding was rust-hybrid; for a 4096-word UDP-simulator write/read transaction it was rust-hybrid. These are workload-specific timing rankings. For packet encoding, native speedups over optimized Python were rust-hybrid: 10.07x, c-hybrid: 7.62x. The native round-median ranges were rust-hybrid: 4.68..7.83 us, c-hybrid: 5.80..7.92 us; assess this dispersion before treating small differences as decisive.

## Reproduction and raw evidence

Use a common Python >=3.11 virtual environment, install the repository development dependencies and the pinned [Linux package list](evidence/software-language/linux-requirements.txt), and build each native backend locally. Native build instructions are in `native/rust/README.md`; the C backend's are in `native/c/README.md` on the `experiment/c-hybrid` branch. Then, from the optimized Python checkout:

```sh
python tools/run_language_experiment.py --worktrees .. \
  --output docs/evidence/software-language/reproduction --tests --rounds 4 --samples 1001
```

[Summary](evidence/software-language/linux/summary.json) lists all 16 raw result files and exact evaluated hashes. Each raw file includes OS/kernel, full CPU description, Python/compiler versions, dependencies, fixed clock specification, samples, fingerprints, phase timing and CPU statistics. The evidence folder also retains initial profiling, candidate timings, full test logs, sanitizer results, build output and lint. Benchmarks run against the unchanged control via --root and never install one variant over another. Native binaries and build directories remain local and ignored. The 16 raw `round-*.json` files and `windows-baseline-profile.prof` are not on main, to keep the repository small. They are on the `experiment/python-optimized` branch at commit `10e312d`. The summary and text logs are kept here.
