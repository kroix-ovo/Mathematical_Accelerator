"""Build and run the cocotb tests (no Makefiles needed).

    python hw/tb/cocotb/run.py --sim verilator
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default="verilator", choices=["verilator", "icarus"])
    args = ap.parse_args()

    sources = [ROOT / p for p in (ROOT / "hw/rtl/sources.f").read_text().split()]
    runner = get_runner(args.sim)
    build_args = ["-Wno-fatal", "-Wno-WIDTH"] if args.sim == "verilator" else ["-g2012"]
    runner.build(
        sources=sources,
        hdl_toplevel="accel_core",
        parameters={"CLKS_PER_BIT": 8},
        build_args=build_args,
        build_dir=ROOT / "build" / f"cocotb_{args.sim}",
        always=True,
    )
    results = runner.test(
        hdl_toplevel="accel_core",
        test_module="test_accel_core",
        test_dir=HERE,
        build_dir=ROOT / "build" / f"cocotb_{args.sim}",
    )
    print(f"results: {results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
