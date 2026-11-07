"""The calculus tools on the simulated board, over both local transports.

Known answers come from closed forms, SymPy or 40-digit mpmath, so these check
the tools' setup and not only that the model agrees with itself. The hardware
limits and the timeout rules get their own tests further down, because those are
the ways a wrong run could cost me a stuck board.
"""

# I check the calculus interface through a simulated device here.
# I cover geometry, flags, convergence and root-finding failures as well as
# ordinary answers. These tests check my setup and driver logic; they don't
# replace the recorded physical-board runs.

from __future__ import annotations

import math
import warnings

import mpmath
import numpy as np
import pytest
import sympy as sp

from mathaccel import calc, cli
from mathaccel._generated import spec
from mathaccel._generated.spec import FpuFlag, FpuOp, Kernel, Op
from mathaccel.compiler.evu_isa import EvuError, Instr, Program, Run, const, memory_image
from mathaccel.compiler.problems import PROBLEMS
from mathaccel.compiler.quadrature import integral_run
from mathaccel.compiler.sympy_to_evu import CompileError
from mathaccel.driver.device import Device, DeviceTimeout, RunTimeout, RunTooLong, connect
from mathaccel.driver.model import DeviceModel
from mathaccel.driver.protocol import Frame
from mathaccel.driver.transport import SimTransport, Transport

x, y, z, t, u, v = sp.symbols("x y z t u v", real=True)


@pytest.fixture(params=["sim", "udpsim"])
def dev(request):
    """Every tool test runs through the model directly and through a real socket."""
    with connect(request.param) as d:
        yield d


def close(result, want: float, rel: float = 3e-6) -> bool:
    return float(result) == pytest.approx(want, rel=rel)


# ---- integrals --------------------------------------------------------------------

def test_box_integral_against_a_closed_form(dev):
    got = calc.integrate_nd(dev, x**2 * y + y**3, [(x, 0, 1), (y, 0, 2)], order=8)
    assert close(got, 14 / 3) and got.converged and got.clean


def test_triple_integral_uses_all_three_dimensions(dev):
    got = calc.integrate_nd(dev, (x + y + z) ** 2, [(x, 0, 1), (y, 0, 1), (z, 0, 1)], order=8)
    assert got.points == 16**3 and close(got, 2.5)


def test_limits_that_depend_on_an_outer_variable(dev):
    """A triangle: y from 0 to x. SymPy gives the exact answer to compare with."""
    want = float(sp.integrate(x * y, (y, 0, x), (x, 0, 1)))
    got = calc.integrate_nd(dev, x * y, [(y, 0, x), (x, 0, 1)], order=8)
    assert close(got, want)


def test_quarter_disk_area_needs_a_jacobian(dev):
    got = calc.integrate_nd(dev, 1, [(y, 0, sp.sqrt(1 - x**2)), (x, 0, 1)], order=32, panels=4, check=False)
    assert close(got, math.pi / 4, rel=2e-4)


def test_named_constants_come_from_params(dev):
    q = sp.Symbol("q")
    assert close(calc.integrate_nd(dev, q * x, [(x, 0, 1)], params={q: 6}, order=4), 3.0)


def test_inner_limit_may_not_use_an_inner_variable(dev):
    with pytest.raises(CompileError, match="outer"):
        calc.integrate_nd(dev, x, [(x, 0, y), (y, 0, x)])


def test_infinite_limit_is_refused_with_a_reason(dev):
    with pytest.raises(CompileError, match="infinite"):
        calc.integrate_nd(dev, sp.exp(-x), [(x, 0, sp.oo)])


@pytest.mark.parametrize("p", [p for p in PROBLEMS if p.name in ("sin", "gaussian_2d", "flux_sphere", "fermi_half")],
                         ids=lambda p: p.name)
def test_class_problems_through_the_tool(dev, p):
    rule = min(p.rules, key=lambda r: math.prod(a * b for a, b in r))
    got = calc.integrate_nd(dev, p.expr, p.limits, panels=[a for a, _ in rule], order=[b for _, b in rule], check=False)
    assert close(got, float(p.exact()), rel=2e-4)


def test_unconverged_rule_is_reported(dev):
    """The plate at height 0.05: the coarse rule looks fine and is off by 0.18%."""
    p = next(p for p in PROBLEMS if p.name == "plate_potential")
    got = calc.integrate_nd(dev, p.expr, p.limits, order=16, panels=1)
    assert got.converged is False and not got.clean
    assert "NOT CONVERGED" in str(got)


def test_tolerance_is_measured_on_the_finer_rule(dev):
    got = calc.integrate_nd(dev, sp.exp(x), [(x, 0, 1)], order=8)
    assert got.points == 16 and got.error_estimate < 1e-6


def test_line_work_integral_ampere(dev):
    """H around a wire: the field of 1/(2 pi r), so the circulation is exactly 1."""
    field = (-y / (2 * sp.pi * (x**2 + y**2)), x / (2 * sp.pi * (x**2 + y**2)))
    got = calc.line_integral(dev, field, (2 * sp.cos(t), 2 * sp.sin(t)), (t, 0, 2 * sp.pi), coords=(x, y), order=32)
    assert close(got, 1.0)


def test_line_integral_of_a_gradient_needs_only_the_ends(dev):
    field = (2 * x * y, x**2 + 3 * z**2, 6 * y * z)
    got = calc.line_integral(dev, field, (t, t**2, t**3), (t, 0, 1), coords=(x, y, z), order=16)
    assert close(got, 4.0)                  # x^2 y + 3 y z^2 from (0,0,0) to (1,1,1)


def test_arc_length_from_a_scalar_field(dev):
    got = calc.line_integral(dev, 1, (3 * sp.cos(t), 3 * sp.sin(t)), (t, 0, sp.pi), coords=(x, y), order=16)
    assert close(got, 3 * math.pi)


def test_scalar_line_integral_against_mpmath(dev):
    want = mpmath.quad(lambda w: (w + w**2) * mpmath.sqrt(1 + 4 * w**2), [0, 1])
    got = calc.line_integral(dev, x + y, (t, t**2), (t, 0, 1), coords=(x, y), order=16)
    assert close(got, float(want))


def test_flux_through_a_sphere_matches_the_divergence_theorem(dev):
    sphere = (sp.sin(u) * sp.cos(v), sp.sin(u) * sp.sin(v), sp.cos(u))
    got = calc.surface_flux(dev, (x**3, y**3, z**3), sphere, (u, 0, sp.pi), (v, 0, 2 * sp.pi),
                            coords=(x, y, z), order=32)
    assert close(got, 12 * math.pi / 5)


def test_swapping_u_and_v_flips_the_normal(dev):
    plane = (u, v, sp.Integer(0))
    up = calc.surface_flux(dev, (0, 0, 1), plane, (u, 0, 2), (v, 0, 3), coords=(x, y, z), order=4)
    down = calc.surface_flux(dev, (0, 0, 1), plane, (v, 0, 3), (u, 0, 2), coords=(x, y, z), order=4)
    assert close(up, 6.0) and close(down, -6.0)


def test_scalar_surface_integral_is_area(dev):
    sphere = (2 * sp.sin(u) * sp.cos(v), 2 * sp.sin(u) * sp.sin(v), 2 * sp.cos(u))
    got = calc.surface_flux(dev, 1, sphere, (u, 0, sp.pi), (v, 0, 2 * sp.pi), coords=(x, y, z), order=32)
    assert close(got, 16 * math.pi, rel=1e-5)


def test_surface_limit_may_depend_on_the_other_variable(dev):
    """Flux of (0,0,1) up through a triangle in the plane z=0, area 1/2."""
    got = calc.surface_flux(dev, (0, 0, 1), (u, v, sp.Integer(0)), (u, 0, 1 - v), (v, 0, 1),
                            coords=(x, y, z), order=4)
    assert close(got, 0.5)


# ---- reporting ---------------------------------------------------------------------

def test_a_nan_integral_is_never_a_plain_number(dev):
    got = calc.integrate_nd(dev, sp.log(x), [(x, -1, 1)], order=4)
    assert got.value.kind == "nan" and not got.clean
    assert got.value.aux & FpuFlag.NV
    with pytest.raises(calc.NumericalError, match="NaN"):
        float(got)
    assert "NaN" in str(got) and "invalid" in str(got)
    assert got.converged is None           # no point in a finer rule


def test_an_infinite_sum_is_reported():
    """Two terms near the top of the range overflow at the very last step."""
    with connect("sim") as dev:
        got = calc.integrate_nd(dev, sp.Float(1e38), [(x, 0, 100)], order=4, check=False)
    assert got.value.kind == "+inf"
    assert "+infinity" in str(got) and "overflow" in str(got)


def test_a_finite_result_with_a_bad_point_is_flagged():
    """min() takes the number when one side is NaN, so the sum looks fine and aux doesn't."""
    with connect("sim") as dev:
        got = calc.integrate_nd(dev, sp.Min(sp.log(x), 1), [(x, -1, 1)], order=4, check=False)
    assert got.value.kind == "number"
    assert not got.value.clean and "invalid" in str(got)


def test_underflow_and_inexact_are_not_trouble():
    s = calc.Scalar(0x3F800000, FpuFlag.NX, FpuFlag.NX | FpuFlag.UF)
    assert s.clean and str(s) == "1"


# ---- map mode ----------------------------------------------------------------------

def test_grad_at_one_point(dev):
    got = calc.grad(dev, x**2 * y + sp.sin(z), (x, y, z), at=(1.5, 2.0, 0.5))
    assert got.values[0] == pytest.approx([6.0, 2.25, math.cos(0.5)], rel=1e-6)
    assert got.clean and got.runs == 3


def test_div_and_curl_at_many_points(dev):
    pts = np.random.default_rng(1).uniform(-2, 2, (50, 3))
    field = (x * y, y * z, sp.sin(x) * z)
    want_div = pts[:, 1] + pts[:, 2] + np.sin(pts[:, 0])
    want_curl = np.stack([-pts[:, 1], -np.cos(pts[:, 0]) * pts[:, 2], -pts[:, 0]], axis=1)
    assert calc.div(dev, field, (x, y, z), at=pts).values[:, 0] == pytest.approx(want_div, rel=1e-5, abs=1e-5)
    assert calc.curl_at(dev, field, (x, y, z), at=pts).values == pytest.approx(want_curl, rel=1e-5, abs=1e-5)


def test_curl_of_a_plane_field_has_one_output(dev):
    assert calc.curl_at(dev, (-y, x), (x, y), at=(1, 2)).values[0, 0] == 2.0


def test_curl_of_a_gradient_is_zero(dev):
    phi = x**2 * y + z**3
    field = [sp.diff(phi, c) for c in (x, y, z)]
    got = calc.curl_at(dev, field, (x, y, z), at=(0.5, 1.5, 2.5))
    assert (got.values == 0).all()


def test_constant_derivative_uses_no_table(dev):
    got = calc.div(dev, (x, y, z), (x, y, z), at=(1, 2, 3))
    assert got.values[0, 0] == 3.0


def test_tensor_grid_keeps_its_shape(dev):
    xs, ys = np.linspace(0, 1, 5), np.linspace(1, 2, 3)
    got = calc.grad(dev, x * y, (x, y), grid=[xs, ys])
    assert got.shape == (5, 3)
    assert got.component(0) == pytest.approx(np.broadcast_to(ys.astype(np.float32), (5, 3)))


def test_a_bad_point_is_named():
    with connect("sim") as dev:
        got = calc.grad(dev, sp.sqrt(x), (x,), at=[[1.0], [-1.0], [4.0], [0.0]])
    assert got.bad.tolist() == [False, True, False, True]
    assert not got.clean and "first is point 1" in str(got)
    assert got.scalars(1)[0].kind == "nan"


def test_map_list_too_big_for_one_run_is_split():
    """Each 3D point costs five words here, so 20,000 won't fit in one run."""
    pts = np.tile(np.array([[1.0, 2.0, 3.0]]), (20000, 1))
    pts[:, 0] += np.arange(20000) / 1000
    with connect("sim") as dev:
        got = calc.map_eval(dev, [x + y * z], (x, y, z), at=pts)
    assert got.runs == 2
    assert got.values[:, 0] == pytest.approx((pts[:, 0].astype(np.float32) + 6.0).astype(np.float64), rel=1e-6)


def test_map_grid_too_big_for_one_run_is_split():
    xs, ys = np.linspace(0, 1, 400), np.linspace(0, 1, 100)
    with connect("sim") as dev:
        got = calc.map_eval(dev, [x + 10 * y], (x, y), grid=[xs, ys])
    assert got.runs == 2 and got.shape == (400, 100)
    want = xs.astype(np.float32)[:, None] + 10 * ys.astype(np.float32)[None, :]
    assert got.component(0) == pytest.approx(want, rel=2e-6, abs=2e-6)


def test_a_single_slice_that_cant_fit_is_refused():
    with connect("sim") as dev, pytest.raises(EvuError, match="slice"):
        calc.map_eval(dev, [x * y * z], (x, y, z), grid=[[0.0, 1.0], np.linspace(0, 1, 300), np.linspace(0, 1, 300)])


def test_map_needs_exactly_one_kind_of_location(dev):
    with pytest.raises(EvuError, match="either"):
        calc.grad(dev, x, (x,))
    with pytest.raises(EvuError, match="either"):
        calc.grad(dev, x, (x,), at=(1,), grid=[[1.0]])


# ---- root finding ------------------------------------------------------------------

def test_bisection_against_mpmath(dev):
    want = mpmath.findroot(lambda w: mpmath.cos(w) - w, 0.7)
    got = calc.root_find(dev, sp.cos(x) - x, x, 0, 1)
    assert got.converged and got.method == "bisect"
    assert abs(float(got) - float(want)) <= 4 * calc.ulp_of(float(want))
    lo, hi = got.bracket
    assert lo <= float(got) <= hi


def test_newton_against_mpmath(dev):
    want = mpmath.findroot(lambda w: mpmath.cos(w) - w, 0.7)
    got = calc.root_find(dev, sp.cos(x) - x, x, 1.0, method="newton")
    assert got.converged and got.iterations < 10
    assert abs(float(got) - float(want)) <= 4 * calc.ulp_of(float(want))


def test_silicon_style_root_with_a_named_constant(dev):
    """The law of mass action for a doping N: n - ni^2/n - N = 0."""
    ni, doping = sp.Symbol("ni"), sp.Symbol("N")
    got = calc.root_find(dev, x - ni**2 / x - doping, x, 1.0, 10.0, params={ni: 2, doping: 3})
    assert close(got, 4.0)


def test_exact_zero_stops_early(dev):
    got = calc.root_find(dev, x - 0.5, x, 0, 1)
    assert got.converged and float(got) == 0.5 and got.iterations == 1


def test_no_sign_change_is_an_error(dev):
    with pytest.raises(calc.RootError, match="same sign"):
        calc.root_find(dev, x**2 + 1, x, -1, 1)


def test_nan_at_the_bracket_is_an_error(dev):
    with pytest.raises(calc.RootError, match="isn't a number"):
        calc.root_find(dev, sp.log(x), x, -1, 2)


def test_newton_reports_a_flat_spot(dev):
    got = calc.root_find(dev, x**2 + 1, x, 0.0, method="newton")
    assert not got.converged and "zero" in got.reason
    with pytest.raises(calc.NumericalError):
        float(got)


def test_newton_reports_running_out_of_steps(dev):
    got = calc.root_find(dev, x**2 + 1, x, 0.5, method="newton", max_iter=6)
    assert not got.converged and "after 6" in got.reason


def test_root_needs_its_constants(dev):
    with pytest.raises(CompileError, match="params"):
        calc.root_find(dev, x - sp.Symbol("a"), x, 0, 1)


def test_root_only_sends_the_program_once():
    """After loading, a step should be one word of x, one flag marker, RUN and a read."""
    model = DeviceModel()
    transport = SimTransport(model)
    writes = []
    send = transport.send

    def spy(wire: bytes) -> None:
        frame = Frame.decode(wire[:-1])
        if frame.op == Op.WRITE_MEM:
            writes.append(len(frame.payload) // 4 - 1)
        send(wire)

    transport.send = spy
    got = calc.root_find(Device(transport), sp.cos(x) - x, x, 0, 1)
    assert sorted(writes)[-1] > 10 and sorted(writes)[-2] == 1
    assert got.converged


# ---- limits ------------------------------------------------------------------------

def test_grid_limits_are_caught_before_anything_is_sent():
    program = Program([Instr(FpuOp.ADD, 0, const(0), const(0), last=True).encode()], [0])
    with pytest.raises(EvuError, match="65,535"):
        Run(program, [], (65536,)).check()
    with pytest.raises(EvuError, match="2\\^32"):
        Run(program, [], (65535, 65535, 2)).check()
    Run(program, [], (65535,)).check()
    Run(program, [], (4, 32768, 32768)).check()


def test_a_rule_past_the_table_limit_goes_on_two_grid_dimensions():
    """65,536 nodes can't sit in one dimension, or in the scratchpad as tables."""
    run = integral_run(sp.exp(-x), [calc.Axis(x, 0, 40, 4096, 16)])
    assert run.grid == (4096, 16) and len(run.tables) == 3
    assert sum(len(t.values) for t in run.tables) == 4096 + 32
    memory_image(run)
    with connect("sim") as dev:
        got = calc.integrate_nd(dev, sp.exp(-x), [(x, 0, 40)], panels=4096, order=16, check=False)
    assert close(got, 1 - math.exp(-40), rel=2e-7)


def test_a_split_axis_gives_the_same_nodes():
    axis = calc.Axis(x, 1, 3, 5, 4)
    mid, offset, w = axis.split()
    nodes, weights = axis.nodes()
    assert np.add.outer(mid, offset).ravel() == pytest.approx(nodes)
    assert np.tile(w, 5) == pytest.approx(weights)


def test_only_the_longest_axis_is_split():
    run = integral_run(x * y, [calc.Axis(x, 0, 1, 4096, 16), calc.Axis(y, 0, 1, 1, 8)])
    assert run.grid == (4096, 16, 8) and len(run.tables) == 5


def test_a_rule_that_needs_too_many_dimensions_is_an_error(dev):
    with pytest.raises(EvuError, match="grid dimensions"):
        calc.integrate_nd(dev, x * y, [(x, 0, 1), (y, 0, 1)], panels=4096, order=16, check=False)


def test_a_finer_rule_too_slow_to_start_is_a_note_not_a_failure(dev):
    got = calc.integrate_nd(dev, x, [(x, 0, 1)], panels=256, order=16, max_seconds=2e-4)
    assert got.converged is None and "no convergence check" in str(got)
    assert close(got, 0.5)


def test_too_many_constants_is_a_compile_error(dev):
    expr = sum(sp.Rational(k, 997) * x**k for k in range(1, 80))
    with pytest.raises(EvuError):
        calc.integrate_nd(dev, expr, [(x, 0, 1)], order=4, check=False)


def test_four_variables_are_refused(dev):
    a = sp.Symbol("a")
    with pytest.raises(EvuError, match="dimensions"):
        calc.integrate_nd(dev, x + y + z + a, [(x, 0, 1), (y, 0, 1), (z, 0, 1), (a, 0, 1)], order=2)


# ---- long runs and timeouts --------------------------------------------------------

def _huge_run() -> Run:
    """A billion points of a one-instruction program. Far too long to start."""
    program = Program([Instr(FpuOp.ADD, 0, const(0), const(0), last=True).encode()], [0])
    return Run(program, [], (1000, 1000, 1000))


def test_huge_run_is_refused_and_nothing_is_sent():
    model = DeviceModel()
    transport = SimTransport(model)
    sent = []
    send = transport.send
    transport.send = lambda wire: (sent.append(wire), send(wire))
    with pytest.raises(RunTooLong, match="can't be cancelled"):
        Device(transport).evu_run(_huge_run(), max_seconds=1.0)
    assert not sent


def test_slow_run_warns_first(monkeypatch):
    run = integral_run(x, [calc.Axis(x, 0, 1, 1, 4)])
    monkeypatch.setattr(calc.evu_model, "cycle_bounds", lambda _: (10**10, 10**11))
    with pytest.warns(UserWarning, match="can't be cancelled"):
        calc._warn_if_slow(run)


class Silent(Transport):
    """A board that never answers one request, and counts what it was sent.

    drop picks the frames to swallow. Everything else goes to a real model, so
    what the board ends up doing can be inspected.
    """

    def __init__(self, drop, model=None) -> None:
        self.model = model or DeviceModel()
        self.inner = SimTransport(self.model)
        self.drop = drop
        self.runs = 0

    def send(self, wire: bytes) -> None:
        frame = Frame.decode(wire[:-1])
        is_evu = frame.op == Op.RUN and frame.payload[0] == Kernel.EVU
        self.runs += is_evu
        if not self.drop(frame, is_evu):
            self.inner.send(wire)

    def recv_frame(self, timeout: float):
        return self.inner.recv_frame(timeout)


def test_lost_request_is_reported_and_never_resent():
    t = Silent(lambda _f, is_evu: is_evu)
    with pytest.raises(RunTimeout) as e:
        Device(t, timeout=0.01).evu_run(integral_run(x, [calc.Axis(x, 0, 1, 1, 4)]))
    assert e.value.state == "not started" and t.runs == 1


def test_lost_reply_is_reported_and_not_rerun():
    model = DeviceModel()

    class LostReply(Silent):
        """The board runs it and its answer vanishes."""

        def send(self, wire: bytes) -> None:
            before = len(self.inner._pending)
            super().send(wire)
            if Frame.decode(wire[:-1]).op == Op.RUN:
                del self.inner._pending[before:]

    t = LostReply(lambda _f, _e: False, model)
    with pytest.raises(RunTimeout) as e:
        Device(t, timeout=0.01).evu_run(integral_run(x, [calc.Axis(x, 0, 1, 1, 4)]))
    assert e.value.state == "finished" and t.runs == 1 and "did not run it again" in str(e.value)


def test_a_silent_board_is_reported_as_running():
    class Busy(Silent):
        """Takes the RUN and then answers nothing, like a board in a long run."""
        busy = False

        def send(self, wire: bytes) -> None:
            is_run = Frame.decode(wire[:-1]).op == Op.RUN
            if is_run:
                self.runs += 1
                self.busy = True
            elif not self.busy:
                self.inner.send(wire)

    t = Busy(lambda _f, _e: False)
    with pytest.raises(RunTimeout) as e:
        Device(t, timeout=0.01).evu_run(integral_run(x, [calc.Axis(x, 0, 1, 1, 4)]))
    assert e.value.state == "running" and t.runs == 1
    assert isinstance(e.value, DeviceTimeout)


def test_timeout_covers_the_high_bound():
    """A run allowed to be slow must be given that long, not the default 5 s."""
    seen = {}

    class Timing(Device):
        def request(self, op, payload, timeout=None, resend=True):
            if op == Op.RUN:
                seen.update(timeout=timeout, resend=resend)
            return super().request(op, payload, timeout, resend)

    run = integral_run(x, [calc.Axis(x, 0, 1, 1, 4)])
    image = memory_image(run)
    dev = Timing(SimTransport())
    dev.write_words(0, image.words)
    dev.evu_start(image, (100, 3 * 10**9))
    assert seen["timeout"] > 30 and seen["resend"] is False


def test_only_harmless_kernels_are_resent():
    """NOP, FILL and an FPU run whose output doesn't touch its input may repeat."""
    seen = []

    class Watch(Device):
        def request(self, op, payload, timeout=None, resend=True):
            if op == Op.RUN:
                seen.append(resend)
            return super().request(op, payload, timeout, resend)

    dev = Watch(SimTransport())
    dev.nop()
    dev.fill(0, 4, 0)
    dev.fpu_kernel(FpuOp.ADD, 4, 0, 4, 8, 12)      # clear of its inputs
    dev.fpu_kernel(FpuOp.ADD, 4, 0, 4, 0, 12)      # result lands on a
    dev.run(Kernel.NOP)
    assert seen == [True, True, True, False, False]


def test_old_bitstream_is_named():
    class Old(Device):
        def ping(self):
            info = super().ping()
            return type(info)(info.magic, (0, 3), info.scratch_addr_bits, info.clk_hz)

    with pytest.raises(RuntimeError, match="v0.3"):
        Old(SimTransport()).evu_run(integral_run(x, [calc.Axis(x, 0, 1, 1, 4)]))


def test_reply_holds_everything_a_caller_needs():
    with connect("sim") as dev:
        run = integral_run(x, [calc.Axis(x, 0, 1, 1, 4)])
        reply = dev.evu_run(run)
    assert len(reply.results) == len(reply.flags) == 1
    assert reply.in_bounds and reply.total_seconds > 0
    assert reply.seconds == reply.cycles / spec.CLK_HZ


# ---- command line ------------------------------------------------------------------

@pytest.mark.parametrize("argv, expect", [
    (["calc", "integrate", "x**2*y", "x=0:1", "y=0:2", "--order", "8"], "0.6666"),
    (["calc", "integrate", "x*y", "y=0:x", "x=0:1", "--order", "8"], "0.125"),
    (["calc", "line", "(-y,x)", "(2*cos(t),2*sin(t))", "t=0:2*pi", "--order", "32"], "25.13"),
    (["calc", "flux", "(x,y,z)", "(sin(u)*cos(v),sin(u)*sin(v),cos(u))", "u=0:pi", "v=0:2*pi", "--order", "32"],
     "12.56"),
    (["calc", "grad", "x**2*y", "--at", "1,2"], "(4, 1)"),
    (["calc", "div", "(x,y,z)", "--at", "1,2,3"], "(3)"),
    (["calc", "curl", "(-y,x,0)", "--at", "1,2,0"], "(0, 0, 2)"),
    (["calc", "root", "cos(x)-x", "x=0:1"], "0.739085"),
    (["calc", "root", "cos(x)-x", "x=1:1", "--newton"], "0.739085"),
])
@pytest.mark.parametrize("port", ["sim", "udpsim"])
def test_cli(capsys, port, argv, expect):
    cli.main(["--port", port] + argv)
    assert expect in capsys.readouterr().out


def test_cli_exit_status_shows_a_bad_answer(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["calc", "integrate", "log(x)", "x=-1:1", "--order", "4"])
    assert e.value.code == 2
    assert "NaN" in capsys.readouterr().out


def test_cli_refuses_a_huge_run(capsys):
    with pytest.raises(RunTooLong):
        cli.main(["calc", "integrate", "sin(x)/x", "x=0:1", "y=0:1", "z=0:1", "--panels", "100", "--order", "16",
                  "--no-check", "--max-seconds", "1"])


def test_no_warnings_on_ordinary_runs():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with connect("sim") as dev:
            calc.integrate_nd(dev, x, [(x, 0, 1)], order=4)
