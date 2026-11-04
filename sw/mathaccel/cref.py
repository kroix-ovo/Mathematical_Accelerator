"""Build and call the C reference models (hw/tb/uvm/dpi/*.c) from Python.

The same C files are the DPI-C models behind the SystemVerilog scoreboards:
fp_ref.c (CPU float arithmetic, and libm in double precision for the phase 3
functions) and cordic_ref.c (the C port of the bit-accurate CORDIC model).
Here I compile them into one shared library and call it through ctypes, so
the Python tests can compare fp_ref with fp32ref/mpref and cordic_ref with
cordic_model, and the ULP tools can use fp_ref as the fast screen for large
sweeps (the *_eval_many functions do a whole array per call).

On Windows I prefer the MinGW gcc 6.2 that ships with Vivado, because that is
the compiler xsim's xsc uses for DPI code: the Python tests then see the same
compiled behaviour (and the same C library) as the xsim scoreboard.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DPI = ROOT / "hw" / "tb" / "uvm" / "dpi"
SRC = DPI / "fp_ref.c"
SOURCES = (SRC, DPI / "cordic_ref.c")
DEPS = (*SOURCES, DPI / "cordic_tables.h")


def find_cc() -> str | None:
    """Vivado's MinGW gcc on Windows (newest 6.2.0 first), else gcc/cc on PATH."""
    if os.name == "nt":
        for ver in ("6.2.0", "*"):
            for guess in sorted(Path("C:/Xilinx/Vivado").glob(f"*/tps/mingw/{ver}/win64.o/nt/bin/gcc.exe"),
                                reverse=True):
                return str(guess)
    return shutil.which("gcc") or shutil.which("cc")


class CRef:
    """A loaded fp_ref library. eval() does one operation, eval_many() an array."""

    def __init__(self, lib: ctypes.CDLL):
        """Declare C argument widths before passing Python data across the library boundary."""
        self.lib = lib
        u32p = ctypes.POINTER(ctypes.c_uint32)
        intp = ctypes.POINTER(ctypes.c_int)
        # I pass operand bits as uint32, not as C floats, so NaN payloads
        # and signed zero survive the call. Flags come back through an
        # output pointer, independently of the returned 32-bit result.
        lib.fp_ref_eval.restype = ctypes.c_uint32
        lib.fp_ref_eval.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32, intp]
        lib.fp_ref_eval_many.restype = None
        lib.fp_ref_eval_many.argtypes = [ctypes.c_int, ctypes.c_int, u32p, u32p, u32p, intp]
        lib.cordic_ref_eval.restype = ctypes.c_uint32
        lib.cordic_ref_eval.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32, intp]
        lib.cordic_ref_eval_many.restype = None
        lib.cordic_ref_eval_many.argtypes = [ctypes.c_int, ctypes.c_int, u32p, u32p, u32p, intp]

    def eval(self, op: int, a: int, b: int = 0) -> tuple[int, int]:
        """Return one libm/CPU reference result and flags from raw operand bit patterns."""
        f = ctypes.c_int(0)
        r = self.lib.fp_ref_eval(int(op), a & 0xFFFFFFFF, b & 0xFFFFFFFF, ctypes.byref(f))
        return int(r), f.value

    def eval_many(self, op: int, a, b=None) -> tuple[np.ndarray, np.ndarray]:
        """Arrays of results (uint32) and flags (int32) for arrays of operand bits."""
        return self._many(self.lib.fp_ref_eval_many, op, a, b)

    def cordic_many(self, op: int, a, b=None) -> tuple[np.ndarray, np.ndarray]:
        """Like eval_many, but from cordic_ref.c (what the hardware returns, bit for bit)."""
        return self._many(self.lib.cordic_ref_eval_many, op, a, b)

    @staticmethod
    def _many(fn, op: int, a, b) -> tuple[np.ndarray, np.ndarray]:
        """Make contiguous 32-bit arrays, then let C fill result and flag arrays in one call."""
        # C walks adjacent words through pointers; NumPy slices need not
        # be adjacent. Materialize the required layout before the call.
        a = np.ascontiguousarray(a, dtype=np.uint32)
        b = np.zeros_like(a) if b is None else np.ascontiguousarray(b, dtype=np.uint32)
        r = np.empty_like(a)
        f = np.empty(a.shape, dtype=np.int32)
        u32p = ctypes.POINTER(ctypes.c_uint32)
        fn(int(op), int(a.size), a.ctypes.data_as(u32p), b.ctypes.data_as(u32p),
           r.ctypes.data_as(u32p), f.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
        return r, f


def load(out_dir: Path | None = None) -> CRef | None:
    """Compile the C models (if needed) and load them. Returns None with no C compiler."""
    cc = find_cc()
    if cc is None:
        return None
    out_dir = out_dir or ROOT / "build" / "cref"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("fp_ref.dll" if os.name == "nt" else "fp_ref.so")
    # Rebuild when any source is newer than the library, so edits to the C
    # files (or regenerated tables) are always picked up.
    if not out.exists() or out.stat().st_mtime < max(s.stat().st_mtime for s in DEPS):
        env = dict(os.environ)
        env["PATH"] = str(Path(cc).parent) + os.pathsep + env.get("PATH", "")
        # -O0 matches xsc's own compile flags.
        subprocess.run([cc, "-O0", "-shared", "-fPIC", "-o", str(out), *map(str, SOURCES), "-lm"],
                       check=True, env=env)
    if os.name == "nt":
        # MinGW's runtime DLLs live next to its gcc.exe.
        os.add_dll_directory(str(Path(cc).parent))
    return CRef(ctypes.CDLL(str(out)))
