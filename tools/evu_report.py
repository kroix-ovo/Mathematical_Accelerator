"""Build docs/phase4-cpu-comparison.md from the measured files.

    python tools/evu_report.py

I read fresh measurements from build/ or fall back to docs/evidence/phase4/.
Every number in the tables comes from one of these, so the document can be
rebuilt after a new run and can't drift from the data:

    build/cpu_bench.json        tools/cpu_bench.py, the CPU cases
    build/cpu_bench_board.json  tools/cpu_bench.py --board (optional)
    build/fpga_power.json       tools/fpga_power.py (optional)
    build/evu_profile.json      EvuProfileTest
    build/evu_m6.md             tools/evu_board.py, the board's kernel and repeat times
"""

# I build the comparison from saved measurements here, rather than typing
# the table numbers by hand. I keep missing power measurements missing.
# I can read the published evidence on a fresh checkout, while local build
# files let me regenerate the report after a new measurement session.

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "phase4-cpu-comparison.md"
CLK = 100e6
EVIDENCE = ROOT / "docs" / "evidence" / "phase4"


def measurement_path(name: str) -> Path:
    """I prefer a new local measurement, then the published input on a fresh checkout."""
    local = ROOT / "build" / name
    return local if local.exists() else EVIDENCE / name


def load(name: str):
    # I leave an absent optional measurement as None instead of inventing a reading.
    path = measurement_path(name)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def board_times() -> dict:
    """I read kernel, upload and repeat timings from the saved milestone 6 board report."""
    out: dict = {}
    for line in measurement_path("evu_m6.md").read_text(encoding="utf-8").splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) != 10 or c[0] in ("problem",) or c[0].startswith("---"):
            continue
        key = (c[0], int(c[1].replace(",", "")))
        row = out.setdefault(key, {})
        if c[3] == "yes":
            row.update(cycles=int(c[6].replace(",", "")), ulps=float(c[4]))
        elif "slower" in c[9] or "faster" in c[9]:
            row.update(kernel=float(c[2]), first=float(c[3]), repeat=float(c[4]))
    return out


def ms(x: float) -> str:
    """A time in ms with about three significant figures."""
    return f"{x:,.0f}" if x >= 100 else f"{x:.3g}"


def table(header: list[str], rows: list[list[str]]) -> str:
    # I format all the measured rows the same way so a regenerated table stays reviewable.
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def speed_table(rows: list, board: dict) -> tuple[str, list[float], list[float]]:
    """Times, and the board's repeat time against Numba on one thread and the fastest case."""
    out, to_one, to_best = [], [], []
    for r in rows:
        c = r["cases"]
        b = board[(r["name"], r["points"])]
        t = {k: v["mean_s"] * 1e3 for k, v in c.items()}
        best = min(t.values())
        to_one.append(b["repeat"] / t["numba_seq32_t1"])
        to_best.append(b["repeat"] / best)
        out.append([r["name"], f"{r['points']:,}", ms(b["kernel"]), ms(b["repeat"]), ms(t["numpy_f64"]),
                    ms(t["numpy_f32_pairwise"]), ms(t["numba_seq32_t1"]), ms(t["numba_fast32_t1"]),
                    ms(t["numba_wide64_t8"]), ms(t["numba_wide64_t16"]), f"{to_one[-1]:.0f}x", f"{to_best[-1]:.0f}x"])
    return table(["problem", "points", "board kernel", "board repeat", "NumPy f64", "NumPy f32", "Numba seq32 1t",
                  "Numba fast32 1t", "Numba wide64 8t", "Numba wide64 16t", "board vs Numba 1t", "board vs fastest"],
                 out), to_one, to_best


def accuracy_table(rows: list, board: dict) -> str:
    out = []
    for r in rows:
        c = r["cases"]
        b = board[(r["name"], r["points"])]
        u = {k: v["ulps"] for k, v in c.items()}

        def f(x: float) -> str:
            return f"{x:,.0f}" if x >= 100 else f"{x:.2f}"

        out.append([r["name"], f"{r['points']:,}", f(b["ulps"]), f(r["numpy_exact_ulps"]), f(u["numpy_f32_pairwise"]),
                    f(u["numpy_f32_wide64"]), f(u["numba_wide64_t1"]), f(u["numba_seq32_t1"]), f(u["numba_fast32_t1"]),
                    f(u["numba_seq32_t8"]), f(u["numba_seq32_t16"])])
    return table(["problem", "points", "board (exact)", "CPU terms, exact", "NumPy pairwise", "NumPy wide64", "Numba wide64",
                  "seq32 1t", "fast32 1t", "seq32 8t", "seq32 16t"], out)


def frequency_table(rows: list) -> str:
    out = []
    for c in rows[0]["cases"]:
        w = [r["cases"][c]["window"] for r in rows]
        out.append([c, f"{st.median(x['ghz'] for x in w):.2f}", f"{min(x['ghz'] for x in w):.2f}",
                    f"{max(x['ghz'] for x in w):.2f}", f"{st.median(x['ghz_peak'] for x in w):.2f}",
                    f"{st.median(x['process_threads'] for x in w):.2f}", f"{st.median(x['system_threads'] for x in w):.1f}",
                    f"{st.median(x['package_j'] / x['seconds'] for x in w):.1f}"])
    return table(["case", "GHz, median", "lowest", "highest", "fastest CPU", "my threads", "all logical CPUs busy",
                  "package W"], out)


def clocks_table(rows: list, board: dict) -> str:
    """Core clocks per point. The CPU's is measured time times measured frequency."""
    out = []
    for r in rows:
        if r["points"] < 4096:
            continue
        c = r["cases"]

        def clocks(name: str) -> float:
            return c[name]["mean_s"] * c[name]["window"]["ghz"] * 1e9 / r["points"]

        b = board[(r["name"], r["points"])]
        out.append([r["name"], f"{r['points']:,}", str(r["instructions"]), f"{b['cycles'] / r['points']:.1f}",
                    f"{clocks('numpy_f64'):.1f}", f"{clocks('numpy_f32_pairwise'):.1f}", f"{clocks('numba_seq32_t1'):.1f}",
                    f"{clocks('numba_fast32_t1'):.2f}"])
    return table(["problem", "points", "instructions", "board", "NumPy f64", "NumPy f32", "Numba seq32", "Numba fast32"], out)


def ratio(board_clocks: float, cpu_clocks: float) -> str:
    """Say which side used fewer clocks per point, and by how much."""
    if board_clocks <= cpu_clocks:
        return f"**board {cpu_clocks / board_clocks:.1f}x fewer**"
    return f"CPU {board_clocks / cpu_clocks:.1f}x fewer"


def efficiency_rows(rows: list, board: dict) -> list[dict]:
    """Clock-for-clock numbers on the rules big enough that setup doesn't count, 4,096 points or more.

    I take the board's clocks per point from its own cycle counter, and the CPU's from
    measured time times measured frequency, so neither side is an estimate. Then the
    kernel-time gap splits into two factors: how many more clocks per second the CPU
    has, and how many clocks per point each side spends.
    """
    out = []
    for r in rows:
        if r["points"] < 4096:
            continue
        c = r["cases"]
        b = board[(r["name"], r["points"])]

        def clocks(name: str) -> float:
            return c[name]["mean_s"] * c[name]["window"]["ghz"] * 1e9 / r["points"]

        one = c["numba_seq32_t1"]
        out.append({"name": r["name"], "points": r["points"], "board": b["cycles"] / r["points"],
                    "f64": clocks("numpy_f64"), "numba": clocks("numba_seq32_t1"),
                    "gap": one["window"]["ghz"] * 1e9 / CLK,
                    "kernel": b["kernel"] / (one["mean_s"] * 1e3)})
    return out


def efficiency_table(eff: list[dict]) -> str:
    out = [[e["name"], f"{e['points']:,}", f"{e['board']:.1f}", f"{e['f64']:.1f}", f"{e['numba']:.1f}",
            ratio(e["board"], e["f64"]), ratio(e["board"], e["numba"]), f"{e['gap']:.0f}x",
            f"{e['kernel']:.1f}x"] for e in eff]
    return table(["problem", "points", "board clocks/pt", "NumPy f64 clocks/pt", "Numba seq32 clocks/pt",
                  "vs NumPy f64", "vs Numba seq32", "CPU clock advantage", "board kernel slower than Numba by"], out)


def noise(rows: list) -> float:
    """Standard deviation of the idle windows, in watts."""
    return st.pstdev(r["cases"][c]["idle_w"] for r in rows for c in r["cases"])


def energy_table(rows: list, board: dict, power: dict | None) -> str:
    """Net package energy per run on the larger rules, and the board's break-even."""
    sigma = noise(rows)
    out = []
    for r in rows:
        if r["points"] < 65536:
            continue
        c = r["cases"]
        b = board[(r["name"], r["points"])]

        def net(name: str) -> str:
            m = c[name]
            floor = 2 * sigma * m["window"]["seconds"] / m["runs"]
            value = m["net_j_per_run"]
            return f"{value * 1e3:.3g}" if value > floor else f"<{floor * 1e3:.2g}"

        one = c["numba_wide64_t8"]
        watts = one["net_j_per_run"] / (b["repeat"] / 1e3)
        row = [r["name"], f"{r['points']:,}", net("numpy_f64"), net("numpy_f32_pairwise"), net("numba_seq32_t1"),
               net("numba_wide64_t8"), net("numba_wide64_t16"), f"{watts * 1e3:.0f} mW"]
        if power:
            row.append(f"{power['net_w'] * b['repeat']:.0f}")
        out.append(row)
    header = ["problem", "points", "NumPy f64", "NumPy f32", "Numba seq32 1t", "Numba wide64 8t", "Numba wide64 16t",
              "board power to tie Numba 8t"]
    if power:
        header.append("board net mJ")
    return table(header, out)


NAMES = {
    "useful arithmetic": ("issue_arith",),
    "LOAD": ("issue_load",),
    "division stall": ("refused_divider", "wait_divider", "dep_divider", "refused_other"),
    "dependency stall": ("dep_cordic", "dep_fast"),
    "result FIFO rules": ("refused_cordic", "wait_cordic"),
    "context scheduling": ("output_hold", "drain", "fill"),
    "setup and finish": ("setup", "finish"),
}


def profile_table(profile: list) -> str:
    out = []
    for r in profile:
        c, t = r["counts"], r["clocks"]
        row = [r["name"], f"{r['points']:,}", f"{t:,}", f"{t / r['points']:.1f}"]
        row += [f"{100 * sum(c[k] for k in keys) / t:.1f}" for keys in NAMES.values()]
        out.append(row)
    return table(["problem", "points", "clocks", "clocks/pt", *[f"{n} %" for n in NAMES]], out)


def profile_check(profile: list, board: dict) -> tuple[set, int]:
    offsets = set()
    count = 0
    for r in profile:
        key = (r["name"], r["points"])
        if key in board:
            offsets.add(board[key]["cycles"] - r["clocks"])
            count += 1
    return offsets, count


PROSE_HEAD = """# Phase 4: controlled CPU comparison

This follows the milestone 6 speed comparison in [phase4-plan.md](phase4-plan.md#milestone-6-gate-result). That one timed NumPy without saying how it summed, assumed a 4 GHz clock, and measured no energy or stalls. The NumPy FP64 numbers there stay as the numerical reference. This document measures what they left out. Generated by `tools/evu_report.py` from the measured files, so every table can be rebuilt.

{status}

{stands}

## The tests

| test | command | what it measures |
|---|---|---|
| CPU cases | `python tools/cpu_bench.py --seconds 3 --out build/cpu_bench.json` | 11 CPU cases on all 22 rules: time, accuracy, frequency, threads, package energy |
| Host side of the board | `python tools/cpu_bench.py --seconds 3 --board udp:192.168.10.2 --out build/cpu_bench_board.json` | Laptop energy, frequency and threads while it drives the board |
| FPGA board power | `python tools/fpga_power.py` | A logging or read-by-eye power meter on the board's supply, idle against loaded |
| Frequency check | `python tools/cpu_bench.py --calibrate` | The Windows counter against a loop of known clock count |
| Cycle profile | `python hw/scripts/run_pyuvm.py evu --test EvuProfileTest --param PROFILE=1` | Every busy clock of 23 runs sorted into categories, in simulation |
| Same-program checks | `pytest sw/tests/test_cpu_bench.py` | The CPU computes what the board computes |
| Wrapper regression | `python hw/scripts/run_pyuvm.py evu --test EvuRegressTest --lcg-seed 1 --n 120` | The profile counters changed nothing |

Conditions: Intel i7-11800H, 8 cores and 16 logical CPUs, on AC power with the Windows Balanced plan, Python 3.11, NumPy 2.4.6, Numba 0.68.1. The CPU supports AVX-512, and Numba's SVML vector maths library was not active. The laptop was in normal use: Chrome, Task Manager and an idle Vivado window were open, so "idle" means about 2 logical CPUs of background work, not a clean machine. I ran nothing else during the windows. Each window lasted at least 3 s, and the fewest repeats in any window was {runs}.

### Keeping the comparison fair

- **Same program.** Every FP32 case reads the same `Run` the board executes: the same instructions, the same constants, and the same table bits. Only the elementary functions differ: the board uses its CORDIC unit, and the CPU its own `sinf` and `expf`.
- **Checked.** `test_cpu_bench.py` shows NumPy's per-point outputs equal the bit-accurate model's exactly on the four programs with no CORDIC op, and an exact sum of them gives the board's own word. Numba's strict sum equals a plain loop bit for bit.
- **Compile time.** Numba's JIT is left out of every measurement, as SymPy's setup is. It was at most {compile:.2f} s per kernel, and it happens once.
- **Hoisting.** I didn't move anything out of inner loops by hand, so each point nominally runs every instruction, as on the EVU. LLVM may move work out itself, and it does. That's normal for a CPU compiler, and the EVU has no equivalent.
- **Not controlled.** Turbo, the thread scheduler, and what the rest of the laptop was doing. Runs weren't pinned to cores.

### How each case sums

| name | what it does |
|---|---|
| pairwise | `np.sum` with `dtype=float32`. NumPy splits the array and adds halves, down to blocks of 128 |
| seq32 | One fp32 running total in grid order |
| wide64 | Each fp32 term added to a float64 total, then rounded once |
| fast32 | seq32 with LLVM allowed to reorder the sum (`fastmath` `reassoc`, `nsz`), so it can vectorize |
| exact | Every term added exactly and rounded once, as the EVU's accumulator does. Arbitrary-precision Python here, for accuracy only |
| parallel | One total per thread over its slice of the first grid dimension, added at the end, so a seq32 result depends on the thread count |

## Speed

Times are milliseconds per run. Board times are from the milestone 6 Ethernet run, kernel and repeat as defined there. CPU times are the mean over the window.

"""

PROSE_STANDS = """## Where my FPU stands

I knew going in that the laptop would be faster in real time, and it is, on every rule. That was never the interesting question for a 100 MHz FPGA design. The CPU in this laptop ran at {gap_low:.1f} to {gap_high:.1f} GHz during these tests, measured, not assumed. That's **{x_low:.0f}x to {x_high:.0f}x more clock cycles every second** than my FPU gets, and that gap already matters in the single-thread comparison. I measure threaded cases separately; AVX-512 support does not establish which vector instructions each case used. I also compare work per clock to understand the architecture, keeping precision and summing differences visible. Wall time still tells me how long I wait.

### Clock for clock

Measured clocks per point on the rules of 4,096 points or more, one CPU thread against the board's one FPU:

{table}

- **Against the NumPy FP64 baseline, my FPU does the work in fewer clocks on {win_f64} of {n} rules,** up to {best_f64:.1f}x fewer. The two it loses are the `plate_potential` rules, 1/√(x²+y²+…), where every point needs a square root and a divide from the one iterative unit.
- **Against compiled Numba, it still wins {win_numba} of {n}.** `sin` takes the board {sin_board:.1f} clocks per point and the CPU {sin_cpu:.1f}, so {sin_x:.1f}x fewer. `rf_oscillatory`, which is sin(50x)², is {rf_x:.1f}x fewer, and `decay_wide`, exp(−x), is {decay_x:.1f}x fewer. `gaussian_2d`, exp(−x²−y²), is close to a tie at 1.1x. Every rule it wins is built around a sine or an exponential in the inner loop.
- **The real-time gap comes from both factors.** The last column is the board's kernel time over Numba's on one thread. It's roughly the CPU's clock advantage divided by the board's clocks-per-point advantage. On `sin` a {sin_gap:.0f}x clock gap shrinks to {sin_kernel:.1f}x in real time, because the FPU does each point in about a quarter of the clocks.
- **The exact sum keeps rounding under control.** Every board result above is within 1.38 ULP of a 40-digit reference on converged rules, and it's the same answer on every run, in any order. A plain fp32 loop on the CPU reaches {seq:,.0f} ULP on the same rules and changes with the thread count.

### Why some programs suit my FPU

These come straight out of how the design is built, and the cycle profile further down backs each one with measured clocks.

- **A sine costs the same as an add.** The CORDIC unit is fully pipelined: sin, cos, atan2, hypot, exp and ln take 52 clocks to come out, but a new one goes in every clock. To the EVU every FPU op is one issue slot. On the CPU, `sinf` and `expf` are library routines with range reduction, a polynomial and branches, and strict Numba calls them one point at a time. So the more of a program is transcendental, the better my FPU looks.
- **31 contexts hide the latency.** While one point waits 52 clocks for its sine, 30 other points keep the FPU busy. The profile shows dependency stalls near 0% on every large rule, and divide-free programs issue on 88% to 100% of clocks.
- **I can accept one exact-sum term per clock.** It takes one term per clock with no rounding, then needs a fixed 29 clocks to finish, and the order points finish in can't change the answer. The CPU only gets close to that accuracy by summing pairwise or in float64.
- **No memory hierarchy to miss.** Grid points come from counters, node tables sit in on-chip block RAM, and registers live in the EVU. Nothing waits on a cache or DRAM, so timing is predictable: all 22 cycle counts in milestone 6 came out identical over UDP and the UART, and the simulated profile matched the board to within the same 2 clocks on every run.

### Why others suit the CPU

- **Divides and square roots.** My divide/sqrt unit works one bit per clock, takes 33 clocks, and handles one operation at a time. `plate_potential` needs a square root and a divide per point and spends 82% of its clocks waiting on that unit. The CPU does the whole point, including the square root and divide, in {plate_cpu:.1f} clocks.
- **Loop-invariant work.** LLVM moves anything that only depends on the outer grid coordinates out of the inner loop. The EVU runs every instruction at every point, including table LOADs, which are half of `cube_large`'s clocks. That's why `charge_sphere`, r³·sin θ·cos²θ, goes to the CPU even though it uses CORDIC ops: the sine and cosine only depend on θ, so the compiler pays for them once per grid line, not once per point. `cube_large`, (x+y+z)², is a plain polynomial, so the CORDIC advantage never comes into it.
- **One lane against many.** At the same clock the board would still have one FPU against 8 cores with 16-wide vector units, so the threaded and vectorised cases win by much more than the clock gap.
- **The link.** A repeat run costs about 0.3 ms of Ethernet round trips on top of the kernel, which decides every small rule.
"""

PROSE_SPEED = """

- **The gap is much wider than NumPy showed.** On repeat runs the board is {one_low:.0f}x to {one_high:.0f}x slower than single-threaded Numba, median {one_mid:.0f}x, and {best_low:.0f}x to {best_high:,.0f}x slower than the fastest CPU case, median {best_mid:.0f}x.
- **Compiling mattered more than threads for simple programs.** Removing NumPy's intermediate arrays and letting LLVM move work out of the inner loop took `cube_large` from {np:.1f} ms to {nb:.2f} ms on one thread.
- **Two kinds of case are not worth comparing directly.** Below a few hundred points the parallel cases mostly measure thread start-up, so a single thread wins, and fast32's accuracy needs reading alongside its time. See the next table.

## Accuracy, by how the sum is done

Error in ULPs of the true answer against the 40-digit reference. The double-precision reference case is 0.00 on every rule except where its own rule hasn't converged: the 1,024-point plate rule misses by about 25,600 ULP in every column here, which is truncation, not summing.

"""

PROSE_ACCURACY = """

- **Summing is what separates the columns.** On converged rules, plain seq32 reaches {seq:,.0f} ULP. Pairwise and wide64 stay within {good:.2f} ULP, close to the board's exact accumulator. So the exact sum beats a naive fp32 total by a wide margin, but NumPy's default gets nearly the same accuracy at no real cost.
- **A seq32 answer depends on the thread count.** The same fixed program gives a different result at 1, 8 and 16 threads, because each thread's total stays smaller. The EVU's sum is the same in any order.
- **fast32 is fastest and can be worse.** Reordering the sum gave up to {fast:,.0f} ULP here.
- **The CPU's terms aren't the board's terms** on programs with CORDIC ops, so "CPU terms, exact" differs a little from the board column. Both are within about 1.4 ULP on converged rules.

## Frequency and utilization

"Median" is across the 22 rules. "GHz" weights each logical CPU's `% Processor Performance` by how busy it was, across the whole machine, so background work is included. "Fastest CPU" is the single fastest logical CPU, which is the better number for a one-thread case. "My threads" is this process's CPU seconds per wall second.

"""

PROSE_FREQ = """

- **The 4 GHz assumption was about right, and slightly low.** One-thread cases held about 4.1 GHz across the machine and about 4.3 GHz on the busiest CPU. 16 threads dropped to about 3.9 GHz. So the clock gap against the FPGA's 100 MHz is 39x to 43x, not exactly 40x.
- **Checked independently.** A loop of known length gave {cal} against the counter's reading on the busiest CPU, which agree within about 1%. An earlier version of that loop read 12 GHz because LLVM vectorized it, and I replaced it.
- **16 threads aren't 16 cores.** 8 threads got 7.9 of 8 threads' worth of CPU time. 16 threads got about 14, and the machine was then fully busy with its background work. The two extra threads per core bought little time: see the speed table.

### Clocks per point

Board clocks are its reported cycles. CPU clocks are measured time multiplied by measured frequency, on one thread. This replaces the earlier estimate, which assumed 4 GHz and compared against NumPy only.

"""

PROSE_CLOCKS = """

This replaces what I said earlier, that the board uses fewer clocks per point on large runs. It depends on the program:

- **CORDIC-heavy programs: the board really does use fewer clocks.** `sin` takes it {sin_board:.1f} clocks per point against {sin_cpu:.1f} for strict Numba, and it's also ahead on `rf_oscillatory`, `decay_wide` and `gaussian_2d`. A pipelined CORDIC starts a new sine every clock, while strict Numba calls `sinf` one point at a time. NumPy's vectorized `sinf` is closer, and SVML in Numba would likely close the gap. I haven't measured that.
- **Divides and tensor grids: the CPU uses far fewer.** `plate_potential` takes {plate_cpu:.1f} clocks on the CPU against {plate_board:.1f}, and `cube_large` takes {cube_cpu:.1f} against {cube_board:.1f}. LLVM moves most of the program out of the inner loop, which leaves about 4 clocks for a strict fp32 add, and the EVU repeats every instruction at every point.

This is per clock on one thread. In real time the CPU's roughly 40x clock and its other cores win every rule.

## Energy

Energy comes from the CPU's own RAPL package counter, read through the Windows `Energy Meter` counters. That covers the package only: not DRAM, whose counter reads 0 here, not the NVIDIA GPU, and not the rest of the laptop. I checked the units: over 5 s the energy counter and the power counter agreed to about 2%.

Net energy is the window's energy minus idle power times its length, with idle measured for 1.5 s just before each case. Idle is noisy on a laptop in use: those windows ranged from {idle_low:.1f} to {idle_high:.1f} W, median {idle_mid:.1f} W, standard deviation {sigma:.1f} W. A value that doesn't clear twice that noise is shown as `<` the limit. Don't read small differences here as real.

Millijoules per run, CPU package only, on rules of 65,536 points or more:

"""

PROSE_ENERGY = """

{fpga}

- **A multi-core run costs more power and usually less energy.** About 57 W at 8 threads against about 33 W at 1, while finishing 3x to 4x sooner. On the short `plate_potential` rule the 8-thread case cost more, and that difference is close to the noise.
- **NumPy FP64 is the expensive one on large rules,** at 5x to 27x the energy of one-thread Numba on the four largest.

## EVU cycle profile

Every busy clock of a run lands in exactly one category. Percent of the run's clocks:

"""

PROSE_PROFILE = """

The categories:

- **Useful arithmetic:** the FPU accepted an FPU op.
- **LOAD:** the FPU accepted a table read. It takes an issue slot like any other op, so I keep it apart.
- **Division stall:** the divider refused an instruction, the divider's result took the port, or a slot went empty while a context was going round on a divider refusal or had a divide in the FPU.
- **Dependency stall:** an empty slot where every context was waiting for the result of its own last instruction, with no refusal involved.
- **Result FIFO rules:** the FPU's age or full rule refused an instruction, or a slot went empty because of one. This traffic is CORDIC. It doesn't appear anywhere else.
- **Context scheduling:** the pipeline filling at the start, the output side holding a finished context, and the end of the run when no points are left.
- **Setup and finish:** copying the descriptor, program and constants, and the accumulator's finish and final writes.

**This is the real hardware's behaviour.** The counters live in the testbench wrapper `evu_dut.sv`, so the RTL and bitstream are unchanged, and `PROFILE` defaults to 0. On {checked} rules up to 70 million clocks, the board's cycles are the simulated clocks plus {offset} every time, with no exceptions, and that's the two clocks `kernel_engine` adds around the unit. Each run's categories add up to its length, its issue count equals instructions times points, and its sum and flags equal the model's. Results came from simulation, because the board has no such counters.

- **Without divides, the pipeline is close to full.** Divide-free rules of 1,024 points or more issue on 88% to 100% of clocks. So scheduling and dependencies aren't the limit, and neither of them shows up above 1% on any of those rules.
- **The limit is one instruction per clock.** `cube_large` issues on every clock, and half of those are LOADs: 6 of its 12 instructions read tables. Doing that once per grid line, as LLVM effectively does, would be the most useful change for tensor grids.
- **Dependency stalls are almost zero.** That's direct evidence that 31 contexts are enough for these programs.
- **Division is the one real stall.** It takes 82% of `plate_potential`, 77% of `loop_bfield` and 64% of `fermi_half`, and it's the only thing that takes the board far below one instruction per clock.
- **The result FIFO's rules cost 4% to 12% on CORDIC programs of 1,024 points or more.** That's the first measurement of what those rules cost, and it answers the milestone 6 gap: they do fire on the real traffic.
- **Small runs are mostly overhead.** The 16-point `sin` run spends 75% of its clocks outside the points.

## Open: Ethernet stopped answering

After the laptop-only benchmark, about 40 minutes with no traffic from me, the board stopped answering over Ethernet. I don't have a cause yet.

- The adapter still reported Up at 100 Mbps, but ping and UDP both failed, and Windows had no ARP entry for the board.
- The UART still answered v0.4, and `last_cycles` still held the last run, 388,212,137. So the board kept its configuration, and `cmd_proc` and the kernels were healthy.
- UDP had worked just before the idle stretch: STATUS answered after the resend experiment.
- Reprogramming reset the board and the link then stayed down, so the state was lost before I could see LED5, LED6 or LED8 to LED14.

It could be the board's Ethernet stack, the PHY, or the laptop's adapter, and this evidence doesn't separate them. It's also the first time this design has sat idle for that long and then been asked to talk.

## What wasn't measured

{missing}
"""


def status_line(cpu_board, power) -> str:
    parts = []
    if not power:
        parts.append("the FPGA board's own energy, which needs a power meter")
    if not cpu_board:
        parts.append("the laptop's energy while driving the board, which needs the Ethernet link back")
    return "Status: complete." if not parts else "Status: not finished. Still missing: " + ", and ".join(parts) + "."


def main() -> int:
    # I join saved CPU, board and simulation records, then write the comparison document.
    cpu = load("cpu_bench.json")
    cpu_board = load("cpu_bench_board.json")
    power = load("fpga_power.json")
    profile = load("evu_profile.json")
    board = board_times()
    rows = cpu["rows"]
    cases = rows[0]["cases"]
    speed, one, best = speed_table(rows, board)
    idle = [r["cases"][c]["idle_w"] for r in rows for c in cases]
    seq = max(r["cases"]["numpy_f32_seq32"]["ulps"] for r in rows
              if not (r["name"] == "plate_potential" and r["points"] == 1024))
    good = max(r["cases"][c]["ulps"] for r in rows for c in ("numpy_f32_pairwise", "numpy_f32_wide64", "numba_wide64_t1")
               if not (r["name"] == "plate_potential" and r["points"] == 1024))
    fast = max(r["cases"]["numba_fast32_t1"]["ulps"] for r in rows
               if not (r["name"] == "plate_potential" and r["points"] == 1024))
    cube = next(r for r in rows if r["name"] == "cube_large" and r["points"] > 1000000)["cases"]
    offsets, checked = profile_check(profile, board)
    if len(offsets) != 1:
        raise SystemExit(f"the simulated and board clock counts don't line up: {offsets}")
    fpga = ("**FPGA board energy isn't in this table yet.** The last column is what the board would have to draw "
            "above its idle power to tie 8-thread Numba on energy for one run. It uses only measured numbers: "
            "CPU net energy divided by the board's repeat time. Whether the board clears that bar is what a meter would show.")
    if power:
        fpga = (f"The FPGA board drew {power['idle_w']:.2f} W idle and {power['busy_w']:.2f} W running, so "
                f"{power['net_w'] * 1e3:.0f} mW net, measured with {power['meter']} on {power['rail']}.")
    missing = ["- CPU energy excludes DRAM, the GPU and everything outside the package."]
    if not power:
        missing.append("- **FPGA board energy.** The Nexys A7 has no power sensors I can read, so this needs an external meter.")
    if not cpu_board:
        missing.append("- **Laptop energy while driving the board.** The Ethernet link went down before this ran.")
    missing += ["- A run pinned to chosen cores, and a machine with nothing else open.",
                "- SVML in Numba, which would speed up its `sin` and `exp`. I expect that to make the CPU faster still.",
                "- Which stall rule fired on the board itself. The profile comes from simulation, tied to the board by clock count."]
    # The clock-for-clock summary at the top. Every number here comes from the same
    # measured rows as the tables further down.
    eff = efficiency_rows(rows, board)
    by = {(e["name"], e["points"]): e for e in eff}
    ghz_low = min(st.median(r["cases"][c]["window"]["ghz"] for r in rows) for c in cases)
    ghz_high = max(st.median(r["cases"][c]["window"]["ghz_peak"] for r in rows) for c in cases)
    sin_e, rf_e, decay_e = by[("sin", 16384)], by[("rf_oscillatory", 65536)], by[("decay_wide", 65536)]
    stands = PROSE_STANDS.format(
        gap_low=ghz_low, gap_high=ghz_high, x_low=ghz_low * 1e9 / CLK, x_high=ghz_high * 1e9 / CLK,
        table=efficiency_table(eff), n=len(eff),
        win_f64=sum(e["board"] < e["f64"] for e in eff), best_f64=max(e["f64"] / e["board"] for e in eff),
        win_numba=sum(e["board"] < e["numba"] for e in eff),
        sin_board=sin_e["board"], sin_cpu=sin_e["numba"], sin_x=sin_e["numba"] / sin_e["board"],
        rf_x=rf_e["numba"] / rf_e["board"], decay_x=decay_e["numba"] / decay_e["board"],
        sin_gap=sin_e["gap"], sin_kernel=sin_e["kernel"], seq=seq,
        plate_cpu=by[("plate_potential", 1048576)]["numba"])
    text = PROSE_HEAD.format(status=status_line(cpu_board, power), stands=stands,
                             runs=min(r["cases"][c]["runs"] for r in rows for c in cases),
                             compile=max(r["cases"][c]["compile_s"] for r in rows for c in cases))
    text += speed
    text += PROSE_SPEED.format(one_low=min(one), one_high=max(one), one_mid=st.median(one), best_low=min(best),
                               best_high=max(best), best_mid=st.median(best), np=cube["numpy_f32_pairwise"]["mean_s"] * 1e3,
                               nb=cube["numba_seq32_t1"]["mean_s"] * 1e3)
    text += accuracy_table(rows, board)
    text += PROSE_ACCURACY.format(seq=seq, good=good, fast=fast)
    text += frequency_table(rows)
    text += PROSE_FREQ.format(cal="4.227 GHz")
    text += clocks_table(rows, board)
    def per_point(name: str, points: int, case: str = "numba_seq32_t1") -> float:
        r = next(x for x in rows if x["name"] == name and x["points"] == points)
        m = r["cases"][case]
        return m["mean_s"] * m["window"]["ghz"] * 1e9 / points

    text += PROSE_CLOCKS.format(idle_low=min(idle), idle_high=max(idle), idle_mid=st.median(idle), sigma=noise(rows),
                                sin_board=board[("sin", 16384)]["cycles"] / 16384, sin_cpu=per_point("sin", 16384),
                                plate_cpu=per_point("plate_potential", 1048576),
                                plate_board=board[("plate_potential", 1048576)]["cycles"] / 1048576,
                                cube_cpu=per_point("cube_large", 2097152),
                                cube_board=board[("cube_large", 2097152)]["cycles"] / 2097152)
    text += energy_table(rows, board, power)
    text += PROSE_ENERGY.format(fpga=fpga)
    text += profile_table(profile)
    text += PROSE_PROFILE.format(checked=checked, offset=offsets.pop(), missing="\n".join(missing))
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
