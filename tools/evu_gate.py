"""Phase 4 milestone 1 gate: is the exact accumulator worth building?

    python tools/evu_gate.py                  # every problem, every rule
    python tools/evu_gate.py --problem gaussian_2d --out build/evu_gate.md

For each integral in sw/mathaccel/compiler/problems.py this runs the bit-accurate
model, then adds the same per-point outputs three ways and compares each with a
40-digit reference:

  exact     the planned accumulator: nothing rounded until the end
  fp32      plain fp32 adds, in grid order
  shuffled  plain fp32 adds, in random order, which shows the order dependence
            the hardware would have, since points finish out of order

The "double" column runs the same rule in double precision with unrounded nodes.
That is the rule's own truncation error, with fp32 rounding out of the picture.
If it's larger than the others, more points would help; if it's far smaller, the
limit is fp32 rounding, of the nodes and of the arithmetic.

Errors are in ULPs of the true answer, so 0.5 is as good as fp32 can do. The
clock column is an estimate from the plan's numbers: nothing here measures RTL.
"""

# I use this gate to find numerical trouble before trusting the RTL.
# I compare the rounded program and summing choices with a high-precision
# reference. I keep the rule's integration error separate from the error
# introduced by fp32 operations and repeated rounding in a running sum.

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sw"))

import sympy as sp  # noqa: E402

from mathaccel import evu_model, ulp  # noqa: E402
from mathaccel._generated import spec  # noqa: E402
from mathaccel._generated.spec import FpuOp  # noqa: E402
from mathaccel.compiler.evu_isa import OP_LOAD, Run  # noqa: E402
from mathaccel.compiler.problems import PROBLEMS, Problem  # noqa: E402
from mathaccel.compiler.quadrature import integral_run  # noqa: E402
from mathaccel.driver.model import CORDIC_OPS  # noqa: E402


def fp32_add_up(values: np.ndarray) -> int:
    """I add fp32 patterns one at a time, in fp32. Returns the final pattern."""
    f = values.view(np.float32)
    total = np.add.accumulate(f, dtype=np.float32)[-1]
    return int(np.array([total], dtype=np.float32).view(np.uint32)[0])


def estimate_clocks(run: Run) -> int:
    """Estimated run time from the plan: the slowest of the FPU limit (one op per
    clock), the divider (one per 33 clocks) and the contexts (32 points in flight)."""
    per_point = 0
    divides = 0
    instrs = run.program.instrs()
    for i in instrs:
        if i.op in (FpuOp.DIV, FpuOp.SQRT):
            per_point += 4 + 33
            divides += 1
        elif i.op in CORDIC_OPS:
            per_point += 4 + 54
        else:
            per_point += 4 + 7
    return round(run.points * max(len(instrs), 33 * divides, per_point / spec.EVU_CONTEXTS))


def double_rule(p: Problem, rule) -> float:
    """The same rule in double precision with unrounded nodes."""
    axes = p.axes(rule)
    nodes = [a.nodes() for a in axes]
    grids = np.meshgrid(*[n for n, _ in nodes], indexing="ij")
    weights = 1.0
    for k, (_, w) in enumerate(nodes):
        shape = [1] * len(nodes)
        shape[k] = -1
        weights = weights * w.reshape(shape)
    f = sp.lambdify([a.symbol for a in axes], p.expr, "numpy")
    return math.fsum(np.broadcast_to(f(*grids) * weights, grids[0].shape).ravel().tolist())


def measure(p: Problem, rule) -> dict:
    """I collect the result and measurements for this case rather than reporting timing alone."""
    t0 = time.perf_counter()
    run = integral_run(p.expr, p.axes(rule))
    res = evu_model.execute(run)
    exact = p.exact()
    out = res.outputs
    shuffled = out[np.random.default_rng(1).permutation(len(out))]
    double = double_rule(p, rule) if run.points <= 3_000_000 else float("nan")
    u = float(ulp.ulp_of(ulp.mpref.mpf_to_fraction(exact)))

    def err(bits: int) -> float:
        return float(abs(evu_model.fp32_value(bits) - exact)) / u

    return {"name": p.name, "points": run.points, "ops": len(run.program.words),
            "regs": len({i.dst for i in run.program.instrs()}),
            "loads": sum(i.op == OP_LOAD for i in run.program.instrs()),
            "exact": err(res.sum), "fp32": err(fp32_add_up(out)), "shuffled": err(fp32_add_up(shuffled)),
            "double": float(abs(double - exact)) / u, "value": evu_model.fp32_value(res.sum),
            "aux": res.aux, "clocks": estimate_clocks(run), "seconds": time.perf_counter() - t0}


def main() -> int:
    """I run the problems, print a table, and optionally save it."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--problem", help="only this problem")
    ap.add_argument("--out", help="also write the table to this file")
    args = ap.parse_args()
    header = ("| problem | points | ops | regs | exact sum | fp32 sum | fp32 shuffled | double rule | "
              "result | est. ms |")
    rule_line = "|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, rule_line]
    print(header)
    print(rule_line)
    for p in PROBLEMS:
        if args.problem and p.name != args.problem:
            continue
        for rule in p.rules:
            m = measure(p, rule)
            line = (f"| {m['name']} | {m['points']:,} | {m['ops']} | {m['regs']} | {m['exact']:.2f} | {m['fp32']:.2f} "
                    f"| {m['shuffled']:.2f} | {m['double']:.2g} | {m['value']:.7g} | {m['clocks'] / 1e5:.2f} |")
            print(line, flush=True)
            lines.append(line)
    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
