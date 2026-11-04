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
| fp_divsqrt | DIV, SQRT | 1 at a time | 33 clocks or more (fixed iteration count) |
| fp_cordic | SIN, COS, ATAN2, HYPOT, EXP, LN | 1 per clock while reserved FIFO space is available | 54 clocks with a free result port; arbitration can delay it |

The FPU kernel reads two operands per element through one RAM port, so it issues one element every 4 clocks. Divide and square root issue every 33 clocks. At 100 MHz the kernel computes 25 million pipelined operations per second, or 3 million divides. Moving the data over the 2 Mbaud UART takes far longer than the arithmetic, so wall time on the board is set by the link.

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

These rows record the earlier phase 2 RTL and timing evidence. They do not establish phase 3 board behavior or timing. Reproduce a 1M run with `python hw/scripts/run_uvm.py fpu --test fpu_lcg_test --op <n> --n 1000000 --lcg-seed <hex>`.

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
results. The publication checks use the unscreened mpmath path.

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
default FIFO age rule bounds latency at 95 clocks in the tested configuration.
The array kernel uses one operation throughout, issues every four clocks,
and reports `4*count + 56` clocks for the phase 3 operations, or one clock
for an empty array. These are core clock counts, separate from transfer time.
