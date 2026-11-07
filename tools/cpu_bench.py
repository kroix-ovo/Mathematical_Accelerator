"""A controlled CPU comparison for the class problems, with power and frequency.

    python tools/cpu_bench.py --out build/cpu_bench.json                 # the laptop only
    python tools/cpu_bench.py --only numba_seq32_t1 --problem gaussian_2d
    python tools/cpu_bench.py --board udp:192.168.10.2 --out build/cpu_bench_board.json
    python tools/cpu_bench.py --calibrate                                # is the frequency counter right?

The first comparison in docs/phase4-plan.md timed NumPy on one precision without
saying how it summed, assumed a 4 GHz clock, and measured no energy. This measures
those things. The FP64 numbers stay as the numerical reference, and everything
here sits beside them.

Every rule below runs the bytecode the board runs, not a second copy of the maths.
It reads the same Run: the same instruction list, the same constants, and the
same tables, down to the bit, so the board and the CPU start from identical fp32
inputs. Only the elementary functions differ. The board has its CORDIC unit and
the CPU has its own sinf and expf, which aren't the same function to the last bit.

Accumulation is where fp32 results differ most, so each way of summing has a name:

  pairwise   np.sum with dtype float32. NumPy splits the array and adds halves,
             down to blocks of 128 that it adds in a loop. Error grows slowly, but
             the result depends on how NumPy blocks the data.
  seq32      one fp32 running total, in grid order. The textbook plain sum.
  wide64     each fp32 term added to a float64 running total, then rounded once.
  exact      every term added exactly, rounded once, which is what the EVU's
             accumulator does. On the CPU this is arbitrary-precision Python, used
             for accuracy only. It isn't a fair speed measurement.

The Numba cases compile one loop per problem with no intermediate arrays. They
use strict IEEE arithmetic, unless the name says fast32, which lets LLVM reorder
the sum so it can vectorize it. In parallel, each thread keeps its own total for
its slice of the first grid dimension and the totals are added at the end, so a
seq32 sum depends on the thread count. That's measured here, not assumed away.

What gets measured, per rule and per case, over a window of at least --seconds:

  time        mean wall time per run, and the fastest run
  frequency   average core clock while busy. I read each logical CPU's
              "% Processor Performance" (Windows' APERF/MPERF ratio) and "% Processor
              Time" at the start and end of the window, then weight by busy time.
  threads     CPU seconds used by this process divided by wall seconds
  energy      RAPL package energy from the "Energy Meter" counters. Net energy
              subtracts the idle power measured around the run, times the duration.

The windows are read in this thread between runs. A sampling thread would compete
for the GIL and disturb the thing being measured.
"""

# I compare CPU implementations of the same rounded program here.
# I leave compilation outside the timed window, then record frequency,
# threads, accuracy and package energy beside wall time. I keep the summing
# methods separate because a fast answer can have a very different error.

from __future__ import annotations

import argparse
import ctypes
import json
import math
import statistics
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from mathaccel import evu_model, ulp  # noqa: E402
from mathaccel._generated import spec  # noqa: E402
from mathaccel._generated.spec import FpuOp  # noqa: E402
from mathaccel.compiler.evu_isa import OP_LOAD, SRC_CONST, Run, memory_image  # noqa: E402
from mathaccel.compiler.problems import PROBLEMS, Problem  # noqa: E402
from mathaccel.compiler.quadrature import integral_run  # noqa: E402

PWH_TO_J = 3.6e-9            # the Energy Meter counters count picowatt-hours
IDLE_SECONDS = 6.0


# ---- Windows performance counters -----------------------------------------------

class _Fmt(ctypes.Structure):
    """PDH_FMT_COUNTERVALUE: a status word, padding, then the double."""
    _fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_double)]


class _Raw(ctypes.Structure):
    """PDH_RAW_COUNTER. FirstValue holds the running total for the energy meters."""
    _fields_ = [("status", wintypes.DWORD), ("time", wintypes.FILETIME), ("first", ctypes.c_longlong),
                ("second", ctypes.c_longlong), ("count", wintypes.DWORD)]


class Counters:
    """Windows' PDH counters, read through ctypes so nothing extra is installed.

    Rate counters such as % Processor Performance only mean something between two
    collections, so a window is: start(), do the work, stop().
    """
    FORMAT_DOUBLE = 0x00000200

    def __init__(self) -> None:
        self.pdh = ctypes.WinDLL("pdh")
        self.query = wintypes.HANDLE()
        self._check(self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)))
        self.logical = int(ctypes.windll.kernel32.GetActiveProcessorCount(0xFFFF))
        self.perf = [self._add(rf"\Processor Information(0,{k})\% Processor Performance") for k in range(self.logical)]
        self.busy = [self._add(rf"\Processor Information(0,{k})\% Processor Time") for k in range(self.logical)]
        self.nominal = self._add(r"\Processor Information(_Total)\Processor Frequency")
        self.pkg = self._add(r"\Energy Meter(RAPL_Package0_PKG)\Energy")
        self.core = self._add(r"\Energy Meter(RAPL_Package0_PP0)\Energy")
        self._collect()

    def _check(self, status: int) -> None:
        if status != 0:
            raise OSError(f"PDH error 0x{status & 0xFFFFFFFF:08X}")

    def _add(self, path: str) -> wintypes.HANDLE:
        handle = wintypes.HANDLE()
        self._check(self.pdh.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(handle)))
        return handle

    def _collect(self) -> None:
        self._check(self.pdh.PdhCollectQueryData(self.query))

    def _value(self, handle) -> float:
        out = _Fmt()
        self._check(self.pdh.PdhGetFormattedCounterValue(handle, self.FORMAT_DOUBLE, None, ctypes.byref(out)))
        return out.value

    def _raw(self, handle) -> int:
        out = _Raw()
        self._check(self.pdh.PdhGetRawCounterValue(handle, None, ctypes.byref(out)))
        return out.first

    def start(self) -> dict:
        """Begin a window."""
        self._collect()
        return {"pkg": self._raw(self.pkg), "core": self._raw(self.core), "t": time.perf_counter(),
                "cpu": time.process_time()}

    def stop(self, begin: dict) -> Window:
        """End a window and work out what happened during it."""
        self._collect()
        wall = time.perf_counter() - begin["t"]
        cpu = time.process_time() - begin["cpu"]
        busy = np.array([max(0.0, self._value(c)) for c in self.busy])
        perf = np.array([max(0.0, self._value(c)) for c in self.perf])
        nominal = self._value(self.nominal)
        weight = busy.sum()
        mean_perf = float((busy * perf).sum() / weight) if weight > 0 else 0.0
        pkg = (self._raw(self.pkg) - begin["pkg"]) * PWH_TO_J
        core = (self._raw(self.core) - begin["core"]) * PWH_TO_J
        return Window(wall, cpu / wall, float(busy.sum() / 100), int((busy > 50).sum()), nominal * mean_perf / 1e5,
                      float(np.max(perf)) * nominal / 1e5, pkg, core)


@dataclass
class Window:
    """What one measurement window saw."""
    seconds: float
    process_threads: float      # this process's CPU seconds per wall second
    system_threads: float       # all processes: logical CPUs' busy time added up
    cpus_over_half: int         # logical CPUs more than half busy
    ghz: float                  # busy-weighted average core clock
    ghz_peak: float             # the fastest single logical CPU's average
    package_j: float
    core_j: float

    @property
    def package_w(self) -> float:
        return self.package_j / self.seconds


def idle(c: Counters, seconds: float = IDLE_SECONDS) -> Window:
    """Power with nothing of mine running, for subtracting later."""
    w = c.start()
    time.sleep(seconds)
    return c.stop(w)


def calibrate(c: Counters) -> None:
    """I check the frequency counter against a clock I can compute myself.

    Each loop below takes exactly 4 clocks: a 64-bit multiply takes 3 and the XOR
    that uses its result takes 1, and each pass depends on the one before. The
    shift runs alongside the multiply, so it adds nothing. Time for N of them then
    gives the core clock with no help from Windows. The XOR matters: a bare chain
    of multiplies is a reduction, and LLVM vectorizes that, which breaks the count.
    """
    import numba

    @numba.njit
    def chain(n: int, x: int) -> int:
        k = 0x9E3779B97F4A7C15
        for _ in range(n):
            x = ((x * k) & 0xFFFFFFFFFFFFFFFF) ^ (x >> 29)
        return x

    chain(10, 3)
    n = 3_000_000_000
    w = c.start()
    t0 = time.perf_counter()
    result = chain(n, 3)
    dt = time.perf_counter() - t0
    out = c.stop(w)
    print(f"chain: {4 * n / dt / 1e9:.3f} GHz at 4 clocks per pass, Windows {out.ghz:.3f} GHz busy-weighted, "
          f"{out.ghz_peak:.3f} GHz on the busiest CPU, {out.process_threads:.2f} threads ({result & 1})")


# ---- the same program, on the CPU ------------------------------------------------

def table_arrays(run: Run) -> list[np.ndarray]:
    """The run's tables as fp32 arrays: the bit patterns the board loads."""
    # I reinterpret uploaded table words as fp32, preserving the board input rounding.
    return [np.asarray(t.values, dtype=np.uint32).view(np.float32).copy() for t in run.tables]


def constants_of(run: Run) -> np.ndarray:
    # I decode the exact constant-pool bits rather than recomputing the expression constants.
    return np.asarray(run.program.constants, dtype=np.uint32).view(np.float32).copy()


NUMPY_OPS = {FpuOp.ADD: np.add, FpuOp.SUB: np.subtract, FpuOp.MUL: np.multiply, FpuOp.DIV: np.divide,
             FpuOp.SIN: np.sin, FpuOp.COS: np.cos, FpuOp.EXP: np.exp, FpuOp.LN: np.log, FpuOp.SQRT: np.sqrt,
             FpuOp.ATAN2: np.arctan2, FpuOp.HYPOT: np.hypot, FpuOp.MIN: np.minimum, FpuOp.MAX: np.maximum}
UNARY = {FpuOp.SIN, FpuOp.COS, FpuOp.EXP, FpuOp.LN, FpuOp.SQRT}


def numpy_program(run: Run) -> list[tuple]:
    """The bytecode as a list ready to replay in NumPy. Each entry is (function,
    destination, a, b), where a source is a register number or a constant scalar."""
    # I decode the bytecode once so timed repetitions only evaluate its operations.
    pool = constants_of(run)

    def source(s: int):
        return pool[s & 0x3F] if s & SRC_CONST else s

    steps = []
    for i in run.program.instrs():
        if i.op == OP_LOAD:
            steps.append(("load", i.dst, i.a, None))
        else:
            steps.append((NUMPY_OPS[FpuOp(i.op)], i.dst, source(i.a), None if i.op in UNARY else source(i.b)))
    return steps


def numpy_shapes(run: Run) -> list[np.ndarray]:
    """Tables reshaped to broadcast along their own grid dimension. A value that
    only depends on one dimension stays that small, which is how anyone who knows
    NumPy would write this."""
    # I broadcast each table along its descriptor dimension, keeping last-axis-fastest order.
    out = []
    for t, values in zip(run.tables, table_arrays(run), strict=True):
        shape = [1] * len(run.grid)
        shape[t.dim] = -1
        out.append(values.reshape(shape))
    return out


def numpy_terms(steps: list, tables: list[np.ndarray], grid: tuple) -> np.ndarray:
    """I evaluate each decoded instruction over arrays before choosing how to sum them."""
    regs: dict = {}

    def read(s):
        return regs[s] if isinstance(s, int) else s

    output = None
    for fn, dst, a, b in steps:
        if fn == "load":
            output = tables[a]
        elif b is None:
            output = fn(read(a))
        else:
            output = fn(read(a), read(b))
        regs[dst] = output
    return np.broadcast_to(output, grid)


def sum_terms(terms: np.ndarray, how: str) -> float:
    """I keep the CPU reduction methods explicit because their rounding differs."""
    if how == "pairwise":
        return float(np.sum(np.ascontiguousarray(terms), dtype=np.float32))
    if how == "seq32":
        return float(np.cumsum(terms.ravel(), dtype=np.float32)[-1])
    if how == "wide64":
        return float(np.float32(np.sum(terms, dtype=np.float64)))
    if how == "exact":
        bits = np.ascontiguousarray(terms).ravel().view(np.uint32)
        return evu_model.fp32_value(evu_model.exact_sum(bits)[0])
    raise ValueError(how)


def numba_source(run: Run, how: str, parallel: bool) -> str:
    """I generate scalar loop code from the same bytecode and table bit patterns.

    Nothing is hoisted by hand: every point runs every instruction, as on the EVU,
    so the instruction counts match. LLVM may still move work out of inner loops,
    which is exactly the sort of thing a CPU compiler is supposed to do.
    """
    dims = len(run.grid)
    pool = len(run.program.constants)
    args = [f"t{k}" for k in range(len(run.tables))] + ["k"]
    head = [f"def kernel({', '.join(args)}):"]
    head += [f"    c{i} = k[{i}]" for i in range(pool)]
    zero = "0.0" if how == "wide64" else "np.float32(0.0)"
    head.append(f"    total = {zero}")
    loop = []
    for d, n in enumerate(run.grid):
        ranged = "prange" if parallel and d == 0 else "range"
        loop.append("    " * (d + 1) + f"for i{d} in {ranged}({n}):")
    pad = "    " * (dims + 1)

    def source(s: int) -> str:
        return f"c{s & 0x3F}" if s & SRC_CONST else f"r{s}"

    names = {FpuOp.ADD: "{a} + {b}", FpuOp.SUB: "{a} - {b}", FpuOp.MUL: "{a} * {b}", FpuOp.DIV: "{a} / {b}",
             FpuOp.SIN: "np.sin({a})", FpuOp.COS: "np.cos({a})", FpuOp.EXP: "np.exp({a})", FpuOp.LN: "np.log({a})",
             FpuOp.SQRT: "np.sqrt({a})", FpuOp.ATAN2: "np.arctan2({a}, {b})", FpuOp.HYPOT: "np.hypot({a}, {b})",
             FpuOp.MIN: "min({a}, {b})", FpuOp.MAX: "max({a}, {b})"}
    body = []
    last = 0
    for i in run.program.instrs():
        if i.op == OP_LOAD:
            body.append(f"{pad}r{i.dst} = t{i.a}[i{run.tables[i.a].dim}]")
        else:
            body.append(f"{pad}r{i.dst} = " + names[FpuOp(i.op)].format(a=source(i.a), b=source(i.b)))
        last = i.dst
    body.append(f"{pad}total += r{last}")
    return "\n".join(head + loop + body + ["    return total"])


def compile_numba(run: Run, how: str, parallel: bool):
    """I compile numba_source and force the compile, so no benchmark pays for it."""
    # I compile and warm the generated kernel before any measurement window starts.
    import numba

    flags = {"parallel": parallel}
    if how == "fast32":
        flags["fastmath"] = {"reassoc", "nsz"}
    namespace = {"np": np, "prange": numba.prange}
    exec(numba_source(run, how, parallel), namespace)      # the source comes from my own compiler
    fn = numba.njit(**flags)(namespace["kernel"])
    arrays = table_arrays(run)
    pool = constants_of(run)
    if not len(pool):
        pool = np.zeros(1, dtype=np.float32)
    t0 = time.perf_counter()
    fn(*arrays, pool)
    return fn, arrays, pool, time.perf_counter() - t0


# ---- the benchmark cases ---------------------------------------------------------

def reference_ulps(value: float, p: Problem) -> float:
    """Error of a double against the 40-digit reference, in ULPs of the true answer."""
    exact = p.exact()
    unit = float(ulp.ulp_of(ulp.mpref.mpf_to_fraction(exact)))
    return float(abs(value - exact)) / unit if value == value else math.inf


def numpy_f64_runner(p: Problem, rule):
    """The original baseline: SymPy's expression in double precision, with unrounded nodes."""
    import sympy as sp

    axes = p.axes(rule)
    nodes = [a.nodes() for a in axes]
    grids = np.meshgrid(*[n for n, _ in nodes], indexing="ij")
    weights = np.ones_like(grids[0])
    for k, (_, w) in enumerate(nodes):
        shape = [1] * len(nodes)
        shape[k] = -1
        weights = weights * w.reshape(shape)
    f = sp.lambdify([a.symbol for a in axes], p.expr, "numpy")

    def one() -> float:
        return float(np.sum(f(*grids) * weights))

    return one


def numpy_f32_runner(run: Run, how: str):
    steps = numpy_program(run)
    tables = numpy_shapes(run)

    def one() -> float:
        return sum_terms(numpy_terms(steps, tables, run.grid), how)

    return one


CASES = {
    "numpy_f64": dict(kind="numpy64", threads=1),
    "numpy_f32_pairwise": dict(kind="numpy32", how="pairwise", threads=1),
    "numpy_f32_seq32": dict(kind="numpy32", how="seq32", threads=1),
    "numpy_f32_wide64": dict(kind="numpy32", how="wide64", threads=1),
    "numba_seq32_t1": dict(kind="numba", how="seq32", threads=1),
    "numba_wide64_t1": dict(kind="numba", how="wide64", threads=1),
    "numba_fast32_t1": dict(kind="numba", how="fast32", threads=1),
    "numba_seq32_t8": dict(kind="numba", how="seq32", threads=8),
    "numba_wide64_t8": dict(kind="numba", how="wide64", threads=8),
    "numba_seq32_t16": dict(kind="numba", how="seq32", threads=16),
    "numba_wide64_t16": dict(kind="numba", how="wide64", threads=16),
}


def build_runner(case: dict, p: Problem, rule, run: Run) -> tuple:
    """(one run as a function, compile seconds)."""
    if case["kind"] == "numpy64":
        return numpy_f64_runner(p, rule), 0.0
    if case["kind"] == "numpy32":
        return numpy_f32_runner(run, case["how"]), 0.0
    import numba

    parallel = case["threads"] > 1
    fn, arrays, pool, compile_s = compile_numba(run, case["how"], parallel)
    if parallel:
        numba.set_num_threads(case["threads"])

    def one() -> float:
        return float(fn(*arrays, pool))

    return one, compile_s


def measure(c: Counters, one, seconds: float, before: Window) -> dict:
    """I collect the result and measurements for this case rather than reporting timing alone."""
    times = []
    value = one()                       # warm the caches before the window opens
    w = c.start()
    end = time.perf_counter() + seconds
    while True:
        t0 = time.perf_counter()
        value = one()
        t1 = time.perf_counter()
        times.append(t1 - t0)
        if t1 >= end:
            break
    out = c.stop(w)
    runs = len(times)
    net = out.package_j - before.package_w * out.seconds
    return {"value": value, "runs": runs, "mean_s": out.seconds / runs, "best_s": min(times),
            "median_s": statistics.median(times), "window": asdict(out), "idle_w": before.package_w,
            "package_j_per_run": out.package_j / runs, "net_j_per_run": net / runs}


def board_runner(dev, run: Run):
    """The board's repeat loop: RUN and read, with the image already loaded."""
    # I load once and time repeat evaluations so upload time does not leak into this case.
    bounds = evu_model.cycle_bounds(run)
    image = memory_image(run)
    dev.write_words(image.base, image.words)
    state = {"cycles": 0}

    def one() -> float:
        done = dev.evu_start(image, bounds)
        state["cycles"] = done.cycles
        return evu_model.fp32_value(int(dev.read_words(image.r, 2)[0]))

    return one, state


def rule_list(only: str | None) -> list:
    return [(p, rule) for p in PROBLEMS if not only or p.name == only for rule in p.rules]


def main() -> int:
    # I collect each case in a timed window and save enough context to interpret its speed and energy.
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--problem", help="only this problem")
    ap.add_argument("--only", action="append", help="only this case, repeatable")
    ap.add_argument("--seconds", type=float, default=3.0, help="length of each measurement window")
    ap.add_argument("--board", help="measure the board over this port instead of the CPU cases")
    ap.add_argument("--calibrate", action="store_true", help="only check the frequency counter")
    ap.add_argument("--out", help="write the results here as JSON")
    args = ap.parse_args()

    c = Counters()
    if args.calibrate:
        print(f"idle: {asdict(idle(c, 5.0))}")
        calibrate(c)
        return 0

    rows = []
    settle = idle(c)
    print(f"idle package {settle.package_w:.2f} W, {settle.system_threads:.2f} logical CPUs busy", flush=True)
    if args.board:
        from mathaccel.driver.device import connect

        dev = connect(args.board)
        dev.ping()
    for p, rule in rule_list(args.problem):
        run = integral_run(p.expr, p.axes(rule))
        row = {"name": p.name, "points": run.points, "instructions": len(run.program.words), "cases": {}}
        if not args.board:
            terms = numpy_terms(numpy_program(run), numpy_shapes(run), run.grid)
            row["numpy_exact_ulps"] = reference_ulps(sum_terms(terms, "exact"), p)
        names = ["board"] if args.board else (args.only or list(CASES))
        for name in names:
            before = idle(c, 1.5)
            if args.board:
                one, state = board_runner(dev, run)
                compile_s = 0.0
            else:
                one, compile_s = build_runner(CASES[name], p, rule, run)
            m = measure(c, one, args.seconds, before)
            if args.board:
                m["cycles"] = state["cycles"]
            m["compile_s"] = compile_s
            m["ulps"] = reference_ulps(m["value"], p)
            row["cases"][name] = m
            w = m["window"]
            print(f"{p.name:16s} {run.points:9,} {name:20s} {m['mean_s'] * 1e3:10.4f} ms  {m['ulps']:9.2f} ULP  "
                  f"{w['ghz']:.2f} GHz  {w['process_threads']:5.2f} thr  {w['package_j'] / w['seconds']:5.1f} W  "
                  f"net {m['net_j_per_run'] * 1e3:9.4f} mJ", flush=True)
        rows.append(row)
    end = idle(c)
    report = {"idle_start": asdict(settle), "idle_end": asdict(end), "rows": rows, "logical_cpus": c.logical,
              "clk_hz": spec.CLK_HZ}
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
