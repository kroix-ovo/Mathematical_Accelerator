"""Run my board correctness and application suite on one divider candidate.

    python tools/board_suite.py --track worktrees/div-pipeline --program --uart COM3
    python tools/board_suite.py --track worktrees/div-multiplicative --program --uart COM3
    python tools/board_suite.py --track . --program --uart COM3          # split baseline
    python tools/board_suite.py --track worktrees/div-pipeline --port udpsim   # dry run, no board
    python tools/board_suite.py --track . --program --uart COM3 --no-eth  # no Ethernet link

The board can't tell me which design it's running, since every candidate reports
the same version word. So this script ties each report to one bitstream: it
records the checkout's commit, whether its sources are dirty, the SHA-256 of the
bitstream and the routed WNS, then (with --program) loads that exact file over
JTAG and runs these, each from the candidate's own checkout and Python, so the
cycle bounds and the model are the candidate's:

  1. eth_bringup.py         Ethernet layer by layer, FPU test vectors, latency
  2. evu_board.py (UDP)     class problems, speed against NumPy, map, roots,
                            long mixed run, large divide runs, random programs
  3. evu_board.py (UART)    the same suite over the serial link (with --uart)
  4. evu_board.py --busy    resend behaviour during long runs (with --busy)

Every report goes in a new folder, build/board/<track>-<time>/ under this
checkout, next to manifest.json, so a rerun never overwrites an older result.
The exit status is non-zero if any step fails.
"""

# I keep the candidates' board runs identical: same steps, same seeds, same
# vector counts. Only the checkout changes. I refuse to program a bitstream
# that's older than its RTL or comes from a dirty checkout, unless I ask for
# it with --allow-dirty, because then the report wouldn't say what ran.

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> str:
    """One git command in the candidate's checkout, as text."""
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def find_vivado(explicit: str | None) -> str:
    """Vivado from --vivado, PATH or the usual Windows install folder."""
    # This is the same search hw/scripts/run_xsim.py uses.
    exe = "vivado" + (".bat" if os.name == "nt" else "")
    if explicit:
        return str(Path(explicit) / "bin" / exe)
    found = shutil.which(exe)
    if found:
        return found
    for guess in sorted(Path("C:/Xilinx/Vivado").glob("*/bin"), reverse=True):
        if (guess / exe).exists():
            return str(guess / exe)
    sys.exit("cannot find vivado; pass --vivado")


def python_for(root: Path) -> str:
    """The candidate's own virtual environment, or this Python if it has none."""
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        if (root / rel).exists():
            return str(root / rel)
    return sys.executable


def wns(root: Path) -> tuple[float, float] | None:
    """Routed WNS and WHS from the full build's timing report."""
    rpt = root / "build/vivado/timing.rpt"
    if not rpt.exists():
        return None
    text = rpt.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"WNS\(ns\).*?\n.*?\n\s+(-?[\d.]+)\s+\S+\s+\d+\s+\d+\s+(-?[\d.]+)", text)
    return (float(m.group(1)), float(m.group(2))) if m else None


def manifest(root: Path) -> dict:
    """Everything that identifies what the board is about to run."""
    bit = root / "build/vivado/nexys_top.bit"
    if not bit.exists():
        sys.exit(f"no bitstream at {bit}; build it first (run_gate.py --vivado)")
    # The bitstream must be newer than the last commit that touched the RTL,
    # the constraints or the build script, or it doesn't match the commit I record.
    paths = ["hw/rtl", "hw/constraints", "hw/scripts/build.tcl", "hw/scripts/sources.f"]
    rtl_time = int(git(root, "log", "-1", "--format=%ct", "--", *paths) or 0)
    dirty = git(root, "status", "--porcelain", "--", *paths)
    return {
        "track": root.name if root != ROOT else "split-baseline",
        "root": str(root),
        "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "head": git(root, "rev-parse", "HEAD"),
        "rtl_commit": git(root, "log", "-1", "--format=%H", "--", *paths),
        "rtl_dirty": bool(dirty),
        "bitstream": str(bit),
        "bitstream_sha256": hashlib.sha256(bit.read_bytes()).hexdigest(),
        "bitstream_time": dt.datetime.fromtimestamp(bit.stat().st_mtime).isoformat(timespec="seconds"),
        "bitstream_newer_than_rtl": bit.stat().st_mtime > rtl_time,
        "routed_wns_whs": wns(root),
        "started": dt.datetime.now().isoformat(timespec="seconds"),
    }


def step(name: str, cmd: list[str], cwd: Path, log: Path, results: list) -> bool:
    """Run one step, echo its output and save it to log. Returns True if it passed."""
    print(f"\n=== {name}\n$ {' '.join(cmd)}", flush=True)
    with log.open("w", encoding="utf-8", newline="\n") as out:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        for line in proc.stdout:
            print(line, end="", flush=True)
            out.write(line)
        code = proc.wait()
    results.append({"step": name, "cmd": cmd, "exit": code, "log": log.name})
    print(f"=== {name}: {'passed' if code == 0 else f'FAILED (exit {code})'}", flush=True)
    return code == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", required=True, help="the candidate's checkout, e.g. worktrees/div-pipeline")
    ap.add_argument("--program", action="store_true", help="load this checkout's bitstream over JTAG first")
    ap.add_argument("--port", default="udp:192.168.10.2", help="evu_board port ('udpsim' for a dry run)")
    ap.add_argument("--uart", help="serial port for the UART suite and the bring-up comparison, e.g. COM3")
    ap.add_argument("--busy", action="store_true", help="also run the resend-during-a-run experiment")
    ap.add_argument("--fptest", type=int, default=5000, help="FPU vectors per op in eth_bringup")
    ap.add_argument("--vivado", help="Vivado install folder, if it isn't on PATH")
    ap.add_argument("--no-eth", action="store_true", help="UART only: skip the bring-up and the UDP suite")
    ap.add_argument("--allow-dirty", action="store_true", help="run even if the bitstream may not match the commit")
    args = ap.parse_args()

    root = (ROOT / args.track).resolve()
    info = manifest(root)
    print(json.dumps(info, indent=1))
    if (info["rtl_dirty"] or not info["bitstream_newer_than_rtl"]) and not args.allow_dirty:
        sys.exit("the bitstream may not match the recorded commit (dirty RTL or older than it); "
                 "rebuild, or pass --allow-dirty")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "build/board" / f"{info['track']}-{stamp}"
    out.mkdir(parents=True)
    py = python_for(root)
    sim = args.port == "udpsim"
    results: list = []
    ok = True

    if args.program and not sim:
        # program.tcl loads build/vivado/nexys_top.bit relative to its own folder,
        # so running the candidate's copy loads the candidate's bitstream.
        ok &= step("program", [find_vivado(args.vivado), "-mode", "batch", "-nojournal", "-nolog",
                               "-source", str(root / "hw/scripts/program.tcl")], root, out / "program.log", results)
        if not ok:
            print("programming failed; nothing else ran")
    if ok and args.no_eth:
        # Without the Ethernet link I can still run the whole suite over the UART:
        # results and the board's clock counts don't depend on the link.
        if not args.uart:
            sys.exit("--no-eth needs --uart")
        ok &= step("evu-uart", [py, "tools/evu_board.py", "--port", args.uart, "--out", str(out / "evu_uart.md")],
                   root, out / "evu_uart.log", results)
    elif ok:
        bring = [py, "hw/scripts/eth_bringup.py", "--fptest", str(args.fptest)]
        bring += ["--port", "udpsim"] if sim else []
        bring += ["--uart", args.uart] if args.uart and not sim else []
        ok &= step("eth-bringup", bring, root, out / "eth_bringup.log", results)
    if ok and not args.no_eth:
        # I keep going after a failed suite: the other link's report still says something.
        ok &= step("evu-udp", [py, "tools/evu_board.py", "--port", args.port, "--out", str(out / "evu_udp.md")],
                   root, out / "evu_udp.log", results)
        if args.uart and not sim:
            ok &= step("evu-uart", [py, "tools/evu_board.py", "--port", args.uart, "--out", str(out / "evu_uart.md")],
                       root, out / "evu_uart.log", results)
        if args.busy and not sim:
            busy = [py, "tools/evu_board.py", "--port", args.port, "--busy", "--out", str(out / "evu_busy.md")]
            busy += ["--uart", args.uart] if args.uart else []
            ok &= step("evu-busy", busy, root, out / "evu_busy.log", results)

    info.update(finished=dt.datetime.now().isoformat(timespec="seconds"), port=args.port, uart=args.uart,
                steps=results, passed=ok)
    (out / "manifest.json").write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    print(f"\n{'PASSED' if ok else 'FAILED'}: reports in {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
