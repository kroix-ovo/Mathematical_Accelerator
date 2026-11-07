"""A fixed list of integrals from my EM/RF, semiconductor and Calc 3 classes.

These are what the expression unit is for, so its accuracy gets measured on
them and not only on random numbers. Each one has a reference value computed in
40-digit mpmath, from a closed form where one exists. tools/evu_gate.py measures
the error of each run against that reference.

A problem can list several rules. The small one is what I'd use in class. The
large ones exist to stress the sum with many terms.
"""

# I keep my class-shaped examples here as expressions, rules and references.
# I use more than one rule where I need to see convergence. These cases are
# my working sample of applications, not a promise about every integrand.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import mpmath
import sympy as sp

from mathaccel.compiler.quadrature import Axis

x, y, z, r, t, u, th, ph = sp.symbols("x y z r t u theta phi", real=True)
pi = sp.pi


@dataclass
class Problem:
    """One integral over a box. rules lists (panels, order) per axis for each size."""
    name: str
    note: str
    expr: sp.Expr
    limits: list[tuple[sp.Symbol, sp.Expr, sp.Expr]]
    exact: Callable[[], mpmath.mpf]
    rules: list[list[tuple[int, int]]]

    def axes(self, rule: list[tuple[int, int]]) -> list[Axis]:
        """The box's axes with one size of rule."""
        return [Axis(s, a, b, panels, order) for (s, a, b), (panels, order) in zip(self.limits, rule, strict=True)]


def _mp(f):
    """I run f at 40 digits, so every reference has far more precision than fp32."""
    with mpmath.workdps(40):
        return +f()


PROBLEMS = [
    Problem("sin", "1D warm-up: integral of sin x on 0..pi", sp.sin(x), [(x, 0, pi)],
            lambda: _mp(lambda: mpmath.mpf(2)),
            [[(1, 16)], [(1024, 16)]]),
    Problem("poly_box", "Calc 3: x^2 y + y^3 over a rectangle (Gauss-Legendre is exact here)",
            x**2 * y + y**3, [(x, 0, 1), (y, 0, 2)],
            lambda: _mp(lambda: mpmath.mpf(14) / 3),
            [[(1, 8), (1, 8)]]),
    Problem("gaussian_2d", "Calc 3: exp(-(x^2+y^2)) over [-3,3]^2", sp.exp(-(x**2 + y**2)),
            [(x, -3, 3), (y, -3, 3)],
            lambda: _mp(lambda: (mpmath.sqrt(mpmath.pi) * mpmath.erf(3)) ** 2),
            [[(1, 32), (1, 32)], [(16, 16), (16, 16)], [(64, 16), (64, 16)]]),
    Problem("charge_sphere", "EM: charge in a sphere, density r cos^2(theta), in spherical coordinates",
            r**3 * sp.cos(th) ** 2 * sp.sin(th), [(r, 0, 1), (th, 0, pi), (ph, 0, 2 * pi)],
            lambda: _mp(lambda: mpmath.pi / 3),
            [[(1, 16), (1, 16), (1, 16)], [(8, 16), (8, 16), (1, 16)]]),
    Problem("circulation", "EM: line integral of H around a wire, B.dl on a circle of radius 2, Ampere's law",
            (-2 * sp.sin(t) * (-2 * sp.sin(t)) + 2 * sp.cos(t) * 2 * sp.cos(t))
            / (4 * sp.cos(t) ** 2 + 4 * sp.sin(t) ** 2), [(t, 0, 2 * pi)],
            lambda: _mp(lambda: 2 * mpmath.pi),
            [[(1, 32)]]),
    Problem("flux_sphere", "Vector calculus: flux of (x^3, y^3, z^3) through the unit sphere",
            (sp.sin(th) ** 4 * sp.cos(ph) ** 4 + sp.sin(th) ** 4 * sp.sin(ph) ** 4 + sp.cos(th) ** 4) * sp.sin(th),
            [(th, 0, pi), (ph, 0, 2 * pi)],
            lambda: _mp(lambda: 12 * mpmath.pi / 5),
            [[(1, 32), (1, 32)]]),
    Problem("dipole_power", "RF: half-wave dipole, integral of cos^2(pi/2 cos th)/sin th",
            sp.cos(pi / 2 * sp.cos(th)) ** 2 / sp.sin(th), [(th, 0, pi)],
            lambda: _mp(lambda: mpmath.quad(
                lambda v: mpmath.cos(mpmath.pi / 2 * mpmath.cos(v)) ** 2 / mpmath.sin(v),
                [0, mpmath.pi / 2, mpmath.pi])),
            [[(1, 32)]]),
    Problem("loop_bfield", "EM: Biot-Savart, Bz of a unit current loop, off axis at rho=0.5, z=0.2",
            (1 - sp.Rational(1, 2) * sp.cos(t))
            / (1 + sp.Rational(1, 4) + sp.Rational(1, 25) - sp.cos(t)) ** sp.Rational(3, 2), [(t, 0, 2 * pi)],
            lambda: _mp(lambda: mpmath.quad(
                lambda v: (1 - mpmath.cos(v) / 2) / (mpmath.mpf(129) / 100 - mpmath.cos(v)) ** mpmath.mpf(1.5),
                [0, mpmath.pi, 2 * mpmath.pi])),
            [[(4, 16)]]),
    Problem("plate_potential", "EM: potential of a uniform square plate at height 0.05 (close to singular)",
            1 / sp.sqrt(x**2 + y**2 + sp.Rational(1, 400)), [(x, -1, 1), (y, -1, 1)],
            lambda: _mp(lambda: mpmath.quad(
                lambda v: 2 * mpmath.asinh(1 / mpmath.sqrt(v**2 + mpmath.mpf(1) / 400)),
                [-1, -mpmath.mpf(1) / 10, 0, mpmath.mpf(1) / 10, 1])),
            [[(1, 32), (1, 32)], [(16, 16), (16, 16)], [(64, 16), (64, 16)]]),
    Problem("fermi_half", "Semiconductor: Fermi-Dirac F_1/2(2), with x = u^2 to remove the sqrt",
            2 * u**2 / (1 + sp.exp(u**2 - 2)), [(u, 0, 7)],
            lambda: _mp(lambda: mpmath.quad(
                lambda v: 2 * v**2 / (1 + mpmath.exp(v**2 - 2)), [0, 1, 2, 3, 5, 7])),
            [[(4, 16)]]),
    Problem("decay_wide", "Sum stress: exp(-x) on 0..40, terms spanning 17 decades",
            sp.exp(-x), [(x, 0, 40)],
            lambda: _mp(lambda: 1 - mpmath.exp(-40)),
            [[(8, 16)], [(4096, 16)]]),
    Problem("rf_oscillatory", "RF: average power, sin^2(50 x) on 0..pi", sp.sin(50 * x) ** 2, [(x, 0, pi)],
            lambda: _mp(lambda: mpmath.pi / 2),
            [[(32, 16)], [(4096, 16)]]),
    Problem("cube_large", "Sum stress: (x+y+z)^2 on the unit cube, 2 million terms", (x + y + z) ** 2,
            [(x, 0, 1), (y, 0, 1), (z, 0, 1)],
            lambda: _mp(lambda: mpmath.mpf(5) / 2),
            [[(1, 8), (1, 8), (1, 8)], [(8, 16), (8, 16), (8, 16)]]),
]
