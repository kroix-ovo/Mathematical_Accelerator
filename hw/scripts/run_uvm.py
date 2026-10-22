"""Run the SystemVerilog UVM 1.2 environments in Vivado xsim.

    python hw/scripts/run_uvm.py fpu --test fpu_regress_test --n 20000 --seed 7
    python hw/scripts/run_uvm.py top --test top_regress_test
    python hw/scripts/run_uvm.py eth --test eth_regress_test --seed 4
    python hw/scripts/run_uvm.py board

Steps, all inside build/uvm_<env>/:
  1. xsc compiles the DPI-C reference model (hw/tb/uvm/dpi/fp_ref.c) into a
     shared library the simulator can call.
  2. xvlog compiles the RTL, the UVM packages and the test top (-L uvm links
     the UVM 1.2 library that ships with Vivado).
  3. xelab elaborates the design into a simulation snapshot and attaches the
     DPI library (-sv_lib).
  4. xsim runs the snapshot with the chosen test, seed and size knobs.

The run passes only if the log contains the test's PASS marker and the UVM
report summary shows no UVM_ERROR or UVM_FATAL.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_xsim import find_bin  # noqa: E402

FP_RTL = ["hw/rtl/pkg/accel_pkg.sv", "hw/rtl/fp/fp_pkg.sv", "hw/rtl/fp/fp_addsub.sv",
          "hw/rtl/fp/fp_mul.sv", "hw/rtl/fp/fp_misc.sv", "hw/rtl/fp/fp_divsqrt.sv", "hw/rtl/fp/fpu.sv"]

ENVS = {
    "fpu": {
        "rtl": FP_RTL,
        "tb": ["hw/tb/uvm/common/fp_common_pkg.sv", "hw/tb/uvm/fpu/fpu_if.sv",
               "hw/tb/uvm/fpu/fpu_uvm_pkg.sv", "hw/tb/uvm/fpu/tb_fpu_uvm.sv"],
        "inc": ["hw/tb/uvm/fpu"],
        "top": "tb_fpu_uvm",
        "default_test": "fpu_regress_test",
        "marker": "FPU_UVM_PASS",
    },
    "top": {
        "rtl": None,   # filled from hw/rtl/sources.f
        "tb": ["hw/tb/uvm/common/fp_common_pkg.sv", "hw/tb/uvm/top/top_if.sv",
               "hw/tb/uvm/top/top_uvm_pkg.sv", "hw/tb/uvm/top/tb_top_uvm.sv"],
        "inc": ["hw/tb/uvm/top"],
        "top": "tb_top_uvm",
        "default_test": "top_regress_test",
        "marker": "TOP_UVM_PASS",
    },
    "eth": {
        "rtl": None,
        "tb": ["hw/tb/uvm/common/fp_common_pkg.sv", "hw/tb/uvm/top/top_if.sv", "hw/tb/common/rmii_phy_if.sv",
               "hw/tb/uvm/top/top_uvm_pkg.sv", "hw/tb/uvm/eth/eth_uvm_pkg.sv", "hw/tb/uvm/eth/tb_eth_uvm.sv"],
        "inc": ["hw/tb/uvm/top", "hw/tb/uvm/eth"],
        "top": "tb_eth_uvm",
        "default_test": "eth_regress_test",
        "marker": "ETH_UVM_PASS",
    },
    # Not a UVM test: nexys_top with Xilinx's unisim models (MMCM, BUFG, ODDR)
    # and the PHY model, run as a directed test. It borrows the SV reference
    # models from the UVM packages, so it is built the same way.
    "board": {
        "rtl": None,
        "tb": ["hw/tb/uvm/common/fp_common_pkg.sv", "hw/tb/uvm/top/top_if.sv", "hw/tb/common/rmii_phy_if.sv",
               "hw/tb/uvm/top/top_uvm_pkg.sv", "hw/tb/uvm/eth/eth_uvm_pkg.sv", "hw/rtl/top/nexys_top.sv",
               "hw/tb/sv/tb_nexys_eth.sv"],
        "inc": ["hw/tb/uvm/top", "hw/tb/uvm/eth"],
        "top": "tb_nexys_eth",
        "unisim": True,
        "default_test": "board",
        "marker": "BOARD_ETH_PASS",
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("env", choices=ENVS)
    ap.add_argument("--test")
    ap.add_argument("--seed", type=int, default=1, help="xsim -sv_seed for randomize()")
    ap.add_argument("--n", type=int, help="+FPU_N item count knob")
    ap.add_argument("--lcg-seed", help="+LCG_SEED (hex) for the LCG sequences")
    ap.add_argument("--op", type=int, help="+FPU_OP op code for fpu_lcg_test; also gives the run its own work dir")
    ap.add_argument("--vivado")
    ap.add_argument("--cov", action="store_true", help="write a functional coverage database")
    args = ap.parse_args()

    cfg = ENVS[args.env]
    rtl = cfg["rtl"] or (ROOT / "hw/rtl/sources.f").read_text().split()
    # Each --op run gets its own directory so several can run in parallel.
    work = ROOT / "build" / (f"uvm_{args.env}" + (f"_op{args.op}" if args.op is not None else ""))
    work.mkdir(parents=True, exist_ok=True)

    def run(cmd: list[str], log: str) -> str:
        res = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
        out = res.stdout + res.stderr
        (work / log).write_text(out)
        if res.returncode != 0 or re.search(r"^ERROR:", out, re.M):
            print(out[-6000:])
            sys.exit(f"{Path(cmd[0]).name} failed; see {work / log}")
        return out

    # Forward slashes on purpose: xelab copies the source path of each DPI
    # call into a C string literal, and a Windows path such as C:\Users holds
    # the two characters backslash-U, which C rejects as an escape sequence.
    run([find_bin(args.vivado, "xsc"), (ROOT / "hw/tb/uvm/dpi/fp_ref.c").as_posix()], "xsc.log")
    incs = [x for d in cfg["inc"] for x in ("-i", (ROOT / d).as_posix())]
    srcs = [(ROOT / s).as_posix() for s in [*rtl, *cfg["tb"]]]
    run([find_bin(args.vivado, "xvlog"), "-sv", "-L", "uvm", *incs, *srcs], "xvlog.log")
    tops = [cfg["top"]]
    libs = ["-L", "uvm"]
    if cfg.get("unisim"):
        # glbl.v models the device-wide reset that every unisim primitive
        # listens to; it must be elaborated as a second top module.
        xvlog = Path(find_bin(args.vivado, "xvlog"))
        glbl = xvlog.parents[1] / "data" / "verilog" / "src" / "glbl.v"
        run([str(xvlog), glbl.as_posix()], "xvlog_glbl.log")
        tops.append("glbl")
        libs += ["-L", "unisims_ver"]
    run([find_bin(args.vivado, "xelab"), *libs, "-sv_lib", "dpi", "-timescale", "1ns/1ps",
         "-debug", "off", *tops, "-s", cfg["top"]], "xelab.log")

    test = args.test or cfg["default_test"]
    # The Vivado tools are .bat files. cmd.exe splits arguments at "=", so
    # each plusarg is wrapped in quotes to reach xsim as one argument.
    def plusarg(text: str) -> list[str]:
        return ["-testplusarg", f'"{text}"' if sys.platform == "win32" else text]

    plus = plusarg(f"UVM_TESTNAME={test}")
    if args.n is not None:
        plus += plusarg(f"FPU_N={args.n}")
    if args.op is not None:
        plus += plusarg(f"FPU_OP={args.op}")
    if args.lcg_seed:
        plus += plusarg(f"LCG_SEED={args.lcg_seed}")
    cov = ["-cov_db_dir", "coverage", "-cov_db_name", test] if args.cov else []
    out = run([find_bin(args.vivado, "xsim"), cfg["top"], "-R", "-sv_seed", str(args.seed), *plus, *cov],
              f"run_{test}.log")

    # Show the scoreboard/coverage summaries and any errors.
    keep = [ln for ln in out.splitlines()
            if re.search(r"UVM_(ERROR|FATAL) (?!:)|\[SB\]|\[COV\]|\[RESULT\]|checked|^\s+F[A-Z0-9]+ +\d+|"
                         r"^\s+[A-Z_]+ +\d+$|UVM_(ERROR|FATAL) :", ln)]
    print("\n".join(keep[-60:]))
    errs = re.search(r"UVM_ERROR\s*:\s*(\d+)", out)
    fats = re.search(r"UVM_FATAL\s*:\s*(\d+)", out)
    if cfg.get("unisim"):
        # The directed board test has no UVM report; it prints its own marker
        # and any $error lines.
        print("\n".join(ln for ln in out.splitlines() if ln.startswith("  [") or "Error" in ln or "ETH_" in ln))
        ok = cfg["marker"] in out and "Error:" not in out
    else:
        ok = cfg["marker"] in out and errs and int(errs.group(1)) == 0 and fats and int(fats.group(1)) == 0
    print(f"{'PASS' if ok else 'FAIL'}: {args.env}/{test} sv_seed={args.seed}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
