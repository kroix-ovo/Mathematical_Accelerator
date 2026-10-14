"""Build and run the pyuvm environments with cocotb (Linux / WSL).

    python hw/tb/pyuvm/run.py fpu --test FpuRegressTest --n 3000
    python hw/tb/pyuvm/run.py top --test TopRegressTest
    python hw/tb/pyuvm/run.py fpu --sim icarus

From Windows, hw/scripts/run_pyuvm.py runs this inside WSL for you.

The simulator compiles the RTL; cocotb then loads the test module, which
imports pyuvm and the mathaccel package (put on PYTHONPATH below), so the
Python reference models are used directly.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cocotb_tools.check_results import get_results
from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

FP_RTL = ["hw/rtl/pkg/accel_pkg.sv", "hw/rtl/fp/fp_pkg.sv", "hw/rtl/fp/fp_addsub.sv",
          "hw/rtl/fp/fp_mul.sv", "hw/rtl/fp/fp_misc.sv", "hw/rtl/fp/fp_divsqrt.sv", "hw/rtl/fp/fpu.sv"]

ENVS = {
    "fpu": {"top": "fpu", "module": "test_fpu_uvm", "dir": HERE / "fpu", "rtl": FP_RTL, "params": {}},
    "top": {"top": "accel_core", "module": "test_top_uvm", "dir": HERE / "top", "rtl": None,
            "params": {"CLKS_PER_BIT": 8}},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("env", choices=ENVS)
    ap.add_argument("--sim", default="verilator", choices=["verilator", "icarus"])
    ap.add_argument("--test", help="pyuvm test class name (default: all tests in the module)")
    ap.add_argument("--n", type=int, help="FPU_N item count")
    ap.add_argument("--lcg-seed", help="LCG_SEED (hex)")
    ap.add_argument("--op", type=int, help="FPU_OP for FpuLcgTest")
    args = ap.parse_args()

    cfg = ENVS[args.env]
    rtl = cfg["rtl"] or (ROOT / "hw/rtl/sources.f").read_text().split()
    build = ROOT / "build" / f"pyuvm_{args.env}_{args.sim}"

    # cocotb's runner hands the simulator's embedded Python the current
    # sys.path, so adding the package and test folders here makes
    # `import mathaccel` and the test module importable inside the simulation.
    sys.path[:0] = [str(ROOT / "sw"), str(cfg["dir"])]
    # Knobs reach the test module as environment variables.
    env: dict[str, str] = {}
    if args.n is not None:
        env["FPU_N"] = str(args.n)
    if args.lcg_seed:
        env["LCG_SEED"] = args.lcg_seed
    if args.op is not None:
        env["FPU_OP"] = str(args.op)
    os.environ.update(env)

    runner = get_runner(args.sim)
    build_args = ["-Wno-fatal", "-Wno-WIDTH", "-Wno-UNUSED"] if args.sim == "verilator" else ["-g2012"]
    runner.build(sources=[ROOT / s for s in rtl], hdl_toplevel=cfg["top"], parameters=cfg["params"],
                 build_args=build_args, build_dir=build, always=True, timescale=("1ns", "1ps"))
    results = runner.test(hdl_toplevel=cfg["top"], test_module=cfg["module"], test_dir=build,
                          build_dir=build, testcase=args.test, extra_env=env)
    tests, failed = get_results(results)
    print(f"{'PASS' if tests and not failed else 'FAIL'}: pyuvm {args.env} ({tests} tests, {failed} failed)")
    return 0 if tests and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
