# My Phase 4 measurement inputs

I kept the existing measurement outputs here so the comparison can be rebuilt
from a fresh checkout. I copied these records from `build/` during publication;
I did not rerun the physical board or the CPU benchmark for this documentation pass.

- `evu_gate.md`: the model accuracy gate before RTL.
- `evu_m5.md`: the host-tool gate.
- `evu_m6.md` and `evu_m6_uart.md`: the recorded board runs over each link.
- `evu_m6_busy*.md`: the recorded busy/timeout behavior checks.
- `cpu_bench.json`: the CPU timing, accuracy, frequency and energy windows.
- `evu_profile.json`: the simulated cycle profile, not board counter readings.
- `routed-timing.rpt` and `routed-utilization.rpt`: the saved 2026-11-05 full-design
  Vivado reports. I only stripped trailing whitespace while copying them; I did
  not reroute the design for the comment pass.

I run `python tools/evu_report.py` to rebuild the CPU comparison. The script
prefers new measurements in `build/` and falls back to these published files.
I still need the FPGA's external-meter energy measurement and the host energy
while driving the board; I haven't filled either one in with an estimate.
