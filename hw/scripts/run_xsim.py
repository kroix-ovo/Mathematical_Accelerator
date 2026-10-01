"""Run the vector testbench in Vivado xsim.

    python hw/scripts/run_xsim.py [--vivado C:/Xilinx/Vivado/2023.2]

Exits non-zero unless the testbench prints PASS.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw"))
sys.path.insert(0, str(ROOT / "hw" / "tb"))


def find_bin(vivado: str | None, name: str) -> str:
    exe = name + (".bat" if os.name == "nt" else "")
    if vivado:
        return str(Path(vivado) / "bin" / exe)
    found = shutil.which(exe)
    if found:
        return found
    for guess in sorted(Path("C:/Xilinx/Vivado").glob("*/bin"), reverse=True):
        if (guess / exe).exists():
            return str(guess / exe)
    sys.exit(f"cannot find {name}; pass --vivado")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vivado")
    args = ap.parse_args()

    from vectors import write_vector_file

    work = ROOT / "build" / "xsim"
    work.mkdir(parents=True, exist_ok=True)
    n = write_vector_file(work / "vectors.txt")
    print(f"{n} vectors")

    sources = [str(ROOT / line) for line in (ROOT / "hw/rtl/sources.f").read_text().split() if line]
    sources.append(str(ROOT / "hw/tb/sv/tb_accel_core.sv"))

    def run(cmd: list[str]) -> str:
        res = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
        if res.returncode != 0:
            print(res.stdout[-4000:], res.stderr[-4000:])
            sys.exit(f"{Path(cmd[0]).name} failed")
        return res.stdout

    run([find_bin(args.vivado, "xvlog"), "--sv", *sources])
    run([find_bin(args.vivado, "xelab"), "tb_accel_core", "-s", "tb_accel_core", "-debug", "off", "-timescale", "1ns/1ps"])
    out = run([find_bin(args.vivado, "xsim"), "tb_accel_core", "-R"])
    lines = [ln for ln in out.splitlines() if "Error" in ln or "PASS" in ln or "FAIL" in ln or ln.startswith("  [")]
    print("\n".join(lines[-30:]))
    return 0 if any(ln.startswith("PASS") for ln in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
