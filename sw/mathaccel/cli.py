"""Command-line checks for the communication system and the FPU.

I use this entry point to identify the board, inspect link errors, test memory,
compare request latency with reported kernel time, and check the FPU on real
hardware. Put the global --port option before the command:
`mathaccel --port COM5 ping`, `--port udp:192.168.10.2 bench`,
`--port COM5 fptest`, `--port udp:192.168.10.2 fptest --op phase3`,
`--port sim fp div 1 3`, `--port sim fp atan2 1 2`.
The simulator uses the same driver calls but does not measure FPGA hardware.

The calc command runs the calculus tools on the expression unit:
`calc integrate "exp(-(x**2+y**2))" x=-3:3 y=-3:3 --order 32`,
`calc line "(-y,x)" "(2*cos(t),2*sin(t))" t=0:2*pi`,
`calc flux "(x,y,z)" "(sin(u)*cos(v),sin(u)*sin(v),cos(u))" u=0:pi v=0:2*pi`,
`calc grad "x**2*y" --at 1,2`, `calc div "(x,y,z)" --at 1,2,3`,
`calc curl "(-y,x,0)" --at 1,2,0`, `calc root "cos(x)-x" x=0:1`.
It exits with status 2 when an answer is NaN, infinite, flagged or not converged.
"""

from __future__ import annotations

import argparse
import struct
import time

import numpy as np
import sympy as sp

from mathaccel import calc, cref, fp32ref, ulp
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuFlag, FpuOp
from mathaccel.driver.device import connect
from mathaccel.driver.model import CORDIC_OPS, fpu_evaluate, fpu_kernel_cycles
from mathaccel.fpgen import stream


def cmd_ping(dev, _args) -> None:
    """Display the configuration returned after Device checks the identity magic."""
    info = dev.ping()
    print(f"magic ok, v{info.version[0]}.{info.version[1]}, "
          f"{1 << info.scratch_addr_bits} scratch words, clk {info.clk_hz / 1e6:.0f} MHz")


def cmd_status(dev, _args) -> None:
    """Show the link counters and most recent kernel cycle count."""
    print(dev.status())


def cmd_memtest(dev, args) -> None:
    """Write reproducible random words, read them back, and count mismatches.

    This overwrites the requested scratchpad span. The measured wall time
    includes packet transfers and host work as well as memory operations.
    """
    rng = np.random.default_rng(args.seed)
    data = rng.integers(0, 2**32, args.words, dtype=np.uint32)
    t0 = time.perf_counter()
    dev.write_words(args.addr, data)
    back = dev.read_words(args.addr, args.words)
    dt = time.perf_counter() - t0
    bad = int(np.count_nonzero(back != data))
    # Both a write and a read move four bytes per word. This payload rate
    # excludes framing overhead, although the elapsed time includes it.
    rate = 2 * 4 * args.words / dt / 1e3
    print(f"{args.words} words round trip in {dt * 1e3:.1f} ms ({rate:.0f} kB/s), mismatches: {bad}")
    if bad:
        # A failing memory check must also fail a shell command or CI step.
        raise SystemExit(1)


def cmd_bench(dev, _args) -> None:
    """Measure NOP request latency, then display device-reported FILL time."""
    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        dev.nop()
    rtt = (time.perf_counter() - t0) / n
    # rtt includes host and link costs. r.seconds below converts only the
    # kernel's reported busy clocks; these are different measurements.
    r = dev.fill(0, 4096, 0xDEADBEEF)
    print(f"NOP round trip: {rtt * 1e6:.0f} us   FILL 4096 words: {r.cycles} cycles ({r.seconds * 1e6:.1f} us)")


def f32_bits(text: str) -> int:
    """Parse an operand: a decimal number (1.5, -2e-40, inf, nan) or raw bits (0x3fc00000)."""
    if text.lower().startswith("0x"):
        return int(text, 16) & 0xFFFFFFFF
    return struct.unpack("<I", struct.pack("<f", float(text)))[0]


def describe(bits: int) -> str:
    return f"{struct.unpack('<f', struct.pack('<I', bits))[0]!r:>16} (0x{bits:08X})"


def flag_names(flags: int) -> str:
    """Print all flags present in the five-bit result status, or a dash for an exact ordinary result."""
    return "|".join(f.name for f in FpuFlag if flags & f) or "-"


def cmd_fp(dev, args) -> None:
    """Run one FPU operation on the device and show the result next to the model's."""
    op = FpuOp[args.op.upper()]
    a = int(args.a, 0) & 0xFFFFFFFF if op == FpuOp.I2F else f32_bits(args.a)
    b = f32_bits(args.b) if args.b is not None else 0
    res, fl, run = dev.fpu(op, [a], [b])
    exp_r, exp_f = fpu_evaluate(op, a, b)
    as_int = op in (FpuOp.F2I, FpuOp.EQ, FpuOp.LT, FpuOp.LE)
    shown = f"{int(np.int32(res[0]))} (0x{int(res[0]):08X})" if as_int else describe(int(res[0]))
    print(f"{op.name}: {shown}  flags {flag_names(int(fl[0]))}  [{run.cycles} cycles]")
    if (int(res[0]), int(fl[0])) != (exp_r, exp_f):
        print(f"MISMATCH: model says 0x{exp_r:08X} flags {flag_names(exp_f)}")
        raise SystemExit(1)


def fptest_ops(name: str) -> list[FpuOp]:
    """The ops named by fptest --op: 'all', 'exact' (0-11), 'phase3' (12-17) or one op."""
    if name == "all":
        return [FpuOp(op) for op in range(spec.FPU_OP_COUNT)]
    if name == "exact":
        return list(fp32ref.EXACT_OPS)
    if name == "phase3":
        return list(CORDIC_OPS)
    return [FpuOp[name.upper()]]


def fptest_vectors(op: FpuOp, n: int, seed: int) -> list[tuple[int, int]]:
    """(a, b) operands. The LCG stream favours special values and ordinary
    numbers, so the CORDIC ops also get half their vectors from the ULP tool's
    generators, which aim at the hard cases (near multiples of pi/2, the exp
    and ln edges, all atan2 quadrants)."""
    if op not in CORDIC_OPS:
        return [(a, b) for _, a, b in stream(seed + int(op), n, int(op))]
    hard = ulp.op_inputs(op, n - n // 2, seed)
    return hard + [(a, b) for _, a, b in stream(seed + int(op), n // 2, int(op))]


def fptest_predict(op: FpuOp, pairs: list[tuple[int, int]], c) -> list[tuple[int, int]]:
    """The (result, flags) the hardware must return. The C port of the CORDIC
    model does a whole array at once, so I use it when a C compiler is around."""
    if op in CORDIC_OPS and c is not None:
        r, f = c.cordic_many(op, [a for a, _ in pairs], [b for _, b in pairs])
        return [(int(x), int(y)) for x, y in zip(r, f, strict=True)]
    return [fpu_evaluate(op, a, b) for a, b in pairs]


def cmd_fptest(dev, args) -> None:
    """Run seeded FPU vectors on the device and compare every result and flag
    word, and every kernel cycle count, with the Python reference model.
    Exits non-zero on any mismatch.

    Ops 0-11 must equal the correctly rounded IEEE answer. The CORDIC ops
    12-17 must equal the bit-accurate model of the unit, and I also measure
    their error against the exact value, since a model and hardware that
    agreed with each other could still both be wrong."""
    ops = fptest_ops(args.op)
    chunk = 1024                     # elements per FPU kernel run
    c = cref.load() if any(op in CORDIC_OPS for op in ops) else None
    bad_total = 0
    for op in ops:
        pairs = fptest_vectors(op, args.n, args.seed)
        got: list[tuple[int, int]] = []
        bad = bad_cycles = 0
        cycles = 0
        t0 = time.perf_counter()
        want = fptest_predict(op, pairs, c)
        for i in range(0, len(pairs), chunk):
            part = pairs[i:i + chunk]
            res, fl, run = dev.fpu(op, [p[0] for p in part], [p[1] for p in part])
            cycles += run.cycles
            bad_cycles += run.cycles != fpu_kernel_cycles(op, len(part))
            got += [(int(r), int(f)) for r, f in zip(res, fl, strict=True)]
        for (x, y), g, w in zip(pairs, got, want, strict=True):
            if g != w:
                bad += 1
                if bad <= 5:
                    print(f"  {op.name} a=0x{x:08X} b=0x{y:08X}: got 0x{g[0]:08X}/{g[1]:05b}"
                          f" want 0x{w[0]:08X}/{w[1]:05b}")
        dt = time.perf_counter() - t0
        per = cycles / len(pairs)
        print(f"{op.name:5s} {len(pairs)} vectors, {bad} mismatches, {bad_cycles} wrong cycle counts, "
              f"{per:.1f} kernel cycles/element, {dt:.2f} s wall time")
        bad_total += bad + bad_cycles
        if op in CORDIC_OPS:
            if c is not None:
                st = ulp.screen(op, pairs, got, c)
            else:
                st = ulp.UlpStats()
                for (x, y), (r, f) in zip(pairs, got, strict=True):
                    st.add(op, x, y, r, f)
            s = st.ops[op]
            # Results equal to the C reference are only known to be under 0.5 ULP.
            worst = f"{s.max_err:.3f}" if s.max_err >= 0.5 else "at most 0.5"
            print(f"      max error {worst} ULP, {s.correctly_rounded} of {s.n} correctly rounded, "
                  f"{st.errors()} over budget or with wrong flags")
            bad_total += st.errors()
    if bad_total:
        raise SystemExit(1)


def calc_expr(text: str):
    """Parse an expression or a parenthesised vector, such as "(-y, x, 0)"."""
    parsed = sp.sympify(text)
    return tuple(parsed) if isinstance(parsed, (tuple, sp.Tuple)) else parsed


def calc_limit(text: str) -> tuple:
    """Parse 'x=0:2*pi' into (symbol, low, high). Limits may use other variables."""
    name, sep, span = text.partition("=")
    low, colon, high = span.partition(":")
    if not (sep and colon):
        raise SystemExit(f"a limit looks like x=0:1, not {text!r}")
    return sp.Symbol(name), sp.sympify(low), sp.sympify(high)


def calc_coords(field, args) -> list:
    """The coordinates, from --coords or else x, y, z used by the expression."""
    if args.coords:
        return [sp.Symbol(n) for n in args.coords.split(",")]
    exprs = field if isinstance(field, tuple) else (field,)
    count = len(exprs) if isinstance(field, tuple) else None
    if count is None:
        used = sorted(s.name for e in exprs for s in e.free_symbols)
        count = max(("xyz".index(n) + 1 for n in used if n in "xyz"), default=1)
    return [sp.Symbol(n) for n in "xyz"[:count]]


def calc_where(args, count: int) -> dict:
    """Where to evaluate: --at 1,2,3 or --grid "0:1:11" per axis."""
    if args.at:
        points = [[float(sp.sympify(v)) for v in p.split(",")] for p in args.at]
        if any(len(p) != count for p in points):
            raise SystemExit(f"each --at point needs {count} values")
        return {"at": points}
    if not args.grid:
        raise SystemExit("give --at or --grid")
    axes = []
    for text in args.grid:
        low, high, n = text.split(":")
        axes.append(np.linspace(float(sp.sympify(low)), float(sp.sympify(high)), int(n)))
    return {"grid": axes}


# I keep the CLI as setup and presentation: calc builds the requested
# expression, and the chosen device evaluates it. I surface bad numerical
# results in the exit status as well as in the printed answer.
def cmd_calc(dev, args) -> None:
    """Run one calculus tool on the device and print its answer and any warnings."""
    options = {"panels": args.panels, "order": args.order, "check": not args.no_check,
               "max_seconds": args.max_seconds} if "panels" in args else {}
    if args.tool == "integrate":
        result = calc.integrate_nd(dev, sp.sympify(args.expr), [calc_limit(t) for t in args.limits], **options)
    elif args.tool == "line":
        result = calc.line_integral(dev, calc_expr(args.field), calc_expr(args.curve), calc_limit(args.limit),
                                    **options)
    elif args.tool == "flux":
        result = calc.surface_flux(dev, calc_expr(args.field), calc_expr(args.surface), calc_limit(args.u),
                                   calc_limit(args.v), **options)
    elif args.tool == "root":
        var, low, high = calc_limit(args.bracket)
        result = calc.root_find(dev, sp.sympify(args.expr), var, float(low), float(high) if not args.newton else None,
                                method="newton" if args.newton else "bisect")
    else:
        field = calc_expr(args.expr)
        coords = calc_coords(field, args)
        tool = {"grad": calc.grad, "div": calc.div, "curl": calc.curl_at}[args.tool]
        result = tool(dev, field, coords, **calc_where(args, len(coords)))
    print(result)
    # A shell script must not mistake a NaN or an unconverged rule for an answer.
    if not result.clean:
        raise SystemExit(2)


def add_calc(sub) -> None:
    """The calc subcommand and its seven tools."""
    cp = sub.add_parser("calc", help="calculus on the expression unit: integrate, line, flux, grad, div, curl, root")
    tools = cp.add_subparsers(dest="tool", required=True)

    def integral(name: str, text: str):
        p = tools.add_parser(name, help=text)
        p.add_argument("--panels", type=int, default=1, help="panels per axis")
        p.add_argument("--order", type=int, default=16, help="Gauss-Legendre nodes per panel")
        p.add_argument("--no-check", action="store_true", help="skip the second run on a finer rule")
        p.add_argument("--max-seconds", type=float, default=30.0, help="refuse a run longer than this")
        return p

    p = integral("integrate", "integrate an expression; inner limits come first and may use outer variables")
    p.add_argument("expr")
    p.add_argument("limits", nargs="+", help="x=0:1, one per variable")
    p = integral("line", "line integral: a vector field gives work, a scalar gives arc length")
    p.add_argument("field")
    p.add_argument("curve", help="(x(t), y(t), ...)")
    p.add_argument("limit", help="t=0:1")
    p = integral("flux", "surface integral: a vector field gives flux, a scalar gives area")
    p.add_argument("field")
    p.add_argument("surface", help="(x(u,v), y(u,v), z(u,v))")
    p.add_argument("u")
    p.add_argument("v")
    for name, text in (("grad", "gradient of a scalar"), ("div", "divergence of a vector field"),
                       ("curl", "curl of a vector field")):
        p = tools.add_parser(name, help=text)
        p.add_argument("expr")
        p.add_argument("--coords", help="names, comma separated, default x,y,z")
        p.add_argument("--at", action="append", help="a point: 1,2,3. Repeat for more.")
        p.add_argument("--grid", action="append", help="low:high:n for each coordinate, repeated in order")
    p = tools.add_parser("root", help="solve f(x)=0 with evaluations on the board")
    p.add_argument("expr")
    p.add_argument("bracket", help="x=0:1. --newton starts from the low value")
    p.add_argument("--newton", action="store_true")


def main(argv: list[str] | None = None) -> None:
    """Parse a subcommand and keep its connection open for the whole operation."""
    ap = argparse.ArgumentParser(prog="mathaccel")
    ap.add_argument("--port", default="sim",
                    help='serial port like COM5, udp:192.168.10.2 for Ethernet, "udpsim", or "sim" (default)')
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    sub.add_parser("status")
    mt = sub.add_parser("memtest")
    mt.add_argument("--addr", type=int, default=0)
    mt.add_argument("--words", type=int, default=4096)
    mt.add_argument("--seed", type=int, default=1)
    sub.add_parser("bench")
    fp = sub.add_parser("fp", help="one FPU operation, e.g. `fp div 1 3` or `fp sqrt 2`")
    fp.add_argument("op", choices=[FpuOp(o).name.lower() for o in range(spec.FPU_OP_COUNT)])
    fp.add_argument("a")
    fp.add_argument("b", nargs="?")
    ft = sub.add_parser("fptest", help="seeded FPU vectors checked against the Python model")
    ft.add_argument("--op", default="all", help="op name, 'all', 'exact' (ops 0-11) or 'phase3' (ops 12-17)")
    ft.add_argument("--n", type=int, default=2000, help="vectors per op")
    ft.add_argument("--seed", type=int, default=1)
    add_calc(sub)
    args = ap.parse_args(argv)
    # The context manager closes a physical serial port even if a command
    # raises. A dictionary selects the handler with no separate CLI branches.
    with connect(args.port) as dev:
        handlers = {"ping": cmd_ping, "status": cmd_status, "memtest": cmd_memtest, "bench": cmd_bench,
                    "fp": cmd_fp, "fptest": cmd_fptest, "calc": cmd_calc}
        handlers[args.cmd](dev, args)


if __name__ == "__main__":
    main()
