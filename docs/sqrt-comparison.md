# My square-root experiments: results

Written 2026-11-11. This is the result of [sqrt-optimization-task.md](sqrt-optimization-task.md). It comes from RTL simulation, routed Vivado builds on this laptop, and board runs of all four designs ("Board runs" at the end). I have no power numbers.

## What I built

The baseline is `main` after the reciprocal divider (E) was merged, plus the square-root hard cases, tagged `baseline/sqrt-start` at `ee853f4`. Its fp_sqrt finds one root bit per clock and takes one square root at a time, so every root costs 33 clocks and blocks the next one. I tried three replacements, each in its own worktree:

| track | branch | what it does |
|---|---|---|
| S1 | `experiment/sqrt-pipeline` | the baseline's digit-by-digit root, unrolled into 13 registered stages of two root bits each, then a 32-entry result queue |
| S2 | `experiment/sqrt-multiplicative` | a 1,024-entry seed for 1/sqrt(a), s0 = a*y0, e = 1 - s0*y0, a third-order step sqrt(a) = s0(1 + e/2 + 3e²/8), then an exact remainder check that picks R0 - 1, R0 or R0 + 1; pipelined on 9 DSPs, then the same queue |
| S3 | `experiment/divsqrt-shared` | one pipeline that does E's divide and S2's square root, sharing the seed read and four of the six multiplies, with one result queue |

All three return the same 26-bit root and sticky bit as the baseline, so packing, rounding and special cases are shared and the results are bit-identical. Like B and E in the divider study, they can't pause: they keep finished results in a queue, promise every accepted operation a place in it, and fpu.sv refuses new requests only once the oldest queued result has waited 8 clocks (`out_urgent`). With two queues (S1 and S2: one for divide, one for square root) the CORDIC FIFO's worst-case bound grows from 128 to 160 clocks; S3 has one queue, so it stays at 128. The longest CORDIC latency any test saw was 91 clocks on all four designs.

## Numerical results

All three passed the same gate as the baseline (`hw/scripts/run_gate.py`): software tests, the UDP simulator, lint and generated-file checks, the FPU, top-level, Ethernet, accumulator, EVU and CORDIC regressions in Verilator and Icarus, the SV UVM FPU and top tests in xsim, and the EVU cycle profile. Every pyuvm FPU run compares result bits and all five flags with the exact `fp32ref` oracle, with 0 mismatches. Per gate that includes:

- **`FpuSqrtTest`.** 9,263 directed square-root hard cases, then random roots: 39,263 results in Verilator and 11,263 in Icarus. The hard cases are the seed-table boundaries for both exponent parities, exact roots, radicands next to rounding boundaries, every subnormal leading-zero count, the largest normal and the special values.
- **`FpuDivTest`.** 35,953 divides, which matters for S3 since its divide is new arithmetic timing.
- **Every kernel cycle count equal to the model's**, with the new timing constants for each design.

S2's arithmetic also has its own exhaustive test, `sw/tests/test_sqrt_multiplicative.py`. A root's significand depends only on the 23 fraction bits and the exponent parity, so 2 x 2²³ cases cover every input. The test runs the RTL's integer datapath on all of them: the estimate R0 is never more than 1.08 units off, and the corrected root and remainder are right every time. S3 runs the same square-root arithmetic, and S1 is the baseline's recurrence step for step.

Two problems turned up on the way, both in the testbench (E-52 and E-54 in [errors_log.md](errors_log.md)). The second one is worth knowing about: with a 16-entry CORDIC FIFO, the random traffic stopped filling the FIFO once square roots were pipelined, so a coverage check failed while every result was right. The test now ends with rounds that fill it on purpose.

## Timing and size

Full Nexys design, routed at 100 MHz (10 ns). "Interval" is the fewest clocks between two square roots the unit accepts, "latency" the clocks from an accepted root to its result on the FPU output.

| track | SQRT interval | SQRT latency | DIV latency | WNS | WHS | total LUT | total FF | DSP | BRAM tiles | divide+sqrt LUT | divide+sqrt FF | divide+sqrt DSP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 33 | 33 | 24 | +0.210 | +0.018 | 20,107 | 13,705 | 27 | 72.5 | 1,056 | 699 | 7 |
| S1 pipeline | 1 | 20 | 24 | +0.038 | +0.029 | 21,132 | 14,176 | 27 | 72.5 | 2,053 | 1,169 | 7 |
| S2 multiplicative | 1 | 23 | 24 | +0.211 | +0.019 | 20,412 | 13,909 | 36 | 73 | 1,267 | 896 | 16 |
| S3 shared | 1 | 26 | 26 | +0.237 | +0.033 | 20,121 | 13,880 | 32 | 73 | 982 | 861 | 12 |

The square-root unit alone (`build_sqrt.tcl`, out of context, 2 ns of input and output delay; for S3 that's the whole shared unit):

| track | WNS | WHS | LUT | FF | DSP | BRAM tiles |
|---|---|---|---|---|---|---|
| baseline | +1.287 | +0.127 | 430 | 265 | 0 | 0 |
| S1 pipeline | +0.217 | +0.028 | 1,472 | 782 | 0 | 0 |
| S2 multiplicative | +0.863 | +0.058 | 738 | 502 | 9 | 0.5 |
| S3 shared | +0.728 | +0.052 | 1,266 | 945 | 12 | 1 |

S1's full-design worst path (+0.038 ns) runs from the EVU into the misc unit, not through the square root. The bigger S1 moved other logic around. But S1's square root alone also only has 0.217 ns of margin, because every stage is two 29-bit subtract-and-compare steps in a row. It's the one candidate whose own arithmetic is near the edge.

## Cycle profile

Kernel clocks per point from `EvuProfileTest`, the same runs for every design. root_large, two_roots and root_divide are new for this study: 16,384 points of sqrt(1 + x²), sqrt(x) + sqrt(1 + x) and x / sqrt(1 + x²).

| run | points | baseline | S1 | S2 | S3 |
|---|---|---|---|---|---|
| loop_bfield | 64 | 39.4 | 17.6 | 17.3 | 17.1 |
| plate_potential | 1,048,576 | 34.3 | 14.5 | 14.4 | 13.8 |
| long_mixed | 65,536 | 33.7 | 20.8 | 20.8 | 20.6 |
| root_large | 16,384 | 33.0 | 7.1 | 7.3 | 7.0 |
| two_roots | 16,384 | 66.0 | 8.1 | 8.2 | 8.2 |
| root_divide | 16,384 | 34.0 | 8.5 | 8.6 | 8.0 |
| fermi_half | 64 | 14.2 | 14.2 | 14.2 | 13.8 |
| lorentz_large | 16,384 | 7.2 | 7.2 | 7.2 | 7.0 |
| fermi_large | 16,384 | 10.1 | 10.1 | 10.1 | 10.1 |
| two_divides | 16,384 | 10.5 | 10.5 | 10.5 | 10.5 |

Every other profiled run (sin, poly_box, gaussian_2d, charge_sphere, circulation, flux_sphere, dipole_power, decay_wide, rf_oscillatory, cube_large) is the same to 0.1 clock on all four designs, as it should be, since they have no square roots. The one exception is dipole_power at 23.9 instead of 23.8 on S3, from the 2 extra clocks on its divide.

What I take from it:

- **Any pipelined root removes the square root as the limit.** plate_potential goes from 34.3 to about 14 clocks per point, and two independent roots per point (two_roots) from 66 to about 8. From here the EVU's fetch and issue rate is the limit, the same as for divides after E.
- **The three candidates are within half a clock of each other everywhere.** Their latencies (20, 23, 26) hardly matter, because 31 contexts hide them.
- **S3 is slightly ahead, not behind**, even though both its operations take longer. I think the reason is the shared queue: fpu.sv has one urgent rule to obey instead of two, and the model's bounds are tighter with the CORDIC bound at 128. I haven't traced it clock by clock. root_divide, which feeds a root into a divide like plate_potential does, is where it shows most: 8.0 against 8.5.

## Recommendation

**S3, the shared divide and square-root pipeline.**

- It's the fastest or tied on every run that uses a root or a divide.
- It has the most timing margin of the four designs: +0.237 ns full design, +0.728 ns for the unit alone.
- The divide and square root together are smaller than today's in LUTs (982 vs 1,056), with one queue instead of two. The CORDIC worst case stays at 128 clocks.
- What it costs: 5 more DSPs than the baseline (32 of 240) and half a BRAM tile. A divide takes 26 clocks instead of E's 24, which no profiled run notices.

S2 is the fallback: the same arithmetic in a separate unit, the most margin in its own logic (+0.863 ns), at 9 extra DSPs. The cost is a second queue and a CORDIC bound of 160 clocks. I'd keep it if I ever want divides and roots to complete in the same clock, which the FPU can't ask for today since it accepts one request per clock.

I wouldn't take S1. It needs no DSPs, which is its only advantage. But it doubles the divide-and-root LUTs, its own arithmetic has the least margin, and its full build came closest to failing timing (+0.038 ns).

## Board runs

On 2026-11-11 I ran `tools/board_suite.py` on all four designs, one after the other on the same board and laptop: the Ethernet bring-up, then the full `evu_board.py` suite over UDP and over the UART. Each design's run was against its own routed bitstream, recorded by SHA-256. The reports and manifests are in [evidence/sqrt-board/](evidence/sqrt-board/).

Every check passed on every design:
- every result was bit-exact against that design's model;
- every cycle count was inside its bounds;
- there were zero CRC and frame errors.

On the six 16,384-point runs, the board's clock count is exactly the simulated profile's count plus the 2 clocks kernel_engine adds, for all four designs over both links. So the profile tables above are what the board does, not an estimate.

Kernel clocks per point on the board (UDP):

| run | baseline | S1 | S2 | S3 |
|---|---|---|---|---|
| loop_bfield (64) | 39.4 | 17.6 | 17.4 | 17.2 |
| plate_potential (1,048,576) | 34.3 | 14.5 | 14.4 | 13.8 |
| root_large | 33.0 | 7.1 | 7.3 | 7.0 |
| two_roots | 66.0 | 8.1 | 8.2 | 8.2 |
| root_divide | 34.0 | 8.5 | 8.6 | 8.0 |
| lorentz_large | 7.2 | 7.2 | 7.2 | 7.0 |

plate_potential at 1,048,576 points, wall time from the laptop:

| | baseline | S1 | S2 | S3 |
|---|---|---|---|---|
| kernel time (ms) | 359.3 | 151.6 | 150.9 | 145.1 |
| repeat run, laptop to answer (ms) | 359.8 | 152.2 | 151.4 | 145.7 |

The same run in NumPy float64 on the laptop takes about 15 ms when repeated, so the board is still about 10 times slower on this problem. That's down from about 25 times with the original square root. The limit is now the EVU's issue rate, not the square root.

The board doesn't change my recommendation: **S3**.
