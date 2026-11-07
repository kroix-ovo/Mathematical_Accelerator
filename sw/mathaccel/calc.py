"""Calculus tools that run on the expression unit (phase 4, milestone 5).

These are what I actually call for class work: integrate_nd, line_integral,
surface_flux, grad, div, curl_at and root_find. Everything symbolic stays on the
laptop. SymPy differentiates and substitutes here, the compiler turns the result
into bytecode, and the FPGA only evaluates what I set up.

How each kind of problem reaches the hardware:

* The compiler and integral_run only know boxes with constant limits, so every
  integral gets rewritten as one. A line integral is already a box in its
  parameter t: F(r(t)) dot r'(t) dt. A surface integral is a box in (u, v):
  F(r(u,v)) dot (r_u x r_v) du dv. A limit that depends on another variable is
  mapped onto 0..1, which adds a Jacobian to the integrand (to_unit_box).
* Integrals use reduce mode: one exact sum and nothing per point crosses the link.
* grad, div, curl_at and root_find use map mode, because they want values at
  points. A program has one output, so each component is its own run.

Map mode writes a result word and a flags word for every point, on top of the
tables, and all of it has to fit in the 65,536-word scratchpad. map_eval splits
a big request into as many runs as it needs.

Results are never plain floats. NaN, infinity and suspicious flags have to be
visible, so a run returns a Scalar or Samples. float(result) raises instead of
quietly handing a NaN to the next line of a calculation.
"""

# I keep the calculus setup here on the host. I turn the requested geometry
# into expressions and tables, then let the FPGA evaluate the fp32 arithmetic.
# I keep flags, convergence and timing beside the answer, since a plausible
# number alone doesn't tell me whether the calculation worked.

from __future__ import annotations

import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import sympy as sp

from mathaccel import evu_model
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuFlag
from mathaccel.compiler.evu_isa import EvuError, Run, Table
from mathaccel.compiler.quadrature import Axis, float_bits, integral_run
from mathaccel.compiler.sympy_to_evu import CompileError, compile_expr
from mathaccel.driver.device import EVU_MAX_SECONDS, Device, EvuReply, RunTooLong

# Flags that say something went wrong. NX is left out because nearly every
# operation sets it. UF only earns a note: a tiny term rounding away is normal in
# an integrand that decays, and it can't move the sum by more than a rounding.
_TROUBLE = {
    FpuFlag.NV: "invalid operation at some point (0/0, inf-inf, sqrt or ln of a negative number, or a signaling NaN)",
    FpuFlag.DZ: "division by zero at some point",
    FpuFlag.OF: "overflow at some point",
}
_TROUBLE_MASK = FpuFlag.NV | FpuFlag.DZ | FpuFlag.OF
_ULP_AT_ONE = 2.0**-23
# Warn before a run that makes me sit and wait, since it can't be stopped.
WARN_SECONDS = 5.0


class NumericalError(ArithmeticError):
    """A result that isn't an ordinary number was used as if it were one."""


class RootError(ValueError):
    """root_find was given something it can't start from."""


def f32(bits: int) -> float:
    """The value of an fp32 pattern as a double, which holds it exactly."""
    return evu_model.fp32_value(bits)


def ulp_of(value: float) -> float:
    """The gap between adjacent fp32 values at this size."""
    return float(np.spacing(np.float32(abs(value)))) if value != 0 else 2.0**-149


def flag_text(flags: int) -> list[str]:
    """Plain-language lines for the flags that mean trouble."""
    return [text for flag, text in _TROUBLE.items() if flags & flag]


@dataclass(frozen=True)
class Scalar:
    """One fp32 result with the flags that came with it.

    flags belong to this value. aux is the OR of every point behind it, which
    can show trouble that a finite final value hides.
    """
    bits: int
    flags: int = 0
    aux: int = 0

    @property
    def value(self) -> float:
        """The number, which may be NaN or infinite. Check kind or use float()."""
        return f32(self.bits)

    @property
    def kind(self) -> str:
        """'number', 'nan', '+inf' or '-inf'."""
        v = self.value
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "+inf" if v > 0 else "-inf"
        return "number"

    @property
    def problems(self) -> list[str]:
        """Everything about this result that I wouldn't want to miss."""
        lines = {"nan": ["the result is NaN, not a number"], "+inf": ["the result is +infinity"],
                 "-inf": ["the result is -infinity"], "number": []}[self.kind]
        return lines + flag_text(self.flags | self.aux)

    @property
    def clean(self) -> bool:
        """A finite number with no trouble flags anywhere behind it."""
        return not self.problems

    def __float__(self) -> float:
        if self.kind != "number":
            raise NumericalError("; ".join(self.problems))
        return self.value

    def __str__(self) -> str:
        shown = {"nan": "NaN", "+inf": "+inf", "-inf": "-inf"}.get(self.kind, f"{self.value:.8g}")
        return shown if self.clean else f"{shown}  [{'; '.join(self.problems)}]"


@dataclass
class Integral:
    """The answer to one integral, and how much to believe it.

    value comes from the finest rule that ran. error_estimate is how far that was
    from the rule below it, which measures the rule's truncation and not fp32
    rounding. converged is None if no comparison ran.
    """
    value: Scalar
    points: int
    instructions: int
    cycles: int
    seconds: float
    wall_seconds: float
    error_estimate: float | None = None
    converged: bool | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.value.clean and self.converged is not False

    def __float__(self) -> float:
        return float(self.value)

    def __str__(self) -> str:
        lines = [f"{self.value}"]
        if self.error_estimate is not None:
            lines.append(f"rule change moved it by {self.error_estimate:.3g}"
                         + ("" if self.converged else "  NOT CONVERGED, raise --panels or --order"))
        lines.append(f"{self.points:,} points, {self.instructions} instructions, kernel {self.seconds * 1e3:.3f} ms, "
                     f"wall {self.wall_seconds * 1e3:.2f} ms")
        return "\n".join(lines + self.notes)


@dataclass
class Samples:
    """A map run: one row per point, one column per output.

    bits and flags are (points, outputs) arrays of raw words. A tensor grid keeps
    its shape, so component(k) gives back an n0 x n1 x n2 array.
    """
    bits: np.ndarray
    flags: np.ndarray
    shape: tuple[int, ...]
    cycles: int = 0
    runs: int = 0
    wall_seconds: float = 0.0
    out_of_bounds: int = 0

    @property
    def values(self) -> np.ndarray:
        """Results as doubles. Look at bad before trusting these."""
        return self.bits.view(np.float32).astype(np.float64)

    def component(self, k: int) -> np.ndarray:
        """Output k, in the shape of the grid."""
        return self.values[:, k].reshape(self.shape)

    @property
    def bad(self) -> np.ndarray:
        """True for each point with a NaN, an infinity or a trouble flag in any output."""
        return (~np.isfinite(self.values) | ((self.flags & _TROUBLE_MASK) != 0)).any(axis=1)

    @property
    def clean(self) -> bool:
        return not self.bad.any()

    def scalars(self, point: int = 0) -> list[Scalar]:
        """One point's outputs with their flags."""
        return [Scalar(int(b), int(f)) for b, f in zip(self.bits[point], self.flags[point], strict=True)]

    def __str__(self) -> str:
        n = len(self.bits)
        if n == 1:
            return "(" + ", ".join(str(s) for s in self.scalars()) + ")"
        lines = [f"{n:,} points, {self.bits.shape[1]} output(s) each, {self.runs} run(s)"]
        if not self.clean:
            first = int(np.argmax(self.bad))
            lines.append(f"{int(self.bad.sum()):,} points are NaN, infinite or flagged, the first is point {first}: "
                         + ", ".join(str(s) for s in self.scalars(first)))
        return "\n".join(lines)


@dataclass
class Root:
    """What root_find found. converged is False if it stopped for another reason,
    and reason says which. Near a root fp32 evaluates f with error of its own, so
    a sign can flip a few ULP early: the x here is as good as f's own rounding."""
    x: Scalar
    fx: Scalar
    iterations: int
    converged: bool
    reason: str
    method: str
    cycles: int
    wall_seconds: float
    bracket: tuple[float, float] | None = None

    @property
    def clean(self) -> bool:
        return self.converged and self.x.clean

    def __float__(self) -> float:
        if not self.converged:
            raise NumericalError(self.reason)
        return float(self.x)

    def __str__(self) -> str:
        state = "converged" if self.converged else "NOT CONVERGED"
        return (f"x = {self.x}   f(x) = {self.fx}\n"
                f"{self.method}, {self.iterations} iterations, {state}: {self.reason}, "
                f"kernel {self.cycles / spec.CLK_HZ * 1e3:.3f} ms, wall {self.wall_seconds * 1e3:.1f} ms")


# ---- turning problems into boxes -------------------------------------------------

Limit = tuple[sp.Symbol, sp.Expr, sp.Expr]


def _sym(x) -> sp.Expr:
    return sp.sympify(x)


def _with_params(expr, params: dict | None) -> sp.Expr:
    """I give any named constants their values before compiling."""
    return _sym(expr).subs(params) if params else _sym(expr)


def to_unit_box(expr, limits: Sequence[Limit]) -> tuple[sp.Expr, list[Limit]]:
    """I substitute variable limits onto a unit interval and include the Jacobian.

    limits follow SymPy's order: the first entry is the innermost integral, and
    a limit may only use variables named later in the list. For x from a(y) to
    b(y), I put x = a + (b - a) s with s on 0..1, which multiplies the integrand
    by the Jacobian b - a. A triangle or a disk then becomes a plain grid.
    """
    expr = _sym(expr)
    box: list[Limit] = []
    for k, (s, a, b) in enumerate(limits):
        a, b = _sym(a), _sym(b)
        if a.has(sp.oo, -sp.oo, sp.zoo) or b.has(sp.oo, -sp.oo, sp.zoo):
            raise CompileError(f"{s} has an infinite limit. Gauss-Legendre needs a finite one: truncate it, or "
                               "substitute a variable that maps it onto a finite range")
        later = {t for t, _, _ in limits[k + 1:]}
        extra = (a.free_symbols | b.free_symbols) - later
        if extra:
            raise CompileError(f"the limits of {s} use {sorted(map(str, extra))}, which aren't variables of an outer "
                               "integral. Outer integrals go later in the list.")
        if a.free_symbols or b.free_symbols:
            u = sp.Dummy(f"{s}_unit", real=True)
            expr = expr.subs(s, a + (b - a) * u) * (b - a)
            box.append((u, sp.Integer(0), sp.Integer(1)))
        else:
            box.append((s, a, b))
    return expr, box


def _per_axis(value: int | Sequence[int], count: int, name: str) -> list[int]:
    """I accept one number for all axes or one per axis."""
    values = [value] * count if isinstance(value, int) else list(value)
    if len(values) != count:
        raise EvuError(f"{name} lists {len(values)} values for {count} axes")
    return values


def _reduce(dev: Device, run: Run, max_seconds: float) -> EvuReply:
    """I run a reduce program, warning first if it will take a while."""
    _warn_if_slow(run)
    return dev.evu_run(run, max_seconds=max_seconds)


def _warn_if_slow(run: Run) -> None:
    low = evu_model.cycle_bounds(run)[0] / spec.CLK_HZ
    if low > WARN_SECONDS:
        warnings.warn(f"this run takes at least {low:.1f} s and can't be cancelled", stacklevel=3)


def integrate_box(dev: Device, expr, limits: Sequence[Limit], *, panels: int | Sequence[int] = 1,
                  order: int | Sequence[int] = 16, check: bool = True, rel_tol: float = 1e-6, abs_tol: float = 0.0,
                  max_seconds: float = EVU_MAX_SECONDS) -> Integral:
    """I run the integral, then refine the rule unless the caller turns that check off.

    Doubling the panels on every axis is how I see whether a rule has converged.
    Milestone 1 showed why that's needed: a 1,024-point rule on the plate problem
    was off by 0.18% in every precision, and nothing in the number itself shows
    that. An answer near zero has no size to compare against, so pass abs_tol
    for an integral that cancels out.
    """
    expr, box = to_unit_box(expr, limits)
    ps = _per_axis(panels, len(box), "panels")
    os = _per_axis(order, len(box), "order")
    if len(box) > spec.EVU_DIMS:
        raise EvuError(f"{len(box)} variables, the hardware grid has {spec.EVU_DIMS} dimensions")

    def rule(scale: int) -> Run:
        return integral_run(expr, [Axis(s, a, b, p * scale, o) for (s, a, b), p, o in zip(box, ps, os, strict=True)])

    run = rule(1)
    reply = _reduce(dev, run, max_seconds)
    wall = reply.total_seconds
    value = Scalar(int(reply.results[0]), int(reply.flags[0]), reply.aux)
    result = Integral(value, run.points, len(run.program.words), reply.cycles, reply.seconds, wall)
    if not reply.in_bounds:
        result.notes.append(f"cycles {reply.cycles} fell outside the expected {reply.bounds}")
    if value.kind != "number" or not check:
        return result

    # The finer rule may not fit. That isn't an error: I still have an answer,
    # I just can't say whether it converged.
    try:
        fine_run = rule(2)
        fine = _reduce(dev, fine_run, max_seconds)
    except (EvuError, RunTooLong) as e:
        result.notes.append(f"no convergence check: {e}")
        return result
    fine_value = Scalar(int(fine.results[0]), int(fine.flags[0]), fine.aux)
    out = Integral(fine_value, fine_run.points, len(fine_run.program.words), fine.cycles, fine.seconds,
                   wall + fine.total_seconds, notes=result.notes)
    if not fine.in_bounds:
        out.notes.append(f"cycles {fine.cycles} fell outside the expected {fine.bounds}")
    if fine_value.kind == "number":
        out.error_estimate = abs(fine_value.value - value.value)
        out.converged = out.error_estimate <= max(rel_tol * abs(fine_value.value), abs_tol)
        if out.converged is False and abs(fine_value.value) < 1e-3:
            out.notes.append("the answer is close to zero, so a relative test may be too strict: try abs_tol")
    return out


def integrate_nd(dev: Device, expr, limits: Sequence[Limit], *, params: dict | None = None, **options) -> Integral:
    """I substitute named parameters and pass the requested limits to the box setup.
    and an inner limit may depend on outer variables. See integrate_box for the
    options."""
    return integrate_box(dev, _with_params(expr, params), limits, **options)


def _coordinates(exprs: Sequence, count: int, coords: Sequence | None) -> list[sp.Symbol]:
    """The coordinate symbols. By default these are x, y, z, matched by name, so
    a symbol with assumptions such as real=True is found too."""
    if coords is not None:
        if len(coords) != count:
            raise EvuError(f"{len(coords)} coordinates for {count} components")
        return list(coords)
    names = {}
    for e in exprs:
        names |= {s.name: s for s in _sym(e).free_symbols}
    return [names.get(n, sp.Symbol(n)) for n in "xyz"[:count]]


def _is_vector(field_expr) -> bool:
    return isinstance(field_expr, (tuple, list, sp.Tuple, sp.MatrixBase))


def line_integral(dev: Device, field_expr, curve: Sequence, limit: Limit, *, coords: Sequence | None = None,
                  params: dict | None = None, **options) -> Integral:
    """I substitute the curve and include its tangent or arc-length factor.

    A vector field gives the work integral, F(r(t)) dot r'(t) dt. A scalar gives
    f(r(t)) |r'(t)| dt, the integral with respect to arc length.
    """
    t = limit[0]
    r = [_with_params(c, params) for c in curve]
    dr = [sp.diff(c, t) for c in r]
    fields = list(field_expr) if _is_vector(field_expr) else [field_expr]
    fields = [_with_params(f, params) for f in fields]
    xs = _coordinates(fields, len(r), coords)
    on_curve = dict(zip(xs, r, strict=True))
    if _is_vector(field_expr):
        if len(fields) != len(r):
            raise EvuError(f"a field of {len(fields)} components on a curve of {len(r)}")
        integrand = sum(f.subs(on_curve, simultaneous=True) * d for f, d in zip(fields, dr, strict=True))
    else:
        integrand = fields[0].subs(on_curve, simultaneous=True) * sp.sqrt(sum(d**2 for d in dr))
    return integrate_box(dev, integrand, [limit], **options)


def surface_flux(dev: Device, field_expr, surface: Sequence, u_limit: Limit, v_limit: Limit, *,
                 coords: Sequence | None = None, params: dict | None = None, **options) -> Integral:
    """I use the surface cross product to carry its area and orientation into the integrand.

    A vector field gives the flux, F(r) dot (r_u x r_v) du dv, so the normal
    points along r_u x r_v: swap u and v to flip it. A scalar field gives
    f(r) |r_u x r_v| du dv. u's limits may depend on v, as in to_unit_box.
    """
    u, v = u_limit[0], v_limit[0]
    r = sp.Matrix([_with_params(c, params) for c in surface])
    if len(r) != 3:
        raise EvuError("a surface needs three components")
    normal = r.diff(u).cross(r.diff(v))
    fields = list(field_expr) if _is_vector(field_expr) else [field_expr]
    fields = [_with_params(f, params) for f in fields]
    xs = _coordinates(fields, 3, coords)
    on_surface = dict(zip(xs, r, strict=True))
    if _is_vector(field_expr):
        if len(fields) != 3:
            raise EvuError("flux needs a field of three components")
        integrand = sum(f.subs(on_surface, simultaneous=True) * n for f, n in zip(fields, normal, strict=True))
    else:
        integrand = fields[0].subs(on_surface, simultaneous=True) * sp.sqrt(normal.dot(normal))
    return integrate_box(dev, integrand, [u_limit, v_limit], **options)


# ---- map mode ---------------------------------------------------------------------

def _points_per_run(program_words: int, tables: int) -> int:
    """I reserve table, result and flag words before deciding how many points fit.
    plus the result word and flags word that map mode can't do without."""
    free = spec.SCRATCH_WORDS - spec.EVU_DESC_WORDS - program_words
    return free // (tables + 2)


def _used(program_expr: sp.Expr, coords: Sequence[sp.Symbol]) -> list[int]:
    """Which coordinates an expression reads. The rest need no table at all."""
    free = program_expr.free_symbols
    return [k for k, c in enumerate(coords) if c in free]


def _map_list(dev: Device, expr: sp.Expr, coords: list, points: np.ndarray, max_seconds: float
              ) -> tuple[np.ndarray, np.ndarray, list[EvuReply]]:
    """I split the point list into scratchpad-sized chunks and preserve their output order."""
    used = _used(expr, coords)
    table_of = {coords[k]: i for i, k in enumerate(used)}
    program = compile_expr(expr, table_of)
    size = len(program.words) + len(program.constants)
    step = min(_points_per_run(size, len(used)), (1 << 16) - 1)
    bits = np.zeros(len(points), dtype=np.uint32)
    flags = np.zeros(len(points), dtype=np.uint32)
    replies = []
    for start in range(0, len(points), step):
        part = points[start:start + step]
        tables = [Table(str(coords[k]), float_bits(part[:, k]), 0) for k in used]
        run = Run(program, tables, (len(part),), reduce=False)
        _warn_if_slow(run)
        reply = dev.evu_run(run, max_seconds=max_seconds)
        bits[start:start + len(part)] = reply.results
        flags[start:start + len(part)] = reply.flags
        replies.append(reply)
    return bits, flags, replies


def _map_grid(dev: Device, expr: sp.Expr, coords: list, axes: list[np.ndarray], max_seconds: float
              ) -> tuple[np.ndarray, np.ndarray, list[EvuReply]]:
    """I cut the slowest grid axis into chunks so the flattened outputs join in grid order.
    first axis, which is the slowest in grid order, so the pieces just join up."""
    used = _used(expr, coords)
    table_of = {coords[k]: i for i, k in enumerate(used)}
    program = compile_expr(expr, table_of)
    rest = int(np.prod([len(a) for a in axes[1:]], dtype=np.int64)) if len(axes) > 1 else 1
    other = sum(len(a) for k, a in enumerate(axes) if k in used and k > 0)
    free = spec.SCRATCH_WORDS - spec.EVU_DESC_WORDS - len(program.words) - len(program.constants) - other
    step = free // (2 * rest + (1 if 0 in used else 0))
    if step < 1:
        raise EvuError(f"one slice of this grid is {rest:,} points, which with its result and flag words can't fit "
                       f"in {spec.SCRATCH_WORDS:,} scratchpad words. Use a smaller grid.")
    step = min(step, (1 << 16) - 1)
    bits, flags, replies = [], [], []
    for start in range(0, len(axes[0]), step):
        first = axes[0][start:start + step]
        pieces = [first] + axes[1:]
        tables = [Table(str(coords[k]), float_bits(pieces[k]), k) for k in used]
        run = Run(program, tables, tuple(len(a) for a in pieces), reduce=False)
        _warn_if_slow(run)
        reply = dev.evu_run(run, max_seconds=max_seconds)
        bits.append(reply.results)
        flags.append(reply.flags)
        replies.append(reply)
    return np.concatenate(bits), np.concatenate(flags), replies


def map_eval(dev: Device, exprs: Sequence, coords: Sequence, *, at=None, grid: Sequence | None = None,
             params: dict | None = None, max_seconds: float = EVU_MAX_SECONDS) -> Samples:
    """I evaluate components at listed points or a tensor grid, splitting runs when needed.

    at is one point or a list of points, each with one value per coordinate.
    grid is one array per coordinate. Exactly one of them must be given. Each
    expression is one or more runs: one because a program has one output, more
    if the points don't fit the scratchpad in one go.
    """
    coords = list(coords)
    if (at is None) == (grid is None):
        raise EvuError("give either at= (a list of points) or grid= (one array per coordinate)")
    if len(coords) > spec.EVU_DIMS:
        raise EvuError(f"{len(coords)} coordinates, the limit is {spec.EVU_DIMS}")
    outputs = [_with_params(e, params) for e in exprs]
    if at is not None:
        points = np.atleast_2d(np.asarray(at, dtype=np.float64))
        if points.shape[1] != len(coords):
            raise EvuError(f"points have {points.shape[1]} values, there are {len(coords)} coordinates")
        shape = (len(points),)
        done = [_map_list(dev, e, coords, points, max_seconds) for e in outputs]
    else:
        axes = [np.atleast_1d(np.asarray(a, dtype=np.float64)) for a in grid]
        if len(axes) != len(coords):
            raise EvuError(f"{len(axes)} grid axes for {len(coords)} coordinates")
        shape = tuple(len(a) for a in axes)
        done = [_map_grid(dev, e, coords, axes, max_seconds) for e in outputs]
    replies = [r for _, _, part in done for r in part]
    return Samples(np.stack([b for b, _, _ in done], axis=1), np.stack([f for _, f, _ in done], axis=1), shape,
                   sum(r.cycles for r in replies), len(replies), sum(r.total_seconds for r in replies),
                   sum(not r.in_bounds for r in replies))


def grad(dev: Device, expr, coords: Sequence, **where) -> Samples:
    """I differentiate on the host, then ask the EVU to sample each gradient component."""
    return map_eval(dev, [sp.diff(_sym(expr), c) for c in coords], coords, **where)


def div(dev: Device, field_expr: Sequence, coords: Sequence, **where) -> Samples:
    """I form the divergence on the host and sample its scalar value on the EVU."""
    if len(field_expr) != len(coords):
        raise EvuError(f"{len(field_expr)} components for {len(coords)} coordinates")
    total = sum(sp.diff(_sym(f), c) for f, c in zip(field_expr, coords, strict=True))
    return map_eval(dev, [total], coords, **where)


def curl_at(dev: Device, field_expr: Sequence, coords: Sequence, **where) -> Samples:
    """I form the curl on the host; a plane field only needs its z component.
    gives one output, the z component."""
    f = [_sym(c) for c in field_expr]
    if len(f) != len(coords) or len(f) not in (2, 3):
        raise EvuError("curl needs a field and coordinates of two or three components")
    if len(f) == 2:
        (x, y), (p, q) = coords, f
        return map_eval(dev, [sp.diff(q, x) - sp.diff(p, y)], coords, **where)
    (x, y, z), (p, q, r) = coords, f
    return map_eval(dev, [sp.diff(r, y) - sp.diff(q, z), sp.diff(p, z) - sp.diff(r, x), sp.diff(q, x) - sp.diff(p, y)],
                    coords, **where)


# ---- root finding ---------------------------------------------------------------

class _Point:
    """One program loaded on the board, evaluated at one x at a time.

    The image only goes across once. Each evaluation then rewrites the single
    table word and runs again, so a step costs three round trips: write x, RUN,
    read the result and flags. That is what makes a host-driven loop tolerable.
    """

    def __init__(self, dev: Device, expr: sp.Expr, x: sp.Symbol, base: int) -> None:
        program = compile_expr(expr, {x: 0})
        self.dev = dev
        self.run = Run(program, [Table(str(x), float_bits(np.zeros(1)), 0)], (1,), reduce=False)
        self.bounds = evu_model.cycle_bounds(self.run)
        self.image = dev.evu_load(self.run, base)
        self.slot = int(self.image.words[spec.EVU_DESC_TABLE]) & (spec.SCRATCH_WORDS - 1)
        self.end = self.image.f + 1
        self.cycles = 0

    def __call__(self, x: float) -> Scalar:
        self.dev.write_words(self.slot, float_bits(np.array([x])))
        done = self.dev.evu_start(self.image, self.bounds)
        self.cycles += done.cycles
        result, flags = self.dev.read_words(self.image.r, 2)     # r and f sit next to each other
        return Scalar(int(result), int(flags), done.aux)


def _fp32(x: float) -> float:
    """I round x to what the board can actually receive before testing the next step."""
    return float(np.float32(x))


def _bisect(f: _Point, a: float, b: float, xtol: float, max_iter: int) -> tuple:
    """I keep halving the sign-change bracket until the fp32 grid or tolerance stops me."""
    lo, hi = _fp32(min(a, b)), _fp32(max(a, b))
    f_lo, f_hi = f(lo), f(hi)
    for name, s in (("the low end", f_lo), ("the high end", f_hi)):
        if s.kind != "number":
            raise RootError(f"f isn't a number at {name} of the bracket: {s}")
    if f_lo.value == 0:
        return lo, f_lo, 0, True, "f is exactly zero at the low end", (lo, hi)
    if f_hi.value == 0:
        return hi, f_hi, 0, True, "f is exactly zero at the high end", (lo, hi)
    if (f_lo.value > 0) == (f_hi.value > 0):
        raise RootError(f"f has the same sign at both ends: f({lo}) = {f_lo}, f({hi}) = {f_hi}. Bisection needs a "
                        "sign change, so pick another bracket or use newton.")
    low_negative = f_lo.value < 0
    for n in range(1, max_iter + 1):
        mid = _fp32((lo + hi) / 2)
        if mid in (lo, hi):
            best, value = (lo, f_lo) if abs(f_lo.value) <= abs(f_hi.value) else (hi, f_hi)
            return best, value, n - 1, True, "the bracket is two adjacent fp32 numbers", (lo, hi)
        f_mid = f(mid)
        if f_mid.kind != "number":
            return mid, f_mid, n, False, f"f isn't a number at x = {mid}", (lo, hi)
        if f_mid.value == 0:
            return mid, f_mid, n, True, "f is exactly zero there", (lo, hi)
        if (f_mid.value < 0) == low_negative:
            lo, f_lo = mid, f_mid
        else:
            hi, f_hi = mid, f_mid
        if hi - lo <= xtol:
            best, value = (lo, f_lo) if abs(f_lo.value) <= abs(f_hi.value) else (hi, f_hi)
            return best, value, n, True, f"the bracket is within {xtol:g}", (lo, hi)
    best, value = (lo, f_lo) if abs(f_lo.value) <= abs(f_hi.value) else (hi, f_hi)
    return best, value, max_iter, False, f"still not there after {max_iter} steps", (lo, hi)


def _newton(f: _Point, df: _Point, x0: float, xtol: float, max_iter: int) -> tuple:
    """I compute host-side Newton steps from the fp32 function and derivative samples.

    The step is worked out on the host in double precision, from fp32 values of
    f and f'. I stop when a step is within two fp32 spacings of x, since a smaller
    step than that is lost when x goes back to the board.
    """
    x = _fp32(x0)
    for n in range(1, max_iter + 1):
        fx = f(x)
        if fx.kind != "number":
            return x, fx, n, False, f"f isn't a number at x = {x}", None
        if fx.value == 0:
            return x, fx, n, True, "f is exactly zero there", None
        slope = df(x)
        if slope.kind != "number":
            return x, fx, n, False, f"f' isn't a number at x = {x}: {slope}", None
        if slope.value == 0:
            return x, fx, n, False, f"f' is zero at x = {x}, so Newton has nowhere to go", None
        step = fx.value / slope.value
        new = _fp32(x - step)
        if not np.isfinite(new):
            return x, fx, n, False, f"the step from x = {x} left the fp32 range", None
        if abs(step) <= max(xtol, 2 * ulp_of(x)):
            return new, f(new), n, True, "the last step was within rounding of x", None
        x = new
    return x, f(x), max_iter, False, f"still not there after {max_iter} steps", None


def root_find(dev: Device, expr, x: sp.Symbol, a: float, b: float | None = None, *, method: str = "bisect",
              xtol: float = 0.0, max_iter: int = 200, params: dict | None = None) -> Root:
    """I keep the iteration on the host and use loaded EVU programs to evaluate each step.

    bisect needs a bracket, a and b, with a sign change. Each step halves it, so
    it takes about 40 steps to run out of fp32 numbers. It can't fail once it has
    a sign change. newton starts from a alone, and also evaluates f', which SymPy
    derives. It usually takes far fewer steps and can wander off, in which case
    converged is False and reason says why.
    """
    expr = _with_params(expr, params)
    extra = expr.free_symbols - {x}
    if extra:
        raise CompileError(f"{sorted(map(str, extra))} have no value: pass them in params")
    if method not in ("bisect", "newton"):
        raise RootError(f"unknown method {method}")
    if method == "bisect" and b is None:
        raise RootError("bisection needs both ends of a bracket")
    t0 = time.perf_counter()
    f = _Point(dev, expr, x, 0)
    if method == "bisect":
        points = (f,)
        found = _bisect(f, a, b, xtol, max_iter)
    else:
        df = _Point(dev, sp.diff(expr, x), x, f.end)
        points = (f, df)
        found = _newton(f, df, a, xtol, max_iter)
    root, fx, n, ok, reason, bracket = found
    cycles = sum(p.cycles for p in points)
    return Root(Scalar(int(float_bits(np.array([root]))[0])), fx, n, ok, reason, method, cycles,
                time.perf_counter() - t0, bracket)
