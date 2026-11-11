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

S2's arithmetic also has its own exhaustive test, `sw/tests/test_sqrt_multiplicative.py`. A root's significand depends only on the 23 fraction bits and the exponent parity, so 2 x 2²³ cases cover every normalized finite-positive significand/parity class. I run an integer model of the RTL datapath on all of them; classification, subnormal normalization, rounding, flags and pipeline behavior still need the separate RTL regressions: the estimate R0 is never more than 1.08 units off, and the corrected root and remainder are right every time. S3 runs the same square-root arithmetic, and S1 is the baseline's recurrence step for step.

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
- **The three candidates are close in these workloads.** The largest gap in this profile is about 0.7 clock per point on plate_potential. Their different latencies (20, 23, 26) mostly disappear behind my 31 contexts; I don't assume that stays true for a short dependent chain or fewer contexts.
- **S3 is slightly ahead on the mixed root/divide runs**, even though its operations take longer. I think the shared queue changes the arbitration and urgent stalls: fpu.sv has one queue to drain instead of two. I haven't traced that clock by clock. The 128-clock CORDIC bound is a worst-case guarantee, not itself a reason that a workload runs faster. root_divide, which feeds a root into a divide like plate_potential does, shows 8.0 against 8.5 clocks per point.

## Recommendation

**S3, the shared divide and square-root pipeline.**

- I get the fastest or essentially tied square-root workloads, with root_divide at 8.0 clocks per point. Divide-only work is mostly unchanged; dipole_power is the small exception below.
- I measured the best full-design setup slack in this set: +0.237 ns. Its isolated shared unit has +0.728 ns, while S2's isolated root has more margin at +0.863 ns. Those aren't the same measurement boundary, and a different placement can move the full-design critical path.
- The divide and square root together are smaller than today's in LUTs (982 vs 1,056), with one queue instead of two. The CORDIC worst case stays at 128 clocks.
- What it costs: 5 more DSPs than the baseline (32 of 240) and half a BRAM tile. A divide takes 26 clocks instead of E's 24. I do see about 0.1 extra clock per point on dipole_power (23.8 to 23.9); it's a small measured cost, not literally free.

I keep S2 as the independent-unit fallback. It has the same root arithmetic and +0.863 ns isolated root slack, but 36 total DSPs versus S3's 32, a second queue, and a 160-clock CORDIC bound. It isn't my fallback for scarce DSPs. Its reason to stay around is a future architecture that could issue a divide and a root together, or needs their resource pools to stay independent. My current FPU accepts one request and returns one result per clock; dual issue would need changes outside either arithmetic unit too.

I wouldn't take S1 here. Its root needs no additional DSPs, which is its main advantage. But it doubles the divide-and-root LUTs, its own arithmetic has the least margin, and its full build came closest to failing timing (+0.038 ns).

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

## How I'm bringing S3 into the main source

I keep the tested `experiment/divsqrt-shared` checkout intact and integrate its
arithmetic into `sqrt-shared`, on top of main's current test and documentation
commits. I rewrote the explanatory comments without changing the RTL tokens
or the executable Python test logic. My new reading notes are in
[sqrt-shared-reading-guide.md](sqrt-shared-reading-guide.md).

I run `python hw/scripts/run_gate.py --vivado` in the integration checkout's
own environment before publishing the PR. I keep that result separately in
[evidence/sqrt-integration/](evidence/sqrt-integration/), so an old candidate
pass isn't presented as a new integration pass. The experiment's measured
route and board results above remain tied to their original manifests. A
fresh integrated bitstream needs its own board run before I call it board-tested.

I haven't measured power, and these board runs cover one board on one day.
The shared-queue explanation for the small speed advantage is still a hypothesis.
The approximately 14 clocks per point suggest issue rate as the next bottleneck,
but I need stall counts by category before choosing multi-issue or instruction
fusion. MDIO link recovery, an explicit bitstream design ID, and power measurements
are still open work, not features included in this integration.
