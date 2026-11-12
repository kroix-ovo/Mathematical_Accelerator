"""Run the class problems on the expression unit and check them every way I can.

    python tools/evu_board.py --port udpsim --out build/evu_m5.md           # no board
    python tools/evu_board.py --port udp:192.168.10.2 --out build/evu_m6.md
    python tools/evu_board.py --port udp:192.168.10.2 --busy --uart COM3    # resend behaviour

For every problem in sw/mathaccel/compiler/problems.py and each of its rules:

  match   the result word, flags word and aux word must equal the bit-accurate
          model exactly. There is no tolerance anywhere in this comparison.
  error   distance from the 40-digit reference, in ULPs of the true answer.
  bounds  the reported cycles must fall inside cycle_bounds. In the simulators
          the reply carries the low bound, so this only means something on the
          board, and the speed columns only mean something there too.
  speed   three times for the board, against NumPy on this laptop:
          kernel  the board's own clock counter;
          first   everything from nothing: write the image, RUN, read the answer;
          repeat  RUN and read only, with the image already loaded.
          NumPy runs the same rule and is timed the same way: the best of several
          repeats of the evaluation and sum alone, with the grids already built
          (repeat) and with building them included (first).

It then checks map mode, chunked map runs and root finding against the model,
and random programs that mix fast, divide and CORDIC ops, which is what the
FPU's result FIFO stall rules need. Any mismatch makes the exit status non-zero.

--busy starts real long runs and sends requests during them, to see what the
board does with a resent RUN. It has no meaning on a simulated port.
"""

# I exercise the complete host-to-device path here: load the image, start
# the run, read the answer and compare its bits and flags with the model.
# I time first and repeat runs separately so the upload cost stays visible.
# Running this against a simulator checks the script, not the physical board.

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sw"))

import sympy as sp  # noqa: E402

from mathaccel import calc, evu_model, ulp  # noqa: E402
from mathaccel._generated import spec  # noqa: E402
from mathaccel._generated.spec import FpuOp, Kernel, Op  # noqa: E402
from mathaccel.compiler import evu_gen  # noqa: E402
from mathaccel.compiler.evu_isa import OP_LOAD, Instr, Program, Run, Table, const, memory_image, reg  # noqa: E402
from mathaccel.compiler.problems import PROBLEMS, Problem  # noqa: E402
from mathaccel.compiler.quadrature import Axis, float_bits, integral_run  # noqa: E402
from mathaccel.driver import protocol as P  # noqa: E402
from mathaccel.driver.device import Device, DeviceTimeout, connect  # noqa: E402
from mathaccel.driver.protocol import Frame  # noqa: E402
from mathaccel.driver.transport import UartTransport, UdpTransport  # noqa: E402

REPEATS = 7             # timing runs for each measurement; I keep the fastest
LOG: list[str] = []


def say(text: str = "") -> None:
    """Print a line and keep it for the --out file."""
    print(text, flush=True)
    LOG.append(text)


def ulps(bits: int, exact, reference_ulp: float) -> float:
    """Distance from the reference in ULPs of the reference."""
    return float(abs(evu_model.fp32_value(bits) - exact)) / reference_ulp


def numpy_setup(p: Problem, rule) -> tuple:
    """Grids, weights and the compiled integrand, in double precision."""
    axes = p.axes(rule)
    nodes = [a.nodes() for a in axes]
    grids = np.meshgrid(*[n for n, _ in nodes], indexing="ij")
    weights = np.ones_like(grids[0])
    for k, (_, w) in enumerate(nodes):
        shape = [1] * len(nodes)
        shape[k] = -1
        weights = weights * w.reshape(shape)
    return sp.lambdify([a.symbol for a in axes], p.expr, "numpy"), grids, weights


def numpy_time(p: Problem, rule, dtype) -> tuple[float, float, float]:
    """(first, repeat, value): NumPy's times for one rule at one precision."""
    def build():
        f, grids, weights = numpy_setup(p, rule)
        return f, [g.astype(dtype) for g in grids], weights.astype(dtype)

    # The first time includes making the grids, and it's a fair match for what the
    # board's "first" column pays to write its tables. The symbolic setup is left
    # out of both: the board's times don't include compiling either.
    f, grids, weights = build()
    t0 = time.perf_counter()
    grids2 = [g.copy() for g in grids]
    weights2 = weights.copy()
    value = float(np.sum(f(*grids2) * weights2))
    first = time.perf_counter() - t0
    repeat = float("inf")
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        value = float(np.sum(f(*grids) * weights))
        repeat = min(repeat, time.perf_counter() - t0)
    return first, repeat, value


def repeat_time(dev: Device, run: Run, image) -> tuple[float, list[int]]:
    """Fastest RUN plus read with the image already there, and every cycle count."""
    # I reuse the loaded image so this measures RUN and readback without another upload.
    bounds = evu_model.cycle_bounds(run)
    best = float("inf")
    cycles = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        done = dev.evu_start(image, bounds)
        dev.read_words(image.r, 2)
        best = min(best, time.perf_counter() - t0)
        cycles.append(done.cycles)
    return best, cycles


def problems_table(dev: Device, only: str | None) -> tuple[int, list[dict]]:
    """The class problems. Returns (failures, one row per rule)."""
    # I compare each class rule with both the bit-level model and its high-precision reference.
    failures = 0
    rows = []
    say("| problem | points | instrs | match | error (ULP) | double rule | cycles | low..high | clocks/pt "
        "| in bounds |")
    say("|---|---|---|---|---|---|---|---|---|---|")
    for p in PROBLEMS:
        if only and p.name != only:
            continue
        exact = p.exact()
        unit = float(ulp.ulp_of(ulp.mpref.mpf_to_fraction(exact)))
        for rule in p.rules:
            run = integral_run(p.expr, p.axes(rule))
            want = evu_model.execute(run)
            got = dev.evu_run(run)
            same = (int(got.results[0]) == want.sum and int(got.flags[0]) == want.sum_flags and got.aux == want.aux)
            image = memory_image(run)
            again, cycles = repeat_time(dev, run, image)
            # A repeat must give the same answer too: the unit keeps no state.
            same = same and all(c >= got.bounds[0] for c in cycles)
            repeat_bits = dev.read_words(image.r, 2)
            same = same and int(repeat_bits[0]) == want.sum and int(repeat_bits[1]) == want.sum_flags
            first64, repeat64, value64 = numpy_time(p, rule, np.float64)
            first32, repeat32, value32 = numpy_time(p, rule, np.float32)
            inside = got.in_bounds and all(got.bounds[0] <= c <= got.bounds[1] for c in cycles)
            failures += (not same) + (not inside)
            row = dict(name=p.name, points=run.points, instrs=len(run.program.words), same=same,
                       err=ulps(want.sum, exact, unit), double=abs(value64 - float(exact)) / unit,
                       single=abs(value32 - float(exact)) / unit, cycles=got.cycles, spread=(min(cycles), max(cycles)),
                       bounds=got.bounds, inside=inside, kernel=got.seconds, first=got.total_seconds, repeat=again,
                       write=got.write_seconds, first64=first64, repeat64=repeat64, first32=first32, repeat32=repeat32,
                       words=len(image.words), value=evu_model.fp32_value(int(got.results[0])))
            rows.append(row)
            if not same:
                say(f"MISMATCH {p.name}: board 0x{int(got.results[0]):08X}/{int(got.flags[0]):05b} aux {got.aux:05b}, "
                    f"model 0x{want.sum:08X}/{want.sum_flags:05b} aux {want.aux:05b}")
            say(f"| {p.name} | {run.points:,} | {row['instrs']} | {'yes' if same else 'NO'} | {row['err']:.2f} "
                f"| {row['double']:.2g} | {got.cycles:,} | {got.bounds[0]:,}..{got.bounds[1]:,} "
                f"| {got.cycles / run.points:.1f} | {'yes' if inside else 'NO'} |")
    return failures, rows


def speed_table(rows: list[dict]) -> None:
    """Board against NumPy. Every time here is in milliseconds."""
    say()
    say("| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat "
        "| NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |")
    say("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        ratio = r["repeat"] / r["repeat64"]
        verdict = f"{ratio:.1f}x slower" if ratio >= 1 else f"{1 / ratio:.1f}x faster"
        say(f"| {r['name']} | {r['points']:,} | {r['kernel'] * 1e3:.3f} | {r['first'] * 1e3:.2f} "
            f"| {r['repeat'] * 1e3:.2f} | {r['first64'] * 1e3:.3f} | {r['repeat64'] * 1e3:.3f} "
            f"| {r['repeat32'] * 1e3:.3f} | {r['single']:.2f} | {verdict} |")


def compare_samples(name: str, got: calc.Samples, want: np.ndarray, want_flags: np.ndarray) -> int:
    """Map results against the model, word for word. Returns the number wrong."""
    bad = int((got.bits != want).sum() + (got.flags != want_flags).sum())
    say(f"| {name} | {got.bits.size:,} | {got.runs} | {got.cycles:,} | {got.out_of_bounds} | {bad} | "
        f"{got.wall_seconds * 1e3:.1f} |")
    return bad + got.out_of_bounds


def model_map(exprs, coords, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """What map_eval should return, one word at a time, from a run of its own."""
    from mathaccel.compiler.sympy_to_evu import compile_expr

    out, flags = [], []
    for e in exprs:
        free = e.free_symbols
        used = [k for k, c in enumerate(coords) if c in free]
        program = compile_expr(e, {coords[k]: i for i, k in enumerate(used)})
        tables = [Table(str(coords[k]), float_bits(points[:, k]), 0) for k in used]
        res = evu_model.execute(Run(program, tables, (len(points),), reduce=False))
        out.append(res.results)
        flags.append(res.flags.astype(np.uint32))
    return np.stack(out, axis=1), np.stack(flags, axis=1)


def map_checks(dev: Device) -> int:
    """grad, div and curl at points, a run split in two, and bad points."""
    # I check every sampled component and flag, including chunked lists and tensor grids.
    x, y, z = sp.symbols("x y z", real=True)
    c = (x, y, z)
    rng = np.random.default_rng(4)
    say()
    say("| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |")
    say("|---|---|---|---|---|---|---|")
    pts = rng.uniform(-3, 3, (2000, 3))
    phi = sp.exp(-(x**2 + y**2)) * sp.sin(z) / (1 + z**2)
    field = (y * sp.cos(z), sp.sqrt(x**2 + y**2), sp.atan2(y, x) * z)
    bad = 0
    grad_exprs = [sp.diff(phi, s) for s in c]
    bad += compare_samples("grad, 2,000 points", calc.grad(dev, phi, c, at=pts), *model_map(grad_exprs, c, pts))
    div_expr = [sum(sp.diff(f, s) for f, s in zip(field, c, strict=True))]
    bad += compare_samples("div, 2,000 points", calc.div(dev, field, c, at=pts), *model_map(div_expr, c, pts))
    curl_exprs = [sp.diff(field[2], y) - sp.diff(field[1], z), sp.diff(field[0], z) - sp.diff(field[2], x),
                  sp.diff(field[1], x) - sp.diff(field[0], y)]
    bad += compare_samples("curl, 2,000 points", calc.curl_at(dev, field, c, at=pts), *model_map(curl_exprs, c, pts))
    # Wide inputs: every word of a NaN, an infinity and a flag has to match as well.
    wild = rng.uniform(-1, 1, (2000, 3)) * 10.0 ** rng.integers(-30, 30, (2000, 3))
    wild[::7, 0] = 0.0
    risky = [sp.log(x) / y + sp.sqrt(z), sp.exp(x) * sp.sin(y) / z]
    bad += compare_samples("mixed with bad points, 2,000 points", calc.map_eval(dev, risky, c, at=wild),
                           *model_map(risky, c, wild))
    many = rng.uniform(0.1, 3, (20000, 3))
    big = [sp.sin(x) * sp.exp(-y) + sp.sqrt(z)]
    bad += compare_samples("split across runs, 20,000 points", calc.map_eval(dev, big, c, at=many),
                           *model_map(big, c, many))
    grid = [np.linspace(0.5, 2, 20), np.linspace(0.5, 2, 20), np.linspace(0.5, 2, 20)]
    g = np.meshgrid(*grid, indexing="ij")
    flat = np.stack([a.ravel() for a in g], axis=1)
    bad += compare_samples("tensor grid, 8,000 points", calc.grad(dev, phi, c, grid=grid),
                           *model_map(grad_exprs, c, flat))
    return bad


def root_checks(dev: Device, reference: Device) -> int:
    """Root finding, against the same loop on the model and against mpmath."""
    # I compare the host iteration with a modeled device, then check its mathematical error.
    import mpmath

    x = sp.Symbol("x", real=True)
    say()
    say("| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |")
    say("|---|---|---|---|---|---|---|---|")
    bad = 0
    cases = [
        (sp.cos(x) - x, "bisect", 0, 1, lambda: mpmath.findroot(lambda w: mpmath.cos(w) - w, 0.7)),
        (sp.cos(x) - x, "newton", 1, None, lambda: mpmath.findroot(lambda w: mpmath.cos(w) - w, 0.7)),
        (x * sp.exp(x) - 3, "bisect", 0, 3, lambda: mpmath.lambertw(3).real),
        (x * sp.exp(x) - 3, "newton", 1, None, lambda: mpmath.lambertw(3).real),
        (sp.tan(x) - x, "newton", 4.5, None, lambda: mpmath.findroot(lambda w: mpmath.tan(w) - w, 4.49)),
        (sp.tanh(x) - 1 / x, "bisect", 0.5, 2, lambda: mpmath.findroot(lambda w: mpmath.tanh(w) - 1 / w, 1.2)),
    ]
    for expr, method, a, b, exact in cases:
        got = calc.root_find(dev, expr, x, a, b, method=method)
        want = calc.root_find(reference, expr, x, a, b, method=method)
        same = got.x.bits == want.x.bits and got.fx.bits == want.fx.bits and got.iterations == want.iterations
        with mpmath.workdps(40):
            error = float(abs(mpmath.mpf(got.x.value) - exact())) / calc.ulp_of(got.x.value)
        bad += not same or not got.converged
        say(f"| {expr} = 0 | {method} | {got.iterations} | {got.x} | {'yes' if same else 'NO'} | {error:.2f} "
            f"| {got.cycles / spec.CLK_HZ * 1e3:.3f} | {got.wall_seconds * 1e3:.1f} |")
    return bad


def random_programs(dev: Device, count: int, seed: int) -> int:
    """Random programs with the mixed traffic the class problems barely have."""
    # I use seeded straight-line programs to mix FPU units beyond the hand-written examples.
    rng = random.Random(seed)
    bad = outside = 0
    points = ops = 0
    peak = 0.0
    for k in range(count):
        run = evu_gen.random_run(rng, max_points=6000)
        want = evu_model.execute(run)
        got = dev.evu_run(run)
        points += run.points
        ops += run.points * len(run.program.words)
        if run.reduce:
            same = int(got.results[0]) == want.sum and int(got.flags[0]) == want.sum_flags
        else:
            same = (got.results == want.results).all() and (got.flags == want.flags.astype(np.uint32)).all()
        same = same and got.aux == want.aux
        if not same:
            bad += 1
            say(f"MISMATCH random program {k}, seed {seed}: {len(run.program.words)} instructions, grid {run.grid}, "
                f"reduce {run.reduce}")
        if not got.in_bounds:
            outside += 1
            say(f"OUT OF BOUNDS random program {k}: {got.cycles} not in {got.bounds}")
        peak = max(peak, got.cycles / got.bounds[1])
    say()
    say(f"Random programs, seed {seed}: {count} runs, {points:,} points, {ops:,} FPU operations, {bad} mismatches, "
        f"{outside} out of bounds, largest cycles/high bound {peak:.3f}.")
    return bad + outside


def long_mixed(dev: Device) -> int:
    """A long run that mixes CORDIC, divide and fast ops, far past the 4,096 points
    simulated so far. The class list's big runs are mostly a single kind of op."""
    x, y = sp.symbols("x y", real=True)
    expr = sp.sin(x) * sp.exp(-y) / (1 + x**2) + sp.sqrt(y) * sp.cos(x * y) + x * y
    run = integral_run(expr, [Axis(x, 0, 2, 16, 16), Axis(y, 0.1, 2, 16, 16)])
    want = evu_model.execute(run)
    got = dev.evu_run(run)
    same = int(got.results[0]) == want.sum and int(got.flags[0]) == want.sum_flags and got.aux == want.aux
    say()
    say(f"Long mixed run: {run.points:,} points, {len(run.program.words)} instructions, {got.cycles:,} cycles "
        f"({got.cycles / run.points:.1f} per point), bounds {got.bounds[0]:,}..{got.bounds[1]:,}, "
        f"{'matches the model' if same else 'MISMATCH'}, {'in bounds' if got.in_bounds else 'OUT OF BOUNDS'}.")
    return (not same) + (not got.in_bounds)


def divide_runs(dev: Device) -> int:
    """Divide-heavy and square-root-heavy runs big enough to show a unit's steady rate.

    These are the same six runs EvuProfileTest simulates. The class list's
    divide-only rules have 32 to 64 points, where setting up the run takes most of
    the clocks, so they can't tell my divider or square-root candidates apart."""
    # I time each run the same way as the class problems (fastest of REPEATS with
    # the image loaded), so the kernel clocks and the wall time line up with the
    # simulated profile in docs/divider-comparison.md.
    x = sp.symbols("x", real=True)
    fermi = next(p for p in PROBLEMS if p.name == "fermi_half")
    runs = [("fermi_large", integral_run(fermi.expr, fermi.axes([(1024, 16)]))),
            ("lorentz_large", integral_run(1 / (1 + x**2), [Axis(x, -4, 4, 1024, 16)])),
            ("two_divides", integral_run(x / (1 + x**2) + 1 / (2 + x), [Axis(x, 0, 1, 1024, 16)])),
            # The square-root study's runs: one root, two independent roots, and a
            # root feeding a divide.
            ("root_large", integral_run(sp.sqrt(1 + x**2), [Axis(x, 0, 1, 1024, 16)])),
            ("two_roots", integral_run(sp.sqrt(x) + sp.sqrt(1 + x), [Axis(x, 0, 1, 1024, 16)])),
            ("root_divide", integral_run(x / sp.sqrt(1 + x**2), [Axis(x, 0, 1, 1024, 16)]))]
    failures = 0
    say()
    say("| large run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |")
    say("|---|---|---|---|---|---|---|---|---|")
    for name, run in runs:
        want = evu_model.execute(run)
        got = dev.evu_run(run)
        same = int(got.results[0]) == want.sum and int(got.flags[0]) == want.sum_flags and got.aux == want.aux
        image = memory_image(run)
        again, cycles = repeat_time(dev, run, image)
        # The repeats must leave the same answer behind as the first run.
        repeat_bits = dev.read_words(image.r, 2)
        same = same and int(repeat_bits[0]) == want.sum and int(repeat_bits[1]) == want.sum_flags
        inside = got.in_bounds and all(got.bounds[0] <= c <= got.bounds[1] for c in cycles)
        failures += (not same) + (not inside)
        if not same:
            say(f"MISMATCH {name}: board 0x{int(got.results[0]):08X}/{int(got.flags[0]):05b} aux {got.aux:05b}, "
                f"model 0x{want.sum:08X}/{want.sum_flags:05b} aux {want.aux:05b}")
        say(f"| {name} | {run.points:,} | {'yes' if same else 'NO'} | {got.cycles:,} "
            f"| {got.bounds[0]:,}..{got.bounds[1]:,} | {got.cycles / run.points:.1f} | {'yes' if inside else 'NO'} "
            f"| {got.seconds * 1e3:.3f} | {again * 1e3:.2f} |")
    return failures


def slow_run(seconds: float) -> Run:
    """A reduce run that takes about this long. A divide per point is 33 clocks."""
    points = int(seconds * spec.CLK_HZ / 34)
    n0 = 2000
    n1 = points // n0
    program = Program([Instr(OP_LOAD, 0, 0).encode(), Instr(FpuOp.DIV, 1, reg(0), const(0), last=True).encode()],
                      [int(float_bits(np.array([3.0]))[0])])
    table = Table("x", float_bits(np.linspace(1, 2, n0)), 0)
    return Run(program, [table], (n0, n1))


def replies_until(transport, t0: float, deadline: float) -> list[tuple[float, int, int]]:
    """Every reply until the deadline: (seconds after t0, op, tag)."""
    out = []
    while time.perf_counter() - t0 < deadline:
        wire = transport.recv_frame(0.05)
        if wire:
            f = Frame.decode(wire)
            out.append((time.perf_counter() - t0, f.op & 0x7F, f.tag))
    return out


def busy_on(name: str, transport, dev: Device, image, length: float) -> None:
    """I send a RUN, then more requests during it, and record what comes back."""
    before = dev.status()
    run_frame = Frame(Op.RUN, 101, bytes([Kernel.EVU]) + P.evu_args(image.desc)).encode()
    t0 = time.perf_counter()
    transport.send(run_frame)
    time.sleep(0.2 * length)
    transport.send(Frame(Op.RUN, 102, bytes([Kernel.EVU]) + P.evu_args(image.desc)).encode())
    time.sleep(0.05)
    transport.send(Frame(Op.PING, 103).encode())
    time.sleep(0.05)
    transport.send(Frame(Op.PING, 104).encode())
    log = replies_until(transport, t0, 3.2 * length)
    after = dev.status()
    say(f"{name}: sent RUN(101) at 0, a second RUN(102) at {0.2 * length:.2f} s, PING(103) and PING(104) after that.")
    for at, op, tag in log:
        say(f"  reply to {Op(op).name}({tag}) {at:.2f} s after the first RUN")
    tags = [tag for _, _, tag in log]
    say(f"  second RUN {'answered, so it ran' if 102 in tags else 'never answered, so it did not run'}; "
        f"frame_errors {before.frame_errors} -> {after.frame_errors}")


def busy_checks(port: str, uart: str | None) -> None:
    """What the board really does with requests during a long run."""
    # I intentionally send traffic during a run to expose transport-specific queuing and drops.
    length = 1.5
    run = slow_run(length)
    say()
    say(f"Requests during a {length} s run, {run.points:,} points.")
    if port.startswith("udp:"):
        host = port[4:].split(":")[0]
        t = UdpTransport(host)
        dev = Device(t)
        image = dev.evu_load(run)
        busy_on("UDP", t, dev, image, length)
        dev.close()
    if uart:
        t = UartTransport(uart)
        dev = Device(t)
        image = dev.evu_load(run)
        busy_on("UART", t, dev, image, length)
        dev.close()
    # A timeout during a real run: the driver must not resend, must say the board is
    # busy, and the board must finish exactly one run.
    # The run is longer than the STATUS retries, so the board really is busy when
    # the driver first looks.
    length = 4.0
    run = slow_run(length)
    with connect(port) as dev:
        image = dev.evu_load(run)
        dev.write_words(image.f, [0xFFFFFFFF])
        marker = dev.status().last_cycles
        t0 = time.perf_counter()
        try:
            dev.run(Kernel.EVU, P.evu_args(image.desc), timeout=0.3)
            say("Forced timeout: the run answered inside 0.3 s, which is wrong for this length")
        except DeviceTimeout:
            reason = dev._after_timeout(image, 0.3)
            say(f"Forced timeout at 0.3 s: state '{reason.state}', {time.perf_counter() - t0:.2f} s in.")
        time.sleep(1.2 * length)
        finished = dev.status()
        said = dev._after_timeout(image, 0.3)
        again = dev.status()
        say(f"  afterwards STATUS answers: state '{said.state}', last_cycles {marker} -> {finished.last_cycles}, "
            f"which is {finished.last_cycles / spec.CLK_HZ:.2f} s, and still {again.last_cycles} a moment later. "
            f"One run, no restart.")


def main() -> int:
    # I select correctness, timing or busy-run checks and keep the device transport explicit.
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="udpsim")
    ap.add_argument("--problem", help="only this problem")
    ap.add_argument("--out", help="also write the report to this file")
    ap.add_argument("--random", type=int, default=100, help="how many random programs")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--busy", action="store_true", help="only the requests-during-a-run experiment")
    ap.add_argument("--uart", help="also run --busy over this serial port")
    args = ap.parse_args()

    failures = 0
    if args.busy:
        busy_checks(args.port, args.uart)
    else:
        with connect(args.port) as dev, connect("sim") as reference:
            info = dev.ping()
            say(f"Board: v{info.version[0]}.{info.version[1]}, {info.clk_hz / 1e6:.0f} MHz, port {args.port}")
            say()
            bad, rows = problems_table(dev, args.problem)
            failures += bad
            speed_table(rows)
            if not args.problem:
                failures += map_checks(dev)
                failures += root_checks(dev, reference)
                failures += long_mixed(dev)
                failures += divide_runs(dev)
                failures += random_programs(dev, args.random, args.seed)
            end = dev.status()
            say()
            say(f"STATUS afterwards: {end.crc_errors} CRC errors, {end.frame_errors} frame errors.")
            say(f"Checks failed: {failures}.")
    if args.out:
        Path(args.out).write_text("\n".join(LOG) + "\n", encoding="utf-8", newline="\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
