# Phase 4 plan: expression unit and integrals

This started as a draft. All seven milestones are done and their measured results are below. The FPGA board's own energy is the one measurement still open, because it needs an external power meter. Any other clock count, LUT count or speed is an estimate until a milestone measures it.

Phase 4 adds an expression evaluation unit (EVU). Python and SymPy compile an expression into a small bytecode program, and the FPGA runs that program over thousands of points without anything crossing the link. Results are either added up into one number or written back to the scratchpad. That gives me the Calc 3 work from my classes: double and triple integrals, line integrals, flux integrals, and sampling of gradient, divergence and curl.

The FPU kernel can't do this. It applies one op across two arrays, so `x^2*sin(x)` would take three kernel runs and the arrays would cross the link in between. The EVU is the first part of this design that runs an instruction stream.

This follows phase 4 of my original September plan ("EVU + compiler + quadrature"), with some changes that are listed under [Changes from the September plan](#changes-from-the-september-plan).

## Decisions

These are proposals. The first four shape the design, so I want to settle them before milestone 2.

| Topic | Proposal |
|---|---|
| Summing results | Exact accumulator: no rounding until the final result, so the order points finish in doesn't matter (milestone 1 measures whether this is worth it) |
| Contexts | 31 points in flight at once, 64 registers each (measured in milestone 3) |
| Program size | 256 instructions and 64 shared constants |
| Points | Tensor grids of up to three dimensions, from node tables in the scratchpad. A list of arbitrary points is a one-dimensional grid |
| Arithmetic | The existing FPU, unchanged apart from one internal move op: fp32, RNE, the same flags |
| Output | Reduce (one sum) or map (one result per point), chosen per run |
| Accuracy | Each operation keeps its existing rule. The integral's error is measured against mpmath, not promised |
| Symbolic work | Stays on the laptop. The FPGA only evaluates what I explicitly set up |

## How a run works

```
SymPy expression + limits
   │  host: simplify, common subexpressions, register allocation, Gauss-Legendre nodes
   ▼
descriptor, program, constants and node tables, written to the scratchpad
   │  RUN EVU (descriptor address)
   ▼
copy program and constants into the EVU
   ▼
point generator ─► free context ─► ready queue ─► fetch instruction ─► read registers ─► fpu
                                       ▲                                                  │
                                       └──────── write result, context ready again ◄──────┘
                                                                │
                              last instruction of the program:  ├─ reduce: exact accumulator
                                                                └─ map: r[i], f[i] in the scratchpad
```

## Datapath

- **Contexts.** A context is one point partway through the program: a program counter, 64 registers and three grid indices. Each context has at most one operation in flight, so the FPU tag only has to name the context, and `TW = 8` stays as it is. The destination register waits in a small per-context table until the result comes back.
- **Ready queue.** A context goes into a FIFO when it is ready for its next instruction, and the dispatcher takes one per clock. That replaces a 32-way arbiter with a queue, and it means a result arriving is what schedules the next step. A slow CORDIC op holds up its own context and nobody else's. A finished context goes through the same queue and picks up the next grid point at the front, so there's no separate free list.
- **Retry queue.** If the FPU refuses an instruction for any reason, the instruction is dropped and its context goes to a second queue to try again. So the pipeline has no stall signal at all. It's a second queue because a result and a refusal can happen on the same clock, and a LUT RAM FIFO has one write port. The two queues take turns when both have work.
- **Why 31 contexts.** Milestone 3 measured this, see its gate result below. One fast instruction takes 16 clocks from one dispatch to the next: 7 stages to reach the FPU, 7 in the FPU, 2 to write back. So it takes about 16 contexts just to fill the FPU with fast ops, and 31 leave room for CORDIC.
- **Register file.** Two reads and one write per clock, built as two copies of one block RAM that take the same writes. The RAM holds 32 banks of 64 words. Contexts use banks 0 to 30 and the shared constants take bank 31, so a source operand is either a register or a constant and no separate path is needed. That is why there are 31 contexts: a 33rd bank would have added two more 36 Kb block RAMs for 64 words.
- **One write path.** Every register write comes back through the FPU's result port, including coordinates and weights from the node tables. A `LOAD` reads the scratchpad and sends the value through `fp_misc` as a new internal move op, so the register file never has to choose between two writers. It costs 7 clocks per load.
- **Divide and square root.** `fp_divsqrt` takes one operation at a time. If a context's next instruction is DIV or SQRT and the unit is busy, the FPU refuses it and that context goes to the retry queue while the others carry on. A program with one divide per point can't go faster than 33 clocks per point.
- **Mixed traffic.** This is the first time CORDIC and 6-clock ops share the result port on the board, so the FIFO's slot reservation and age rule will finally run on hardware.

### Instruction set

Each instruction is one 32-bit word: op, destination register, two sources, and a last-instruction bit.

| Instruction | Meaning |
|---|---|
| any FPU op, 0–17 | `R[d] = op(a, b)`, where a and b are registers or constants |
| `LOAD d, table` | `R[d] = table[index of the table's dimension]` |

The rest comes from the compiler:

| Expression | Compiled as |
|---|---|
| `-x` | `x * -1`, which gets signed zero right |
| `x^n`, small integer n | repeated multiplies, sharing squares |
| `x^y` | `exp(y * ln x)`. This compounds two rounded results, so it isn't covered by the 1 ULP budget. I'd measure it and document it |
| `tan x` | `sin x / cos x`, and `sinh`, `cosh`, `tanh` from `exp` |
| repeated subexpressions | computed once, using SymPy's `cse` |

The September plan also had a `SELECT` instruction for piecewise functions. I've left it out for now. It takes three source operands, which would mean a third register read port. Compares return 0 or 1, so `c*a + (1-c)*b` works for finite values, but NaN and infinity leak through. I'd add it only if a class problem needs it.

### Exact accumulator

Adding a million fp32 values one at a time in fp32 loses digits: once the running total is large, small terms get rounded away. It also makes the answer depend on the order the points finish in, and with CORDIC ops and the ready queue that order isn't fixed.

So I'd add into one long fixed-point register that covers the whole fp32 range: about 277 bits from 2^-149 to 2^128, plus about 32 bits of headroom for 2^32 terms. That's about 310 bits. A carry can't cross that in one clock at 100 MHz, so the register is split into segments, each holding its carry for the next clock, and one term can be accepted per clock. At the end the carries settle and the total is rounded once to fp32.

- **Verification gets simpler.** The sum is exact, so `Fraction` arithmetic in Python predicts it bit for bit, in any order.
- **Special values.** Infinities and NaN can't go in a fixed-point register, so they set state bits instead. The rules match repeated ADD: +∞ and −∞ together give NaN with NV.
- **Fallback.** If milestone 2 shows this is too large or too slow, I'd sum in fp32 in a fixed order, with the order kept by a reorder buffer, and document the accuracy.

### Protocol

A new kernel, `EVU = 0x03`, takes one argument: the scratchpad address of a descriptor. `RUN`'s argument space is small, and a descriptor can also be reused between runs.

| Descriptor field | Contents |
|---|---|
| mode | reduce or map |
| program | address and length |
| constants | address and count |
| grid | n0, n1, n2, each at most 65,535 |
| tables | up to 6: base address and which dimension indexes it |
| outputs | result address `r`, flags address `f` |

The descriptor is 16 words, with the layout in the `[evu]` section of `spec/accel.toml`. The unit reads it itself, along with the program and constants, so `evu_core` only needs that one address to start.

The reply has `cycles` and `aux` as usual, with `aux` the OR of every point's flags. In reduce mode the sum and its flags go to `r` and `f`, so reading them is one more request. Bad programs must not hang the board: `cmd_proc` checks the descriptor's own address, `evu_core` checks every field, address span and instruction word, and a program that never sets the last-instruction bit stops at the end of its length. This is protocol v0.4, and [protocol.md](protocol.md#evu-kernel) has the exact rules and their order.

## Verification

Same style as phases 2 and 3: a bit-accurate model, an independent accuracy reference, and scoreboards on every result.

- **Interpreter.** `sw/mathaccel/evu_model.py` runs the bytecode using `fpu_evaluate` for each op and `Fraction` for the sum. It predicts every result and flag word exactly. The RTL must match it bit for bit.
- **Compiler checks.** The compiled program against SymPy `lambdify` on random points, to catch compiler bugs. This compares double-precision evaluation with fp32, so it uses a tolerance.
- **Integral accuracy.** Against mpmath quadrature, on a fixed list of class problems, including some with a known closed form.
- **Block environment.** A pyuvm EVU environment around the real `fpu`, with random programs from a generator as well as compiled expressions. Coverage: every op, every source kind, program length, grid shape, map and reduce, divider busy, the FIFO stall rules, NaN and infinity reaching the accumulator.
- **Top level.** A C port of the interpreter for the SV scoreboard, `evu_ref.c`, using `fp_ref.c` and `cordic_ref.c`, checked against the Python one by `test_evu_ref.py`. Both top environments have EVU runs.
- **Cycle counts.** The other kernels' models predict `cycles` exactly and replies are compared byte for byte. The EVU's count depends on the ready queue and the FPU's arbitration, and predicting it would take a clock-accurate model of both, which would be easy to break and hard to trust. So for a successful EVU run the scoreboards decode the reply, compare everything else exactly, and check `cycles` against a low and a high bound from `cycle_bounds` in `evu_model.py`. Refused runs have no cycle count, so their replies still compare byte for byte.

## Milestones

1. **Bytecode, compiler and model (no RTL).** ISA in `spec/accel.toml`, the SymPy compiler, the Python interpreter, Gauss-Legendre nodes, a list of class problems. **Measurement gate:** compare exact summing with plain fp32 summing on those problems. This shows whether the accumulator is worth building, and how many digits an fp32 integrand supports at all.
2. **Exact accumulator.** Standalone RTL, pyuvm block test, out-of-context timing. **Gate:** one term per clock at 100 MHz, or fall back as described above.
3. **EVU core.** Contexts, ready queue, register file, point generator, around the real `fpu`, tested in pyuvm. **Gate:** I measure ops per clock on real programs and check timing out of context. The 32-context choice gets confirmed or changed here.
4. **Integration.** Kernel 0x03, descriptor checks, one FPU shared between both kernels, protocol v0.4, C port, SV and pyuvm top environments, full build at 100 MHz (currently WNS +0.154 ns, so watch it).
5. **Host tools.** `integrate_nd`, `line_integral`, `surface_flux`, `grad`, `div`, `curl_at`, and `root_find` (Newton or bisection run by the host, with evaluations on the FPGA), plus a CLI command.
6. **Board.** The class problem list on hardware, compared with mpmath for accuracy and with NumPy on this laptop for speed.
7. **Docs.** ISA document, measured accuracy and speed, `verification.md`, README.

## Milestone 1 gate result

Measured on 2026-11-03 with `python tools/evu_gate.py`, on the bit-accurate model. No RTL exists yet. Errors are in ULPs of the true answer against a 40-digit reference, so 0.5 is the best fp32 can do. "Double rule" is the same rule in double precision with unrounded nodes, which shows the rule's own truncation error.

| problem | points | exact sum | plain fp32 sum | fp32, shuffled | double rule |
|---|---|---|---|---|---|
| charge_sphere | 4,096 | 0.24 | 24.24 | 12.76 | 3e-9 |
| gaussian_2d | 1,024 | 0.52 | 7.52 | 3.52 | 3e-8 |
| gaussian_2d | 1,048,576 | 0.48 | 16,381 | 17,802 | 2e-9 |
| plate_potential | 1,024 | 25,621 | 25,622 | 25,616 | 26,000 |
| plate_potential | 65,536 | 0.30 | 2.30 | 11.70 | 4e-9 |
| decay_wide | 65,536 | 0.00 | 591 | 414 | 7e-11 |
| cube_large | 2,097,152 | 0.00 | 1,582 | 7,761 | 2e-9 |

Over all 22 runs of the 13 problems:

- **Exact summing is worth building.** Apart from the coarse plate rule, its error was at most 1.38 ULP on every run, however many points. Plain fp32 summing grew with the point count, reaching about 17,800 ULP at a million points, which is about 0.13% of the answer.
- **Plain fp32 isn't stable.** Changing the order changed the answer on nearly every large run, and the hardware finishes points out of order. Keeping fp32 would need the reorder buffer as well.
- **An fp32 integrand is good enough.** With exact summing, the rounding in the points and the nodes mostly cancels out, leaving results within about 1.4 ULP, or 6 to 7 significant digits.
- **Rule choice still matters.** The plate problem's 1,024-point rule misses by about 0.18% in every column, including double precision. That's truncation near the singularity, and no accumulator fixes it. 65,536 points fix it. The tools in milestone 5 need to show when a rule hasn't converged.

Decision: build the exact accumulator in milestone 2, subject to its timing gate.

All 13 problems compiled to 23 instructions or fewer and at most 5 registers, so the limits of 256 instructions and 64 registers are generous for this list.

## Milestone 2 gate result

Measured on 2026-11-03 on `hw/rtl/fp/fp_accum.sv`, standalone and out of context with `hw/scripts/build_accum.tcl`, on the xc7a100t at 100 MHz with 2 ns of input and output budget.

| | |
|---|---|
| Rate | one term per clock |
| Setup slack | WNS +0.799 ns |
| Hold slack | WHS +0.146 ns |
| Size | 1,264 LUTs, 799 flip-flops, no block RAM or DSP |
| Finish to result | 29 clocks, always |

The worst path is the second half of rounding, `round_post` into the output register, which the other FPU units share. The segment adders aren't near the limit, because each one only carries across 33 bits. So the gate passes and the fp32 fallback isn't needed. This is the unit alone: full-design timing comes in milestone 4.

The final design differs from the description above in one way that made it smaller. Terms are made two's complement before they go in, and the segments above a negative term add its sign extension, so a carry is always 0 or 1 and no segment needs a subtract.

The pyuvm environment checks every sum bit for bit against both `exact_sum` and separate `Fraction` arithmetic, and checks the 29-clock latency. Results: `AccumRegressTest` on Verilator with 2,061 sums and 113,774 terms, 0 errors, 19/19 coverage bins; a second seed with 461 sums, 0 errors; Icarus with 361 sums, 0 errors and no X on the outputs. The headroom for very large term counts is by design, not tested: a simulation can't reach anywhere near 2^42 terms.

## Milestone 3 gate result

Measured on 2026-11-04 on `hw/rtl/evu/evu_core.sv` with the real `fpu`, `fp_accum` and `scratch_mem` around it, in the wrapper `hw/tb/pyuvm/evu/evu_dut.sv`.

### Speed

"Ops per clock" counts accepted FPU requests, including LOADs, from start to done. That includes copying the program and finishing the sum, which is why the shortest runs look slow. The FPU's limit is 1.0.

| program | instructions | points | ops per clock | clocks per point |
|---|---|---|---|---|
| poly_box | 11 | 64 | 0.785 | 14.0 |
| gaussian_2d | 11 | 1,024 | 0.891 | 12.3 |
| charge_sphere | 15 | 4,096 | 0.927 | 16.2 |
| flux_sphere | 23 | 1,024 | 0.944 | 24.4 |
| rf_oscillatory | 6 | 512 | 0.863 | 7.0 |
| cube_large | 12 | 512 | 0.964 | 12.4 |
| plate_potential (2 divides) | 12 | 1,024 | 0.180 | 66.8 |
| dipole_power (1 divide, 3 CORDIC) | 10 | 32 | 0.226 | 44.3 |
| 20 fast ops | 20 | 256 | 0.934 | 21.4 |
| 20 ops, 3 CORDIC | 20 | 256 | 0.863 | 23.2 |
| 20 ops, 1 divide | 20 | 256 | 0.573 | 34.9 |
| 20 CORDIC ops | 20 | 256 | 0.473 | 42.3 |

With the context count changed and nothing else, the same runs gave:

| contexts | gaussian_2d | charge_sphere | flux_sphere | 20 ops, 3 CORDIC | 20 CORDIC ops |
|---|---|---|---|---|---|
| 31 | 0.891 | 0.927 | 0.944 | 0.863 | 0.473 |
| 16 | 0.815 | 0.749 | 0.685 | 0.705 | 0.267 |
| 8 | 0.414 | 0.376 | 0.345 | 0.361 | 0.134 |

- **31 contexts stays.** Going from 16 to 31 gained 9% to 38% on class problems. Each context only costs block RAM that is already there, so I see no reason to go lower.
- **Divide-free class problems of 512 points or more ran at 0.86 to 0.96 ops per clock.** The FPU is the limit there, as planned.
- **The divider is the real limit on some problems.** Programs with divides ran at about 33 clocks per divide (66.8 clocks per point with 2 divides), as predicted. Four of the 13 class problems have a divide or a square root.
- **A program's speed estimate was off.** I guessed 11 clocks per instruction and it's 16, because a LOAD needs two more pipeline stages and every instruction takes the same path. With 31 contexts this doesn't cost throughput on these programs.
- **Age rule.** It refused requests on most mixed programs, up to 4,614 times in a 66,265-clock run, and the results were still right. The stall path that was simulation-only in phase 3 will now run on the board.

### Timing and size

Built out of context with `hw/scripts/build_evu.tcl`, on the xc7a100t at 100 MHz with 2 ns of input and output budget, including the whole 64 Ki word scratchpad.

| | |
|---|---|
| Setup slack | WNS +0.244 ns |
| Hold slack | WHS +0.042 ns |
| `evu_core` without the accumulator | 2,585 LUTs, 817 flip-flops, 5.5 block RAM tiles |
| `fp_accum` | 1,266 LUTs, 799 flip-flops |

The ten worst setup paths are all inside the FPU, with the adder at the top, so no path of the new unit is the limit. The tight hold path, +0.042 ns, is in `evu_core`. This is a unit test of timing, not a result for the whole design: full-design timing comes in milestone 4, where the design is about 4,000 LUTs larger and already at +0.154 ns.

### Verification

`EvuRegressTest` is directed runs, all 13 class problems at their smallest rule, and random programs. For each run the scoreboard rebuilds the run from the words seen going into the scratchpad, then checks every word the unit wrote, `aux`, and every FPU request against `evu_model.py`. The monitor also checks that no context ever has two instructions in flight.

| run | runs | points | FPU operations | errors | coverage |
|---|---|---|---|---|---|
| Verilator, seed 1 | 169 | 27,700 | 386,864 | 0 | 42/42 |
| Verilator, seed 7 | 169 | 27,708 | 383,958 | 0 | 42/42 |
| Verilator, 16-entry CORDIC FIFO | 109 | 21,531 | 230,741 | 0 | 43/43 |
| Icarus, no X on any checked signal | 64 | 12,663 | 160,621 | 0 | 42/42 |

To check that these tests can fail, I put 12 bugs into the RTL one at a time, such as a lost retry, an instruction run twice, flags leaking between points, a wrong grid dimension and no limit on map output. All 12 were caught.

Not covered yet: the unit isn't connected to `cmd_proc` or `kernel_engine`, so nothing has run on the board. A descriptor with a zero grid size or length isn't rejected yet, because that check belongs to milestone 4. It can't hang, but a run could take a long time. Very long runs aren't simulated either: the largest here is 4,096 points.

## Milestone 4 gate result

Measured on 2026-11-04 on the whole design: `evu_core` inside `kernel_engine`, reached through `cmd_proc`, sharing the one FPU and the scratchpad with the other kernels.

### What changed in the RTL

- **Sharing.** `kernel_engine` owns the FPU. While a run is in progress it hands the FPU and both scratchpad ports to `evu_core`, and otherwise the FPU kernel has them. Only one kernel runs at a time, so a two-way mux is all it takes.
- **Checks.** A new CHECK phase comes after the descriptor is read. It works out n0·n1·n2 by shift and add, which takes 34 clocks but needs no DSP block, and then tests each address span with one adder, one per clock. Instruction words are checked as they are copied.
- **Refusals.** `evu_core` has a status output, and `cmd_proc` sends that as the reply. A refused run writes nothing and leaves `last_cycles` alone.
- **Limits I added.** At most 2^32 points in a run, which keeps the sum well inside the accumulator's headroom. Even at one op per clock, that many points take about 43 seconds per instruction, and there's no way to stop a run short of resetting the board.

### Timing and size

The full build with `hw/scripts/build.tcl`, including the bitstream.

| | before the EVU | now |
|---|---|---|
| Setup slack | WNS +0.154 ns | WNS +0.287 ns |
| Hold slack | WHS +0.013 ns | WHS +0.015 ns |
| LUTs | 16,875 of 63,400 (26.6%) | 19,545 (30.8%) |
| Flip-flops | | 13,406 of 126,800 (10.6%) |
| Block RAM tiles | 66.5 of 135 (49.3%) | 72 (53.3%) |
| DSP blocks | 20 of 240 | 20 |

- **The gate passes, with more slack than before.** Placement varies between builds, so I wouldn't read the gain as an improvement. What matters is that adding the EVU didn't use up the margin.
- **The worst setup paths are still in `cmd_proc`,** from the frame length through the byte counters, the same logic that was the limit before this phase. No path of the EVU is in the ten worst.
- **Hold slack is thin but met,** about the same as before the EVU. Its worst paths are single register-to-register hops in the accumulator and CORDIC.
- **Size.** `evu_core` is 4,019 LUTs and 1,680 flip-flops in the design, with the accumulator counted inside that. The whole design only grew by 2,670 LUTs. The earlier figure comes from an older build, and I haven't traced where the difference went.

### Verification

| run | what it covers | result |
|---|---|---|
| Top level, Verilator, `TopRegressTest` | everything through the UART: 13 class problems, the kernels taking turns, every refusal, random programs, then all the old traffic | 306 requests, 32 EVU runs, 0 errors |
| Top level, Verilator, `TopEvuTest`, 40 random, seed 3 | | 303 requests, 60 EVU runs, 0 errors |
| Top level, Icarus, `TopEvuTest` | the same, 4-state | 151 requests, 22 EVU runs, 0 errors |
| Top level, xsim, `top_regress_test`, seeds 1 and 5 | hand-written SV programs, C reference | 255 requests, 18 EVU runs, 0 mismatches each |
| Block, Verilator, seed 1, `--n 120` | | 204 runs, 189 refusals, 387,144 FPU operations, 0 errors, 45/45 bins |
| Block, Verilator, seed 7, `--n 120` | | 205 runs, 188 refusals, 384,240 FPU operations, 0 errors, 45/45 bins |
| Block, 16-entry CORDIC FIFO | | 130 runs, 121 refusals, 230,909 FPU operations, 0 errors, 46/46 bins |
| Block, Icarus | no X on any checked signal | 71 runs, 81 refusals, 160,677 FPU operations, 0 errors, 45/45 bins |
| `EvuLimitTest` | grids at the exact edges of both point limits | 206 grids, 0 errors |
| `test_evu_ref.py` | C port against the Python model, including 3,000 spoiled descriptors | passed |
| Old traffic | Ethernet in both environments, FPU block in xsim, vectors in cocotb and xsim | passed |
| pytest, ruff, `gen_spec --check` | | 318 passed, clean, current |

The scoreboards' cycle bounds held on every run. The largest count seen was 0.43 of the high bound.

**xsim found a bug the others didn't.** `desc_addr` was wired to `arg32(0)`, a function that reads `args`. A function in a continuous assignment only reacts to its arguments, so xsim worked it out once at time 0 and every run started from an X address. Verilator re-evaluates it anyway, and synthesis would have been fine. It's now a plain slice of `args`.

**Injected bugs.** I put 30 bugs into the RTL and the C model, one at a time. The first pass of 26 caught 21 and showed real gaps in my tests:

- Table 5 was never tested for running off the end.
- No grid had a product whose low bits were all zero, which is what a lost high bit looks like.
- No grid made the running sum carry out of its top bit.

I added cases for those, then sharper versions of two bugs, and 27 of the 30 are now caught. I don't count the other three as misses. One only differs at exactly 2^32 + 1 points, which is 641 × 6,700,417 and can't be a grid. One removes a gate on registers that reset before they're used again. The third was a 32-bit wrap in the descriptor check that a cast in my edit probably undid, and the version that really wraps is one of the 27.

Not covered yet: nothing has run on the board. The bitstream is built but not programmed. Very long runs aren't simulated: the largest is still 4,096 points. The Python driver can start a run with `Kernel.EVU`, but the host tools are milestone 5. A run can't be cancelled, so a huge grid keeps the board busy until it finishes or is reset.

## Milestone 5 gate result

Measured on 2026-11-05 on the Python model, through `--port sim` and through `--port udpsim`, which puts the model behind a real local UDP socket. Nothing in this section ran on hardware, so cycle counts here are the model's low bound and no time means anything.

### What exists now

`sw/mathaccel/calc.py` has `integrate_nd`, `line_integral`, `surface_flux`, `grad`, `div`, `curl_at` and `root_find`, and `mathaccel calc` puts all seven on the command line. `Device.evu_run` writes a `memory_image`, runs it, and returns results, flags, cycles, aux and three wall-clock times. `tools/evu_board.py` is the comparison I use for this gate and the next.

### How problems reach the hardware

- **Line and surface integrals become boxes in their parameters.** A line integral is `F(r(t))·r'(t) dt` over `t`, and a surface integral is `F(r(u,v))·(r_u × r_v) du dv`. A scalar field gives arc length or area instead. SymPy does the differentiating on the laptop.
- **Limits that depend on another variable** are mapped onto 0..1 with `x = a + (b−a)s`, which adds a Jacobian `b−a`. Limits follow SymPy's order, innermost first, and an infinite limit is refused with a reason.
- **Reduce mode for integrals, map mode for everything else.** A program has one output, so a 3D gradient or curl takes three runs.
- **Root finding is a host loop.** The program is written once, then each step writes one word of x, runs, and reads two words. Newton gets f′ from SymPy as a second loaded program.

### Timeouts

I read the RTL before changing anything, and a resent RUN is worse than the plan assumed. `protocol.md` says a frame arriving during a command is dropped, and that's only true on the UART. Over UDP, `eth_stack` holds a waiting datagram and replays it when the core is idle, so a resent RUN starts a second run the moment the first ends. Milestone 6 measured this, and found that only one more datagram fits during a run, not two. On the UART a resend after a lost reply also runs twice.

- `Device.run` no longer resends by default. NOP, FILL, and FPU runs whose outputs stay clear of their inputs still can, because repeating those changes nothing and a timeout on them means a lost frame: the largest FPU run takes about 20 ms.
- `evu_start` sends RUN exactly once, with a timeout of 1.2 times the `cycle_bounds` high bound plus 2 s, and at least 5 s.
- Before RUN it writes a marker into `f[0]` that no real flags word can equal. If the reply never comes, it asks STATUS, which can't restart anything, and raises `RunTimeout` with one of three states: `running`, `finished` (only the reply was lost) or `not started` (the request was lost). Running it again is left to the caller.
- A run whose low bound is over 30 s is refused before anything is sent, `max_seconds` can raise that, and a run over 5 s warns first. The cycle counter wraps at 42.9 s, so beyond that the reported cycles are modulo 2^32.
- A connection checks once that the board reports v0.4 or later, so an old bitstream gives a clear error.

### Limits

`Run.check` now rejects a grid dimension over 65,535 and more than 2^32 points. It used to leave both to the hardware.

**That check found a real problem.** The large rules of `decay_wide` and `rf_oscillatory` are 4,096 panels of 16 nodes, 65,536 points on one axis. That is one over the dimension limit, and their tables alone would need 131,072 words in a 65,536-word scratchpad. So milestone 1 measured two runs the board could never have done. Panels of equal width fix both: `integral_run` now puts such an axis on two grid dimensions, with `x = mid[panel] + offset[node]`, when the tables wouldn't otherwise fit. Those two rules now need 4,128 table words. It costs one ADD per point and rounds x once more. A problem that would need more than 3 dimensions or 6 tables after splitting is refused.

Map mode needs a result word and a flags word per point on top of the tables, so a list of 3D points costs five words each and about 13,000 fit in one run. `map_eval` splits a longer list, or a tensor grid along its first axis, into as many runs as it takes. A grid is refused if one slice can't fit.

### Reporting

A result is a `Scalar`, `Samples`, `Integral` or `Root`, never a bare float. `float()` raises on NaN and infinity. NV, DZ and OF are spelled out, even when a finite sum hides them in `aux`. NX is left out because nearly every op sets it, and UF is left out as well. `Samples.bad` marks each bad point. `integrate_nd` runs a second time on doubled panels and reports whether the two agree: this is what milestone 1 asked for, and the 1,024-point plate rule, which is off by 0.18%, now reports `NOT CONVERGED`. `mathaccel calc` exits with status 2 on any of these.

### Results

`python tools/evu_board.py --port udpsim --random 60`, with every model result sent through the socket and compared with a separate run of the model:

| check | result |
|---|---|
| all 22 rules of the 13 class problems | 0 mismatches in result, flags or aux |
| error against the 40-digit reference | at most 1.38 ULP, except the unconverged 1,024-point plate rule at 25,621 ULP |
| map mode: grad, div, curl, bad points, a split list, a tensor grid | 62,000 words, 0 wrong |
| root finding, 6 cases | same result and iteration count as the direct model, at most 0.37 ULP against mpmath |
| 65,536-point mixed run, 60 random programs | 0 mismatches |

`sw/tests/test_calc.py` adds 126 tests on both simulated ports. Known answers come from closed forms, SymPy and mpmath: Ampère's law, a line integral of a gradient, the divergence theorem on a sphere, areas, triangles and quarter disks through variable limits, roots of `cos x = x` and a mass-action equation. Timeouts are tested with transports that lose the request, lose the reply, or stay silent, and each checks that exactly one RUN was sent.

| final checks | result |
|---|---|
| pytest | 444 passed |
| ruff | clean |
| `gen_spec --check` | current |

Not covered here: anything about real time, real long runs, or a real lost frame. On `udpsim` the timeout is scaled up 100 times, because the model is far slower than the hardware, and one run hit the unscaled timeout before I did that. Over a socket the operating system's queue stands in for the board's two slots, so the resend behaviour itself is only read from the RTL until milestone 6 measures it. `protocol.md` still has the old sentence about dropped frames, which is milestone 7's to fix. A reduce run can't say which point went bad, only that one did: rerun it in map mode to find out.

## Milestone 6 gate result

Measured on 2026-11-05 on the real board over Ethernet, at 100 Mbit/s on a direct cable. No number in this section comes from a simulator.

The bitstream was out of date: `cmd_proc.sv`, `kernel_engine.sv` and `evu_core.sv` were newer than it. I rebuilt it with `.Xil` removed and no simulations running: WNS +0.287 ns, WHS +0.015 ns, 19,545 LUTs, 72 block RAM tiles, 20 DSP blocks, the same as milestone 4. It programmed with `hw/scripts/program.tcl`, and `ping` reports v0.4 at 100 MHz on both UDP and COM3.

The link took some recovering. The adapter stayed `Disconnected` through the first programming and through a cable replug. The UART had also stopped answering by then, so the board had probably lost its volatile configuration, though I didn't see that happen. Programming it again after the replug brought the link up at 100 Mbps within 2 s. I ran the checks over the UART first, while the link was down, so every check below has been on the board over both transports.

### Correctness

`python tools/evu_board.py --port udp:192.168.10.2 --random 100`. Each result, flags word and `aux` word is compared exactly with the bit-accurate model, with no tolerance. Each rule also ran seven more times from the loaded image.

| check | result |
|---|---|
| all 22 rules of the 13 class problems, up to 2,097,152 points | 0 mismatches |
| error against the 40-digit reference | at most 1.38 ULP, except the unconverged 1,024-point plate rule at 25,621 ULP |
| cycles inside `cycle_bounds` | every run |
| map mode: grad, div, curl, bad points, a split list, a tensor grid | 62,000 result words and their flags, 0 wrong |
| root finding, 6 cases | same result and iteration count as the model, at most 0.37 ULP against mpmath |
| 65,536-point run mixing CORDIC, divide and fast ops | matches, 4,385,408 cycles, 66.9 per point |
| 100 random programs | 14,265 points, 178,212 FPU operations, 0 mismatches, largest cycles/high bound 0.437 |
| CRC and frame errors after that run | 0 and 0 |
| `pytest sw/tests/test_device.py`, UDP and COM3 | 30 passed each |
| `memtest --words 65536` over UDP | 0 mismatches, 805 kB/s |
| `mathaccel --port COM3 calc`: integrate, flux, curl, root, and a NaN | right answers, and exit status 2 on the NaN |

- **No mismatch to debug.** This was the first time CORDIC and fast ops shared the result port on hardware, and the first runs past 4,096 points. Nothing disagreed with the model, so no tolerance changed.
- **The stall rules aren't proven by this.** The results are right with the traffic that should trigger the FIFO rules, but nothing on the board counts how often they fired.
- **Cycle counts don't depend on the link.** All 22 first-run counts are identical over UDP and the UART.
- **Real counts sit near the low bound, which the plan predicted.** `cube_large` took 172 clocks more than its low bound over 2 million points, and the largest count on the class list was 0.10 of its high bound.

### What the board does with a request during a run

`python tools/evu_board.py --busy --port udp:192.168.10.2 --uart COM3`. During a 1.46 s run I sent a second RUN at 0.30 s and two PINGs just after it.

| | UDP | UART |
|---|---|---|
| second RUN | held, and **ran again**: replied at 2.91 s | dropped, never ran |
| the two PINGs | dropped, no reply | dropped, no reply |
| `frame_errors` | unchanged | +3 |

So the old driver could really have run a kernel twice over UDP, with nothing in the counters to show it. The PINGs show something milestone 5 got wrong. A datagram keeps its receive slot until its own command finishes, so during a run one slot is taken and only one more datagram fits. Anything after that is dropped silently, which only LED6 shows. `protocol.md` says two can wait, which is only true while the core is idle.

With a forced 0.3 s timeout on a 3.88 s run, the driver didn't resend, reported `running`, and reported `finished` once the run ended. `last_cycles` showed one 3.88 s run, not two. The three frame errors STATUS shows now are from this experiment. I haven't seen a real lost frame, so `not started` and a real lost reply are only tested with simulated transports.

### Speed

Times are milliseconds. "Kernel" is the board's own counter. "First" is everything from nothing: write the image, run, read the answer. "Repeat" is RUN and read with the image already there. NumPy runs the same rule, and its repeat is the fastest of seven. SymPy's setup is left out of both sides.

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | board vs f64 |
|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 1.23 | 0.32 | 0.045 | 0.003 | 0.006 | 95.5x slower |
| sin | 16,384 | 0.657 | 156.17 | 0.97 | 0.166 | 0.086 | 0.025 | 11.3x slower |
| poly_box | 64 | 0.009 | 0.93 | 0.31 | 2.057 | 0.010 | 0.005 | 30.3x slower |
| gaussian_2d | 1,024 | 0.127 | 2.13 | 0.54 | 0.042 | 0.012 | 0.007 | 46.5x slower |
| gaussian_2d | 65,536 | 7.918 | 14.55 | 8.43 | 1.076 | 0.662 | 0.151 | 12.7x slower |
| gaussian_2d | 1,048,576 | 126.786 | 147.97 | 127.45 | 27.325 | 21.553 | 9.895 | 5.9x slower |
| charge_sphere | 4,096 | 0.663 | 2.44 | 1.13 | 0.224 | 0.125 | 0.060 | 9.1x slower |
| charge_sphere | 262,144 | 42.328 | 46.98 | 42.94 | 12.239 | 10.452 | 5.194 | 4.1x slower |
| circulation | 32 | 0.002 | 1.97 | 0.33 | 0.026 | 0.004 | 0.004 | 87.9x slower |
| flux_sphere | 1,024 | 0.250 | 8.33 | 0.68 | 0.189 | 0.134 | 0.071 | 5.1x slower |
| dipole_power | 32 | 0.015 | 1.96 | 0.36 | 0.044 | 0.007 | 0.006 | 54.1x slower |
| loop_bfield | 64 | 0.045 | 1.98 | 0.44 | 0.045 | 0.009 | 0.014 | 48.8x slower |
| plate_potential | 1,024 | 0.684 | 2.46 | 1.13 | 0.048 | 0.012 | 0.015 | 96.8x slower |
| plate_potential | 65,536 | 43.618 | 53.48 | 44.32 | 0.840 | 0.558 | 0.104 | 79.4x slower |
| plate_potential | 1,048,576 | 697.846 | 738.36 | 698.56 | 39.977 | 29.571 | 11.567 | 23.6x slower |
| fermi_half | 64 | 0.024 | 2.49 | 0.44 | 0.075 | 0.014 | 0.015 | 31.3x slower |
| decay_wide | 128 | 0.009 | 2.43 | 0.39 | 0.042 | 0.005 | 0.004 | 75.7x slower |
| decay_wide | 65,536 | 5.203 | 28.96 | 5.68 | 0.823 | 0.311 | 0.124 | 18.3x slower |
| rf_oscillatory | 512 | 0.036 | 6.72 | 0.45 | 0.030 | 0.009 | 0.006 | 50.1x slower |
| rf_oscillatory | 65,536 | 6.535 | 38.19 | 7.21 | 0.827 | 0.500 | 0.123 | 14.4x slower |
| cube_large | 512 | 0.064 | 1.48 | 0.36 | 0.029 | 0.006 | 0.006 | 59.8x slower |
| cube_large | 2,097,152 | 251.661 | 263.63 | 252.25 | 32.781 | 20.526 | 10.852 | 12.3x slower |

- **The FPGA loses all 22.** Repeat runs are 4.1x to 97x slower than double-precision NumPy, and NumPy's answer is more accurate as well. In single precision NumPy is faster again, and it matches the board's accuracy to within about 1 ULP here.
- **The best case for the board** is `charge_sphere` at 262,144 points, 4.1x slower. It has no divides. The worst is `plate_potential` at 1,024 points, about 97x, where two divides per point hold the board to about 66 clocks a point.
- **Kernel time alone doesn't rescue it.** Without any link, the kernel still lost 19 of 22 rules, by up to 78x. It only matched or beat NumPy on `sin` at 16 points, `poly_box` at 64 points, `circulation` at 32 points, and with the link those take over 0.3 ms. Timings this small are noisy: `poly_box` lost in the UART session.
- **Transfer dominates small problems, the kernel dominates large ones.** A repeat costs about 0.3 ms on top of the kernel, for three requests. A first run adds the image: 1 to 2 ms for a small one, 156 ms for the 32,800-word `sin` tables. On a repeat run, the link is most of the time up to about 1,000 points.
- **Splitting a long axis paid off twice.** The 65,536-point `decay_wide` and `rf_oscillatory` rules take 29 to 38 ms to start, where the `sin` rule with a quarter of the points takes 156 ms.
- **One odd number.** NumPy's 2.06 ms first call on `poly_box` is the only first call the board beat, at 0.93 ms. It's far from NumPy's usual time, so I read it as noise on the laptop and not a result.

This matches what the plan expected. As a way to get answers fast, the EVU loses to this laptop. It's useful as a calculator I can check bit for bit: every result here is predicted exactly by a model and measured against a 40-digit reference, and its cycle counts came out the same on both links. A faster fabric, more FPU lanes or a wider link would change these numbers, but none of those are measured here.

[phase4-cpu-comparison.md](phase4-cpu-comparison.md) follows this up with Numba, measured frequency, package energy and a cycle profile of the EVU.

### What wasn't run or covered

- Energy wasn't measured here. The follow-up measures the CPU side, and the FPGA side still needs a meter.
- No run came near the 30 s default limit: the longest was 0.70 s of kernel, or 3.88 s in the timeout test. The cycle counter's wrap at 42.9 s is from the RTL, not from a run.
- The FIFO stall rules aren't counted on the board. The follow-up counts them in simulation, tied to the board's cycle counts.
- These are the class list and random programs, a sample, not a proof of all programs.
- `protocol.md` still needed both corrections to its text on frames during a run. Milestone 7 made them.

## Milestone 7 result

Done on 2026-11-07. The docs now cover everything measured up to this point:

- [verification.md](verification.md#phase-4-checks) has the phase 4 test environments, the command and result for every check, and the [board results](verification.md#phase-4-board-results).
- [protocol.md](protocol.md#udp-transport) says what really happens to a datagram during a run: one is held and run afterwards, and the rest are dropped silently. The error table now separates the UART from UDP, and it says an EVU run is never resent.
- [numerics.md](numerics.md#phase-4-sums-and-integrals) has the accumulator's rules and the measured integral accuracy.
- [phase4-cpu-comparison.md](phase4-cpu-comparison.md) opens with a clock-for-clock summary against the laptop's CPU and the architectural reasons behind it.
- The README describes phase 4 and the comparison, and [errors_log.md](errors_log.md) records every fault with its fix.

Still open: the FPGA board's energy, which needs a meter, and the laptop's energy while driving the board, which needs the Ethernet link back. Both are listed in the comparison's status line. I didn't write a separate ISA document: the instruction format is in [protocol.md](protocol.md#evu-kernel), and the compiler's translations are in [Instruction set](#instruction-set) above.

## Speed: what to expect

The September example was a 2D Gauss-Legendre integral with 64 × 64 = 4,096 nodes and a 20-op integrand, which is about 82,000 FPU operations, or 0.82 ms at one op per clock. Two corrections to what I said about it before:

- **The link still matters on the first run.** "Under 100 bytes" counted only the request and reply. The program, constants and node tables also have to get there: 64 nodes and 64 weights is 256 words, so about 1–2 KB in total, plus a few round trips of about 100 µs each. That is on the order of 1 ms the first time and less on repeat runs with the same nodes.
- **It may not beat NumPy.** NumPy measured 1.7 ns per element for simple division on this laptop. 82,000 such operations would take about 0.14 ms, faster than the EVU's best case, although transcendental functions cost NumPy much more than that. Milestone 6 measures it. I expect this to be useful as a calculator, and as a design I can verify bit for bit, and it might only beat the CPU on some problems.

## Resources and risks

With the EVU in, the design uses 19,545 of 63,400 LUTs (30.8%), 72 of 135 block RAM tiles (53.3%) and 20 of 240 DSP blocks, so there is still room.

| Risk | Plan |
|---|---|
| Timing. The full-design margin has been small since Ethernet | Done for now: WNS +0.287 ns with the EVU in. The worst path is still in `cmd_proc`, so fix that first if it becomes the limit |
| The accumulator's final carry and rounding | Done: it runs once at the end in a fixed 29 clocks |
| Integrand precision. fp32 has about 7 digits, and each op rounds | Milestone 1 measures this. If results aren't good enough for class use, I'd rather know before writing RTL |
| Rounding error near singular integrands | Gauss-Legendre doesn't handle these well in any precision. Document the limit |
| Map mode writes two scratchpad words per result | Done: a 32-entry output FIFO, and at most 32 points can be between start and written, so it can't overflow |

## Out of scope

- Restructuring the scratchpad for one element per clock. I said earlier this would come with phase 4, but it isn't needed: the EVU keeps its working values in its own register file and reads the scratchpad only for node tables, a few words per point. The FPU kernel stays at 4 clocks per element.
- Parallel FPU lanes (phase 6, field sums) and the stencil and tridiagonal engines.
- The complex unit, fp64, and DDR2.
- The text front end (phase 5). It would call these tools, but it isn't part of this phase. It still only picks the operation and operands. It doesn't solve anything.
- The every-input sweeps left over from phase 3.

## Changes from the September plan

| September plan | This plan | Why |
|---|---|---|
| Separate reduce engine doing a weighted sum | Weights are multiplied in the bytecode, and the accumulator only adds | Less hardware, and any weighting works |
| Barrel-interleaved points in fixed time slots | Ready queue | Fixed slots waste time when latencies run from 7 to 54 clocks |
| `LOADK`, `LOADVAR` instructions | Shared constant bank, one `LOAD` | Constants need no instruction at all |
| `NEG`, `POW`, `SELECT` as instructions | Compiled to existing ops, `SELECT` left out | Avoids hardware until a problem needs it |
| Results over UART | Ethernet, already working | Phase 8 was moved earlier |
| Agent and REPL as phase 5 with a local LLM | Small text-to-operation translator | Class rules: the tool only computes what I explicitly ask |
