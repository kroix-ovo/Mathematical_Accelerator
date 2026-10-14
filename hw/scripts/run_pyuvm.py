"""Run the pyuvm environments from Windows by calling into WSL.

    python hw/scripts/run_pyuvm.py fpu --test FpuRegressTest --n 3000

Everything after the script name is passed to hw/tb/pyuvm/run.py. WSL needs:
  ~/tools/oss-cad-suite   (YosysHQ OSS CAD Suite: Verilator, Icarus)
  ~/venvs/mathaccel       (python venv with cocotb, pyuvm, numpy)
Override those locations with the OSS_CAD and PYUVM_VENV environment variables.
On Linux, run hw/tb/pyuvm/run.py directly instead.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import PureWindowsPath

ROOT = PureWindowsPath(__file__).parents[2]


def wsl_path(p: PureWindowsPath) -> str:
    """C:\\Users\\x -> /mnt/c/Users/x"""
    drive = p.drive.rstrip(":").lower()
    return "/mnt/" + drive + "/" + "/".join(p.parts[1:])


def main() -> int:
    oss = os.environ.get("OSS_CAD", "~/tools/oss-cad-suite")
    venv = os.environ.get("PYUVM_VENV", "~/venvs/mathaccel")
    args = " ".join(shlex.quote(a) for a in sys.argv[1:])
    cmd = (f"source {oss}/environment && source {venv}/bin/activate && "
           f"cd {shlex.quote(wsl_path(ROOT))} && python hw/tb/pyuvm/run.py {args}")
    return subprocess.call(["wsl.exe", "-e", "bash", "-c", cmd])


if __name__ == "__main__":
    sys.exit(main())
