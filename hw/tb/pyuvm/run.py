"""Build and run the pyuvm environments with cocotb (Linux / WSL).

    python hw/tb/pyuvm/run.py fpu --test FpuRegressTest --n 3000
    python hw/tb/pyuvm/run.py top --test TopRegressTest
    python hw/tb/pyuvm/run.py eth --test EthRegressTest --seed 3
    python hw/tb/pyuvm/run.py fpu --sim icarus
    python hw/tb/pyuvm/run.py cordic --test CordicRegressTest --n 5000
    python hw/tb/pyuvm/run.py accum --test AccumRegressTest --n 1000
    python hw/tb/pyuvm/run.py evu --test EvuRegressTest --n 200
    python hw/tb/pyuvm/run.py evu --test EvuGateTest --param CONTEXTS=8

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
          "hw/rtl/fp/fp_mul.sv", "hw/rtl/fp/fp_misc.sv", "hw/rtl/fp/fp_divsqrt.sv",
          "hw/rtl/fp/cordic_pkg.sv", "hw/rtl/fp/fp_cordic.sv", "hw/rtl/fp/fpu.sv"]
CORDIC_RTL = ["hw/rtl/pkg/accel_pkg.sv", "hw/rtl/fp/fp_pkg.sv", "hw/rtl/fp/cordic_pkg.sv", "hw/rtl/fp/fp_cordic.sv"]

ACCUM_RTL = ["hw/rtl/pkg/accel_pkg.sv", "hw/rtl/fp/fp_pkg.sv", "hw/rtl/fp/fp_accum.sv"]

EVU_RTL = FP_RTL + ["hw/rtl/fp/fp_accum.sv", "hw/rtl/evu/evu_core.sv", "hw/rtl/core/scratch_mem.sv",
                    "hw/tb/pyuvm/evu/evu_dut.sv"]

# I register the accumulator and EVU as block environments here. The evu16
# variant shrinks the CORDIC FIFO to exercise backpressure with the same RTL.
ENVS = {
    "fpu": {"top": "fpu", "module": "test_fpu_uvm", "dir": HERE / "fpu", "rtl": FP_RTL, "params": {}},
    # The same tests with a 16-entry CORDIC result FIFO, small enough for the
    # mixed sequences to fill it, so the FIFO's stall rules get exercised.
    "fpu16": {"top": "fpu", "module": "test_fpu_uvm", "dir": HERE / "fpu", "rtl": FP_RTL, "params": {"FD": 16}},
    # The phase 3 CORDIC unit on its own (inside fpu.sv it is also covered by the fpu env).
    "cordic": {"top": "fp_cordic", "module": "test_cordic_uvm", "dir": HERE / "cordic", "rtl": CORDIC_RTL,
               "params": {}},
    # The phase 4 exact accumulator on its own.
    "accum": {"top": "fp_accum", "module": "test_accum_uvm", "dir": HERE / "accum", "rtl": ACCUM_RTL, "params": {}},
    # The phase 4 expression unit around the real FPU, accumulator and scratchpad.
    "evu": {"top": "evu_dut", "module": "test_evu_uvm", "dir": HERE / "evu", "rtl": EVU_RTL, "params": {}},
    # The same with a 16-entry CORDIC FIFO, so its stall rules refuse requests too.
    "evu16": {"top": "evu_dut", "module": "test_evu_uvm", "dir": HERE / "evu", "rtl": EVU_RTL, "params": {"FD": 16}},
    "top": {"top": "accel_core", "module": "test_top_uvm", "dir": HERE / "top", "rtl": None,
            "params": {"CLKS_PER_BIT": 8}},
    "eth": {"top": "accel_core", "module": "test_eth_uvm", "dir": HERE / "eth", "rtl": None,
            "params": {"CLKS_PER_BIT": 8}},
}


def main() -> int:
    """Build one selected DUT configuration, run its pyuvm test and check the simulator results file."""
    ap = argparse.ArgumentParser()
    ap.add_argument("env", choices=ENVS)
    ap.add_argument("--sim", default="verilator", choices=["verilator", "icarus"])
    ap.add_argument("--test", help="pyuvm test class name (default: all tests in the module)")
    ap.add_argument("--n", type=int, help="FPU_N item count")
    ap.add_argument("--lcg-seed", help="LCG_SEED (hex)")
    ap.add_argument("--op", type=int, help="FPU_OP for FpuLcgTest")
    ap.add_argument("--seed", type=int, help="ETH_SEED for the Ethernet PHY randomization")
    ap.add_argument("--param", action="append", default=[], help="override a DUT parameter, NAME=VALUE")
    args = ap.parse_args()

    cfg = dict(ENVS[args.env])
    cfg["params"] = {**cfg["params"], **{k: int(v) for k, v in (p.split("=") for p in args.param)}}
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
    if args.seed is not None:
        env["ETH_SEED"] = str(args.seed)
    # The tests can see which parameters the DUT was built with.
    env.update({f"PARAM_{k}": str(v) for k, v in cfg["params"].items()})
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
