"""ULP error measurement for the phase 3 functions.

A ULP ("unit in the last place") is the gap between two neighbouring fp32
values. Near a value v with 2^k <= |v| < 2^(k+1), the gap is 2^(k-23); in the
subnormal range it is always 2^-149. I measure error as

    err = |result - exact| / ulp(exact)

where exact is mpref.exact_value() at 256 bits. Two numbers describe a result:

* err < 0.5 means the result is the nearest fp32 value, away from exact
  ties. Even ideal rounding can cost up to 0.5 ULP; a particular result can
  have much less error. I also count equality with the rounded reference.
* err < 1 means the result is one of the two fp32 values on either side of
  the true answer ("faithfully rounded"). This is the phase 3 target written
  as "<= 1 ULP" in docs/phase3-plan.md.

For results decided by a rule (NaN, infinities, the exact list in mpref) there
is no error to measure; those must match mpref bit for bit, flags included.

UlpStats collects per-function statistics: a histogram, the worst error and
the input that produced it, and flag mismatches against the policy. Both the
standalone CORDIC testbench and tools/measure_ulp.py use it.
"""

from __future__ import annotations

import math
import random
import struct
from dataclasses import dataclass, field
from fractions import Fraction

import mpmath
import numpy as np

from mathaccel import mpref
from mathaccel._generated.spec import FpuFlag, FpuOp
from mathaccel.fp32ref import NX, is_inf, is_nan, to_fraction

# Histogram bucket upper edges, in ULPs. The last bucket catches the rest.
BUCKETS = (0.5, 0.75, 1.0, 2.0, 4.0, math.inf)


def ulp_of(v: Fraction) -> Fraction:
    """The fp32 gap at magnitude |v| (v != 0), with the subnormal floor 2^-149."""
    v = abs(v)
    k = v.numerator.bit_length() - v.denominator.bit_length()
    if Fraction(2) ** k > v:                   # bit lengths can overshoot by one
        k -= 1
    return Fraction(2) ** (max(k, -126) - 23)


def ulp_error(result_bits: int, exact: mpmath.mpf) -> float:
    """|result - exact| in ULPs of exact. Infinite or NaN results count as infinite error."""
    if is_nan(result_bits) or is_inf(result_bits):
        return math.inf
    ex = mpref.mpf_to_fraction(exact)
    if ex == 0:
        return 0.0 if result_bits & 0x7FFFFFFF == 0 else math.inf
    # The ULP is taken at the exact value. When exact overflows fp32 (exp of a
    # big number) the result is +inf and was handled above.
    return float(abs(to_fraction(result_bits) - ex) / ulp_of(ex))


def flag_names(flags: int) -> str:
    """Format the exception bits for a failure report without losing combined flags."""
    return "|".join(f.name for f in FpuFlag if flags & f) or "-"


@dataclass
class OpStats:
    """Statistics for one function."""
    n: int = 0                                  # results with a measured error
    n_rule: int = 0                             # results decided by a rule (checked bit for bit)
    hist: list[int] = field(default_factory=lambda: [0] * len(BUCKETS))
    max_err: float = 0.0
    worst: tuple[int, int, int] | None = None   # (a, b, result) at max_err
    correctly_rounded: int = 0                  # results equal to mpref's rounding
    rule_errors: int = 0                        # special-case results that differ from mpref
    over_budget: int = 0                        # measured results with err >= budget
    flag_errors: int = 0
    examples: list[str] = field(default_factory=list)


class UlpStats:
    """Checks results against mpref and keeps ULP statistics per function.

    add() returns a short problem description, or None when the result is
    within budget with the right flags, so a scoreboard can log the failure.
    """

    def __init__(self, budget: float = 1.0, max_examples: int = 10):
        """Set the error limit and cap stored examples so a large sweep has bounded diagnostic output."""
        self.budget = budget
        self.max_examples = max_examples
        self.ops: dict[int, OpStats] = {}

    def add(self, op: int, a: int, b: int, result: int, flags: int) -> str | None:
        """Check one result, record its error/flags, and return a readable explanation if it fails."""
        st = self.ops.setdefault(op, OpStats())
        exp_bits, exp_flags = mpref.evaluate(op, a, b)
        rule = mpref.special(op, a, b)
        problem = None
        if rule is not None:
            # Rule-decided result: exact match, flags included.
            st.n_rule += 1
            if (result, flags) != rule:
                st.rule_errors += 1
                problem = (f"expected {rule[0]:08x}/{flag_names(rule[1])}, "
                           f"got {result:08x}/{flag_names(flags)}")
        else:
            st.n += 1
            err = ulp_error(result, mpref.exact_value(op, a, b))
            if result == exp_bits and not math.isfinite(err):
                # Overflow: +-inf is the IEEE result when the value rounds past
                # the largest float, so it counts as correctly rounded.
                err = 0.5
            for i, edge in enumerate(BUCKETS):
                if err < edge or edge == math.inf:
                    st.hist[i] += 1
                    break
            if result == exp_bits:
                st.correctly_rounded += 1
            if err > st.max_err or st.worst is None:
                st.max_err, st.worst = err, (a, b, result)
            if err >= self.budget:
                st.over_budget += 1
                problem = f"{err:.3f} ULP (budget {self.budget}); correctly rounded is {exp_bits:08x}"
            # Flags: NX is always set here; OF/UF depend on the rounded value,
            # so compare them with the flags mpref gives for this result's
            # own magnitude rather than for the correctly rounded value.
            want = exp_flags if result == exp_bits else _flags_for(result, exp_flags)
            if flags != want:
                st.flag_errors += 1
                problem = (problem + "; " if problem else "") + \
                    f"flags {flag_names(flags)}, expected {flag_names(want)}"
        if problem and len(st.examples) < self.max_examples:
            st.examples.append(f"{FpuOp(op).name} a={a:08x} b={b:08x} -> {result:08x}: {problem}")
        return problem

    def errors(self) -> int:
        """Results over budget, plus wrong rule results and wrong flags."""
        return sum(st.over_budget + st.rule_errors + st.flag_errors for st in self.ops.values())

    def report(self) -> str:
        """A text table, one line per function, followed by example failures."""
        heads = " ".join(f"<{e:g}" if e != math.inf else ">=4" for e in BUCKETS)
        lines = [f"{'op':6s} {'measured':>9s} {'max ULP':>8s} {'corr.rnd':>8s}  histogram [{heads}]"
                 f"  rule/flag errors  worst input"]
        for op in sorted(self.ops):
            st = self.ops[op]
            cr = 100.0 * st.correctly_rounded / st.n if st.n else 100.0
            worst = f"a={st.worst[0]:08x} b={st.worst[1]:08x}" if st.worst else "-"
            lines.append(f"{FpuOp(op).name:6s} {st.n:9d} {st.max_err:8.3f} {cr:7.2f}%  "
                         f"{st.hist}  {st.rule_errors}/{st.flag_errors} (of {st.n + st.n_rule})  {worst}")
        for st in self.ops.values():
            lines += ["  " + e for e in st.examples]
        return "\n".join(lines)


def _flags_for(result: int, ref_flags: int) -> int:
    """Expected flags for a result that is not the correctly rounded value.

    The phase 3 rule is NX always, plus OF when the result is infinite and UF
    when it is tiny after rounding. A result one ULP away from the correctly
    rounded one can sit on the other side of the 2^-126 boundary, so its own
    tininess decides UF. I approximate "tiny after rounding" by the result
    itself being below 2^-126, which is exact unless the result is the
    smallest normal number; in that case I accept the reference's choice.
    """
    mag = result & 0x7FFFFFFF
    if mag == 0x00800000:
        return ref_flags
    flags = NX
    if mag < 0x00800000:
        flags |= int(FpuFlag.UF)
    if mag == 0x7F800000:
        flags |= int(FpuFlag.OF)
    return flags


# ---------------------------------------------------------------------------
# Input sets
# ---------------------------------------------------------------------------

def f32_bits(x: float) -> int:
    """Nearest fp32 pattern to a Python float."""
    return struct.unpack("<I", struct.pack("<f", x))[0]


def random_exponent_bits(rng: random.Random, e_lo: int, e_hi: int, neg: bool = True) -> int:
    """A normal fp32 with biased exponent uniform in [e_lo, e_hi] and a random mantissa."""
    s = rng.getrandbits(1) if neg else 0
    return (s << 31) | (rng.randint(e_lo, e_hi) << 23) | rng.getrandbits(23)


def near_half_pi_multiples(count: int, rng: random.Random) -> list[int]:
    """fp32 values just around k * pi/2: sin or cos of these is close to zero,
    which is the hard case for argument reduction."""
    out = []
    with mpmath.workprec(200):
        for _ in range(count):
            k = rng.choice([rng.randint(1, 64), rng.randint(1, 1 << 20), rng.getrandbits(40) + 1])
            t = float(k * mpmath.pi / 2)
            base = f32_bits(t)
            out.append((base + rng.randint(-2, 2)) & 0x7FFFFFFF)
    return out


# The fp32 inputs closest to a multiple of pi/2, found by tools/ph_worst_case.py
# with an exhaustive search over every exponent. The first is the classic
# worst case 16367173 * 2^72 (distance about 2^-29.9 quarter turns).
PH_WORST = (0x6F79BE45, 0x50A3E87F, 0x6FF9BE45, 0x5123E87F, 0x437CE5F1, 0x7079BE45)


def sincos_inputs(n: int, seed: int) -> list[int]:
    """Directed and random inputs for SIN and COS."""
    rng = random.Random(seed)
    xs: list[int] = [0x00000000, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7F800001,
                     0x00000001, 0x80400000, 0x00800000, 0x3F800000, 0xBF800000, 0x7F7FFFFF,
                     0xFF7FFFFF, 0x3FC90FDB, 0x40490FDB, 0x40C90FDB, 0x3F490FDB, 0x39800000,
                     0x397FFFFF, 0x39000000, 0x38FFFFFF, 0x4B800000, 0x4C000000, 0x5F000000]
    xs += list(PH_WORST) + [x | 0x80000000 for x in PH_WORST]
    xs += near_half_pi_multiples(max(1, n // 8), rng)
    # Angles a little way from k * pi/2, where the reduced fraction is near the
    # CORDIC's small-angle threshold (2^-13 of a quarter turn).
    with mpmath.workprec(200):
        for _ in range(max(1, n // 16)):
            k = rng.choice([rng.randint(1, 64), rng.randint(1, 1 << 20)])
            d = rng.uniform(2.0 ** -15, 2.0 ** -11) * rng.choice([-1, 1])
            xs.append(f32_bits(float((k + d) * mpmath.pi / 2)))
    while len(xs) < n:
        r = rng.random()
        if r < 0.40:
            xs.append(random_exponent_bits(rng, 115, 130))      # 2^-12 .. 2^4: ordinary angles
        elif r < 0.55:
            xs.append(random_exponent_bits(rng, 100, 118))      # around the tiny-input threshold
        elif r < 0.85:
            xs.append(random_exponent_bits(rng, 131, 254))      # large arguments
        elif r < 0.95:
            xs.append(random_exponent_bits(rng, 1, 99))         # tiny normals
        else:
            xs.append(rng.getrandbits(32))                      # anything, including NaN/subnormal
    return xs


def _pair_with_gap(rng: random.Random, gap: int, e_lo: int = 1, e_hi: int = 254) -> tuple[int, int]:
    """Two normal floats whose biased exponents differ by gap (first minus second), random signs."""
    e1 = rng.randint(max(e_lo, e_lo + gap), min(e_hi, e_hi + gap))
    e2 = e1 - gap
    a = (rng.getrandbits(1) << 31) | (e1 << 23) | rng.getrandbits(23)
    b = (rng.getrandbits(1) << 31) | (e2 << 23) | rng.getrandbits(23)
    return a, b


def atan2_hypot_inputs(n: int, seed: int) -> list[tuple[int, int]]:
    """Directed and random operand pairs for ATAN2 (a = y, b = x) and HYPOT.

    The interesting places: zeros and infinities on either side (the Annex F
    table), all four quadrants, |y| close to |x| (the octant fold), exponent
    gaps around the atan2 y/x threshold (13, 14, 15), huge gaps where the
    smaller input vanishes, subnormals, and values near overflow for hypot.
    """
    rng = random.Random(seed)
    specials = [0x00000000, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7F800001,
                0x3F800000, 0xBF800000, 0x00000001, 0x807FFFFF, 0x7F7FFFFF, 0xFF7FFFFF]
    pairs = [(a, b) for a in specials for b in specials]
    pairs += [(f32_bits(3.0), f32_bits(4.0)), (f32_bits(1.0), f32_bits(1.0)),
              (f32_bits(-1.0), f32_bits(-1.0)), (0x7F7FFFFF, 0x7F7FFFFF), (0x00000003, 0x00000004)]
    while len(pairs) < n:
        r = rng.random()
        if r < 0.30:
            pairs.append(_pair_with_gap(rng, rng.randint(-12, 12), 100, 154))   # ordinary
        elif r < 0.45:
            pairs.append(_pair_with_gap(rng, rng.choice([-15, -14, -13, 13, 14, 15])))  # threshold
        elif r < 0.55:
            a = rng.getrandbits(32) & 0x7FFFFFFF                              # |y| ~ |x|
            pairs.append(((rng.getrandbits(1) << 31) | a,
                          (rng.getrandbits(1) << 31) | ((a + rng.randint(-3, 3)) & 0x7FFFFFFF)))
        elif r < 0.70:
            pairs.append(_pair_with_gap(rng, rng.randint(-60, 60)))               # wide gaps
        elif r < 0.78:
            pairs.append(_pair_with_gap(rng, rng.randint(-2, 2), 250, 254))       # near overflow
        elif r < 0.88:
            # Subnormals against small normals or other subnormals.
            a = (rng.getrandbits(1) << 31) | rng.getrandbits(23)
            b = (rng.getrandbits(1) << 31) | (rng.randint(0, 30) << 23) | rng.getrandbits(23)
            pairs.append((a, b) if rng.random() < 0.5 else (b, a))
        else:
            pairs.append((rng.getrandbits(32), rng.getrandbits(32)))             # anything
    return pairs


def exp_inputs(n: int, seed: int) -> list[int]:
    """Directed and random inputs for EXP.

    The edges: |x| around 2^-25 (below it e^x rounds to 1), the overflow
    threshold near 88.72, the end of the normal range near -87.34, the last
    nonzero result near -103.97, and |x| = 128 where the rule takes over.
    """
    rng = random.Random(seed)
    xs = [0x00000000, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7F800001, 0x00000001,
          0x3F800000, 0xBF800000, 0x33000000, 0x32FFFFFF, 0xB3000000, 0xB2FFFFFF,
          0x42B17217, 0x42B17218, 0x42B17219, 0xC2AEAC4F, 0xC2AEAC50, 0xC2CFF1B4, 0xC2CFF1B5,
          0xC2CFF1B6, 0x42FFFFFF, 0x43000000, 0xC2FFFFFF, 0xC3000000, 0x7F7FFFFF, 0xFF7FFFFF]
    edges = (88.7228, -87.3365, -103.972, 128.0, -128.0)
    while len(xs) < n:
        r = rng.random()
        if r < 0.40:
            xs.append(f32_bits(rng.uniform(-104.0, 89.0)))                 # the whole useful range
        elif r < 0.60:
            xs.append(random_exponent_bits(rng, 98, 127))                  # small |x|, around 2^-25
        elif r < 0.75:
            xs.append((f32_bits(rng.choice(edges)) + rng.randint(-200, 200)) & 0xFFFFFFFF)
        elif r < 0.85:
            # Near multiples of ln2/2, where k = round(x/ln2) changes.
            xs.append(f32_bits((rng.randint(-300, 254) + 0.5) * 0.6931471805599453 / 2
                               + rng.uniform(-1e-6, 1e-6)))
        else:
            xs.append(rng.getrandbits(32))
    return xs


def ln_inputs(n: int, seed: int) -> list[int]:
    """Directed and random inputs for LN: every exponent, subnormals, values
    just around 1 (where ln x ~ x - 1 is small), around the near-1 threshold
    |x - 1| = 2^-12, and around sqrt(2) and sqrt(1/2), where the significand
    is folded."""
    rng = random.Random(seed)
    xs = [0x00000000, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7F800001, 0x00000001,
          0x3F800000, 0x3F800001, 0x3F7FFFFF, 0xBF800000, 0x007FFFFF, 0x00800000, 0x7F7FFFFF,
          0x3FB504F3, 0x3FB504F4, 0x3F3504F3, 0x3F3504F4, 0x40000000, 0x3F000000]
    while len(xs) < n:
        r = rng.random()
        if r < 0.35:
            xs.append(rng.getrandbits(31) & 0x7F7FFFFF | (rng.randint(1, 254) << 23) & 0x7F800000)
        elif r < 0.55:
            xs.append((0x3F800000 + rng.randint(-(1 << 13), 1 << 12)) & 0x7FFFFFFF)   # |x - 1| < ~2^-10
        elif r < 0.70:
            # Around the near-1 threshold |x - 1| = 2^-12 on both sides of 1.
            xs.append(rng.choice([0x3F800800, 0x3F7FF000]) + rng.randint(-64, 64))
        elif r < 0.80:
            xs.append(rng.choice([0x3FB504F3, 0x3F3504F3]) + rng.randint(-1000, 1000))
        elif r < 0.90:
            xs.append(rng.getrandbits(23))                                   # subnormals
        else:
            xs.append(rng.getrandbits(32))
    return xs


def screen(op: int, pairs: list[tuple[int, int]], results: list[tuple[int, int]], c,
           budget: float = 1.0) -> UlpStats:
    """Measure results against the C reference first, and mpmath only where they differ.

    pairs are the (a, b) operands, results the (result, flags) under test (from
    the model or the board) and c a loaded cref.CRef. A result equal to C's is
    counted as correctly rounded, so only the ones that differ pay for an exact
    mpmath measurement. That makes a million results take about a minute. The
    agreeing samples rely on the C reference being correctly rounded, so this
    is a fast measurement, not a proof of the error bound.
    """
    st = UlpStats(budget=budget)
    cr, cf = c.eval_many(op, np.array([a for a, _ in pairs], dtype=np.uint32),
                         np.array([b for _, b in pairs], dtype=np.uint32))
    agree = 0
    ops = st.ops.setdefault(op, OpStats())
    for (a, b), (r, f), r2, f2 in zip(pairs, results, cr, cf, strict=True):
        if (r, f) != (int(r2), int(f2)):
            st.add(op, a, b, r, f)
        elif mpref.special(op, a, b) is not None:
            ops.n_rule += 1                     # rule-decided and matching: not a measured error
        else:
            agree += 1
    # Agreeing results are correctly rounded, so their error is at most 0.5.
    ops.n += agree
    ops.correctly_rounded += agree
    ops.hist[0] += agree
    return st


def op_inputs(op: int, n: int, seed: int) -> list[tuple[int, int]]:
    """(a, b) operand pairs for any phase 3 op; b is 0 for unary ops."""
    if op in (FpuOp.ATAN2, FpuOp.HYPOT):
        return atan2_hypot_inputs(n, seed)
    if op == FpuOp.EXP:
        return [(x, 0) for x in exp_inputs(n, seed)]
    if op == FpuOp.LN:
        return [(x, 0) for x in ln_inputs(n, seed)]
    return [(x, 0) for x in sincos_inputs(n, seed)]
