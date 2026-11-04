"""Measure the ULP error of the CORDIC model (or the C reference) against mpmath.

    python tools/measure_ulp.py --op sin --n 20000
    python tools/measure_ulp.py --op vec --n 200000 --screen
    python tools/measure_ulp.py --op both --n 1000000 --screen
    python tools/measure_ulp.py --op sin --n 200000 --screen --W 40 --Z 40 --N 38
    python tools/measure_ulp.py --sweep --n 200000

Without --screen every result is measured against mpmath (about 10,000 per
second). With --screen, each model result is first compared with the C
reference (fp_ref.c, double precision, essentially always correctly rounded).
Results that agree are counted as correctly rounded; only the ones that
differ get an exact mpmath measurement. That is the plan's screening idea for
the board sweeps, and it makes a million inputs take about a minute.

--sweep runs the screen for a list of narrower datapath widths, to find the
smallest widths that still meet the budget.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sw"))

from mathaccel import cordic_model, cref, ulp  # noqa: E402
from mathaccel._generated.spec import FpuOp  # noqa: E402


def run(op: int, pairs: list[tuple[int, int]], p: cordic_model.CordicParams, impl: str, screen,
        budget: float) -> ulp.UlpStats:
    """Measure one op over (a, b) operand pairs and return the statistics."""
    st = ulp.UlpStats(budget=budget)
    if impl == "c":
        c = screen or cref.load()
        results = [c.eval(op, a, b) for a, b in pairs]
    else:
        results = [cordic_model.evaluate(op, a, b, p) for a, b in pairs]
    if screen is not None:
        return ulp.screen(op, pairs, results, screen, budget)
    # The full mpmath path, for when I need every sample checked independently.
    for (a, b), (r, f) in zip(pairs, results, strict=True):
        st.add(op, a, b, r, f)
    return st


def main() -> int:
    """Choose functions and widths, run a reproducible sample, and fail on a measured budget violation."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", default="both",
                    help="sin, cos, atan2, hypot, exp, ln, both (sin and cos), vec (atan2 and hypot), "
                         "hyp (exp and ln) or all")
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--impl", default="model", choices=["model", "c"])
    ap.add_argument("--screen", action="store_true", help="measure exactly only where the result differs from C")
    ap.add_argument("--budget", type=float, default=1.0)
    ap.add_argument("--sweep", action="store_true")
    for f in dataclasses.fields(cordic_model.CordicParams):
        ap.add_argument(f"--{f.name}", type=int, default=f.default)
    args = ap.parse_args()

    names = [f.name for f in dataclasses.fields(cordic_model.CordicParams)]
    p = cordic_model.CordicParams(**{n: getattr(args, n) for n in names})
    groups = {"both": [FpuOp.SIN, FpuOp.COS], "vec": [FpuOp.ATAN2, FpuOp.HYPOT], "hyp": [FpuOp.EXP, FpuOp.LN],
              "all": [FpuOp.SIN, FpuOp.COS, FpuOp.ATAN2, FpuOp.HYPOT, FpuOp.EXP, FpuOp.LN]}
    ops = groups.get(args.op) or [FpuOp[args.op.upper()]]
    screen = cref.load() if (args.screen or args.sweep) else None
    inputs = {op: ulp.op_inputs(op, args.n, args.seed) for op in ops}

    if args.sweep:
        # Narrow one width at a time from the defaults; report the worst error.
        configs = [p]
        for w in (44, 42, 40, 38):
            configs.append(dataclasses.replace(p, W=w, Z=w))
        for n in (40, 38, 36):
            configs.append(dataclasses.replace(p, N=n))
        for L in (92, 88, 84):
            configs.append(dataclasses.replace(p, L=L))
        print(f"{args.n} inputs per op, screened against C")
        for cfg in configs:
            worst = 0.0
            for op in ops:
                st = run(op, inputs[op], cfg, "model", screen, args.budget)
                worst = max(worst, st.ops[op].max_err)
            print(f"L={cfg.L} Z={cfg.Z} W={cfg.W} N={cfg.N}: max {worst:.3f} ULP")
        return 0

    total_bad = 0
    for op in ops:
        t0 = time.perf_counter()
        st = run(op, inputs[op], p, args.impl, screen, args.budget)
        print(st.report())
        print(f"  ({len(inputs[op])} inputs, {time.perf_counter() - t0:.1f} s, {p})")
        total_bad += st.errors()
    return 1 if total_bad else 0


if __name__ == "__main__":
    sys.exit(main())
