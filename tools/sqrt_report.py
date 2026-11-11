"""Collect my square-root experiment results into one comparison (docs/sqrt-comparison.md).

    python tools/sqrt_report.py            print the tables
    python tools/sqrt_report.py --json F   also save the collected numbers

The same evidence as tools/divider_report.py, from the same files: each track's
own gate summary, EvuProfileTest cycle profile, routed full-design reports, the
square-root unit's out-of-context build (build/vivado_sqrt/) and the timing
constants in its sw/mathaccel/driver/model.py. The baseline is this checkout:
the reciprocal divider (E) with the original one-at-a-time square root.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from divider_report import kernel_cycles, model_constants, ooc_util, timing, utilization

ROOT = Path(__file__).resolve().parents[1]

# (label, checkout, short description)
TRACKS = [
    ("baseline", ROOT, "fp_sqrt: 1 bit per clock, one root at a time"),
    ("S1 pipeline", ROOT / "worktrees/sqrt-pipeline", "13 stages of two root steps, result queue"),
    ("S2 multiplicative", ROOT / "worktrees/sqrt-multiplicative", "1/sqrt table + 3rd-order step, DSP pipeline"),
    ("S3 shared", ROOT / "worktrees/divsqrt-shared", "one pipeline for divide and square root"),
]
# Instance names of the divide and square-root units in the routed hierarchy.
UNITS = ("u_div", "u_sqrt", "u_ds")


def collect(root: Path) -> dict:
    """Everything one track's own runs left behind."""
    row: dict = {"root": str(root)}
    summ = root / "build/gate/summary.json"
    if summ.exists():
        s = json.loads(summ.read_text(encoding="utf-8"))
        row["gate"] = {"passed": s["passed"], "head": s["head"], "dirty": s["dirty"], "steps": len(s["steps"]),
                       "failed": [x["step"] for x in s["steps"] if not x["ok"]]}
    prof = root / "build/evu_profile.json"
    if prof.exists():
        row["profile"] = json.loads(prof.read_text(encoding="utf-8"))
    row["model"] = model_constants(root)
    row["timing"] = timing(root / "build/vivado/timing.rpt")
    row["util"] = utilization(root / "build/vivado/util_hier.rpt")
    ooc = root / "build/vivado_sqrt"
    row["ooc"] = {"timing": timing(ooc / "timing.rpt"), **ooc_util(ooc / "util.rpt")}
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()
    rows = [(label, desc, collect(root)) for label, root, desc in TRACKS]
    if args.json:
        Path(args.json).write_text(json.dumps([{"label": lb, "desc": d, **r} for lb, d, r in rows], indent=1))

    print("| track | gate | SQRT interval | SQRT latency | kernel clocks/root | WNS | WHS | LUT | FF | DSP | BRAM "
          "| divide+sqrt LUT | divide+sqrt FF | divide+sqrt DSP |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for label, _, r in rows:
        m = r["model"]
        g = r.get("gate", {})
        gate = f"{g['steps'] - len(g['failed'])}/{g['steps']}" if g else "-"
        per = kernel_cycles(4096, m["SQRT_LANES"], m["SQRT_INTERVAL"], m["SQRT_LAST"]) / 4096
        t = r["timing"] or (float("nan"), float("nan"))
        u = r["util"].get("nexys_top", {})
        units = [r["util"][n] for n in UNITS if n in r["util"]]
        ds = {k: sum(x[k] for x in units) for k in ("lut", "ff", "dsp")} if units else {}
        print(f"| {label} | {gate} | {m['SQRT_INTERVAL']} | {m['SQRT_LAST'] - 6} | {per:.2f} | {t[0]:+.3f} "
              f"| {t[1]:+.3f} | {u.get('lut', '-')} | {u.get('ff', '-')} | {u.get('dsp', '-')} | {u.get('bram', '-')} "
              f"| {ds.get('lut', '-')} | {ds.get('ff', '-')} | {ds.get('dsp', '-')} |")

    print()
    print("| track | square-root unit alone: WNS | WHS | LUT | FF | DSP | BRAM tiles |")
    print("|---|---|---|---|---|---|---|")
    for label, _, r in rows:
        o = r["ooc"]
        if o.get("lut") is None:
            print(f"| {label} | - |")
            continue
        t = o.get("timing") or (float("nan"), float("nan"))
        print(f"| {label} | {t[0]:+.3f} | {t[1]:+.3f} | {o.get('lut', 0):g} | {o.get('ff', 0):g} "
              f"| {o.get('dsp', 0):g} | {o.get('bram', 0):g} |")

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
