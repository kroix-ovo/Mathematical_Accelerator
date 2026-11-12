"""Run my divider validation gate: the same checks for the baseline and every candidate.

    python hw/scripts/run_gate.py                 software, RTL simulations, profile
    python hw/scripts/run_gate.py --vivado        also build and route the full design
    python hw/scripts/run_gate.py --only pyuvm    just the steps whose name starts with "pyuvm"
    python hw/scripts/run_gate.py --list          print the steps without running them
    python hw/scripts/run_gate.py --only sw --keep   re-run some steps, keep the other results

I wrote this for docs/divider-optimization-task.md. Every divider candidate lives
in its own worktree, and I need to be sure each one passed exactly the same list,
so the list lives here instead of in my notes. I run it from the checkout I want
to test; every path below is relative to this script's own repository, so a
worktree tests its own RTL and writes its own build/ folder.

Each step's console output goes to build/gate/<step>.log, and build/gate/summary.json
records pass/fail, time, the commit it ran on and the lines I compare between
candidates (result counts, latencies, the overlap probe). With --keep, the steps I
run replace their old rows and every other row stays, still naming the commit it
ran on, so a test-only fix doesn't force the whole gate to run again. The steps are
the CI list from .github/workflows/ci.yml plus the SV UVM tests in xsim, the EVU
cycle profile and, with --vivado, the routed build. Steps that use different
simulator build folders run in parallel; steps that share one run one after
another, because a second build would overwrite the first.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_xsim import find_bin  # noqa: E402

OUT = ROOT / "build" / "gate"


def _venv_python() -> str:
    """This checkout's own virtual environment if it has one, else the running Python.

    A worktree needs its own .venv: an editable install from another checkout
    would import that checkout's mathaccel package and test the wrong host code.
    """
    for p in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if p.exists():
            return str(p)
    return sys.executable


PY = _venv_python()


def wsl(cmd: str) -> list[str]:
    """Wrap a shell command so it runs in WSL with the OSS CAD Suite and the pyuvm venv.

    PYTHONPATH=sw makes the simulator's embedded Python import this checkout's
    mathaccel package, not one installed somewhere else.
    """
    drive = ROOT.drive.rstrip(":").lower()
    path = "/mnt/" + drive + "/" + "/".join(ROOT.parts[1:])
    script = ("source ~/tools/oss-cad-suite/environment && source ~/venvs/mathaccel/bin/activate && "
              f"cd '{path}' && PYTHONPATH=sw {cmd}")
    return ["wsl.exe", "-e", "bash", "-c", script] if sys.platform == "win32" else ["bash", "-c", script]


def pyuvm(args: str) -> list[str]:
    """A pyuvm run through hw/tb/pyuvm/run.py."""
    return wsl(f"python hw/tb/pyuvm/run.py {args}")


# (name, group, command). Steps in the same group share a build folder, so they
# run in order. Different groups run at the same time.
STEPS: list[tuple[str, str, list[str]]] = [
    # First make sure the host package under test is this checkout's own.
    ("sw-import-check", "sw", [PY, "-c", "import pathlib, sys, mathaccel; "
                               f"p = pathlib.Path(mathaccel.__file__).resolve(); print(p); "
                               f"sys.exit(0 if pathlib.Path(r'{ROOT}') in p.parents else 1)"]),
    ("sw-gen-spec", "sw", [PY, "tools/gen_spec.py", "--check"]),
    ("sw-gen-cordic", "sw", [PY, "tools/gen_cordic.py", "--check"]),
    ("sw-ruff", "sw", [PY, "-m", "ruff", "check", "sw", "tools", "hw/tb", "hw/scripts"]),
    ("sw-pytest", "sw", [PY, "-m", "pytest", "-q"]),
    ("sw-pytest-udpsim", "sw", [PY, "-m", "pytest", "-q", "sw/tests/test_device.py", "--port", "udpsim"]),
    ("cocotb-verilator", "cocotb_v", wsl("python hw/tb/cocotb/run.py --sim verilator")),
    ("pyuvm-fpu", "fpu_v", pyuvm("fpu --test FpuRegressTest --n 3000")),
    # Divide only: about 6,000 directed hard cases, then 30,000 random divides.
    ("pyuvm-fpu-div", "fpu_v", pyuvm("fpu --test FpuDivTest --n 30000")),
    # Square root only: about 9,300 directed hard cases, then 30,000 random roots.
    ("pyuvm-fpu-sqrt", "fpu_v", pyuvm("fpu --test FpuSqrtTest --n 30000")),
    ("pyuvm-fpu-icarus-corner", "fpu_i", pyuvm("fpu --sim icarus --test FpuCornerTest")),
    ("pyuvm-fpu-icarus-div", "fpu_i", pyuvm("fpu --sim icarus --test FpuDivTest --n 2000")),
    ("pyuvm-fpu-icarus-sqrt", "fpu_i", pyuvm("fpu --sim icarus --test FpuSqrtTest --n 2000")),
    ("pyuvm-fpu16", "fpu16_v", pyuvm("fpu16 --test FpuRegressTest --n 3000")),
    ("pyuvm-top", "top_v", pyuvm("top --test TopRegressTest")),
    ("pyuvm-eth", "eth_v", pyuvm("eth --test EthRegressTest")),
    ("pyuvm-eth-icarus-multi", "eth_i", pyuvm("eth --sim icarus --test EthMultiTest")),
    ("pyuvm-accum", "accum_v", pyuvm("accum --test AccumRegressTest --n 1000")),
    ("pyuvm-evu", "evu_v", pyuvm("evu --test EvuRegressTest --n 120")),
    ("pyuvm-evu-limit", "evu_v", pyuvm("evu --test EvuLimitTest --n 200")),
    ("pyuvm-evu-profile", "evu_v", pyuvm("evu --test EvuProfileTest --param PROFILE=1")),
    ("pyuvm-evu16", "evu16_v", pyuvm("evu16 --test EvuRegressTest --n 60")),
    ("pyuvm-evu-icarus", "evu_i", pyuvm("evu --sim icarus --test EvuRegressTest --n 15")),
    ("pyuvm-cordic", "cordic_v", pyuvm("cordic --test CordicRegressTest --n 5000")),
    ("xsim-fpu", "xsim_fpu", [PY, "hw/scripts/run_uvm.py", "fpu", "--test", "fpu_regress_test", "--n", "20000"]),
    ("xsim-top", "xsim_top", [PY, "hw/scripts/run_uvm.py", "top", "--test", "top_regress_test"]),
]
VIVADO = ("vivado-build", "vivado", [find_bin(None, "vivado"), "-mode", "batch", "-nojournal",
                                     "-log", "build/vivado/build.log", "-source", "hw/scripts/build.tcl"])

# Lines worth keeping in the summary, so candidates can be compared without logs.
KEEP = re.compile(r"checked \d+ results|CORDIC results:|divide/sqrt units:|CORDIC FIFO|PROFILE |"
                  r"WNS:|passed|failed|PASS:|FAIL:|UVM_ERROR :|UVM_FATAL :")


def run_step(name: str, cmd: list[str], head: str) -> dict:
    """Run one step, keep its whole output in a log file and return a summary row."""
    t0 = time.time()
    if name == "vivado-build":
        (ROOT / "build" / "vivado").mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
    out = res.stdout + res.stderr
    (OUT / f"{name}.log").write_text(out, encoding="utf-8")
    ok = res.returncode == 0
    keep = [ln.strip() for ln in out.splitlines() if KEEP.search(ln)][-40:]
    row = {"step": name, "ok": ok, "seconds": round(time.time() - t0, 1), "head": head, "lines": keep}
    print(f"{'PASS' if ok else 'FAIL'}  {name:28s} {row['seconds']:8.1f} s", flush=True)
    return row


def main() -> int:
    """Select the steps, run each group in order and the groups in parallel, and write the summary."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--vivado", action="store_true", help="also build and route the full Nexys design")
    ap.add_argument("--only", action="append", default=[], help="run only steps whose name starts with this")
    ap.add_argument("--skip", action="append", default=[], help="skip steps whose name starts with this")
    ap.add_argument("--jobs", type=int, default=6, help="groups to run at the same time")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep", action="store_true", help="merge into the existing summary instead of replacing it")
    args = ap.parse_args()

    steps = STEPS + ([VIVADO] if args.vivado else [])
    steps = [s for s in steps if (not args.only or any(s[0].startswith(o) for o in args.only))
             and not any(s[0].startswith(k) for k in args.skip)]
    if args.list:
        for name, group, cmd in steps:
            print(f"{name:28s} [{group}] {' '.join(cmd[-1:]) if cmd[0] == 'wsl.exe' else ' '.join(cmd)}")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)

    # Which commit am I testing? The summary records it beside every result.
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip())
    print(f"gate for {ROOT} at {head}{' (with uncommitted changes)' if dirty else ''}", flush=True)

    groups: dict[str, list[tuple[str, list[str]]]] = {}
    for name, group, cmd in steps:
        groups.setdefault(group, []).append((name, cmd))

    def run_group(items: list[tuple[str, list[str]]]) -> list[dict]:
        return [run_step(n, c, head) for n, c in items]

    # The Vivado build uses most of the machine, so it runs after the simulations.
    vivado = groups.pop("vivado", [])
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = [r for rs in pool.map(run_group, groups.values()) for r in rs]
    rows += run_group(vivado)
    if args.keep and (OUT / "summary.json").exists():
        # Keep the old rows I didn't re-run, in the order of STEPS.
        old = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
        done = {r["step"] for r in rows}
        kept = [dict(r, head=r.get("head", old["head"])) for r in old["steps"] if r["step"] not in done]
        order = {name: k for k, (name, _, _) in enumerate(STEPS + [VIVADO])}
        rows = sorted(kept + rows, key=lambda r: order.get(r["step"], len(order)))
    summary = {"root": str(ROOT), "head": head, "dirty": dirty, "seconds": round(time.time() - t0, 1),
               "passed": all(r["ok"] for r in rows), "steps": rows}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    bad = [r["step"] for r in rows if not r["ok"]]
    print(f"{'PASS' if not bad else 'FAIL'}: {len(rows) - len(bad)}/{len(rows)} steps"
          + (f"; failed: {', '.join(bad)}" if bad else ""))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
