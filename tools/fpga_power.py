"""Measure the FPGA board's power with an external meter.

    python tools/fpga_power.py --meter "USB meter model" --rail "USB input" --port udp:192.168.10.2

The Nexys A7 has no power sensor the design can read, so a meter has to sit on the
board's supply, and this script gives it two steady states to read. It holds the
board idle, then keeps it running a heavy program back to back, and asks for the
meter's reading after each one. The net power is the second minus the first.

A run can't be cancelled, so the busy phase is many short runs, and the board is
idle again as soon as the script stops.
"""

# I need an external meter for the board, so I make steady idle and loaded
# periods and record the readings I type in. I also measure the kernel's
# duty fraction: the board is briefly idle between requests, and I account
# for that before reporting incremental power while the kernel is active.

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from mathaccel import evu_model  # noqa: E402
from mathaccel.compiler.evu_isa import memory_image  # noqa: E402
from mathaccel.compiler.problems import PROBLEMS  # noqa: E402
from mathaccel.compiler.quadrature import integral_run  # noqa: E402
from mathaccel.driver.device import connect  # noqa: E402


def read_watts(prompt: str) -> float:
    """I read the external meter value; I can type watts or a volts-times-amps expression."""
    text = input(prompt)
    return float(eval(text, {"__builtins__": {}}))      # a number typed by the person at the keyboard


def main() -> int:
    # I load one heavy rule, record idle power, then repeat short runs while the meter settles.
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meter", required=True, help="what's measuring, for the report")
    ap.add_argument("--rail", required=True, help="where it's connected, for the report")
    ap.add_argument("--port", default="udp:192.168.10.2")
    ap.add_argument("--problem", default="gaussian_2d", help="which class problem to keep running")
    ap.add_argument("--seconds", type=float, default=30.0, help="how long to hold the busy state")
    ap.add_argument("--out", default=str(ROOT / "build" / "fpga_power.json"))
    args = ap.parse_args()

    p = next(q for q in PROBLEMS if q.name == args.problem)
    run = integral_run(p.expr, p.axes(max(p.rules, key=lambda r: sum(a * b for a, b in r))))
    with connect(args.port) as dev:
        dev.ping()
        image = memory_image(run)
        dev.write_words(image.base, image.words)
        idle_w = read_watts("Board idle. Let the meter settle, then type its watts: ")
        bounds = evu_model.cycle_bounds(run)
        print(f"Running for {args.seconds:.0f} s. Note the steady reading.")
        end = time.perf_counter() + args.seconds
        busy_clocks = runs = 0
        t0 = time.perf_counter()
        while time.perf_counter() < end:
            busy_clocks += dev.evu_start(image, bounds).cycles
            runs += 1
        elapsed = time.perf_counter() - t0
        busy_w = read_watts("Done. Type the steady watts you saw: ")
    duty = busy_clocks / 100e6 / elapsed
    print(f"{runs} runs, kernel busy {duty * 100:.1f}% of the time")
    report = {"meter": args.meter, "rail": args.rail, "problem": args.problem, "idle_w": idle_w, "busy_w": busy_w,
              "net_w": (busy_w - idle_w) / duty, "duty": duty, "runs": runs}
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"net {report['net_w']:.3f} W with the kernel running, saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
