"""Check the Python fp32 reference against two independent references.

* NumPy float32: the CPU's arithmetic, results only (NumPy cannot report the
  inexact flag, so flags are not compared here).
* hw/tb/uvm/dpi/fp_ref.c: the same CPU arithmetic plus hardware exception
  flags, built as a shared library if a C compiler is available. This is the
  DPI model the SV scoreboard uses, so this test also checks that the SV and
  Python scoreboards agree with each other.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import warnings
from pathlib import Path

import numpy as np
import pytest

from mathaccel import fp32ref as R
from mathaccel._generated.spec import FpuOp
from mathaccel.fpgen import stream

ROOT = Path(__file__).resolve().parents[2]

SPECIALS = [
    0x00000000, 0x80000000, 0x00000001, 0x80000001, 0x007FFFFF, 0x00800000,
    0x00800001, 0x3F800000, 0xBF800000, 0x3F800001, 0x3FFFFFFF, 0x40000000,
    0x7F7FFFFF, 0xFF7FFFFF, 0x7F800000, 0xFF800000, 0x7FC00000, 0xFFC00001,
    0x7F800001, 0xFFA00000, 0x4F000000, 0xCF000000, 0x4EFFFFFF, 0x3F000000,
    0x3EFFFFFF, 0x34000000, 0x00400000, 0x7F000000,
]


# ---------------------------------------------------------------------------
# Hand-checked values
# ---------------------------------------------------------------------------

def test_directed_values():
    one, two = 0x3F800000, 0x40000000
    assert R.fadd(one, one) == (two, 0)
    # 0.1f + 0.2f = 0x3E99999A, inexact
    assert R.fadd(0x3DCCCCCD, 0x3E4CCCCD) == (0x3E99999A, R.NX)
    # 1 + 2^-24 is exactly halfway between 1 and the next float: ties to even -> 1
    assert R.fadd(one, 0x33800000) == (one, R.NX)
    # 1 + 3*2^-24 is halfway between 1+2^-23 and 1+2^-22 -> even mantissa 1+2^-22
    assert R.fadd(one, 0x34400000) == (0x3F800002, R.NX)
    assert R.fsub(one, one) == (0x00000000, 0)               # x - x = +0
    assert R.fadd(0x80000000, 0x80000000) == (0x80000000, 0)  # -0 + -0 = -0
    assert R.fmul(0x7F7FFFFF, two) == (0x7F800000, R.OF | R.NX)
    assert R.fmul(0x00800000, 0x3F000000) == (0x00400000, 0)  # exact subnormal: no UF
    assert R.fmul(0x00800001, 0x3F000000) == (0x00400000, R.UF | R.NX)  # tie to even
    assert R.fdiv(one, 0x00000000) == (0x7F800000, R.DZ)
    assert R.fdiv(0, 0) == (R.CANON_NAN, R.NV)
    assert R.fsqrt(0xBF800000) == (R.CANON_NAN, R.NV)
    assert R.fsqrt(0x80000000) == (0x80000000, 0)
    assert R.fsqrt(0x40800000) == (two, 0)
    assert R.fadd(0x7F800001, one) == (R.CANON_NAN, R.NV)    # sNaN input
    assert R.fadd(0x7FC00001, one) == (R.CANON_NAN, 0)       # qNaN input
    assert R.ff2i(0x3F000000) == (0, R.NX)                   # 0.5 -> 0 (even)
    assert R.ff2i(0x3FC00000) == (2, R.NX)                   # 1.5 -> 2
    assert R.ff2i(0x4F000000) == (R.INT_MAX, R.NV)           # 2^31 out of range
    assert R.ff2i(0xCF000000) == (R.INT_MIN, 0)              # -2^31 fits
    assert R.fi2f(0x7FFFFFFF) == (0x4F000000, R.NX)
    assert R.fmin(0x00000000, 0x80000000) == (0x80000000, 0)
    assert R.fmax(0x7FC00000, one) == (one, 0)
    assert R.fmin(0x7F800001, one) == (one, R.NV)
    assert R.feq(0x00000000, 0x80000000) == (1, 0)
    assert R.flt(0x7FC00000, one) == (0, R.NV)
    assert R.feq(0x7FC00000, one) == (0, 0)


# ---------------------------------------------------------------------------
# NumPy cross-check (results only)
# ---------------------------------------------------------------------------

def _np_canon(x: np.ndarray) -> np.ndarray:
    bits = x.view(np.uint32).copy()
    bits[np.isnan(x)] = R.CANON_NAN
    return bits


NUMPY_OPS = {
    FpuOp.ADD: lambda a, b: a + b,
    FpuOp.SUB: lambda a, b: a - b,
    FpuOp.MUL: lambda a, b: a * b,
    FpuOp.DIV: lambda a, b: a / b,
    FpuOp.SQRT: lambda a, b: np.sqrt(a),
}


def _cases(op: int, n: int, seed: int):
    pairs = [(a, b) for a in SPECIALS for b in SPECIALS]
    pairs += [(a, b) for _, a, b in stream(seed, n, op)]
    return pairs


@pytest.mark.parametrize("op", list(NUMPY_OPS))
def test_against_numpy(op):
    pairs = _cases(op, 20000, seed=0xC0FFEE + op)
    a = np.array([p[0] for p in pairs], dtype=np.uint32).view(np.float32)
    b = np.array([p[1] for p in pairs], dtype=np.uint32).view(np.float32)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        expect = _np_canon(NUMPY_OPS[op](a, b).astype(np.float32))
    for (x, y), e in zip(pairs, expect, strict=True):
        got, _ = R.evaluate(op, x, y)
        assert got == int(e), f"op {FpuOp(op).name} a={x:08x} b={y:08x}: model {got:08x} numpy {int(e):08x}"


def test_i2f_against_numpy():
    ints = np.array([0, 1, -1, 2**24 + 1, 2**31 - 1, -(2**31), 16777217, -16777219]
                    + list(np.random.default_rng(1).integers(-(2**31), 2**31, 20000)), dtype=np.int64)
    expect = ints.astype(np.int32).astype(np.float32).view(np.uint32)
    for i, e in zip(ints, expect, strict=True):
        assert R.fi2f(int(i) & 0xFFFFFFFF)[0] == int(e)


# ---------------------------------------------------------------------------
# C / DPI reference cross-check (results and flags)
# ---------------------------------------------------------------------------

def _find_cc() -> str | None:
    """Prefer the exact gcc that Vivado's xsc uses for DPI code (MinGW 6.2),
    so this test sees the same compiled behaviour as the xsim scoreboard."""
    if os.name == "nt":
        for ver in ("6.2.0", "*"):
            for guess in sorted(Path("C:/Xilinx/Vivado").glob(f"*/tps/mingw/{ver}/win64.o/nt/bin/gcc.exe"),
                                reverse=True):
                return str(guess)
    return shutil.which("gcc") or shutil.which("cc")


@pytest.fixture(scope="module")
def cref(tmp_path_factory):
    cc = _find_cc()
    if cc is None:
        pytest.skip("no C compiler for the DPI reference")
    out = tmp_path_factory.mktemp("cref") / ("fp_ref.dll" if os.name == "nt" else "fp_ref.so")
    src = ROOT / "hw/tb/uvm/dpi/fp_ref.c"
    env = dict(os.environ)
    env["PATH"] = str(Path(cc).parent) + os.pathsep + env.get("PATH", "")
    # -O0 matches xsc's own compile flags.
    subprocess.run([cc, "-O0", "-shared", "-fPIC", "-o", str(out), str(src), "-lm"],
                   check=True, env=env)
    if os.name == "nt":
        os.add_dll_directory(str(Path(cc).parent))
    lib = ctypes.CDLL(str(out))
    lib.fp_ref_eval.restype = ctypes.c_uint32
    lib.fp_ref_eval.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int)]

    def call(op, a, b):
        f = ctypes.c_int(0)
        r = lib.fp_ref_eval(op, a, b, ctypes.byref(f))
        return r, f.value
    return call


@pytest.mark.parametrize("op", list(FpuOp))
def test_against_c_reference(cref, op):
    mismatches = []
    for a, b in _cases(op, 20000, seed=0xBEEF00 + op):
        want = R.evaluate(op, a, b)
        got = cref(op, a, b)
        if want != got:
            mismatches.append(f"a={a:08x} b={b:08x} model={want[0]:08x}/{want[1]:02x} c={got[0]:08x}/{got[1]:02x}")
    assert not mismatches, f"{FpuOp(op).name}: {len(mismatches)} mismatches, first:\n" + "\n".join(mismatches[:10])


def test_generator_is_deterministic():
    assert list(stream(42, 50)) == list(stream(42, 50))
    # Pinned values. fp_lcg.svh (the SV twin) checks itself against the same
    # numbers in fp_lcg::selftest(), so a change on either side is caught.
    assert list(stream(1, 4)) == [
        (0, 0x815A399D, 0x015A3991), (10, 0x80000000, 0xBEEB20A4),
        (0, 0x53AE1CEB, 0x3E8D6160), (6, 0x485A870C, 0x48601DA5)]
    assert list(stream(0xACE1, 3, 2)) == [
        (2, 0x937A0DAF, 0x385FF4F8), (2, 0xCC0A361E, 0xBF3D2359), (2, 0x00000000, 0x80000000)]
