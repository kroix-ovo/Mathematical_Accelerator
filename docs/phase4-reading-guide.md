# How I follow a Phase 4 calculation

I find this easier to read as one calculation making its way through the project.
For `x^2*sin(x)`, I need nodes to evaluate, weights if I'm integrating, a few
instructions, and somewhere to put the answer. I don't send a symbolic expression
to the FPGA. I do the symbolic setup on the laptop and send ordinary 32-bit words.

## I set up the problem on the host

I start in [`calc.py`](../sw/mathaccel/calc.py). For an integral, I substitute any
parameters, rewrite variable limits onto a box when needed, and include the
Jacobian. For a line or surface integral, I also include the tangent, arc length,
or oriented surface cross product. That is how the geometry becomes the scalar
expression the EVU will evaluate.

I build Gauss-Legendre nodes and weights in
[`quadrature.py`](../sw/mathaccel/compiler/quadrature.py). I compute the rule on
the host, then round the tables to fp32 before uploading them. I keep that rounding
in the reference calculation too. For a long axis I can store panel midpoints and
local offsets separately, trading one extra ADD for much smaller tables.

## I turn the expression into instructions

I lower the expression in
[`sympy_to_evu.py`](../sw/mathaccel/compiler/sympy_to_evu.py). I share repeated
subexpressions, lower functions to available FPU operations, remove unused work,
and reuse registers after their last read. I multiply quadrature weights into the
program's output, so the accumulator only needs to add terms.

I define the instruction fields and descriptor in
[`evu_isa.py`](../sw/mathaccel/compiler/evu_isa.py), using the constants generated
from [`accel.toml`](../spec/accel.toml). I use word addresses throughout. A source
operand names either a context register or a constant; a LOAD names a table whose
descriptor tells me which grid index to use. I mark the final instruction so the
EVU knows when that point has produced its output.

## I load once, then start a run

I lay out the descriptor, program, constants and tables in scratchpad, followed by
result and flag space. In [`device.py`](../sw/mathaccel/driver/device.py), I can
load that image once and start it repeatedly. I send an EVU RUN exactly once. If
its reply times out, I inspect STATUS and the flag sentinel instead of resending
something that might already be running.

I check the descriptor pointer in
[`cmd_proc.sv`](../hw/rtl/core/cmd_proc.sv). I hand a valid pointer to
[`kernel_engine.sv`](../hw/rtl/core/kernel_engine.sv), which gives the EVU ownership
of the shared FPU and scratchpad ports for the run. I check descriptor contents,
spans and instruction words inside the EVU before writing any result.

## I keep independent points moving

I follow the stage comments in [`evu_core.sv`](../hw/rtl/evu/evu_core.sv) here.
I call one partly evaluated point a context, with its own registers, grid indices
and accumulated flags. I have 31 contexts because the last of the 32 register
banks holds shared constants.

I pick work from ready and retry queues, read an instruction, read its operands,
then offer it to the FPU. If the FPU refuses, I put the context on the retry queue.
If it accepts, I wait for the tagged result before running that context's next
instruction. Other contexts can keep moving meanwhile. I allow at most one
instruction in flight per context, so its tag is enough to find its destination.

## I finish either a map or a sum

I write a map result and its flags back to the original point's slots, even when
points finish out of order. I buffer completed outputs because each point needs
two scratchpad writes.

For a reduction, I send each completed term to
[`fp_accum.sv`](../hw/rtl/fp/fp_accum.sv). I represent finite fp32 values as signed
integers in units of `2^-149`. I split the default 320-bit total into ten segments
so a carry only crosses one segment per clock. I can accept one term per clock;
at the end I settle the carries, scan the magnitude and round once, in 29 clocks.
I keep NaN and infinity state separately. I get an order-independent sum of the
rounded terms, which doesn't remove error from the terms or the integration rule.

## I check what actually happened

I predict result bits and flags with
[`evu_model.py`](../sw/mathaccel/evu_model.py), and use a separately structured
[`C interpreter`](../hw/tb/uvm/dpi/evu_ref.c) for the SV command scoreboard. I use
the block monitors to check accepted FPU requests as well as final writes, so
lost or duplicate instructions can't hide behind an unchanged sum.

I check numerical convergence by refining the sampling rule on the host. I keep
that separate from matching the bit-level model: hardware can implement a coarse
rule perfectly and still give a poor approximation to the integral. I keep the
[recorded measurements](evidence/phase4/README.md) beside the source, and distinguish
simulated stall counts, routed timing and physical-board results in the
[verification notes](verification.md#phase-4-board-results).
