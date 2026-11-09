# My next task: split divide/sqrt, then compare divider architectures

Created: 2026-11-08.

Status: the initial directories and Git worktrees are set up. I have not started
the split or measured a new implementation. The repository started this task at
`533f5fe1116004fb321a9b7b8cd0f57f98e74013` on `main`.

I created the five experiment branches as scaffolds from the common setup commit,
tagged `baseline/divider-experiments-start`. These still contain the combined
divide/sqrt unit. Before any divider experiments, I will fast-forward each branch
to the verified `baseline/divsqrt-split` commit. I have not created that split tag
yet. [My worktree setup notes](divider-worktrees.md) explain the layout and gate.

I want to find out which divider architecture actually helps my Phase 4 workloads
on the Nexys A7-100T. First I will separate division and square root in the main
repository. Once that works, I will use worktrees inside the main project folder
to compare divider changes against the same baseline. I will leave speeding up
the square-root arithmetic for a later task.

## 1. I establish the split baseline on main

- [ ] I inspect `git status` and preserve unrelated files, including the existing
  untracked `clockInfo.txt`. I keep a record of the old source commit and saved
  Phase 4 board/timing evidence before changing anything.
- [ ] I split `hw/rtl/fp/fp_divsqrt.sv` into independent division and square-root
  modules, provisionally `fp_div.sv` and `fp_sqrt.sv`. Each gets its own state,
  request readiness, in-flight tag and held completion. Shared helper functions
  are fine; a shared blocking arithmetic state machine would defeat this step.
- [ ] I keep the existing recurrence, rounding and special-value behavior in
  both units. This first change is separation, not a new division algorithm or
  a faster square-root algorithm. I record any unavoidable cycle change.
- [ ] I change `hw/rtl/fp/fpu.sv` so one unit can accept work while the other is
  busy, subject to the existing single instruction issue port and safe completion
  capacity. I prove that a sqrt and an independent divide can be in flight at
  the same time. A dependency within one point still has to wait for its operand.
- [ ] I handle simultaneous divide, sqrt, short-pipeline and CORDIC completions.
  I hold results until accepted, preserve their tags and flags, and prevent loss,
  duplication and starvation. I document the arbitration policy and queue limits.
- [ ] I preserve the array FPU kernel's ordering and result/flag write spacing.
  That kernel currently uses a running element counter for output addresses and
  shares one scratchpad write port between results and flags. I cannot assume
  it tolerates arbitrary out-of-order or adjacent completions.
- [ ] I update every source manifest and explicit build/test source list,
  including `hw/rtl/sources.f`, the pyuvm and SV UVM runners, and standalone EVU
  timing builds. I keep source selection consistent across simulators and Vivado.
- [ ] I separate division and sqrt timing assumptions in the models. In
  particular, I update `sw/mathaccel/driver/model.py`,
  `sw/mathaccel/evu_model.py`, `hw/tb/uvm/dpi/evu_ref.c`, and scoreboards or tests
  that assume one shared `DS_PERIOD`. I update timeout bounds where needed.
- [ ] I correct the EVU lower bound for independently operating units. With
  division count D, sqrt count S, and acceptance intervals Idiv and Isqrt, its
  execution lower bound must account for `max(instruction_count, D*Idiv,
  S*Isqrt)`, plus setup/finish, rather than treating both units as one shared
  `(D+S)*DS_PERIOD` bottleneck. Bounds still have to be conservative for the
  actual interfaces, output mode and implementation.
- [ ] I add thorough first-person comments explaining the new units, readiness,
  tags, completion arbitration and timing to someone who knows basic RTL but
  does not know this project. I define abbreviated ports on their declaration
  lines and keep the comments loose and readable.
- [ ] I pass the baseline validation gate below, record its measured results,
  and commit the split baseline on `main`. I record the resulting full SHA and
  create `baseline/divsqrt-split` at that commit before making experiment branches.

I will not start the divider experiments from the old combined-unit commit.
Every candidate must inherit the same split square-root unit and baseline FPU
integration, so I can tell what the divider change actually contributed.

## 2. I create the worktrees inside the project

Before I create them, I add `/worktrees/` to the main checkout's `.gitignore` as
part of the baseline/setup work. I use real Git worktrees, not copied source
folders. Their build products and environments stay inside their own checkout.

```text
Mathematical_Accelerator/
  hw/                         my verified split baseline on main
  sw/
  docs/
  worktrees/
    div-two-step/             two binary recurrence steps per clock
    div-pipeline/             exact recurrence pipeline
    div-srt4/                 radix-4 SRT
    div-dual-lane/            two divider execution lanes
    div-multiplicative/       Newton-Raphson or Goldschmidt
```

The initial setup creates these worktrees from the setup tag. These commands are
the creation record, not commands to rerun over the existing worktrees:

```powershell
git worktree add -b experiment/div-two-step worktrees/div-two-step baseline/divider-experiments-start
git worktree add -b experiment/div-pipeline worktrees/div-pipeline baseline/divider-experiments-start
git worktree add -b experiment/div-srt4 worktrees/div-srt4 baseline/divider-experiments-start
git worktree add -b experiment/div-dual-lane worktrees/div-dual-lane baseline/divider-experiments-start
git worktree add -b experiment/div-multiplicative worktrees/div-multiplicative baseline/divider-experiments-start
git worktree list
```

- [x] I confirm all five scaffold branches start at the same setup SHA.
- [ ] After the split passes its gate and is committed on `main`, I fast-forward
  all five clean scaffold branches to `baseline/divsqrt-split`, following the
  setup notes. I verify they share that full SHA before experimenting.
- [ ] I keep `main` as the reference while I work on the candidates.
- [ ] I run commands from the intended worktree and verify its source paths.
  Each worktree gets its own Python environment/editable installation: reusing
  an editable install that points at `main` could test the wrong host code.
- [ ] I keep candidate logs, generated files, simulation outputs and Vivado
  reports in that worktree's `build/`. I do not share writable build directories.
- [ ] If I discover a baseline correctness bug, I fix and validate it as a common
  change, then apply it consistently and record the comparison baseline again.
  I do not quietly give only one candidate a different surrounding design.

## 3. I run five divider experiments

All of these improve division only. The split sqrt module stays identical to
the baseline. If a candidate needs a different FPU completion wrapper, I explain
and measure that integration cost alongside its arithmetic changes.

### A. Two binary recurrence steps per clock

- [ ] I compute two dependent binary quotient steps in one iteration clock,
  retaining the existing normalization and rounding where possible.
- [ ] I handle the odd final step correctly: the present division loop needs
  27 quotient bits, so the final iteration must not execute an unwanted extra
  step or corrupt the remainder/sticky bit.
- [ ] I compare a parameterized one-step implementation against the baseline
  before judging the two-step version.
- [ ] I measure request acceptance interval and result latency separately.

The earlier estimate of about 20 clocks comes from replacing 27 iteration
clocks with 14 while leaving the surrounding work unchanged. It is an estimate,
not a required result or evidence of passing 100 MHz timing.

### B. Fully pipelined exact recurrence

- [ ] I distribute the exact division recurrence across registered stages.
  Each stage carries its own residual, quotient state, exponent, special-case
  information, validity and tag.
- [ ] I target an initiation interval of one clock: independent divisions can
  enter on consecutive clocks even though each result takes multiple clocks.
- [ ] I compare practical stage groupings, such as one or two binary steps per
  stage, using routed timing and area rather than just stage count.
- [ ] I reserve completion capacity before accepting work and test pipeline
  fill, drain, reset and mixed-operation result collisions.

I do not assume that adding a register to the existing iterative feedback loop
creates this throughput. Independent divisions need their own pipeline state.

### C. Radix-4 SRT

- [ ] I implement division using radix-4 quotient selection and a precisely
  defined residual representation, keeping the separate baseline sqrt unit.
- [ ] I establish the digit-selection bounds for the actual widths and table
  truncations, including corrected literature and any boundary cases.
- [ ] I verify quotient conversion, normalization, exact remainder/sticky
  information and final rounding.
- [ ] I measure the complete operator, including selection and final conversion,
  rather than comparing only the number of recurrence iterations.

I keep this distinct from experiment A: two unrolled binary steps do not by
themselves constitute an SRT radix-4 implementation.

### D. Two divider execution lanes

- [ ] I instantiate two copies of the baseline division unit, with deterministic
  selection of a ready lane. Square root remains one separate baseline unit.
- [ ] I carry tags, result bits and flags independently through both lanes.
- [ ] I handle simultaneous completions and retain fairness with sqrt, CORDIC
  and the short arithmetic units.
- [ ] I compare division-only streams as well as mixed programs, and report
  aggregate acceptance rate separately from one division's latency.

The original researched option included pooled divide/sqrt lanes. For this task
I narrow that to two divider lanes after the mandatory split. This keeps the
experiment about division and avoids changing the sqrt capacity between tracks.
I do not combine this with a faster divider algorithm in the first comparison.

### E. Newton-Raphson or Goldschmidt

- [ ] I select an initial reciprocal approximation and document its error bound.
- [ ] I specify internal fixed-point significand widths, iteration counts and
  truncation/rounding at every intermediate step.
- [ ] I implement exact residual checking and final correction to produce the
  same correctly rounded division result and flags as the baseline.
- [ ] I handle subnormals, exponent extremes, signs, exact results, NaNs and
  exceptions explicitly. I do not treat a faithful or 1-ULP answer as a pass.
- [ ] I account for lookup memory, DSPs, multipliers, correction logic, latency,
  initiation interval and any contention with existing arithmetic.

I can compare Newton-Raphson and Goldschmidt as separate configurations in this
track. If they become independently maintained implementations, I can add a
sixth sibling worktree from the same baseline. Neither experiment changes sqrt.

## 4. I use one validation gate for every implementation

**Numerical behavior**

- [ ] Result bits and all five flags match the existing exact oracle for
  division, including subnormal inputs/results, signed zeros, infinities,
  signaling/quiet NaNs and the project's canonical NaN policy.
- [ ] I target exact/inexact quotients, rounding boundaries, overflow, underflow
  and minimum-subnormal divided by two. I keep tininess-after-rounding behavior.
- [ ] I supplement seeded random cases with directed hard cases, exhaustive
  reduced-precision checks and appropriate residual/digit-selection invariants.
  TestFloat is useful, but does not replace those divider-specific checks.
- [ ] I verify that sqrt and every other existing operation still match their
  original result/flag behavior.

**Integration**

- [ ] I exercise division and sqrt concurrently, bursts of each alone, result
  collisions with short operations/CORDIC, output-buffer pressure, and reset
  with work in flight. Every accepted request completes exactly once.
- [ ] I check EVU map output indices, every point's flags, reduce results and
  flags, `aux`, valid cycle bounds, and the old array kernel's write ordering.
- [ ] I keep the existing single-send EVU RUN timeout behavior and transport
  behavior. New cycle bounds must not silently make driver timeouts unsafe.
- [ ] I run software tests, UDP simulator tests, lint and generated-file checks,
  then the relevant FPU/EVU/top regressions in Verilator, Icarus and SV UVM/xsim.
  I use the existing runners and record exact commands, seeds and vector counts.

**Implementation and evidence**

- [ ] I build and route the full Nexys design, not just the divider in isolation.
  I check setup, hold, clock and constrained I/O timing at the existing 100 MHz
  target, and record LUTs, flip-flops, BRAM and DSPs.
- [ ] I keep software-model predictions, RTL simulation, routed reports and
  physical-board measurements clearly separated.
- [ ] After simulation and routed timing pass, I identify the exact bitstream
  and source commit before running the same board correctness and application
  suite over Ethernet and UART. I preserve the old reports rather than overwrite
  the historical evidence.

## 5. I compare the candidates fairly

I compare each candidate with the new split baseline. I also retain the original
combined-unit results so the benefit and resource cost of the split itself stay
visible. The sqrt RTL, clock target, workloads, input bits, compiler settings and
oracle must match between divider candidates.

For each candidate I record:

| Measurement | What I want to learn |
|---|---|
| Independent division acceptance interval | How often I can start another division |
| Dependent division latency | How long one point waits before using its quotient |
| Division-only throughput | Whether the arithmetic change helps without sqrt overlap |
| Mixed divide/sqrt throughput | Whether separating the units and scheduling work pays off |
| Full kernel clocks per point | Whether the whole EVU application improves |
| First-run and repeat-run wall time | How the link/setup changes the practical result |
| Completion stalls and retry counts | Whether I moved the bottleneck into arbitration |
| Passing routed period and resource use | Whether the improvement fits the real FPGA |
| Exact result/flag/aux mismatches | Whether I preserved the numerical contract |

I run all 22 existing class rules, the long mixed program, seeded random programs
and map-mode checks. I pay particular attention to the division-heavy plate,
loop-field and Fermi examples. I include divide-free cases to catch collateral
slowdowns; I do not expect a faster divider to speed up every program.

I will not rank candidates by clock count alone if one needs a different clock
period. I compare full-run time at a passing routed period and list the resource
tradeoff. A proposed initiation interval is not a measurement, and an isolated
operator result is not full-design or board proof.

## Done means

- [ ] The main repository has a verified, documented split division/sqrt baseline.
- [ ] The experiment worktrees live under `worktrees/` and share the recorded
  split baseline SHA.
- [ ] Each of the five tracks has an implementation and evidence, or a concrete
  documented reason it cannot meet correctness, timing or resource requirements.
- [ ] The comparison records the same application suite and numerical contract
  for every candidate, with remaining limitations stated plainly.
- [ ] I recommend a divider based on full-design results and explain its cost.
  I keep all experiment branches available until I choose what to integrate.

Table-value reuse, compiler hoisting, wider instruction issue, extra FPU cores
and faster sqrt arithmetic are later work. I keep them out of these divider
comparisons so I can attribute the result to the change I am testing.

## Research references I will keep beside the experiments

- [Existing cycle profile](phase4-cpu-comparison.md#evu-cycle-profile) and
  [recorded Phase 4 evidence](evidence/phase4/README.md).
- [CORE-V-WALLY radix-4 stage](https://github.com/openhwfoundation/cvw/blob/47effe667e5428f5b36a4ebe24c716226b5ca7f0/src/fpu/fdivsqrt/fdivsqrtstage4.sv)
  and [configurable iterator](https://github.com/openhwfoundation/cvw/blob/47effe667e5428f5b36a4ebe24c716226b5ca7f0/src/fpu/fdivsqrt/fdivsqrtiter.sv).
- [Digit Serial Methods, with mechanically checked proofs](https://arxiv.org/abs/1708.00140).
- [Correction to SRT digit-selection derivation](https://arxiv.org/abs/1411.6498).
- [Correctly Rounded Floating-Point Division for DSP-Enabled FPGAs](https://www.bogdan-pasca.org/resources/publications/2012-FPL-Division.pdf).
- [AMD-K7 division/sqrt algorithms](https://www.acsel-lab.com/arithmetic/arith14/papers/ARITH14_Oberman.pdf).
- [Final correction of reciprocal, division and sqrt approximations](https://arxiv.org/abs/2404.00387).
- [TestFloat's capabilities and limitations](https://github.com/ucb-bar/berkeley-testfloat-3/blob/master/doc/TestFloat-general.html).

I use my supplied IEEE 754-2019 standard and `docs/numerics.md` for the numerical
rules. I audit each imported source's license and preserve required notices if I
reuse code. Paper and ASIC performance figures remain background until I measure
the corresponding candidate in this project.
