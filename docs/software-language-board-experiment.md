# Physical FPGA: Python, optimized Python, Rust and C

**Recommendation for sustained-transfer stress testing: `rust-hybrid`.** The highest equal-weight sustained-throughput score was `rust-hybrid`. The score is the geometric mean of the six sustained read, write and write/read throughput speedups over the original Python control. This workload mix is stated explicitly below.

## What ran

All four implementations drove the physical Nexys A7 over Ethernet, from the same WSL2 Python environment. The physical link was 100 Mbps; the FPGA clock was 100 MHz and the device reported protocol v0.4. Windows COM3 also answered the initial PING/STATUS probe, but UART is not part of the four-way timing comparison. No simulator was used for measured requests.

The bitstream was programmed once through Vivado 2023.2/JTAG and held fixed: SHA-256 `4fd517e3d5cd29f11f5fe4af778f43312bf70b0c445efbe6329a0f7c13dad58a`; hardware source commit `3b2aa73d7bedc081cc658c514e33f603f05c0a33`, main snapshot `0a19084e5bebeaffdbe08c43e588467c77101189`. Routed WNS/WHS: [0.21, 0.018] ns. The board manifest and programming transcript are retained with the evidence.

This FPGA contains the newer divider; the host experiment remains pinned to its original control `98be2be8e8b0fd9e1f600631fb54dae9638ce726`. All four host branches use the same existing protocol and produce identical programs/images. Their original divider cycle estimates do not describe the newer hardware, so this comparison checks actual FPGA cycles for cross-implementation equality, rather than treating those old model bounds as hardware performance predictions. Result and flag comparisons still use the existing bit-accurate mathematical reference models.

Evaluated host commits:

- software-control: `98be2be8e8b0fd9e1f600631fb54dae9638ce726`
- python-optimized: `707da1d1d13ec82bf61840549cc96eaec974b769`
- rust-hybrid: `6e6a99d40393c6ecf3b48acf6c13c90880233e00`
- c-hybrid: `09f291cef9690c251f49b802701c2f3e949960bc`

Common platform: Linux-6.18.58.1-microsoft-standard-WSL2-x86_64-with-glibc2.35; Python 3.11.18 (main, Oct 26 2026, 14:05:07) [Clang 23.1.3 ]. The pinned dependency list and CPU/compiler details (host-environment.txt) are in the evidence folder. Native release extensions were prepared before testing, and each raw result records its binary SHA-256 and selected backend. The control's tracked sources were untouched; all three experimental framing source hashes match.

## Method and correctness

Four independent subprocess rounds in rotated order: control/Python/Rust/C, Python/Rust/C/control, Rust/C/control/Python, C/control/Python/Rust. This gives every implementation every order position. All use hash seed 0, workload seed 20261111, identical bytes, packet sizes, batching, transport, request matching, timeout and retry rules. BLAS/OpenMP thread counts are fixed to one. Each case has three warmups. Six sustained windows per process each last at least six seconds and 11 operations. That is at least 576 seconds of measured sustained-window exposure across the matrix, plus correctness, request-latency and EVU tests.

The same seeded uint32 words include zeros, signed zero, infinities, signaling/quiet NaN payloads and arbitrary patterns. Reads are checked after every operation. Write-only windows read back their entire range at completion; the driver retains normal acknowledgements on every frame. Scratchpad-end and 255/256-word frame boundaries are checked. All 18 FPU operations use 128 raw operand patterns against the independent Python reference. Every one of the 22 existing mathematical rules, up to 2,097,152 points, is checked bit-for-bit, including flags and aux. Additional map, reduce, simple-equation, full-upload and preloaded-image cases are measured repeatedly.

All 16 retained complete runs passed. Dataset/image hashes, all mathematical results/flags, FPU/EVU aux values and actual hardware cycle counts match across all four implementations and rounds. During the timed/correctness traffic, the board's CRC and frame error counters did not increase.

Each process also passed real-board recovery with three deterministic injected faults: discard the outgoing RUN (not started), discard its completed reply (finished), and report an immediate receive timeout while a long RUN is executing (running). Every logical RUN was requested once, with no automatic RUN retransmission. The running case was allowed to complete, and STATUS/flags confirmed completion. These are synthetic loss tests on real hardware, not claims of naturally observed packet loss. STATUS cleanup retries are safe and are outside all timed cases.

The initial pilot exposed two test-harness issues: a wrong reference-result field name, and a cleanup STATUS query dropped behind an already queued query. Both were corrected before the matrix; the failed pilot logs and successful pilot are retained. Production driver code was not changed.

The third-round control process later stopped during fault-test image setup because the synthetic 5 ms receive timeout was also affecting that upload. The short timeout was restricted to the post-RUN recovery query, and that interrupted process was rerun in full. The other completed JSON measurements were validated and reused. The failed log is preserved; timed code, production drivers, workload inputs and FPGA configuration were unchanged by this harness correction.

## Sustained physical-link performance

Values are medians of the four per-round results. MB/s uses decimal payload bytes. A write/read transaction counts both directions' useful payload; it is not a claim of one-way line rate. Sustained throughput includes validation overhead and loop overhead; operation latency excludes the subsequent host-side integrity assertion.

| Workload, useful payload throughput | Original Python | Optimized Python | Python + Rust | Python + C |
|---|---|---|---|---|
| sustained/write/4096 | 0.872 MB/s | 2.562 MB/s | 3.001 MB/s | 3.004 MB/s |
| sustained/read/4096 | 0.895 MB/s | 2.863 MB/s | 2.860 MB/s | 2.955 MB/s |
| sustained/roundtrip/4096 | 0.918 MB/s | 2.626 MB/s | 2.979 MB/s | 2.828 MB/s |
| sustained/write/32768 | 0.876 MB/s | 2.806 MB/s | 3.215 MB/s | 3.243 MB/s |
| sustained/read/32768 | 0.908 MB/s | 3.083 MB/s | 3.076 MB/s | 3.050 MB/s |
| sustained/roundtrip/32768 | 0.874 MB/s | 2.957 MB/s | 3.309 MB/s | 3.173 MB/s |
| Equal-weight sustained score vs control | 1.000x | 3.157x | 3.448x | 3.413x |

| Latency / resource metric | Original Python | Optimized Python | Python + Rust | Python + C |
|---|---|---|---|---|
| PING | 0.181 ms | 0.135 ms | 0.131 ms | 0.130 ms |
| STATUS | 0.197 ms | 0.130 ms | 0.131 ms | 0.129 ms |
| NOP | 0.177 ms | 0.135 ms | 0.134 ms | 0.129 ms |
| 255-word write/read | 2.139 ms | 0.674 ms | 0.583 ms | 0.597 ms |
| 256-word write/read | 2.634 ms | 0.974 ms | 0.904 ms | 0.843 ms |
| 32K-word write/read | 296.630 ms | 85.600 ms | 76.595 ms | 77.207 ms |
| Full basic equation, 64 points | 1.915 ms | 0.998 ms | 0.968 ms | 0.968 ms |
| Loaded basic equation + readback | 1.239 ms | 0.721 ms | 0.686 ms | 0.633 ms |
| Full map, 4096 points | 55.091 ms | 19.238 ms | 15.686 ms | 15.514 ms |
| Full reduce, 16384 points | 75.836 ms | 26.767 ms | 22.544 ms | 21.888 ms |
| Loaded reduce + readback | 1.486 ms | 1.562 ms | 1.477 ms | 1.343 ms |
| 32K write/read pooled p95 | not estimated (81 samples) | 114.515 ms | 94.385 ms | 112.423 ms |
| 32K write/read pooled p99 | not estimated (81 samples) | not estimated (273 samples) | not estimated (304 samples) | not estimated (295 samples) |
| 32K write/read pooled p99.9 | not estimated (81 samples) | not estimated (273 samples) | not estimated (304 samples) | not estimated (295 samples) |
| 32K write/read process CPU | 66.7% | 19.9% | 12.9% | 12.4% |
| Whole-process peak RSS | 186.8 MiB | 187.0 MiB | 187.2 MiB | 187.1 MiB |

The FPGA cycle count is measured by the board, independent of host timing. Selected equal-cycle examples:

| Kernel | Actual FPGA cycles in all implementations | Kernel time at 100 MHz |
|---|---|---|
| evu/basic | 316 | 0.003 ms |
| evu/map | 16,704 | 0.167 ms |
| evu/reduce | 74,750 | 0.748 ms |
| class/gaussian_2d/2 | 12,678,603 | 126.786 ms |
| class/cube_large/1 | 25,166,100 | 251.661 ms |

Language changes did not reduce any FPGA kernel cycle count. Raw EVU samples retain upload, RUN round-trip, readback and kernel-cycle fields, so host serialization/transport improvements remain separate from hardware execution. The earlier software-only report isolates CRC/COBS and packet creation/parsing; these board tests add the actual physical-link costs.

## Decision for this workload

Rust's sustained score relative to C was 1.010x. The explicit engineering tie-break first prefers optimized Python if it is within 5% of the best score; otherwise it prefers Rust if Rust is within 5% of the best. The local Rust codec has no unsafe code and avoids manual native buffer ownership. That 5% is a decision tolerance, not a statistical confidence interval. If C is more than 5% ahead of the simpler choices, the measured fastest implementation wins this rule.

- python-optimized, 32K write/read throughput per round: 2.865, 2.712, 3.049, 3.213 MB/s.
- rust-hybrid, 32K write/read throughput per round: 3.214, 3.256, 3.391, 3.363 MB/s.
- c-hybrid, 32K write/read throughput per round: 3.215, 3.130, 2.917, 3.531 MB/s.

For your sustained-transfer stress testing, move forward with **`rust-hybrid`**. Keep Python for SymPy, compilation, NumPy, CLI and test orchestration; the native choice only accelerates framing. A single small equation still has fixed request/link/kernel costs, and its absolute latency differences are much smaller than long-transfer savings. Keep the optimized Python fallback available for environments without the native build. This report recommends a branch; it does not silently merge a winner into main.

The advantage is workload-dependent: read-only throughput was essentially tied with optimized Python, while writes and mixed write/read transfers benefited more. The roughly 1% Rust-versus-C aggregate difference is smaller than the observed run-to-run variation; it does not establish statistical language superiority. Rust is the practical overall recommendation, combining native throughput with safer local buffer ownership.

A separate follow-up worth deeper research is safe pipelining of memory-transfer requests. Native framing's low process CPU usage and the remaining stop-and-wait overhead suggest that request/link waiting now matters more. That inference should be investigated before changing the protocol, and EVU RUN must retain its send-once recovery semantics. No such backend change was made here.

## Limits and reproduction

Results apply to this laptop, WSL2 network path, 100 Mbps Ethernet link and stop-and-wait protocol. They do not measure native Windows bindings, UART throughput, pipelined requests or a different link. WSL and background scheduling affect tails; four rotated rounds reduce order bias but do not provide an isolated laboratory. Whole-process RSS includes model evaluation. Process CPU includes validation and local orchestration; FPGA energy and host energy are not measured. Tail percentiles are withheld when retained sample counts do not support them.

Evidence: [summary](evidence/software-language-board/20261111/summary.json), [board manifest](evidence/software-language-board/20261111/board-manifest.json), [matrix log](evidence/software-language-board/20261111/matrix.txt). Every summary raw-file entry links by filename to the complete samples, correctness records, cycle fingerprints, fault recovery, dependency versions and native binary hashes.

After programming the exact recorded bitstream, recreate the common environment and release extensions, then run from the optimized Python checkout:

```sh
python tools/run_board_language_experiment.py \
  --output docs/evidence/software-language-board/NEW-RUN --rounds 4 --seconds 6
```

The output directory must contain a verified board-manifest.json for the programmed bitstream. A new run should use a new evidence directory to preserve prior samples.
