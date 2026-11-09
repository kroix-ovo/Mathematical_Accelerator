# My divider experiments: results

Written 2026-11-09. This is the result of [divider-optimization-task.md](divider-optimization-task.md). Everything below comes from RTL simulation and routed Vivado builds on this laptop. I haven't programmed the board with any of these designs, so there are no board timings, no wall-clock run times and no power numbers yet.

## What I built

First I split `fp_divsqrt` into independent `fp_div` and `fp_sqrt` units on `main`. The arithmetic and the 33-clock timing of each op are unchanged; the difference is that a square root can now run while a divide is in progress. That's tagged `baseline/divsqrt-split` at `02c785e`, and all five experiment branches start from it. Then I tried five dividers, each in its own worktree, with the square-root unit left exactly as in the baseline:

| track | branch | what fp_div does |
|---|---|---|
| A | `experiment/div-two-step` | two quotient bits per clock: compares 2r with d, 2d and 3d at once (the radix-4 digit of two restoring steps) |
| B | `experiment/div-pipeline` | 14 registered stages of A's step, one divide can enter every clock, then a 32-entry result queue |
| C | `experiment/div-srt4` | radix-4 SRT: digits -2..2 picked from 8 residual bits and 3 divisor bits, two's complement residual, on-the-fly quotient conversion |
| D | `experiment/div-dual-lane` | two copies of the baseline divider; the older result goes out first so results stay in order |
| E | `experiment/div-multiplicative` | 512-entry reciprocal seed, one third-order Newton step y1 = y0(1 + e + e²), the quotient multiply and an exact remainder correction, pipelined on 7 DSPs |

Every one returns the same 27-bit quotient and sticky bit as the baseline loop, so the packing, rounding and special cases are shared and the results are bit-identical. B and E can't pause, so they keep finished divides in a queue and promise each accepted divide a place in it. Stopping the FPU whenever a divide result waits, which the baseline does, would stop it almost all the time with a result every clock. So in B and E, fpu.sv only refuses requests once the oldest queued divide has waited 8 clocks. That also raises the CORDIC FIFO's worst-case latency bound from 96 to 128 clocks, though the longest I saw in the tests was 93.

## Numerical results

All five passed the same gate as the baseline (`hw/scripts/run_gate.py`): software tests, the UDP simulator, lint and generated-file checks, the FPU, top-level, Ethernet, accumulator, EVU and CORDIC regressions in Verilator and Icarus, the SV UVM FPU and top tests in xsim, and the EVU cycle profile. Every pyuvm FPU run compares result bits and all five flags with the exact `fp32ref` oracle, and every run had 0 mismatches. That's 107,664 FPU results per gate, including 43,906 divides from `FpuDivTest`: 5,953 directed hard cases (divisors on the SRT and reciprocal-table interval edges, extreme significands, exact quotients, near-ties, subnormals, overflow edges), run in both Verilator and Icarus, plus 32,000 random divides. A and B also ran their one-bit configuration (`DIV_STEPS=1`) through the divide and mixed regressions, another 60,776 results with 0 mismatches.

Each nonstandard algorithm also has its own model test in `sw/tests`:
- **A and B.** The two-bit step equals two one-bit steps, checked exhaustively for every pair of 10-bit significands with an odd quotient length.
- **C.** The SRT thresholds keep |w| ≤ (2/3)d for every divisor interval, derived again with exact fractions. A bit-level model matches floor division for every pair of 10-bit significands.
- **E.** For every one of the 2²³ divisor significands, the reciprocal is within 6.1 units of 2⁵⁵/D. The correction step needs it within 32, so it always finds the exact quotient.

## Timing and size

Full Nexys design, routed at 100 MHz (10 ns). "Interval" is the fewest clocks between two divides the unit accepts, and "latency" the clocks from an accepted divide to its result on the FPU output. Both are checked exactly against the RTL by the top-level FPU kernel cycle counts.

| track | DIV interval | DIV latency | FPU kernel clocks per divide | WNS | WHS | total LUT | total FF | DSP | BRAM tiles | fp_div LUT | fp_div FF | fp_div DSP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| combined unit (before the split) | 33 | 33 | 33 | +0.287 | +0.015 | 19,545 | 13,406 | 20 | 72 | 650 (with sqrt) | 385 (with sqrt) | 0 |
| split baseline | 33 | 33 | 33 | +0.052 | +0.024 | 19,857 | 13,590 | 20 | 72 | 512 | 302 | 0 |
| A two-step | 20 | 20 | 20 | +0.104 | +0.024 | 19,932 | 13,603 | 20 | 72 | 612 | 329 | 0 |
| B pipeline | 1 | 21 | 4 (fetch-limited) | +0.191 | +0.018 | 21,520 | 14,576 | 20 | 72 | 2,047 | 1,306 | 0 |
| C SRT-4 | 20 | 20 | 20 | **-0.101** | +0.014 | 19,952 | 13,621 | 20 | 72 | 637 | 341 | 0 |
| D dual lane | 33 per lane, 2 lanes | 33 | 16.5 | +0.056 | +0.017 | 20,326 | 13,902 | 20 | 72 | 974 | 605 | 0 |
| E reciprocal | 1 | 24 | 4 (fetch-limited) | +0.210 | +0.018 | 20,107 | 13,705 | 27 | 72.5 | 598 | 440 | 7 |

The split's square-root unit is 407 LUTs and 259 FFs in every build.

**C misses 100 MHz in the full design, but not because of its divider.** Its worst path is the Ethernet transmitter's checksum (`u_enc` → `u_eth/u_tx/psum`). That path was already close in every build, and C's slightly larger divider moved the placement enough to push it 0.101 ns over. By my rule that's a fail: a candidate has to meet timing in the full design. The worst path in the split, A and E builds is the kernel's operand path into `fp_misc`. In B it's the same Ethernet checksum path, and in D it's `cmd_proc`'s length counter, the worst path of the original Phase 4 build. None of the worst paths in any build runs through `fp_div`. With 0.05 to 0.3 ns of slack everywhere, routed WNS differences between candidates are mostly placement luck; they don't rank the dividers.

To see each divider's own margin, I placed and routed `fp_div` alone (`hw/scripts/build_div.tcl`, out of context, 2 ns budget on each input and output):

| track | fp_div alone: WNS | WHS | LUT | FF | DSP | BRAM tiles |
|---|---|---|---|---|---|---|
| split baseline | +0.974 | +0.136 | 519 | 308 | 0 | 0 |
| A two-step | +0.827 | +0.170 | 623 | 335 | 0 | 0 |
| B pipeline | +1.090 | +0.071 | 2,204 | 1,386 | 0 | 0 |
| B with 1 bit per stage (27 stages) | +0.450 | +0.058 | 1,939 | 1,670 | 0 | 0 |
| C SRT-4 | +1.356 | +0.136 | 648 | 347 | 0 | 0 |
| D dual lane | +0.621 | +0.135 | 1,009 | 617 | 0 | 0 |
| E reciprocal | +0.680 | +0.028 | 803 | 524 | 7 | 0.5 |

On its own the SRT divider has the most margin of all, so C's full-design failure really is about the Ethernet path. For B I also built the 27-stage, one-bit-per-stage grouping in the full design: WNS +0.307, divider 1,808 LUTs and 1,577 FFs. Against the 14-stage version (2,047 and 1,306), it trades 239 LUTs for 271 more flip-flops, and its latency is 34 clocks instead of 21. Its latency is also longer than the 32-entry queue, so a pure divide stream could wait for queue places, and the regression did reach all 32. The 14-stage grouping is the better one.

## What it does to my programs

EVU cycle profile, clocks per point, for every run in `EvuProfileTest`. I added three large divide-heavy runs to the profile for this comparison: the class list's divide-only rules have 32 to 64 points, where setting up the run takes a big share of the clocks.

| run | points | divides, roots per point | combined unit | split | A | B | C | D | E |
|---|---|---|---|---|---|---|---|---|---|
| dipole_power | 32 | 1, 0 | 45.8 | 45.8 | 32.7 | 22.9 | 32.7 | 29.6 | 23.8 |
| fermi_half | 64 | 1, 0 | 37.9 | 37.9 | 25.3 | 14.4 | 25.3 | 22.3 | 14.2 |
| fermi_large | 16,384 | 1, 0 | - | 33.0 | 20.9 | 10.1 | 20.9 | 18.0 | 10.1 |
| lorentz_large, 1/(1+x²) | 16,384 | 1, 0 | - | 33.0 | 20.2 | 7.2 | 20.2 | 17.3 | 7.2 |
| two_divides | 16,384 | 2, 0 | - | 66.0 | 40.3 | 10.6 | 40.3 | 34.2 | 10.5 |
| loop_bfield | 64 | 1, 1 | 70.5 | 42.2 | 39.0 | 39.2 | 39.0 | 38.9 | 39.4 |
| plate_potential | 1,048,576 | 1, 1 | 66.6 | 34.0 | 33.6 | 34.0 | 33.6 | 34.0 | 34.3 |
| long_mixed | 65,536 | 1, 1 | 66.9 | 35.4 | 34.1 | 33.6 | 34.1 | 34.0 | 33.7 |

The other 16 runs, everything without a divide, took exactly the same number of clocks in all six designs, so none of the dividers slowed anything else down. Every run was inside the cycle bounds and gave the model's sum and flags exactly. The full table is in `build/` of each checkout, and `python tools/divider_report.py` prints it again.

- **The split did most of the work on my worst programs.** plate_potential went from 66.6 to 34.0 clocks per point, loop_bfield from 70.5 to 42.2 and long_mixed from 66.9 to 35.4, because the square root and the divide of different points now run at the same time.
- **After the split, those programs wait on square root, and no divider changes that.** All five candidates leave plate_potential between 33.6 and 34.3 clocks per point: the floor is one square root every 33 clocks. In the profile, the divide/sqrt share of plate_potential's clocks stays at 61% to 65% for every candidate, and it's the square-root unit refusing. Faster square root, the task I left for later, is what would move these.
- **On divide-only programs, B and E reach the instruction-issue limit.** fermi_large has 9 instructions per point and runs at 10.1 clocks per point in both. Divider stalls fall from 70% of its clocks to 1%, and 89% of clocks issue an instruction. A and C give 20.9 there and D gives 18.0. With two divides per point, A and C (40.3) and D (34.2) are limited by the divider again, while B and E stay at about 10.5.
- **A and C are identical in cycles.** Both take 20 clocks per divide, so the profile can't tell them apart. The difference between them is only the hardware.
- **D's two lanes give half the interval but the same latency.** That's worth 33 → 17-18 clocks per point on one divide per point. It's no use when a single point needs its quotient soon, and it's the largest of the iterative designs.

## My recommendation

**I'd take E, with B as the fallback.** Both accept a divide every clock and give the same speed on everything I ran: divide-only programs become issue-limited at about 10 clocks per point instead of 33. E's divider is 598 LUTs and 440 FFs plus 7 of my 220 unused DSPs and half a block RAM tile; B's is 2,047 LUTs and 1,306 FFs. In the full design E costs +250 LUTs and +115 FFs over the split baseline, and B costs +1,663 LUTs and +986 FFs. Both met timing in the full build (E +0.210, B +0.191). E's latency is 3 clocks longer (24 against 21), which the 31 contexts hide: on every run of 1,024 points or more, their profiles match to within 0.3 clocks per point.

What E costs that B doesn't:
- Its correctness rests on an error bound, not on the algorithm being exact step by step. I checked that bound for all 2²³ divisors, so it isn't a guess, but anyone changing its widths or table has to re-run `test_div_multiplicative.py`.
- It uses 7 DSPs. That doesn't matter now, but it would if I pipelined square root the same way later.

Either pipelined divider changes the FPU integration: the urgent rule, and a CORDIC worst-case bound of 128 clocks instead of 96. If I didn't want that change, A is the cheapest option that keeps the baseline integration: +100 LUTs for 33 → 20 clocks per divide.

Before I merge anything:
- Run the board correctness and application suite over Ethernet and the UART on E's bitstream (the build is in `worktrees/div-multiplicative/build/vivado/`). I haven't done that, and only that measures first-run and repeat-run wall time.
- Look at the Ethernet checksum path. It failed C's build and is the worst path in B's, so whichever divider I merge, it will cause trouble sooner or later.

The square-root unit is next. plate_potential and loop_bfield can't go below 33 clocks per point until it gets faster.

## How I ran it

All the evidence came from `hw/scripts/run_gate.py` in each worktree. Each summary row records the commit it ran on. For every track, the commits between the RTL gate run and the branch head change only tests and scripts, not RTL:

| track | branch head | RTL steps ran on | tests/scripts re-run on |
|---|---|---|---|
| split baseline | `main` | `56508b6` | `98be2be` (profile, software), `48964a6` (lint) |
| A | `db8a765` | `8260098` | `166b826` (software) |
| B | `4c68322` | `9b766ef` | `c04f351` (FPU steps, after fixing the overlap probe for a pipelined divider) |
| C | `bcd496c` | `66dd1d2` | |
| D | `78df998` | `a9f447c` | `21ce118` (software) |
| E | `6d6e09c` | `083ccbc` | |

Two shared fixes reached every branch as identical cherry-picks: the cycle-bound tests no longer assume a 33-clock divider (`324e24f`), and the gate can re-run some steps and keep the rest (`98be2be`). The full Vivado builds ran from the experiment commits before those script changes, with identical RTL. The problems I hit along the way are E-44 to E-49 in the [errors log](errors_log.md).
