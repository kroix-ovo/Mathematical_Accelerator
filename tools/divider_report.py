"""Collect my divider experiment results into one comparison (docs/divider-comparison.md).

    python tools/divider_report.py            print the tables
    python tools/divider_report.py --json F   also save the collected numbers

Each candidate lives in its own worktree under worktrees/, and the split baseline
is this checkout. From each one I read only files its own gate produced:
  build/gate/summary.json      pass/fail of every gate step and the commit tested
  build/evu_profile.json       EvuProfileTest: clocks per run, by category
  build/vivado/timing.rpt      routed WNS and WHS of the full Nexys design
  build/vivado/util_hier.rpt   routed LUTs, FFs, BRAM, DSPs, total and per unit
and from its sources the timing constants in sw/mathaccel/driver/model.py,
which the top-level testbenches check exactly against the RTL's FPU kernel
cycle counts. The combined-unit numbers (before the split) come from the saved
Phase 4 reports.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (label, checkout, short description)
TRACKS = [
    ("split baseline", ROOT, "fp_div: 1 bit per clock, one divide at a time"),
    ("A two-step", ROOT / "worktrees/div-two-step", "2 bits per clock, compare 2r with d, 2d, 3d"),
    ("B pipeline", ROOT / "worktrees/div-pipeline", "14 stages of the 2-bit step, result queue"),
    ("C SRT-4", ROOT / "worktrees/div-srt4", "radix-4 SRT, digits -2..2, 14 clocks"),
    ("D dual lane", ROOT / "worktrees/div-dual-lane", "two baseline lanes, older result first"),
    ("E reciprocal", ROOT / "worktrees/div-multiplicative", "table + 3rd-order Newton step, DSP pipeline"),
]


def model_constants(root: Path) -> dict[str, int]:
    text = (root / "sw/mathaccel/driver/model.py").read_text(encoding="utf-8")
    return {k: int(v) for k, v in re.findall(r"^(DIV_LANES|DIV_INTERVAL|DIV_LAST|SQRT_\w+) = (\d+)", text, re.M)}


def kernel_cycles(n: int, lanes: int, interval: int, last: int, fetch: int = 4) -> int:
    """Same recurrence as unit_kernel_cycles in model.py."""
    at = [0]
    for k in range(1, n):
        free = at[k - lanes] + interval if k >= lanes else 0
        at.append(max(at[-1] + fetch, free))
    return at[-1] + last


def timing(path: Path) -> tuple[float, float] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"WNS\(ns\).*?\n.*?\n\s+(-?[\d.]+)\s+\S+\s+\d+\s+\d+\s+(-?[\d.]+)", text)
    return (float(m.group(1)), float(m.group(2))) if m else None


def utilization(path: Path) -> dict[str, dict[str, int]]:
    """Rows of the hierarchical utilization report: instance -> LUT, FF, BRAM, DSP."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 10 and cells[3].isdigit():
            # | Instance | Module | Total LUTs | Logic LUTs | LUTRAMs | SRLs | FFs | RAMB36 | RAMB18 | DSP Blocks |
            out[cells[1]] = {"lut": int(cells[3]), "lutram": int(cells[5]), "srl": int(cells[6]),
                             "ff": int(cells[7]), "bram": float(cells[8]) + float(cells[9]) / 2, "dsp": int(cells[10])}
    return out


def collect(root: Path) -> dict:
    row: dict = {"root": str(root)}
    summ = root / "build/gate/summary.json"
    if summ.exists():
        s = json.loads(summ.read_text(encoding="utf-8"))
        row["gate"] = {"passed": s["passed"], "head": s["head"], "dirty": s["dirty"],
                       "steps": len(s["steps"]), "failed": [x["step"] for x in s["steps"] if not x["ok"]]}
        lines = [ln for st in s["steps"] for ln in st["lines"]]
        found = [re.search(r"checked (\d+) results, 0 errors", ln) for ln in lines]
        row["checked"] = sum(int(m.group(1)) for m in found if m)
    prof = root / "build/evu_profile.json"
    if prof.exists():
        row["profile"] = json.loads(prof.read_text(encoding="utf-8"))
    row["model"] = model_constants(root)
    row["timing"] = timing(root / "build/vivado/timing.rpt")
    row["util"] = utilization(root / "build/vivado/util_hier.rpt")
    # fp_div placed and routed on its own (build_div.tcl).
    ooc = root / "build/vivado_div"
    row["ooc"] = {"timing": timing(ooc / "timing.rpt"), **ooc_util(ooc / "util.rpt")}
    return row


def ooc_util(path: Path) -> dict[str, float]:
    """LUTs, FFs, DSPs and BRAM tiles from a flat utilization report."""
    out: dict[str, float] = {}
    if not path.exists():
        return out
    text = path.read_text(encoding="utf-8", errors="replace")
    for key, label in (("lut", "Slice LUTs"), ("ff", "Slice Registers"), ("dsp", "DSPs"), ("bram", "Block RAM Tile")):
        m = re.search(rf"^\|\s*{label}\*?\s*\|\s*([\d.]+)", text, re.M)
        if m:
            out[key] = float(m.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()
    rows = [(label, desc, collect(root)) for label, root, desc in TRACKS]
    if args.json:
        Path(args.json).write_text(json.dumps([{"label": lb, "desc": d, **r} for lb, d, r in rows], indent=1))

    print("| track | gate | DIV interval | DIV latency | kernel clocks/divide | WNS | WHS | LUT | FF | DSP | "
          "BRAM | fp_div LUT | fp_div FF | fp_div DSP |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for label, _, r in rows:
        m = r["model"]
        g = r.get("gate", {})
        gate = (f"{g['steps'] - len(g['failed'])}/{g['steps']}" if g else "-")
        per = kernel_cycles(4096, m["DIV_LANES"], m["DIV_INTERVAL"], m["DIV_LAST"]) / 4096
        interval = f"{m['DIV_INTERVAL']}" + (f" per lane x{m['DIV_LANES']}" if m["DIV_LANES"] > 1 else "")
        t = r["timing"] or (float("nan"), float("nan"))
        u = r["util"].get("nexys_top", {})
        d = r["util"].get("u_div", {})
        print(f"| {label} | {gate} | {interval} | {m['DIV_LAST'] - 6} | {per:.2f} | {t[0]:+.3f} | {t[1]:+.3f} | "
              f"{u.get('lut', '-')} | {u.get('ff', '-')} | {u.get('dsp', '-')} | {u.get('bram', '-')} | "
              f"{d.get('lut', '-')} | {d.get('ff', '-')} | {d.get('dsp', '-')} |")

    print()
    print("| track | fp_div alone: WNS | WHS | LUT | FF | DSP | BRAM tiles |")
    print("|---|---|---|---|---|---|---|")
    for label, _, r in rows:
        o = r["ooc"]
        t = o.get("timing") or (float("nan"), float("nan"))
        print(f"| {label} | {t[0]:+.3f} | {t[1]:+.3f} | {o.get('lut', '-'):g} | {o.get('ff', '-'):g} | "
              f"{o.get('dsp', '-'):g} | {o.get('bram', '-'):g} |" if o.get("lut") is not None else f"| {label} | - |")

    base = {p["name"] + str(p["points"]): p for p in rows[0][2].get("profile", [])}
    print()
    names = [label for label, _, _ in rows]
    print("| run | points | " + " | ".join(names) + " |")
    print("|---|---|" + "---|" * len(names))
    for key, p in base.items():
        cells = []
        for _, _, r in rows:
            q = {x["name"] + str(x["points"]): x for x in r.get("profile", [])}.get(key)
            cells.append(f"{q['clocks'] / q['points']:.1f}" + ("" if q["good"] else " (bad)") if q else "-")
        print(f"| {p['name']} | {p['points']} | " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
