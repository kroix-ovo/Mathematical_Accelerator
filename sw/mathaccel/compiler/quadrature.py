"""Gauss-Legendre nodes and weights, and building a run for an integral over a box.

Gauss-Legendre with n nodes integrates polynomials up to degree 2n-1 exactly,
which is why it needs far fewer points than Simpson's rule on smooth functions.
For a large point count I use composite rules: several panels of a fixed order.
That avoids very high orders, and it copes better with a function that varies a
lot in one part of the interval.

The nodes go to the FPGA as fp32, so each one is rounded. That rounding is part
of the answer's error, and tools/evu_gate.py measures it along with the rest.
numpy computes them in double precision, which is far more than fp32 needs.

A rule with many nodes can outgrow the hardware in two ways: one grid dimension
holds 65,535 points, and every node costs two table words in a 65,536-word
scratchpad. Panels of equal width get around both. Node k of panel p is
mid[p] + offset[k] and its weight is w[k] alone, so the axis becomes two grid
dimensions, with one short table of midpoints and two of offsets and weights.
integral_run does this by itself when the tables wouldn't fit. It costs one more
ADD per point, and it rounds x a second time, which is part of the measured error.
"""

# I build the sampling rule here, before anything reaches the board.
# I send nodes and weights as fp32 bit patterns, so their rounding is part of
# the calculation too. When a long axis won't fit, I split panels from their
# local offsets rather than trying to upload a huge expanded table.

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import sympy as sp

from mathaccel._generated import spec
from mathaccel.compiler.evu_isa import EvuError, Run, Table
from mathaccel.compiler.sympy_to_evu import compile_expr


def float_bits(x: np.ndarray) -> np.ndarray:
    """fp32 patterns of an array of doubles, each rounded once to nearest even."""
    return np.asarray(x, dtype=np.float64).astype(np.float32).view(np.uint32)


# Table words I'm willing to spend. What's left of the scratchpad holds the
# descriptor, the program, the constants and the outputs.
TABLE_WORD_LIMIT = spec.SCRATCH_WORDS - 4096


def gauss_legendre(a: float, b: float, panels: int, order: int) -> tuple[np.ndarray, np.ndarray]:
    """Nodes and weights in double precision for a composite rule on [a, b]."""
    t, w = np.polynomial.legendre.leggauss(order)
    edges = np.linspace(a, b, panels + 1)
    half = (edges[1:] - edges[:-1]) / 2
    mid = (edges[1:] + edges[:-1]) / 2
    return (mid[:, None] + half[:, None] * t).ravel(), (half[:, None] * w).ravel()


@dataclass
class Axis:
    """One variable of integration: its symbol, limits and composite rule."""
    symbol: sp.Symbol
    a: sp.Expr
    b: sp.Expr
    panels: int = 1
    order: int = 16

    def nodes(self) -> tuple[np.ndarray, np.ndarray]:
        """This axis's nodes and weights in double precision."""
        return gauss_legendre(float(self.a), float(self.b), self.panels, self.order)

    @property
    def count(self) -> int:
        """Nodes on this axis."""
        return self.panels * self.order

    def split(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Panel midpoints, offsets within a panel, and one panel's weights."""
        a, b = float(self.a), float(self.b)
        half = (b - a) / self.panels / 2
        t, w = np.polynomial.legendre.leggauss(self.order)
        return a + half * (2 * np.arange(self.panels) + 1), half * t, half * w


def _axes_to_split(axes: list[Axis]) -> set[int]:
    """Which axes go on two grid dimensions. I take the longest first and stop as
    soon as the tables fit, because a plain axis is cheaper when it works."""
    size = {k: 2 * a.count for k, a in enumerate(axes)}
    chosen: set[int] = set()
    while sum(size.values()) > TABLE_WORD_LIMIT or any(a.count >= 1 << 16 and k not in chosen
                                                       for k, a in enumerate(axes)):
        k = max((k for k in size if k not in chosen), key=size.get, default=None)
        if k is None:
            break
        chosen.add(k)
        size[k] = axes[k].panels + 2 * axes[k].order
    return chosen


def integral_run(expr: sp.Expr, axes: list[Axis]) -> Run:
    """A reduce run that integrates expr over a box: two tables per axis, or three
    on an axis split across two grid dimensions."""
    split = _axes_to_split(axes)
    tables: list[Table] = []
    variables: dict[sp.Symbol, int] = {}
    weights: list[int] = []
    grid: list[int] = []
    expr = sp.sympify(expr)
    for k, axis in enumerate(axes):
        name = str(axis.symbol)
        if k in split:
            # The grid gets the panels as the slower dimension, so the points run
            # in the same order as the unsplit rule's.
            mid_values, offset_values, w = axis.split()
            mid, offset = sp.Dummy(f"{name}_mid", real=True), sp.Dummy(f"{name}_offset", real=True)
            expr = expr.subs(axis.symbol, mid + offset)
            variables[mid] = len(tables)
            tables.append(Table(f"{name}_mid", float_bits(mid_values), len(grid)))
            variables[offset] = len(tables)
            tables.append(Table(f"{name}_offset", float_bits(offset_values), len(grid) + 1))
            grid += [axis.panels, axis.order]
            dim = len(grid) - 1
        else:
            x, w = axis.nodes()
            variables[axis.symbol] = len(tables)
            tables.append(Table(name, float_bits(x), len(grid)))
            grid.append(axis.count)
            dim = len(grid) - 1
        weights.append(len(tables))
        tables.append(Table(f"w_{name}", float_bits(w), dim))
    if len(grid) > spec.EVU_DIMS or len(tables) > spec.EVU_TABLES:
        raise EvuError(f"this rule needs {len(grid)} grid dimensions and {len(tables)} tables, the hardware has "
                       f"{spec.EVU_DIMS} and {spec.EVU_TABLES}: use fewer nodes")
    run = Run(compile_expr(expr, variables, weights), tables, tuple(grid))
    run.check()
    return run
