# How I'm reading my shared divide/root pipeline

I wrote this after the S3 study because "it shares some multipliers" skips most
of what I need to understand when I come back to the RTL. My actual source is
[fp_divsqrt.sv](../hw/rtl/fp/fp_divsqrt.sv), with the admission and result-port
rules in [fpu.sv](../hw/rtl/fp/fpu.sv). This is the pipelined S3 implementation,
not the original iterative unit that happened to use the same module name.

## the shape of the hardware

```text
FPU request, operands and tag
              |
     reserve one queue slot
              |
  C classify -> P normalize -> s1 seed
              |
     s2..s18 shared multiply/refine schedule
              |
          s19 exact correction
              |
       K pack -> R1/R2 round
              |
      one 32-entry result queue
              |
        FPU result arbitration -> result and original tag
```

I get one combined DIV-or-SQRT acceptance per clock when there is a free
reservation. Different operations can occupy all those stages at once. I don't
get one divide plus one root accepted on the same clock. My current FPU only
has one request port and one result port anyway.

The measured free-port FPU latency is 26 clocks for either operation. I count
C, P, nineteen arithmetic stages, packing and two rounding stages, followed
by queue and FPU output registration under the testbench's cycle convention.
An occupied result port can add waiting time, so 26 isn't a promise for every
mixed-traffic response. The tag travels with the operation to keep that safe.

## what I'm estimating

For division I normalize the dividend and divisor into 24-bit integers X and D,
each with its leading 1 at bit 23. I want the exact integer quotient
`floor(X * 2^26 / D)` and whether its remainder is nonzero. The floating-point
exponent is handled alongside that integer problem.

For a root I normalize the input into a 24-bit significand na and an unbiased
exponent E. I split E into its parity p and an even part. The integer problem
is `R = isqrt(N)`, where `N = na << (27+p)`. Its exact remainder is `N-R*R`.
That gets me the same 26 root bits and sticky information the old recurrence
produced; changing the absolute exponent mostly changes the output exponent.

I don't round a rough reciprocal and hope it is good enough. Both paths use
an approximate start and then an exact integer correction before final RNE
rounding. My width choices and right-shift floors are part of that algorithm.

## how I share the stages

| stages | divide path | root path | why I keep it |
| --- | --- | --- | --- |
| C, P | classify both operands, normalize X/D | classify A, normalize na and get exponent parity | I handle special answers and normal arithmetic separately but keep their timing aligned |
| s1 | 512-entry reciprocal seed | 1,024-entry reciprocal-root seed | I select the appropriate seed in the same schedule slot; the tables still have different contents |
| s2, s3 / M1 | D times Y0 | na times Y0, called S0 | I share the multiply and delay its product for later reuse |
| s4, s5 / M2 | unused result | S0 times Y0 | I need a second product to form the root's error |
| s6 | e = 1-d*y0 | e = 1-a*y0*y0 | I form a small signed error and keep different scaled versions |
| s7, s8 / M3 | shortened error squared | shortened error squared | I share the quadratic correction multiply |
| s9 | e + e² | e/2 + 3e²/8 | I align each correction at its own fixed-point scale |
| s10, s11 / M4 | Y0 times correction | upper S0 bits times correction | I share the refining multiply |
| s12 | refined reciprocal Y1t | integer root estimate R0 | I now have the estimate the remaining steps can check |
| s13, s14 / M5 | X times Y1t | R0 squared | I share this multiply even though its two meanings differ |
| s15 | quotient estimate Q0 | signed remainder N-R0² | I hold the root's remainder while division finishes |
| s16, s17 / M6 | Q0 times D | unused result | I need the product for the exact divide remainder |
| s18 | X*2^26-Q0*D | delayed root remainder | I put both operations at the same correction boundary |
| s19 | adjust against D | adjust against 2R0+1 | I recover the exact integer answer and sticky bit |

M1, M3, M4 and M5 serve both operation types. M2 and M6 are needed by one type
only. Those six logical multiply positions map to 12 DSPs in the recorded S3
route; a wide multiply can consume more than one DSP. I haven't clock-gated
unused slots or measured power, so resource sharing alone isn't an energy result.

The RTL's sq_l, p_l, x_l, a_l, y0_l and m1_l arrays carry the matching operation
choice, parity, operands and products alongside the arithmetic. Index k means
stage s<k>. The sideband delay carries sign, special-answer selection, flags,
tag and exponent. If I move a multiplier stage, I have to move its sideband
and operand delays too, otherwise individually correct math can use the wrong
request's data.

## the last correction is the important bit

For division I calculate `r = X*2^26-Q0*D`. If r is negative, Q0 is too high;
I subtract one from Q0 and add D back to r. If r is at least D, I add one to Q0
and subtract D. Otherwise I leave it alone. The final remainder tells me sticky.

For a root I calculate `r = N-R0²`. Stepping the root up changes the square by
`2R0+1`; stepping it down restores `2R0-1` to the remainder. That is where the
root correction thresholds come from. After correcting I want
`0 <= remainder < 2R+1` for the corrected integer root R.

I only keep the low remainder bits in the hardware. That works because the
true small signed error fits the retained width, not because N or the complete
products are small. My tests check the intermediate ranges and exact corrected
answers. I wouldn't reuse those truncated expressions with an arbitrary seed.

## why the queue counts two different things

The core can't stop after accepting an operation. I reserve a slot before I
let it enter, so a result will have somewhere to go even if short FPU operations
keep taking the output port.

- I use `cnt` for all accepted operations whose results haven't left: both
  unfinished pipeline work and stored answers.
- I use `q_cnt` for finished answers actually sitting in the queue.
- When the core finishes, q_cnt increases but cnt doesn't change.
- When the FPU acknowledges a stored answer, both counts decrease.

Checking only q_cnt would admit too many operations while results were still
in flight. Both roots and divides spend the same credits. At capacity the ready
rule waits until the following clock to reuse a released slot; I accept that
conservative bubble instead of creating a combinational ready shortcut.

I timestamp an answer when it enters the queue. After AGE=8 clocks of queue
waiting, out_urgent asks the FPU to stop issuing new requests, let the short
pipeline drain and take the old answer. The shared queue goes before the CORDIC
FIFO, but I only have one 32-entry queue ahead of it. That keeps the existing
128-clock CORDIC bound rather than the independent S2 design's 160-clock bound.

Reset clears validity, pointers and counts. I don't need to erase every payload
register or RAM word: old bits cannot become a response unless validity says
they belong to an accepted operation. The busy_div/busy_sqrt outputs only observe
part of the arithmetic pipeline for coverage; I don't use them as queue credits.

## how I check the explanation against the code

[The exhaustive root model](../sw/tests/test_sqrt_multiplicative.py) enumerates
both exponent parities and every normalized significand, checks register ranges,
and compares the corrected root and remainder with an independent integer-root
answer. The [divide model](../sw/tests/test_div_multiplicative.py) checks every
divisor's reciprocal bound and exact correction on boundary/random operands.
Neither test clocks the RTL or proves special-value handling on its own.

My FPU regression separately checks exact bits and flags, table edges, signed
zero, NaNs, infinities, subnormal normalization, mixed operation tags, queue
pressure and reset. I explicitly fill the small CORDIC FIFO rather than hoping
random traffic reaches the coverage goal. The integration gate also checks the
array kernel, EVU cycle model, both software links, SV UVM and full-design timing.

I keep [study measurements](sqrt-comparison.md),
[original board evidence](evidence/sqrt-board/README.md) and
[integration evidence](evidence/sqrt-integration/) separate. A successful route
creates a new bitstream; it doesn't establish that I programmed and tested that
new file on the physical board.
