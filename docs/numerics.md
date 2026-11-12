# FPU numerics

For operations 0–11, this file defines what the FPU computes bit for bit. The RTL (`hw/rtl/fp/`), the Python reference (`sw/mathaccel/fp32ref.py`) and the C/DPI reference (`hw/tb/uvm/dpi/fp_ref.c`) implement these rules, and the testbenches compare result patterns and flags exactly. The six [phase 3 functions](#phase-3-functions) instead have an approximation budget, checked separately from their exact match to the finite-width datapath model.

## Format and rounding

* IEEE-754 binary32 (single precision) for every floating-point input and output.
* One rounding mode: round to nearest, ties to even.
* Subnormal numbers are fully supported as inputs and outputs. Nothing is flushed to zero, which matters for values like intrinsic carrier concentrations in semiconductor problems.
* Every NaN result is the canonical quiet NaN `0x7FC00000`. Input NaN payloads and signs are not propagated. This follows the RISC-V F extension. NumPy on x86 propagates payloads instead, so tests canonicalize NumPy's NaNs before comparing.
* A signaling NaN input (exponent all ones, fraction nonzero, fraction bit 22 clear) raises the invalid flag on every operation that reads it.

## Exception flags

Each result carries five flags, in RISC-V `fflags` order:

| bit | flag | set when |
|---|---|---|
| 4 | NV invalid | sNaN input; ∞−∞, 0·∞, 0/0, ∞/∞; sqrt of a negative number; LT/LE with any NaN; F2I of NaN, ∞ or out-of-range values |
| 3 | DZ divide by zero | finite nonzero / ±0 |
| 2 | OF overflow | the rounded result is too large for binary32 (the result is ±∞ in RNE) |
| 1 | UF underflow | the result is tiny **after rounding** and inexact |
| 0 | NX inexact | the result differs from the exact value, including overflow |

Underflow uses the "after rounding" tininess rule, the one x86 SSE and RISC-V use. A result is tiny if, rounded to 24 bits as if the exponent range were unlimited, it is below 2^-126. An exact subnormal result raises neither UF nor NX.

## Operations

| op | rule |
|---|---|
| ADD, SUB | exact sum, rounded once. An exact zero sum is +0, except (−0) + (−0) = −0 |
| MUL | exact product, rounded once. The sign of a zero or infinite result is the XOR of the input signs |
| DIV | exact quotient, rounded once. x/±0 = ±∞ with DZ for finite nonzero x |
| SQRT | correctly rounded square root. sqrt(−0) = −0. Negative inputs give NaN with NV |
| I2F | signed 32-bit integer to binary32. Rounds (NX) when \|n\| > 2^24 and the low bits are lost |
| F2I | binary32 to signed 32-bit integer, RNE. NaN → 0x7FFFFFFF; values ≥ 2^31 (and +∞) → 0x7FFFFFFF; values < −2^31 (and −∞) → 0x80000000; all of these raise NV only. In-range inexact conversions raise NX |
| MIN, MAX | IEEE 754-2019 minimumNumber / maximumNumber (RISC-V fmin/fmax): a NaN operand loses to a number, two NaNs give the canonical NaN, −0 < +0. Only sNaN raises NV |
| EQ | 1 if equal (−0 == +0), else 0. Quiet: NV only for sNaN |
| LT, LE | 1 if less (or equal), else 0. Signaling: NV for any NaN |

## Hardware timing

| unit | ops | throughput | latency (FPU input to result) |
|---|---|---|---|
| fp_addsub | ADD, SUB | 1 per clock | 7 clocks |
| fp_mul | MUL | 1 per clock | 7 clocks |
| fp_misc | I2F, F2I, MIN, MAX, EQ, LT, LE | 1 per clock | 7 clocks |
| fp_div | DIV | 1 per clock (pipelined) | 24 clocks with a free result port |
| fp_sqrt | SQRT | 1 at a time, independent of fp_div | 33 clocks or more (fixed iteration count) |
| fp_cordic | SIN, COS, ATAN2, HYPOT, EXP, LN | 1 per clock while reserved FIFO space is available | 54 clocks with a free result port; at most 128 when mixed ops make it wait (up to 32 queued divide results can go first) |

The FPU kernel reads two operands per element through one RAM port, so it issues one element every 4 clocks. Divide and square root issue every 33 clocks. Until 2026-11-08 one fp_divsqrt unit did both; I split it so a divide and a square root can run at the same time, which matters to EVU programs that need both per point. The arithmetic, results and clock counts of each operation are unchanged. At 100 MHz the kernel computes 25 million pipelined operations per second, or 3 million divides. Moving the data over the 2 Mbaud UART takes far longer than the arithmetic, so wall time on the board is set by the link.

## Verification evidence

Two reference models implement these rules independently:

* `fp32ref.py` computes every result exactly with Python's `Fraction` (unlimited-precision rationals) and rounds once. It uses no floating-point arithmetic.
* `fp_ref.c` uses the CPU's own single-precision hardware and reads the flags from the CPU status register through `<fenv.h>`. The negative-sqrt and compare-flag cases are written as explicit rules there, because the gcc 6.2 that xsim uses gets them wrong (see the comments in the file).

`sw/tests/test_fp32ref.py` checks the Python model against NumPy (results) and against the C model built with the same compiler and flags that xsim's `xsc` uses (results and flags). It runs a 28 × 28 special-value matrix plus 20,000 generated cases per op.

The RTL has been run against both models:

| run | reference | vectors | result |
|---|---|---|---|
| SV UVM, `fpu_lcg_test`, one run per op, LCG seeds 0x2000 + op | C/DPI | 1,000,000 per op, 12,000,000 total | 0 mismatches |
| SV UVM, `fpu_regress_test` | C/DPI | corner matrix, LCG, random, ties, underflow, mixed bursts | 0 mismatches |
| pyuvm, `FpuRegressTest`, Verilator and Icarus | Python exact | corner matrix, random, mixed bursts | 0 mismatches |
| xsim UART vectors and both top-level UVM envs | Python / SV device models | FPU kernel through the UART for all 12 ops | byte-exact replies |

These rows record the phase 2 evidence for ops 0–11. The phase 3 evidence is under [Measured accuracy](#measured-accuracy). Reproduce a 1M run with `python hw/scripts/run_uvm.py fpu --test fpu_lcg_test --op <n> --n 1000000 --lcg-seed <hex>`.

## Phase 3 functions

| Code | Function | Operands and result |
|---|---|---|
| 12 | SIN | Sine of `a`, in radians |
| 13 | COS | Cosine of `a`, in radians |
| 14 | ATAN2 | Angle of `(x=b, y=a)`, in radians, retaining signed-axis cases |
| 15 | HYPOT | `sqrt(a*a + b*b)`, with scaling to avoid intermediate overflow |
| 16 | EXP | `e^a` |
| 17 | LN | Natural logarithm of `a` |

I use a target error strictly below 1 ULP of the high-precision value. Here
ULP means fp32 spacing at that magnitude, with a floor of `2^-149` for
subnormals. This differs from counting representable steps between two
already-rounded answers. A model can match the hardware exactly while both
remain a fraction of a ULP away from the mathematical function.

`cordic_model.py` and `cordic_ref.c` reproduce the finite-width algorithm.
`mpref.py` evaluates at 256 bits and rounds once; it is the high-precision
accuracy reference, rather than an interval proof for every input.
`measure_ulp.py` reports the observed maximum, its inputs, a histogram and
flag errors. A finite sample establishes the error on that sample only.
Its optional C screen is faster but relies on the C reference for agreeing
results.

The phase 3 flag policy sets NX on ordinary approximated results, including
an accidentally exact value such as `hypot(3,4)`. The explicit exact cases
are `sin(±0)=±0`, `cos(±0)=1`, `exp(±0)=1`, `ln(1)=+0`,
`atan2(±0,+x)=±0` for nonnegative `x`, and hypot with a zero operand.
Infinite-input limits and NaNs follow the rules in `mpref.special` and the
matching RTL branches. An sNaN raises NV; sin/cos of infinity and ln of a
negative number are invalid. `ln(±0)` returns negative infinity with DZ.
Overflow raises OF with NX; tiny approximated results raise UF with NX.
The underflow decision retains the shared after-rounding rule, including
the boundary near the smallest normal number.

The standalone CORDIC latency is 52 clocks. A result passes through a FIFO
and the FPU output register, giving 54 clocks when the port is free. The
default FIFO age rule bounds latency at 95 clocks in the tested configuration;
the longest measured in simulation was 91.
The array kernel uses one operation throughout, issues every four clocks,
and reports `4*count + 56` clocks for the phase 3 operations, or one clock
for an empty array. These are core clock counts, separate from transfer time.

### Measured accuracy

Every number here is the largest error found in a sample, against the
256-bit mpmath value. None of them is a proven bound over all inputs. All
runs reported zero budget failures, zero flag errors and zero errors on
rule-decided inputs (special values and limits).

| Function | Model, 1M inputs: max ULP | Correctly rounded | Worst input bits | Board, 1M vectors: max ULP | Correctly rounded |
|---|---|---|---|---|---|
| SIN | 0.577 | 99.89% | `487a45a4` | 0.554 | 99.94% |
| COS | 0.571 | 99.97% | `4517e059` | 0.566 | 99.99% |
| ATAN2 | 0.599 | 99.88% | `54817c7a, 5b664338` | 0.599 | 99.93% |
| HYPOT | 0.502 | 99.93% | `4d13ce2b, 4d5060c7` | 0.502 | 99.94% |
| EXP | 0.500 | above 99.99% | `b6f9003c` | 0.500 | above 99.99% |
| LN | 0.588 | 98.69% | `3f7feb89` | 0.588 | 99.09% |

The model column comes from
`python tools/measure_ulp.py --op all --n 1000000 --screen` (seed 1), with
every input aimed at the generators' hard cases. The board column comes from
`mathaccel --port udp:192.168.10.2 fptest --op phase3 --n 1000000`, where half
the vectors come from those generators and half from the general LCG stream,
so the two samples differ and their percentages can't be compared directly.
Both columns use the C screen: results equal to the C reference count as
correctly rounded, and only the rest get an exact mpmath measurement. The
percentages leave out rule-decided inputs. The board matched the bit-accurate
model on every vector, so its column is the model's error on that sample.
[Verification](verification.md#phase-3-board-results) has the full board run.

The largest error depends on the sample. An earlier million-input run with
different inputs reached 0.629 ULP on ATAN2, the highest I have measured for
any function, which is still inside the 1 ULP budget.

LN has the lowest correctly rounded rate, and most of that comes from its
near-1 path. In the model sample, 8,257 of 174,352 near-1 results (4.7%) were
not correctly rounded, against 3,937 of 753,732 main-path results (0.52%). The
largest LN error is on the main path, though: 0.588 ULP there, 0.572 ULP on
the near-1 path.

## Phase 4: sums and integrals

The expression unit doesn't add any new arithmetic. Every instruction is one of the FPU operations above, with its own rounding and flags. What's new is how a run's results are combined.

### The exact accumulator

In reduce mode every point's fp32 output is added into a fixed-point register that covers the whole fp32 range, from 2^-149 to 2^128, with headroom for 2^32 terms. Nothing is rounded until the end, when the total is rounded once to nearest even. So the sum is exact, and the order the points finish in can't change it. The result's flags are the ones that final rounding raises (NX, OF), plus NV for a signalling NaN among the terms or for +∞ and −∞ together, the same rules repeated ADD would follow. An infinity or NaN term sets state bits instead of entering the register. The Python model predicts the sum with `Fraction` arithmetic, so it's checked bit for bit.

`aux` in the RUN reply is the OR of every point's flags. A finite sum can hide an NV or DZ from one point, so the host tools spell those out from `aux`.

### What the integrals measure

The answer to an integral has two errors on top of fp32: each point is computed in fp32, and the quadrature rule itself has truncation error. Measured against a 40-digit reference on all 22 rules of the class problem list, on the board:

* Every rule whose answer had converged came within **1.38 ULP**, however many points it used, up to 2,097,152. The fp32 rounding in the points and nodes mostly cancels in an exact sum.
* The 1,024-point `plate_potential` rule misses by 25,621 ULP, about 0.18%, in every precision including double. That's the rule failing near a near-singular integrand, not the arithmetic. The host tools run every integral again on a finer rule and report `NOT CONVERGED` when the two disagree, which catches this case.
* For comparison, adding the same fp32 terms in a plain fp32 loop in grid order reached about 16,400 ULP at a million points, and the result changed with the order. [phase4-cpu-comparison.md](phase4-cpu-comparison.md#accuracy-by-how-the-sum-is-done) has the full table.

Two compiler choices add rounding that isn't covered by the per-op budgets: `x^y` is compiled as `exp(y·ln x)`, which rounds twice, and a long integration axis split into panels computes each node as `mid + offset`, which rounds once more. Both are included in the measured results above wherever a class problem uses them.
