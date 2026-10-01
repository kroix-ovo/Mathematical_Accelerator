"""Build and run the cocotb tests (no Makefiles needed).

I compile the same ordered RTL source list used by xsim, then load the Python
test module into the selected simulator. This runner connects the HDL compiler
to cocotb; the actual stimulus and assertions are in test_accel_core.py.

    python hw/tb/cocotb/run.py --sim verilator
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cocotb_tools.check_results import get_results
from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
# Absolute paths allow the simulator to run in its own generated build folder.


def main() -> int:
    """Select the simulator, build accel_core, and run its cocotb assertions."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default="verilator", choices=["verilator", "icarus"])
    args = ap.parse_args()

    sources = [ROOT / p for p in (ROOT / "hw/rtl/sources.f").read_text().split()]
    # Package declarations occur before the modules that import them, so the
    # source-file ordering is part of the shared build description.
    runner = get_runner(args.sim)
    build_args = ["-Wno-fatal", "-Wno-WIDTH"] if args.sim == "verilator" else ["-g2012"]
    # Existing Verilator warnings are not treated as compile failures; actual
    # compiler errors and failed test assertions still fail the simulation.
    runner.build(
        sources=sources,
        hdl_toplevel="accel_core",
        parameters={"CLKS_PER_BIT": 8},  # Must match CPB in the UART test.
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
    # Outside pytest, runner.test can return an XML path even when assertions
    # failed. Inspect the report ourselves before declaring a successful run.
    # A missing or malformed report raises, which also makes the command fail.
    tests, failed = get_results(results)
    print(f"tests: {tests}, failures/errors: {failed}")
    return 0 if tests > 0 and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
