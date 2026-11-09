"""Render the measured four-way comparison and explicit cost assumptions."""
from __future__ import annotations

import json
import statistics
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
evidence = root / "docs/evidence/software-language"
summary = json.loads((evidence / "linux/summary.json").read_text())
names = ["software-control", "python-optimized", "rust-hybrid", "c-hybrid"]
variants = summary["variants"]
raw = {name: [json.loads((evidence / "linux" / filename).read_text())
              for filename in summary["raw_files"] if filename.endswith(f"-{name}.json")]
       for name in names}


def ns(name, case):
    return variants[name]["cases"][case]["median_ns"]


def row(label, case, divisor=1000, unit="us"):
    return "| " + label + " | " + " | ".join(f"{ns(name, case) / divisor:.3f} {unit}" for name in names) + " |\n"


lines = ["# Software language experiment\n\n",
         "Measured 2026-11-09. The optimized Python baseline is the default recommendation for occasional use. "
         "Native framing is an optional measured optimization for sustained packet work; physical-board performance "
         "remains unmeasured. See the tables below before selecting a backend.\n\n",
         "## Pinned implementations\n\n",
         f"Control: `{summary['baseline']}`. This was main at task start. Other divider work advanced main "
         "during the experiment; it was deliberately kept outside this comparison. The detached control's tracked "
         "sources were never edited. All three experimental worktrees are siblings of the existing divider worktrees "
         "under `worktrees/`, with branch names `experiment/python-optimized`, `experiment/rust-hybrid` and "
         "`experiment/c-hybrid`.\n\n",
         "Evaluated commits (subsequent report/evidence commits do not change the measured implementation):\n\n"]
for name in names:
    lines.append(f"- {name}: `{variants[name]['head']}`\n")
environment = raw["software-control"][0]
cpu_model = next((line.split(":", 1)[1].strip() for line in environment["cpu"].splitlines()
                  if line.startswith("model name")), "see raw CPU description")
lines += [f"\nEnvironment: {cpu_model}; {environment['logical_cpus']} logical CPUs; "
          f"{environment['platform']}; Python {environment['python'].splitlines()[0]}; "
          f"{environment['rust']}; {environment['cc'].splitlines()[0]}. Dependencies: " +
          ", ".join(f"{key} {value}" for key, value in environment["dependencies"].items()) + ".\n"]
lines += ["\nThe shared Python series contains the table-CRC candidate (`f4516ed`), "
          "API preservation (`2bade95`) and selection of standard-library CRC (`a485949`). "
          "Both native branches merge these exact commits; they do not independently port Python optimizations. "
          "The runner verifies identical experimental `framing.py` hashes and checks that all other production "
          "Python files match the pinned control. No RTL, numerical model, compiler transformation or protocol "
          "layout changed.\n\n",
          "## Audit and optimization decisions\n\n",
          "The pre-edit [audit](software-language-audit.md) records architecture and initial cProfile evidence. "
          "In that Windows mixed workload, CRC accounted for 1.199 seconds of main-thread self time; COBS encode "
          "and decode took 0.283 and 0.139 seconds cumulative. Compilation took 0.790 seconds cumulative, with "
          "SymPy lowering, CSE and fp32 constant conversion already present. Nested profile times overlap, "
          "and server-thread work is not included in that profile.\n\n",
          "A Python table CRC passed correctness tests but lost to `binascii.crc_hqx` in an exploratory 1 KiB "
          "Windows check: median 1000-call times were 0.1594 versus 0.00288 seconds. The final optimized Python "
          "implementation therefore uses the standard library, with explicit initial CRC and empty-input behavior. "
          "This is ordinary Python application optimization that already benefits from CPython's native library; "
          "the experiment does not pretend Python's ecosystem is entirely interpreted. "
          "[Python documents the polynomial and seed](https://docs.python.org/3.11/library/binascii.html#binascii.crc_hqx).\n\n",
          "Rust and C replace exactly CRC, COBS encoding and COBS decoding. They use equivalent table CRC and "
          "marker-based COBS loops, identical per-call batching, immutable borrowed input, a temporary output buffer "
          "and a copy into Python-owned bytes. Both hold the GIL. The common Python adapter preserves keyword "
          "arguments, seeds and CobsError. Rust uses safe slices, Vec, typed DecodeError and PyO3 0.27.2; C uses "
          "fixed-width unsigned types, checked capacities and the CPython API.\n\n",
          "SymPy, quadrature, lowering, CSE, register allocation, constant-pool logic, NumPy, CLI, packet layouts, "
          "device/request orchestration, UDP/UART, EVU timeout recovery and the existing DPI-C models remain as "
          "before. In particular, EVU RUN is still sent once, with STATUS and EVU_NOT_RUN recovery. No arithmetic "
          "order, NaN handling, rounding or accumulator changes were introduced. Compiler caches, FIFO conversion, "
          "constant-index dictionaries, NumPy copy removal and receive-buffer changes were deferred: this experiment "
          "does not have evidence that they would improve the selected workload enough to justify extra changes.\n\n",
          "## Correctness and validation\n\n",
          "| Variant | Full software suite | Additional UDP device suite |\n|---|---|---|\n"]
for name in names:
    def outcome(suffix, name=name):
        text = (evidence / "linux" / f"{name}-{suffix}.txt").read_text()
        return next(line for line in reversed(text.splitlines()) if " passed" in line)
    lines.append(f"| {name} | {outcome('software')} | {outcome('udp')} |\n")
lines += ["\nRust: three native unit tests passed. C: known vectors, bounds checks and 2,049 seeded input "
          "lengths passed under GCC AddressSanitizer plus UndefinedBehaviorSanitizer. The sanitizer run covers "
          "the C codec core, not a sanitizer build of CPython. Both native extensions also pass the full Python "
          "suite and differential tests. The unchanged control also passed the 17 new common tests in a separate "
          "run against its own source. Windows control passed 458 tests; final optimized Python's targeted "
          "compiler/driver recovery and new differential run passed 143 tests. A table-candidate full run passed "
          "475 tests. Final four-way software results above are all from the same Linux environment.\n\n",
          "Added tests compare against framing source read directly from the immutable Git commit: seeded CRCs, "
          "arbitrary initial seeds, incremental updates, exact COBS vectors and malformed-frame rejection messages. "
          "They cover 254/255-byte blocks, maximum/truncated packets, strided raw-word arrays, 255/256-word frame "
          "boundaries, UART partial reads, several frames per UDP datagram, stale tags/opcodes, and retry rules. "
          "Existing tests retain all three RUN recovery outcomes, invalid descriptors, cycle bounds, map/reduce, "
          "signed zero, NaNs, infinities and reference-model checks.\n\n",
          "All 16 timed processes produced identical fingerprint dictionaries: wire bytes, accepted and rejected "
          "compiler cases, instruction words, constant pools, memory images, class-workload results/flags and "
          "simulator cycle counts. This is strong regression evidence for this dataset, not exhaustive proof of "
          "all possible programs. Existing full repository lint and generated-spec/table checks passed. RTL "
          "simulation and physical-board tests were not rerun because the measured change is confined "
          "to host framing.\n\n",
          "The first Linux attempt had five missing-cocotb dependency failures (453 passed); after installing "
          "the declared development dependencies, the full matrix was rerun successfully. The failed attempt is "
          "retained as evidence. The expected long-run warning remains; any Numba threading warning is captured "
          "in the raw logs.\n\n",
          "## Comparable runtime measurements\n\n",
          "Four independent subprocess rounds, rotated order so each variant occupies every position. "
          "Same interpreter, dependencies, seed 20261109, hash seed 0 and single-thread NumPy/BLAS settings. "
          "Each case has three warmups. Protocol microbenchmarks have 1001 samples per process, driver cases "
          "101, compiler and class EVU cases 11. Values below are medians of the four per-round medians. "
          "Raw samples, p95, supported p99, process CPU utilization, throughput and simulator upload/run/readback "
          "phase timings are retained in JSON. Cold compilation clears SymPy caches inside the timed call; "
          "imports and process startup are excluded. Warm compilation does not add a new compiled-program cache.\n\n",
          "| Metric | Original Python | Optimized Python | Python + Rust | Python + C |\n",
          "|---|---|---|---|---|\n",
          "| Correctness | Pass | Pass | Pass | Pass |\n"]
lines += [row("Warm compile, near instruction limit", "compile_warm/near_limit", 1e6, "ms"),
          row("Cold compile, near instruction limit", "compile_cold/near_limit", 1e6, "ms"),
          row("Class compile, Gaussian", "class_compile/gaussian_2d", 1e6, "ms"),
          row("CRC, 1024 bytes", "crc/1024"), row("COBS encode, 1024 bytes", "encode/1024"),
          row("COBS decode, 1024 bytes", "decode/1024"), row("Packet encode, 1024-byte payload", "packet_encode/1024"),
          row("Packet decode, 1024-byte payload", "packet_decode/1024"),
          row("Small request, UDP simulator PING", "udpsim/ping"),
          row("4096-word write + read, UDP simulator", "udpsim/transfer/4096", 1e6, "ms"),
          row("Full EVU, 16-point CORDIC, UDP simulator", "udpsim/evu_full/cordic", 1e6, "ms"),
          row("Loaded EVU, 16-point CORDIC, UDP simulator", "udpsim/evu_loaded/cordic", 1e6, "ms")]
p99 = [statistics.median(r["benchmarks"]["packet_encode/1024"]["p99_ns"] for r in raw[name]) / 1000
       for name in names]
lines.append("| p99, packet encode 1024 bytes | " + " | ".join(f"{value:.3f} us" for value in p99) + " |\n")
lines.append("| Peak process RSS | " + " | ".join(f"{variants[name]['peak_rss_kib_median'] / 1024:.1f} MiB"
                                                       for name in names) + " |\n")
transfer_ops = {name: statistics.median(r["benchmarks"]["udpsim/transfer/4096"]["operations_per_second"]
                                       for r in raw[name]) for name in names}
lines.append("| UDP-sim useful read+write payload throughput | " + " | ".join(
    f"{transfer_ops[name] * 32768 / 1e6:.2f} MB/s" for name in names) + " |\n")
lines.append("| Process CPU, UDP-sim 4096-word transfer | " + " | ".join(
    f"{statistics.median(r['benchmarks']['udpsim/transfer/4096']['process_cpu_percent'] for r in raw[name]):.1f}%"
    for name in names) + " |\n")
lines += ["| Implementation effort | Existing | Small stdlib adapter | New Rust crate, bindings, build/test tools "
          "| New C core, bindings, build/test tools |\n",
          "| Maintenance complexity | Existing | Low incremental | Toolchain + Cargo/PyO3 and FFI "
          "| Toolchain + manual bounds/ownership and FFI |\n\n",
          "These are software-only results. The UDP server is a functional Python model in the same process; "
          "its framing is accelerated too, so its gains cannot be projected onto a physical FPGA link. The "
          "in-process simulator has no physical transport latency. Reported model cycles are consistency checks, "
          "not measured FPGA time; they match across languages because bytecode and hardware were unchanged. "
          "For physical-board large-transfer throughput, end-to-end latency, p99 latency, kernel time and energy: "
          "**not measured**. No fixed bitstream and physical transport were configured for this matrix.\n\n",
          "The per-process p99 is descriptive (about ten tail observations per 1001-sample protocol case). "
          "Driver and compiler sample counts do not support p99; no p99.9 claims are made. Process peak RSS "
          "includes imports, reference-library initialization and all workloads; it is not per-operation memory. "
          "Process CPU percentages include the local simulation server thread. The laptop was not isolated or "
          "affinity-pinned, and WSL scheduling/background work can affect small differences. No universal compiler "
          "speedup should be inferred: production compiler source is identical.\n\n",
          "## Native development and total cost\n\n",
          "Measured release-build command wall times were 20.784 s for Rust and 1.602 s for C, excluding toolchain "
          "installation and dependency downloads. These are single builds, not stable comparative build benchmarks. "
          "Rust uses locked PyO3 dependencies and maturin; C uses setuptools and the CPython C API. Neither adds "
          "a new Python runtime dependency to the original application. Rust avoids unsafe code in the local crate; "
          "C requires explicit bounds and buffer cleanup. Both still require FFI and platform-build maintenance. "
          "The source trees and test/build scripts are small enough to review directly; lines of code are a "
          "complexity proxy, not a winner criterion. No actual human engineering or debugging hours were measured.\n\n",
          "For J = wd Td + wb Tb + wm Tm + we N Te, let delta K be the weighted incremental native "
          "engineering/debugging/maintenance cost, expressed in seconds, and delta t the measured saving per "
          "operation against optimized Python. Break-even N = delta K / (we delta t), only when delta t > 0. "
          "The following sensitivity analysis assumes we=1 and hypothetical aggregate delta K of 1, 4 or "
          "16 engineer-hours, valuing one engineering second as one runtime second. These are explicit scenarios, "
          "not estimates of this task's development effort. Higher engineering weights raise N proportionally.\n\n",
          "| Native variant | Operation | Saving vs optimized Python | N at 1 h | N at 4 h | N at 16 h |\n",
          "|---|---|---|---|---|---|\n"]
# Source counts include comments/blank lines and native unit tests, but exclude
# generated tables, lockfiles, Python wrappers and the pre-existing DPI models.
source_counts = {}
for name, pattern in (("rust-hybrid", "native/rust/src/*.rs"), ("c-hybrid", "native/c/*.[ch]")):
    files = [p for p in (root.parent / name).glob(pattern) if p.name != "crc_table.h"]
    source_counts[name] = sum(len(p.read_text().splitlines()) for p in files)
lock = tomllib.loads((root.parent / "rust-hybrid/native/rust/Cargo.lock").read_text())
external_crates = sum(p["name"] != "mathaccel-framing" for p in lock["package"])
for name in names[2:]:
    for case in ("packet_encode/1024", "udpsim/transfer/4096"):
        delta = (ns("python-optimized", case) - ns(name, case)) / 1e9
        values = [f"{hours * 3600 / delta:,.0f}" for hours in (1, 4, 16)] if delta > 0 else ["never"] * 3
        lines.append(f"| {name} | {case} | {delta * 1e6:.3f} us | " + " | ".join(values) + " |\n")
packet_winner = min(names, key=lambda name: ns(name, "packet_encode/1024"))
transfer_winner = min(names, key=lambda name: ns(name, "udpsim/transfer/4096"))
lines += ["\nThe UDP-simulator break-even rows apply only to that simulator workload; physical transport savings "
          "are unknown. Runtime CPU/energy prices can be substituted for we, but there is no measured board "
          "energy or maintenance-history dataset here.\n\n",
          f"New native source (including comments, blanks and unit tests): Rust {source_counts['rust-hybrid']} "
          f"lines, C {source_counts['c-hybrid']} lines. This excludes generated tables, lockfiles and Python "
          f"build/adapter code. Cargo.lock contains {external_crates} external Rust crates (including build-time "
          "dependencies); the C core links no third-party codec library and uses the existing Python headers. "
          "Build tools are separate from runtime dependencies.\n\n",
          "For occasional interactive computations, use optimized Python: the stdlib CRC removes substantial "
          "measured overhead with little build burden. For repeated large batches, native framing is useful when "
          "framing is actually on the critical path and execution counts justify the cost above; keep symbolic "
          "compilation and mathematical workflows in Python. For continuous host-to-FPGA traffic, retain both "
          "native options for a physical-board follow-up under one fixed bitstream and link configuration. "
          "Select by measured link workload and team/toolchain preference; these simulations cannot establish "
          "a physical-board or universal language winner.\n\n",
          f"The lowest measured median for 1024-byte packet encoding was {packet_winner}; for a "
          f"4096-word UDP-simulator write/read transaction it was {transfer_winner}. "
          "These are workload-specific timing rankings. For packet encoding, native speedups over optimized "
          "Python were " + ", ".join(
              f"{name}: {ns('python-optimized', 'packet_encode/1024') / ns(name, 'packet_encode/1024'):.2f}x"
              for name in names[2:]) + ". The native round-median ranges were " + ", ".join(
                  f"{name}: {min(variants[name]['cases']['packet_encode/1024']['round_medians_ns']) / 1000:.2f}"
                  f"..{max(variants[name]['cases']['packet_encode/1024']['round_medians_ns']) / 1000:.2f} us"
                  for name in names[2:]) + "; assess this dispersion before treating small differences "
          "as decisive.\n\n",
          "## Reproduction and raw evidence\n\n",
          "Use a common Python >=3.11 virtual environment, install the repository development dependencies "
          "and the pinned [Linux package list](evidence/software-language/linux-requirements.txt), and build "
          "each native backend locally. Native branch instructions are in `native/rust/README.md` and "
          "`native/c/README.md`. Then, from the optimized Python checkout:\n\n",
          "```sh\npython tools/run_language_experiment.py --worktrees .. \\\n"
          "  --output docs/evidence/software-language/reproduction --tests --rounds 4 --samples 1001\n```\n\n",
          "[Summary](evidence/software-language/linux/summary.json) lists all 16 raw result files and exact "
          "evaluated hashes. Each raw file includes OS/kernel, full CPU description, Python/compiler versions, "
          "dependencies, fixed clock specification, samples, fingerprints, phase timing and CPU statistics. "
          "The evidence folder also retains initial profiling, candidate timings, full test logs, sanitizer "
          "results, build output and lint. Benchmarks run against the unchanged control via --root and never "
          "install one variant over another. Native binaries and build directories remain local and ignored.\n"]
(root / "docs/software-language-experiment.md").write_text("".join(lines))
print(root / "docs/software-language-experiment.md")
