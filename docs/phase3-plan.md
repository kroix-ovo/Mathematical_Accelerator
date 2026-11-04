# Phase 3 plan: transcendental functions

This is the original design plan. The current implementation includes all
six functions, a 44-fraction-bit datapath, 42 CORDIC stages, generated
SV/C tables, FPU result buffering and protocol 0.3 integration. Some choices
below were estimates made before implementation; for example the reduction
window is now 96 bits. Read [the HDL guide](hdl-reading-guide.md#follow-one-cordic-operation)
for the implemented circuit, [numerics](numerics.md#phase-3-functions) for
the current rules, and [verification](verification.md#phase-3-implementation-checks)
for the simulation checks and [board results](verification.md#phase-3-board-results).
The board milestone and phase 3 CLI checks are complete; the optional
exhaustive sweeps have not been run.

Phase 3 adds sin, cos, atan2, hypot, exp and ln to the FPU as new operations, computed by a fully pipelined CORDIC unit. These are the functions my EM/RF and semiconductor classes use most: phasor conversion between rectangular and polar form, wave and field expressions, carrier statistics, and decibels.

## Decisions

| Topic | Decision |
|---|---|
| Functions | sin, cos, atan2(y, x), hypot(x, y), exp, ln |
| Accuracy | Aim for ≤ 1 ULP. If that can't close timing at 100 MHz, accept ≤ 2–4 ULP and document it |
| sin/cos range | Full fp32 range, using Payne–Hanek argument reduction |
| Structure | Fully pipelined: one operation per clock, fixed latency |
| Rounding and NaN | As in phase 2: RNE for the final rounding, canonical NaN, the same five flags |

ULP means a unit in the last place: the gap between two neighbouring fp32 values. "≤ 1 ULP" means the hardware result is always one of the two fp32 values on either side of the true answer. Unlike phase 2, these results can't be checked bit for bit, because the true values are irrational. Verification measures the error instead.

## How CORDIC computes these functions

CORDIC rotates a vector (x, y) through a fixed sequence of angles atan(2⁻ⁱ). Each rotation needs only a shift and an add. There are two modes:

- **Rotation:** start at (K, 0) and drive the remaining angle to zero. This gives cos θ and sin θ.
- **Vectoring:** drive y to zero. This gives the angle atan2(y, x), and x scaled by a known constant, which gives hypot.

The hyperbolic version uses atanh(2⁻ⁱ) steps and must repeat certain iterations (4, 13, 40, …). It gives e^r = cosh r + sinh r in rotation mode, and ln m = 2·atanh((m−1)/(m+1)) in vectoring mode.

## Datapath

```
unpack + special values
   │
   ├─ sin/cos ─ Payne–Hanek: m × (window of 2/π bits chosen by exponent) → quadrant q, fraction f
   ├─ exp ─────  k = round(x / ln 2), r = x − k·ln 2
   ├─ ln ──────  x = m·2^e with m ∈ [√½, √2)
   └─ atan2/hypot ─ align x, y to a common exponent, fold into the first quadrant
   │
CORDIC stages (circular or hyperbolic, one stage per clock, W-bit fixed point)
   │
reconstruct: quadrant swap and sign, add e·ln 2, add k to the exponent, scale by 1/K for hypot
   │
normalize → round_pre → round_post (reused from fp_pkg)
```

Design notes:

- **Angle units.** The Payne–Hanek output f is in units of π/2. I'll scale the CORDIC arctangent table into the same units, so the angle never needs multiplying by π/2.
- **Payne–Hanek multiply.** The 24-bit mantissa times about 64 bits of 2/π. That's a few DSP48 blocks over 2–3 pipeline stages, with the 2/π bits stored in a small ROM.
- **Small results lose relative precision in fixed point.** This is the main accuracy risk: sin of a small x, ln near 1, and atan2 with a very small y/x. For example, sin(2⁻¹⁰) with 32-bit internals has an absolute error near 2⁻³², which is about 2 ULP of the result. Planned handling:
  - a wider datapath, starting at W ≈ 40 bits;
  - short paths for tiny inputs: sin x → x and ln(1+ε) → ε;
  - measure first, then decide.
- **Result collisions.** The CORDIC latency (about 45–55 clocks) differs from the 7-clock pipelined units, so results can collide on the FPU's single result port. Plan: a small output FIFO for the CORDIC unit, with `req_ready` held low when the FIFO could overflow. This replaces the current "everything finishes in 7 clocks" assumption in `fpu.sv`.
- **Op code width.** Phase 2 used op codes 0–11, and six new ops need 12–17. The FPU's 4-bit op field becomes 5 bits. The FPU kernel's `fop` byte already has room. `BAD_ARG` changes to `fop > 17`. Protocol version becomes 0.3.

## Flags policy

| Flag | When |
|---|---|
| NV | sNaN input; sin/cos of ±∞; ln of a negative number |
| DZ | ln(±0) = −∞ |
| OF | exp overflow (x > ~88.72); hypot overflow |
| UF | tiny results (sin of tiny x, exp of very negative x), using the same after-rounding rule as phase 2 |
| NX | every result except a listed set of exact cases: sin(±0), cos(0) = 1, exp(0) = 1, ln(1) = 0, atan2(±0, +x) = ±0, hypot with a zero input |

The hardware can't detect exactness in general, for example hypot(3, 4) = 5. So the rule is the listed set, and the reference models follow the same rule.

## Verification

- **Reference 1:** Python with `mpmath` at 200+ bits, rounded once to fp32. This gives the correctly rounded answer; error is measured in ULP against it.
- **Reference 2:** C, computing in double precision with libm and rounding to float, called from the SV scoreboard through DPI. It's fast, and a second opinion.
- **Scoreboards:** check every result against the ULP budget, keep a histogram and maximum error per function, and compare flags with the policy above.
- **Directed and coverage cases:** tiny inputs, inputs near multiples of π/2, huge sin/cos arguments up to 1e38, the exp overflow and underflow thresholds, ln near 1, atan2 on the axes and in all four quadrants, hypot overflow and underflow scaling.
- **Exhaustive check on the board (stretch).** A unary function has 2³² inputs, about 51 GB of traffic over Ethernet at ~11 MB/s, which is roughly 80 minutes. (Correction, 2026-11-03: the measured rate is about 0.7 MB/s, because every request waits for its reply, so this would take about 26 hours per function. It has not been run.) The PC screens all of them with the fast C reference and rechecks only the suspicious ones with mpmath. That would prove the error bound for sin, cos, exp and ln on the real hardware, not just on samples.

## Milestones

Milestones 1–7 are done, apart from the exhaustive sweeps in milestone 6. The measured results are in [numerics.md](numerics.md#measured-accuracy) and [verification.md](verification.md#phase-3-board-results); this plan is otherwise kept as written.

1. **References and tooling:** mpmath and C references, ULP measurement, flags rule, op codes 12–17 in `spec/accel.toml`.
2. **sin/cos:** Payne–Hanek plus circular CORDIC in a standalone pyuvm block test. **Accuracy gate:** choose W, check ≤ 1 ULP, run a Vivado timing build of the unit alone. If 1 ULP can't meet timing, fall back to the 2–4 ULP budget here.
3. **atan2/hypot:** circular vectoring mode.
4. **exp/ln:** hyperbolic mode, including the repeated iterations.
5. **Integration:** 5-bit op field, CORDIC output FIFO in `fpu.sv`, kernel cycle formula (measure, then update `model.py` and `top_scoreboard.svh`), SV and pyuvm environments, full build at 100 MHz (WNS currently +0.195 ns, so watch it).
6. **Board:** `fptest` for the new ops over UDP, then the exhaustive sweeps if time allows.
7. **Docs:** measured ULP table per function in `numerics.md`, plus updates to `verification.md` and the README.
