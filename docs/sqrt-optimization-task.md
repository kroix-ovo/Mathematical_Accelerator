# My next task: compare square-root architectures

Created: 2026-11-11.

Status (2026-11-11): the three candidates are implemented, and each passes the
square-root tests in simulation. Their full gates and Vivado builds are next.
Results and my recommendation will go in [sqrt-comparison.md](sqrt-comparison.md).

After the divider task, plate_potential and loop_bfield are limited by the square
root: every divider candidate left plate at about 34 clocks per point, because
fp_sqrt still finds one root bit per clock and takes one square root at a time
(33 clocks each). This task does for square root what the divider task did for
division: same baseline for every candidate, same gate, same builds, same board
suite.

The baseline is `main` after the reciprocal divider (E) was merged, plus the
square-root hard cases, tagged `baseline/sqrt-start` at `ee853f4`. All three
experiment branches start from that commit.

| Directory under the main folder | Branch | What I change |
|---|---|---|
| `worktrees/sqrt-pipeline` | `experiment/sqrt-pipeline` | S1: the baseline's exact digit-by-digit root, pipelined two bits per stage |
| `worktrees/sqrt-multiplicative` | `experiment/sqrt-multiplicative` | S2: reciprocal-root seed, third-order correction, exact remainder fix |
| `worktrees/divsqrt-shared` | `experiment/divsqrt-shared` | S3: one pipeline shared by divide (E's arithmetic) and square root (S2's) |

## 1. Common work on main, before any candidate

- [x] I add directed square-root hard cases to the FPU tests (`sqrt_hard_cases`,
  about 9,300 operands): the seed-table boundaries for both exponent parities,
  exact roots, radicands next to rounding boundaries, every subnormal
  leading-zero count, the largest normal and the special values. `FpuSqrtTest`
  sends them and then random square roots; the gate runs it under Verilator and
  Icarus.
- [x] I make the overlap and result-queue probes find out from the DUT whether
  each divide or square-root unit is pipelined, so one testbench serves every
  candidate.
- [x] I add `build_sqrt.tcl`, the square root's out-of-context timing build.
- [x] I tag the start commit and make the three worktrees, each with its own
  `.venv` and editable install.

## 2. The three candidates

### S1. Pipelined digit recurrence

- [x] Every step of the baseline's root recurrence gets its own registers; two
  steps per stage (`STEPS = 2`), so 13 stages. The arithmetic is the baseline's,
  step for step, so it's exact by construction.
- [x] A promised-place result queue (DQ = 32) and the `out_urgent` rule, the
  same as fp_div's. fpu.sv refuses requests on either unit's `out_urgent`.
- [x] The CORDIC worst case grows from 128 to 160 clocks, because both queues
  can be full of promised results ahead of it.

### S2. Multiplicative square root

- [x] A 1,024-entry seed for 1/sqrt(a), indexed by the exponent parity and the
  top 9 fraction bits, then s0 = a*y0, e = 1 - s0*y0 exactly, and
  sqrt(a) = s0 (1 + e/2 + 3e^2/8).
- [x] An exact remainder check picks R0 - 1, R0 or R0 + 1, which gives the
  baseline's root and sticky bit exactly.
- [x] Because a square root's significand depends only on the 23 fraction bits
  and the exponent parity, `test_sqrt_multiplicative.py` runs the RTL's integer
  datapath over all 2 x 2^23 cases: R0 is never more than 1.08 units off, and
  every corrected root and remainder is right.
- [x] Same queue and urgent rule as S1; CORDIC worst case 160 clocks.

### S3. One shared divide and square-root pipeline

- [x] E's divide arithmetic and S2's square-root arithmetic, re-timed so the seed
  read and four of the six multiplies are shared, with one result queue.
- [x] The FPU accepts one request per clock, so a unit that accepts one
  operation per clock can't limit throughput; what sharing buys is fewer DSPs,
  one queue instead of two, and the CORDIC worst case staying at 128 clocks.
- [x] The cost: a divide takes 2 clocks longer than E and a square root 3 longer
  than S2. The testbench probes read the shared unit's own busy flags.

## 3. One gate for every candidate

- [ ] The full gate (`run_gate.py`): software tests, the udpsim device tests,
  lint and generated-file checks, every pyuvm environment in Verilator and
  Icarus, including `FpuSqrtTest` and `FpuDivTest`, and both xsim UVM tests.
  Every result and flag bit-exact against the oracle; every kernel cycle count
  equal to the model's.
- [ ] The full Nexys build at 100 MHz, with setup, hold and resource numbers.
- [ ] The square-root unit placed and routed alone (`build_sqrt.tcl`).
- [ ] The EVU cycle profile, with the square-root-heavy runs.
- [ ] The board suite over Ethernet and the UART for the candidates I take to
  the board, against the same baseline bitstream.

## 4. How I compare them

The same measurements as the divider comparison: acceptance interval and
latency, kernel clocks per point on every profiled run (plate_potential,
loop_bfield and long_mixed above all), routed WNS and WHS, LUTs, FFs, DSPs and
BRAM for the whole design and for the divide and square-root units, and the
board's own clock counts and repeat-run wall time. Divide-free and
square-root-free programs should not change at all; if one does, I want to know
why.

## Done means

- [ ] Each candidate has passed the gate and the full build, or has a concrete,
  documented reason it can't.
- [ ] The comparison uses the same suite and numerical contract for all three,
  with the limits stated plainly.
- [ ] I recommend one, based on full-design and board results, and say what it
  costs.

## References

- [Correctly Rounded Floating-Point Division for DSP-Enabled FPGAs](https://www.bogdan-pasca.org/resources/publications/2012-FPL-Division.pdf),
  whose final-correction idea S2 uses for square root.
- [Final correction of reciprocal, division and sqrt approximations](https://arxiv.org/abs/2404.00387).
- [AMD-K7 division/sqrt algorithms](https://www.acsel-lab.com/arithmetic/arith14/papers/ARITH14_Oberman.pdf),
  for sharing one multiplier between divide and square root.
- The divider study: [divider-comparison.md](divider-comparison.md).
