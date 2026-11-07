"""Compile a SymPy expression into expression unit bytecode.

The steps, in order:

1. Common subexpressions. I use SymPy's cse() to pull out repeated work, so
   sin(x)*sin(x) + sin(x) computes sin(x) once. I save an issue slot and a
   dependency wait that way. A sine has 54 clocks of free-port FPU latency,
   although independent sines can enter the pipeline one per clock.
2. Lowering. Each SymPy node becomes FPU operations on numbered values. SymPy
   writes x/y as x * y**-1 and x - y as x + (-1)*y; I turn those back into DIV
   and SUB, which round once instead of twice. A function with no FPU op
   (tan, sinh, ...) becomes the ops it is built from.
3. Dead code. Anything the output doesn't need is dropped.
4. Register allocation. A value only needs a register from when it is computed
   until its last use, so registers get reused and most programs stay far
   below the 64 available. A result may land in the register of one of its own
   sources, because a context reads its sources before its result comes back.

Numbers are computed exactly here, then rounded once to fp32. So 2*pi in an
expression is one correctly rounded constant, not pi rounded and then doubled.

Things I leave out on purpose: Piecewise (there is no SELECT instruction, see
docs/phase4-plan.md) and any function with no FPU equivalent, such as erf. Those
raise CompileError instead of giving a wrong answer.
"""

# I use SymPy to describe the calculation, then lower it to the operations
# my FPU actually has. I share repeated work, discard unused values and reuse
# registers after their last read. I reject unsupported expressions here so
# they don't quietly turn into a different calculation on the board.

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

import sympy as sp

from mathaccel._generated import spec
from mathaccel._generated.spec import FpuOp
from mathaccel.compiler.evu_isa import OP_LOAD, EvuError, Instr, Program, const, reg
from mathaccel.fp32ref import round_fraction


class CompileError(EvuError):
    """An expression the expression unit can't evaluate."""


def fp32_of(number: sp.Expr) -> int:
    """I round a high-precision SymPy number once into the fp32 constant pool."""
    value = sp.N(number, 80)
    if not value.is_real or not value.is_finite:
        raise CompileError(f"{number} is not a finite real number")
    exact = Fraction(int(sp.Rational(value).p), int(sp.Rational(value).q))
    if exact == 0:
        return 0
    bits, _ = round_fraction(int(exact < 0), abs(exact))
    return bits


@dataclass(frozen=True)
class _Const:
    """A value from the constant pool."""
    index: int


@dataclass
class _Op:
    """One computed value: an FPU op, or a LOAD when table is set."""
    op: int
    a: int | _Const | None = None
    b: int | _Const | None = None
    table: int | None = None


Value = int | _Const        # an int is the number of a computed value


@dataclass
class _Builder:
    """Lowers expressions to numbered values, sharing anything computed twice."""
    tables: dict[sp.Symbol, int]
    ops: list[_Op] = field(default_factory=list)
    constants: list[int] = field(default_factory=list)
    done: dict[sp.Expr, Value] = field(default_factory=dict)
    seen: dict[tuple, int] = field(default_factory=dict)

    def constant(self, bits: int) -> _Const:
        """The pool entry for an fp32 pattern, adding it the first time it is used."""
        if bits not in self.constants:
            self.constants.append(bits)
        return _Const(self.constants.index(bits))

    def number(self, x) -> _Const:
        """The pool entry for a SymPy number."""
        return self.constant(fp32_of(sp.sympify(x)))

    def emit(self, op: int, a: Value, b: Value | None = None) -> int:
        """I add one FPU op, or return the earlier value if this exact op already ran."""
        key = (op, a, b)
        if key not in self.seen:
            self.ops.append(_Op(op, a, b))
            self.seen[key] = len(self.ops) - 1
        return self.seen[key]

    def lower(self, e: sp.Expr) -> Value:
        """Lower one expression, remembering the result so it is only computed once."""
        if e not in self.done:
            self.done[e] = self._lower(e)
        return self.done[e]

    def _lower(self, e: sp.Expr) -> Value:
        if e.is_number:
            return self.number(e)
        if e.is_Symbol:
            if e not in self.tables:
                raise CompileError(f"{e} has no value: it isn't a grid variable or a constant")
            self.ops.append(_Op(OP_LOAD, table=self.tables[e]))
            return len(self.ops) - 1
        if e.is_Add:
            return self._add(e)
        if e.is_Mul:
            return self._mul(e)
        if e.is_Pow:
            return self._pow(e.base, e.exp)
        return self._function(e)

    def _add(self, e: sp.Add) -> Value:
        # A term with a negative coefficient becomes a SUB of its positive
        # form. I start from a positive term so nothing has to be negated.
        terms = sorted(e.args, key=lambda t: bool(t.as_coeff_Mul()[0] < 0))
        total = self.lower(terms[0])
        for t in terms[1:]:
            if t.as_coeff_Mul()[0] < 0:
                total = self.emit(FpuOp.SUB, total, self.lower(-t))
            else:
                total = self.emit(FpuOp.ADD, total, self.lower(t))
        return total

    def _product(self, factors: list[sp.Expr]) -> Value | None:
        """Multiply factors together; None when there are none."""
        value = None
        for f in factors:
            v = self.lower(f)
            value = v if value is None else self.emit(FpuOp.MUL, value, v)
        return value

    def _mul(self, e: sp.Mul) -> Value:
        # Sort into what is multiplied and what is divided by, so that a/b
        # is one DIV. A numeric coefficient stays a multiply by a rounded
        # constant, because the divider is much slower than the multiplier.
        coeff, rest = e.as_coeff_Mul()
        top: list[sp.Expr] = []
        bottom: list[sp.Expr] = []
        for f in sp.Mul.make_args(rest):
            base, power = f.as_base_exp()
            if power.is_number and power.is_negative:
                bottom.append(sp.Pow(base, -power))
            else:
                top.append(f)
        num = self._product(top)
        den = self._product(bottom)
        if den is not None:
            value = self.emit(FpuOp.DIV, num if num is not None else self.number(1), den)
        else:
            value = num
        return value if coeff == 1 else self.emit(FpuOp.MUL, value, self.number(coeff))

    def _int_power(self, value: Value, n: int) -> Value:
        """value^n for n >= 1, by square and multiply: x^5 = (x^2)^2 * x. emit()
        shares the squares with the rest of the program."""
        if n == 1:
            return value
        half = self._int_power(value, n // 2)
        square = self.emit(FpuOp.MUL, half, half)
        return square if n % 2 == 0 else self.emit(FpuOp.MUL, square, value)

    def _pow(self, base: sp.Expr, power: sp.Expr) -> Value:
        # I prefer multiply/sqrt for integer and half-integer powers; general powers use exp(y*ln(x)).
        if power.is_Integer or (power.is_Rational and power.q == 2):
            # x^(n/2) is the square root raised to n, so one SQRT is enough.
            root = self.lower(base) if power.is_Integer else self.emit(FpuOp.SQRT, self.lower(base))
            n = int(power) if power.is_Integer else int(power.p)
            value = self._int_power(root, abs(n))
            return value if n > 0 else self.emit(FpuOp.DIV, self.number(1), value)
        # A general power: exp(y ln x). It combines two rounded results, so
        # the 1 ULP budget doesn't cover it, and it gives NaN for x < 0.
        log_base = self.number(sp.log(base)) if base.is_number else self.emit(FpuOp.LN, self.lower(base))
        return self.emit(FpuOp.EXP, self.emit(FpuOp.MUL, self.lower(power), log_base))

    def _sqrt_one_minus_square(self, x: sp.Expr) -> Value:
        # I share the same lowering path for the inverse-trig denominator.
        return self.lower(sp.sqrt(1 - x**2))

    def _function(self, e: sp.Expr) -> Value:
        # I lower named functions to existing FPU ops, and reject anything I cannot represent.
        f = e.func
        if f is sp.sin:
            return self.emit(FpuOp.SIN, self.lower(e.args[0]))
        if f is sp.cos:
            return self.emit(FpuOp.COS, self.lower(e.args[0]))
        if f is sp.exp:
            return self.emit(FpuOp.EXP, self.lower(e.args[0]))
        if f is sp.log:
            return self.emit(FpuOp.LN, self.lower(e.args[0]))
        if f is sp.atan2:
            return self.emit(FpuOp.ATAN2, self.lower(e.args[0]), self.lower(e.args[1]))
        if f is sp.tan:
            return self.lower(sp.sin(e.args[0]) / sp.cos(e.args[0]))
        if f is sp.sec:
            return self.lower(1 / sp.cos(e.args[0]))
        if f is sp.csc:
            return self.lower(1 / sp.sin(e.args[0]))
        if f is sp.cot:
            return self.lower(sp.cos(e.args[0]) / sp.sin(e.args[0]))
        if f is sp.sinh:
            x = e.args[0]
            return self.lower((sp.exp(x) - sp.exp(-x)) / 2)
        if f is sp.cosh:
            x = e.args[0]
            return self.lower((sp.exp(x) + sp.exp(-x)) / 2)
        if f is sp.tanh:
            x = e.args[0]
            return self.emit(FpuOp.DIV, self.lower(sp.sinh(x)), self.lower(sp.cosh(x)))
        if f is sp.atan:
            return self.emit(FpuOp.ATAN2, self.lower(e.args[0]), self.number(1))
        if f is sp.asin:
            return self.emit(FpuOp.ATAN2, self.lower(e.args[0]), self._sqrt_one_minus_square(e.args[0]))
        if f is sp.acos:
            return self.emit(FpuOp.ATAN2, self._sqrt_one_minus_square(e.args[0]), self.lower(e.args[0]))
        if f is sp.Abs:
            x = self.lower(e.args[0])
            return self.emit(FpuOp.MAX, x, self.emit(FpuOp.MUL, x, self.number(-1)))
        if f in (sp.Min, sp.Max) and len(e.args) >= 2:
            op = FpuOp.MIN if f is sp.Min else FpuOp.MAX
            value = self.lower(e.args[0])
            for a in e.args[1:]:
                value = self.emit(op, value, self.lower(a))
            return value
        raise CompileError(f"no FPU equivalent for {f.__name__}: {e}")


def _allocate(ops: list[_Op], output: int) -> list[Instr]:
    """I discard dead values and reuse registers once their final source read is done."""
    needed = {output}
    for k in range(output, -1, -1):
        if k in needed:
            needed |= {s for s in (ops[k].a, ops[k].b) if isinstance(s, int)}
    keep = [k for k in range(output + 1) if k in needed]

    last_use = {k: k for k in keep}
    for k in keep:
        for s in (ops[k].a, ops[k].b):
            if isinstance(s, int):
                last_use[s] = k

    free = list(range(spec.EVU_REGISTERS - 1, -1, -1))     # pop() hands out R0 first
    where: dict[int, int] = {}
    out: list[Instr] = []

    def field_of(s: Value | None) -> int:
        if s is None:
            return 0
        return const(s.index) if isinstance(s, _Const) else reg(where[s])

    for k in keep:
        o = ops[k]
        fields = (field_of(o.a), field_of(o.b))
        # Sources that end here go back on the free list first, so the result
        # can take one of their registers. dict.fromkeys avoids freeing twice for x*x.
        for s in dict.fromkeys(x for x in (o.a, o.b) if isinstance(x, int)):
            if last_use[s] == k:
                free.append(where[s])
        if not free:
            raise CompileError(f"needs more than {spec.EVU_REGISTERS} registers")
        where[k] = free.pop()
        if o.table is not None:
            out.append(Instr(OP_LOAD, where[k], a=o.table))
        else:
            out.append(Instr(o.op, where[k], *fields))
        if last_use[k] == k and k != output:
            free.append(where[k])
    return out


def compile_expr(expr: sp.Expr, tables: dict[sp.Symbol, int], weights: list[int] | None = None) -> Program:
    """I turn the expression into bytecode, with tables supplying its grid variables.

    weights lists more tables (the quadrature weights), multiplied in after the
    expression so that the output of a point is f(x) * w. Other free symbols
    are an error: give them a value before compiling.
    """
    b = _Builder(tables)
    replacements, reduced = sp.cse(sp.sympify(expr), symbols=sp.numbered_symbols("_cse"), order="none")
    for sym, sub in replacements:
        b.done[sym] = b.lower(sub)
    output = b.lower(reduced[0])
    for w in weights or []:
        b.ops.append(_Op(OP_LOAD, table=w))
        load = len(b.ops) - 1
        output = b.emit(FpuOp.MUL, output, load)
    if isinstance(output, _Const):
        # The output has to come from an instruction, so a constant expression
        # needs one. Multiplying by exactly 1 keeps the value.
        output = b.emit(FpuOp.MUL, output, b.number(1))
    instrs = _allocate(b.ops, output)
    if len(instrs) > spec.EVU_PROGRAM_LEN:
        raise CompileError(f"{len(instrs)} instructions, the limit is {spec.EVU_PROGRAM_LEN}")
    words = [i.encode() for i in instrs[:-1]]
    t = instrs[-1]
    words.append(Instr(t.op, t.dst, t.a, t.b, last=True).encode())
    program = Program(words, b.constants)
    program.check()
    return program
