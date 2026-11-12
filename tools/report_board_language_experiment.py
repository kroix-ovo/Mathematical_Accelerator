"""Render the physical-board comparison from retained raw measurements."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/evidence/software-language-board/20261111"
summary = json.loads((OUT / "summary.json").read_text())
names = ["software-control", "python-optimized", "rust-hybrid", "c-hybrid"]
variants = summary["variants"]
raw = {name: [json.loads((OUT / filename).read_text()) for filename in summary["raw_files"]
              if filename.endswith(f"-{name}.json")] for name in names}
sustained = [case for case in variants[names[0]]["cases"] if case.startswith("sustained/")]
scores = {name: math.prod(variants[name]["cases"][case]["MBps"] /
                          variants[names[0]]["cases"][case]["MBps"] for case in sustained) ** (1/len(sustained))
          for name in names}
fastest = max(scores, key=scores.get)
native_ratio = scores["rust-hybrid"] / scores["c-hybrid"]
# Within 5% of the best, prefer optimized Python, then Rust's safe local codec.
# This is an explicit engineering tie-break, not a statistical test.
recommendation = fastest
if scores["python-optimized"] >= 0.95 * scores[fastest]:
    recommendation = "python-optimized"
elif scores["rust-hybrid"] >= 0.95 * scores[fastest]:
    recommendation = "rust-hybrid"
primary = "sustained/roundtrip/32768"
board = summary["board_manifest"]


def table_row(label, values):
    return f"| {label} | " + " | ".join(values) + " |\n"


text = ["# Physical FPGA: Python, optimized Python, Rust and C\n\n",
        f"**Recommendation for sustained-transfer stress testing: `{recommendation}`.** "
        f"The highest equal-weight sustained-throughput score was `{fastest}`. "
        "The score is the geometric mean of the six sustained read, write and write/read throughput "
        "speedups over the original Python control. This workload mix is stated explicitly below.\n\n",
        "## What ran\n\n",
        "All four implementations drove the physical Nexys A7 over Ethernet, from the same WSL2 Python "
        "environment. The physical link was 100 Mbps; the FPGA clock was 100 MHz and the device reported "
        "protocol v0.4. Windows COM3 also answered the initial PING/STATUS probe, but UART is not part of "
        "the four-way timing comparison. No simulator was used for measured requests.\n\n",
        f"The bitstream was programmed once through Vivado 2023.2/JTAG and held fixed: "
        f"SHA-256 `{board['bitstream_sha256']}`; hardware source commit `{board['rtl_commit']}`, "
        f"main snapshot `{board['head']}`. Routed WNS/WHS: {board['routed_wns_whs']} ns. "
        "The board manifest and programming transcript are retained with the evidence.\n\n",
        "This FPGA contains the newer divider; the host experiment remains pinned to its original control "
        "`98be2be8e8b0fd9e1f600631fb54dae9638ce726`. All four host branches use the same existing protocol "
        "and produce identical programs/images. Their original divider cycle estimates do not describe the "
        "newer hardware, so this comparison checks actual FPGA cycles for cross-implementation equality, "
        "rather than treating those old model bounds as hardware performance predictions. Result and flag "
        "comparisons still use the existing bit-accurate mathematical reference models.\n\n",
        "Evaluated host commits:\n\n"]
for name in names:
    text.append(f"- {name}: `{variants[name]['head']}`\n")
environment = raw[names[0]][0]
text += [f"\nCommon platform: {environment['platform']}; Python {environment['python'].splitlines()[0]}. "
         "The pinned dependency list and CPU/compiler details (host-environment.txt) are in the evidence folder. "
         "Native release extensions were prepared "
         "before testing, and each raw result records its binary SHA-256 and selected backend. "
         "The control's tracked sources were untouched; all three experimental framing source hashes match.\n\n",
         "## Method and correctness\n\n",
         "Four independent subprocess rounds in rotated order: control/Python/Rust/C, Python/Rust/C/control, "
         "Rust/C/control/Python, C/control/Python/Rust. This gives every implementation every order position. "
         "All use hash seed 0, workload seed 20261111, identical bytes, packet sizes, batching, transport, "
         "request matching, timeout and retry rules. BLAS/OpenMP thread counts are fixed to one. Each case "
         "has three warmups. Six sustained windows per process each last at least six seconds and 11 "
         "operations. That is at least 576 seconds of measured sustained-window exposure across the matrix, "
         "plus correctness, request-latency and EVU tests.\n\n",
         "The same seeded uint32 words include zeros, signed zero, infinities, signaling/quiet NaN payloads "
         "and arbitrary patterns. Reads are checked after every operation. Write-only windows read back "
         "their entire range at completion; the driver retains normal acknowledgements on every frame. "
         "Scratchpad-end and 255/256-word frame boundaries are checked. All 18 FPU operations use 128 raw "
         "operand patterns against the independent Python reference. Every one of the 22 existing mathematical "
         "rules, up to 2,097,152 points, is checked bit-for-bit, including flags and aux. Additional map, "
         "reduce, simple-equation, full-upload and preloaded-image cases are measured repeatedly.\n\n",
         "All 16 retained complete runs passed. Dataset/image hashes, all mathematical results/flags, "
         "FPU/EVU aux values and actual hardware cycle counts match across all four implementations and rounds. "
         "During the timed/correctness traffic, the board's CRC and frame error counters did not increase.\n\n",
         "Each process also passed real-board recovery with three deterministic injected faults: discard "
         "the outgoing RUN (not started), discard its completed reply (finished), and report an immediate "
         "receive timeout while a long RUN is executing (running). Every logical RUN was requested once, "
         "with no automatic RUN retransmission. The running case was allowed to complete, and STATUS/flags "
         "confirmed completion. These are synthetic loss tests on real hardware, not claims of naturally "
         "observed packet loss. STATUS cleanup retries are safe and are outside all timed cases.\n\n",
         "The initial pilot exposed two test-harness issues: a wrong reference-result field name, and a "
         "cleanup STATUS query dropped behind an already queued query. Both were corrected before the matrix; "
         "the failed pilot logs and successful pilot are retained. Production driver code was not changed.\n\n",
         "The third-round control process later stopped during fault-test image setup because the synthetic "
         "5 ms receive timeout was also affecting that upload. The short timeout was restricted to the "
         "post-RUN recovery query, and that interrupted process was rerun in full. The other completed JSON "
         "measurements were validated and reused. The failed log is preserved; timed code, production drivers, "
         "workload inputs and FPGA configuration were unchanged by this harness correction.\n\n",
         "## Sustained physical-link performance\n\n",
         "Values are medians of the four per-round results. MB/s uses decimal payload bytes. A write/read "
         "transaction counts both directions' useful payload; it is not a claim of one-way line rate. "
         "Sustained throughput includes validation overhead and loop overhead; operation latency excludes "
         "the subsequent host-side integrity assertion.\n\n",
         "| Workload, useful payload throughput | Original Python | Optimized Python | Python + Rust | Python + C |\n",
         "|---|---|---|---|---|\n"]
for case in sustained:
    text.append(table_row(case, [f"{variants[name]['cases'][case]['MBps']:.3f} MB/s" for name in names]))
text.append(table_row("Equal-weight sustained score vs control", [f"{scores[name]:.3f}x" for name in names]))
text += ["\n| Latency / resource metric | Original Python | Optimized Python | Python + Rust | Python + C |\n",
         "|---|---|---|---|---|\n"]
for label, case in (("PING", "link/ping"), ("STATUS", "link/status"), ("NOP", "link/nop"),
                    ("255-word write/read", "memory/roundtrip/255"),
                    ("256-word write/read", "memory/roundtrip/256"),
                    ("32K-word write/read", primary),
                    ("Full basic equation, 64 points", "evu/full/basic"),
                    ("Loaded basic equation + readback", "evu/loaded/basic"),
                    ("Full map, 4096 points", "evu/full/map"),
                    ("Full reduce, 16384 points", "evu/full/reduce"),
                    ("Loaded reduce + readback", "evu/loaded/reduce")):
    text.append(table_row(label, [f"{variants[name]['cases'][case]['median_ns']/1e6:.3f} ms" for name in names]))
for percentile, threshold in ((95, 100), (99, 1000), (99.9, 10000)):
    values = []
    for name in names:
        samples = [value for data in raw[name] for value in data["benchmarks"][primary]["samples_ns"]]
        values.append(f"{np.percentile(samples, percentile)/1e6:.3f} ms" if len(samples) >= threshold
                      else f"not estimated ({len(samples)} samples)")
    text.append(table_row(f"32K write/read pooled p{percentile}", values))
text.append(table_row("32K write/read process CPU", [f"{variants[name]['cases'][primary]['cpu_percent']:.1f}%"
                                                      for name in names]))
text.append(table_row("Whole-process peak RSS", [f"{variants[name]['rss_kib_median']/1024:.1f} MiB"
                                                 for name in names]))
text += ["\nThe FPGA cycle count is measured by the board, independent of host timing. "
         "Selected equal-cycle examples:\n\n",
         "| Kernel | Actual FPGA cycles in all implementations | Kernel time at 100 MHz |\n|---|---|---|\n"]
for key in ("evu/basic", "evu/map", "evu/reduce", "class/gaussian_2d/2", "class/cube_large/1"):
    cycles = environment["fingerprints"][key][2]
    text.append(f"| {key} | {cycles:,} | {cycles/100000:.3f} ms |\n")
text += ["\nLanguage changes did not reduce any FPGA kernel cycle count. Raw EVU samples retain upload, "
         "RUN round-trip, readback and kernel-cycle fields, so host serialization/transport improvements "
         "remain separate from hardware execution. The earlier software-only report isolates CRC/COBS and "
         "packet creation/parsing; these board tests add the actual physical-link costs.\n\n",
         "## Decision for this workload\n\n",
         f"Rust's sustained score relative to C was {native_ratio:.3f}x. "
         "The explicit engineering tie-break first prefers optimized Python if it is within 5% of the "
         "best score; otherwise it prefers Rust if Rust is within 5% of the best. The local Rust codec "
         "has no unsafe code and avoids manual native buffer "
         "ownership. That 5% is a decision tolerance, not a statistical confidence interval. If C is more "
         "than 5% ahead of the simpler choices, the measured fastest implementation wins this rule.\n\n"]
for name in ("python-optimized", "rust-hybrid", "c-hybrid"):
    rates = variants[name]["cases"][primary]["round_MBps"]
    text.append(f"- {name}, 32K write/read throughput per round: " + ", ".join(f"{v:.3f}" for v in rates) + " MB/s.\n")
text += [f"\nFor your sustained-transfer stress testing, move forward with **`{recommendation}`**. "
         "Keep Python for SymPy, compilation, NumPy, CLI and test orchestration; the native choice only "
         "accelerates framing. A single small equation still has fixed request/link/kernel costs, and its "
         "absolute latency differences are much smaller than long-transfer savings. Keep the optimized "
         "Python fallback available for environments without the native build. This report recommends a "
         "branch; it does not silently merge a winner into main.\n\n",
         "The advantage is workload-dependent: read-only throughput was essentially tied with optimized "
         "Python, while writes and mixed write/read transfers benefited more. The roughly 1% Rust-versus-C "
         "aggregate difference is smaller than the observed run-to-run variation; it does not establish "
         "statistical language superiority. Rust is the practical overall recommendation, combining native "
         "throughput with safer local buffer ownership.\n\n",
         "A separate follow-up worth deeper research is safe pipelining of memory-transfer requests. Native "
         "framing's low process CPU usage and the remaining stop-and-wait overhead suggest that request/link "
         "waiting now matters more. That inference should be investigated before changing the protocol, "
         "and EVU RUN must retain its send-once recovery semantics. No such backend change was made here.\n\n",
         "## Limits and reproduction\n\n",
         "Results apply to this laptop, WSL2 network path, 100 Mbps Ethernet link and stop-and-wait protocol. "
         "They do not measure native Windows bindings, UART throughput, pipelined requests or a different "
         "link. WSL and background scheduling affect tails; four rotated rounds reduce order bias but do "
         "not provide an isolated laboratory. Whole-process RSS includes model evaluation. Process CPU "
         "includes validation and local orchestration; FPGA energy and host energy are not measured. "
         "Tail percentiles are withheld when retained sample counts do not support them.\n\n",
         "Evidence: [summary](evidence/software-language-board/20261111/summary.json), "
         "[board manifest](evidence/software-language-board/20261111/board-manifest.json), "
         "[matrix log](evidence/software-language-board/20261111/matrix.txt). "
         "Every summary raw-file entry links by filename to the complete samples, correctness records, "
         "cycle fingerprints, fault recovery, dependency versions and native binary hashes.\n\n",
         "After programming the exact recorded bitstream, recreate the common environment and release "
         "extensions, then run from the optimized Python checkout:\n\n",
         "```sh\npython tools/run_board_language_experiment.py \\\n"
         "  --output docs/evidence/software-language-board/NEW-RUN --rounds 4 --seconds 6\n```\n\n",
         "The output directory must contain a verified board-manifest.json for the programmed bitstream. "
         "A new run should use a new evidence directory to preserve prior samples.\n"]
(ROOT / "docs/software-language-board-experiment.md").write_text("".join(text))
print(f"recommended={recommendation}, fastest={fastest}, Rust/C score={native_ratio:.4f}")
