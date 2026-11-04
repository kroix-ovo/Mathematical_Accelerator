"""Generate hw/rtl/fp/cordic_pkg.sv and hw/tb/uvm/dpi/cordic_tables.h from the
bit-accurate CORDIC model.

Usage: python tools/gen_cordic.py [--check]
--check exits non-zero if a generated file is out of date (used in CI).

The datapath widths and every constant the CORDIC unit needs (the Payne-Hanek
ROM of 2/pi windows, the arctangent table, the CORDIC gain correction, pi/2)
are defined once, in sw/mathaccel/cordic_model.py. This script copies them
into a SystemVerilog package, so the RTL and the Python model that the
testbench checks it against always use the same numbers. The C header holds
the same tables for cordic_ref.c, the C port of the model that the
SystemVerilog scoreboards call through DPI. To change a width,
edit CordicParams in the model, rerun this script, and remeasure with
tools/measure_ulp.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from mathaccel import cordic_model as M  # noqa: E402

OUT = ROOT / "hw" / "rtl" / "fp" / "cordic_pkg.sv"
OUT_C = ROOT / "hw" / "tb" / "uvm" / "dpi" / "cordic_tables.h"
HEADER = "AUTO-GENERATED from sw/mathaccel/cordic_model.py by tools/gen_cordic.py -- do not edit."


def hexw(value: int, width: int) -> str:
    """A width-bit SV literal in hex, e.g. 46'h0..."""
    return f"{width}'h{value:0{(width + 3) // 4}X}"


def render() -> str:
    """Render the SV constants from the model; this is the editable source of generated comments too."""
    p = M.DEFAULT
    rom = M.ph_rom(p)
    atan = M.atan_table(p)
    atan_r = M.atan_rad_table(p)
    pio2_z, pi_z = M.pi_z(p)
    hl, hn = M.atanh_tables(p)
    hs = M.hyp_shifts(p)
    erom = M.exp_rom(p)
    zw = p.Z + 7                       # z width: 6 integer bits and a sign
    lines = [
        f"// {HEADER}",
        "//",
        "// Widths and constant tables for fp_cordic.sv. The numbers come from the",
        "// bit-accurate Python model, which the testbench compares the RTL against,",
        "// so both always use the same constants. See cordic_model.py for how each",
        "// one is derived and tools/measure_ulp.py for how the widths were chosen.",
        "//",
        "// sin/cos work with angles in quarter turns (units of pi/2): the",
        "// Payne-Hanek reduction produces them in those units, so the CORDIC never",
        "// multiplies an angle by pi/2. atan2 accumulates its angle in radians, so",
        "// it has its own arctangent table, and the result needs no scaling either.",
        "// exp works in units of ln2 for the same reason as sin/cos, and ln in",
        "// natural units, each with its own hyperbolic arctangent table.",
        "//",
        "// An integer table entry T with F fraction bits represents T / 2^F.",
        "// I generate these bit patterns once so the hardware and datapath models",
        "// share the same quantization. The separate mpmath reference checks",
        "// mathematical accuracy; sharing tables alone cannot establish it.",
        "package cordic_pkg;",
        "",
        "  // Datapath widths.",
        f"  localparam int PH_L    = {p.L};    // bits of 2/pi in the Payne-Hanek window",
        f"  localparam int PH_F    = {p.F};    // fraction bits of the reduced angle f",
        f"  localparam int CZ      = {p.Z};    // fraction bits of the CORDIC angle z",
        f"  localparam int CW      = {p.W};    // fraction bits of the CORDIC x and y",
        f"  localparam int CN      = {p.N};    // CORDIC iterations, one pipeline stage each",
        f"  localparam int TINY_E  = {p.TINY_E};   // biased exponents below this take the tiny-input path",
        f"  localparam int SMALL_T = {p.SMALL_T};    // f' < 2^-SMALL_T takes the sin t = t path",
        f"  localparam int ATAN_D  = {p.ATAN_D};    // atan2 exponent gap from which y/x replaces the angle",
        f"  localparam int LN_T    = {p.LN_T};    // ln of x within 2^-LN_T of 1 takes the d/(2+d) path",
        f"  localparam int EXP_TINY_E = {p.EXP_TINY_E};  // exp: biased exponents below this give 1",
        f"  localparam int EXP_BIG_E  = {p.EXP_BIG_E};  // exp: biased exponents from this overflow/underflow",
        "  // z holds up to |ln x|/2 < 64: CZ fraction bits, 6 integer bits, sign.",
        "  localparam int ZW = CZ + 7;",
        "",
        "  // Start value of x: the product of 1/sqrt(1 + 2^-2i) over all stages, with",
        "  // CW fraction bits. Each stage stretches the vector by sqrt(1 + 2^-2i);",
        "  // starting this much shorter makes it end on the unit circle.",
        f"  localparam logic [CW+1:0] CORDIC_K = {hexw(M.cordic_k(p), p.W + 2)};",
        "",
        "  // pi/2 with 31 fraction bits, for the small-angle path.",
        f"  localparam logic [31:0] PIO2 = {hexw(M.pio2_const(), 32)};",
        "",
        "  // The same K with 32 fraction bits, to pre-scale the atan2/hypot inputs.",
        f"  localparam logic [31:0] KV = {hexw(M.kv_const(p), 32)};",
        "",
        "  // atan2 base angles pi/2 and pi in radians, CZ fraction bits, z's width.",
        f"  localparam logic [ZW-1:0] PIO2_Z = {hexw(pio2_z, zw)};",
        f"  localparam logic [ZW-1:0] PI_Z   = {hexw(pi_z, zw)};",
        "",
        "  // exp start value G/2, CW fraction bits: G = 1 / prod sqrt(1 - 2^-2s) undoes",
        "  // the hyperbolic stages' length change.",
        f"  localparam logic [CW+1:0] HYP_G2 = {hexw(M.hyp_g2(p), p.W + 2)};",
        "",
        "  // ln2/2 with CZ+8 fraction bits, for ln's base angle |e| * ln2/2.",
        f"  localparam logic [50:0] LN2H = {hexw(M.ln2h_const(p), 51)};",
        "",
        "  // sqrt(2) as a 24-bit significand, rounded up: m >= SQRT2_M means m >= sqrt(2).",
        f"  localparam logic [23:0] SQRT2_M = {hexw(M.SQRT2_M, 24)};",
        "",
        "  // Per-stage constants. Each stage calls these with its constant index i,",
        "  // so they fold to constants; no table exists in the hardware.",
        "  //   atan_q   atan(2^-i) in quarter turns (sin/cos)",
        "  //   atan_r   atan(2^-i) in radians (atan2, hypot)",
        "  //   hshift   the shift stage i uses in hyperbolic modes (4 and 13 repeat)",
        "  //   atanh_l  atanh(2^-hshift(i)) in units of ln2 (exp)",
        "  //   atanh_n  atanh(2^-hshift(i)) in natural units (ln)",
        "  // All have CZ fraction bits and are rounded to nearest.",
    ]

    def table(name: str, values, width: int, ret: str) -> None:
        """Emit a constant-index function so each unrolled CORDIC stage gets its own fixed value."""
        lines.append(f"  function automatic {ret} {name}(input int i);"
                     " // Iteration index: selects this generated stage's fixed angle or hyperbolic shift amount.")
        lines.append("    case (i)")
        for i, v in enumerate(values):
            lines.append(f"      {i:2d}: return {hexw(v, width) if width else v};")
        lines.extend(["      default: return '0;", "    endcase", "  endfunction", ""])

    table("atan_q", atan, zw, "logic [ZW-1:0]")
    table("atan_r", atan_r, zw, "logic [ZW-1:0]")
    table("atanh_l", hl, zw, "logic [ZW-1:0]")
    table("atanh_n", hn, zw, "logic [ZW-1:0]")
    table("hshift", hs, 0, "int")
    lines += [
        "  // Payne-Hanek window ROM. For an input with biased exponent e, the entry",
        "  // holds PH_L bits of 2/pi starting at fraction bit b_(e-151): the bits",
        "  // before it only add whole turns, and the bits after it are too small to",
        "  // matter. Windows for small exponents start before b_1 and so begin with",
        "  // zeros. Only exponents TINY_E .. 254 reach the reduction. Synthesis",
        "  // turns this case statement into a ROM of LUTs addressed by e.",
        "  function automatic logic [PH_L-1:0] ph_window(input logic [7:0] e);"
        " // Biased exponent: selects the 2/pi bit window used to reduce the input angle.",
        "    case (e)",
    ]
    for i, w in enumerate(rom):
        lines.append(f"      8'd{p.TINY_E + i}: return {hexw(w, p.L)};")
    lines += [
        "      default: return '0;",
        "    endcase",
        "  endfunction",
        "",
        "  // exp's window ROM, used by the same multiplier. For biased exponent e the",
        "  // entry is 2^(e-64)/ln2, rounded, so that significand times entry is",
        "  // |x|/ln2 with 86 fraction bits (and stays below 2^94, no wrap).",
        "  function automatic logic [PH_L-1:0] exp_window(input logic [7:0] e);"
        " // Biased exponent: aligns the 1/ln2 constant for exponential argument reduction.",
        "    case (e)",
    ]
    for i, w in enumerate(erom):
        lines.append(f"      8'd{p.EXP_TINY_E + i}: return {hexw(w, p.L)};")
    lines += [
        "      default: return '0;",
        "    endcase",
        "  endfunction",
        "",
        "endpackage",
        "",
    ]
    return "\n".join(lines)


def u128_init(v: int) -> str:
    """A ROM entry as a {high, low} pair of 64-bit halves for the C header."""
    return f"{{0x{v >> 64:016X}ull, 0x{v & ((1 << 64) - 1):016X}ull}}"


def render_c() -> str:
    """The same widths and tables as cordic_pkg.sv, as a C header for cordic_ref.c."""
    p = M.DEFAULT
    pio2_z, pi_z = M.pi_z(p)
    hl, hn = M.atanh_tables(p)

    def arr(name: str, values, ctype: str = "int64_t") -> list[str]:
        """Format a typed C array while preserving every model constant as an integer literal."""
        suffix = "LL" if ctype == "int64_t" else ""
        out = [f"static const {ctype} {name}[{len(values)}] = {{"]
        for i in range(0, len(values), 4):
            out.append("    " + " ".join(f"{v}{suffix}," for v in values[i:i + 4]))
        out.append("};")
        return out

    lines = [
        f"/* {HEADER} */",
        "/* Widths and tables for cordic_ref.c: the same numbers as hw/rtl/fp/cordic_pkg.sv. */",
        "/* Integer entries use the same fixed-point scaling as the SV package.",
        " * Edit CordicParams/the model and regenerate; do not tune this copy alone. */",
        "#ifndef CORDIC_TABLES_H",
        "#define CORDIC_TABLES_H",
        "#include <stdint.h>",
        "",
        f"#define PH_L {p.L}",
        f"#define PH_F {p.F}",
        f"#define CZ {p.Z}",
        f"#define CW {p.W}",
        f"#define CN {p.N}",
        f"#define TINY_E {p.TINY_E}",
        f"#define SMALL_T {p.SMALL_T}",
        f"#define ATAN_D {p.ATAN_D}",
        f"#define LN_T {p.LN_T}",
        f"#define EXP_TINY_E {p.EXP_TINY_E}",
        f"#define EXP_BIG_E {p.EXP_BIG_E}",
        f"#define SQRT2_M 0x{M.SQRT2_M:X}u",
        "",
        f"static const int64_t CORDIC_K = {M.cordic_k(p)}LL;",
        f"static const uint64_t KV = {M.kv_const(p)}ull;",
        f"static const uint64_t PIO2_C = {M.pio2_const()}ull;",
        f"static const int64_t PIO2_Z = {pio2_z}LL;",
        f"static const int64_t PI_Z = {pi_z}LL;",
        f"static const int64_t HYP_G2 = {M.hyp_g2(p)}LL;",
        f"static const int64_t LN2H = {M.ln2h_const(p)}LL;",
        "",
        *arr("ATAN_Q", M.atan_table(p)),
        *arr("ATAN_R", M.atan_rad_table(p)),
        *arr("ATANH_L", hl),
        *arr("ATANH_N", hn),
        *arr("HSHIFT", M.hyp_shifts(p), "int"),
        "",
        "/* ROM entries as {high, low} 64-bit halves. PH_ROM is indexed by",
        "   biased exponent - TINY_E, EXP_ROM by biased exponent - EXP_TINY_E. */",
        f"static const uint64_t PH_ROM[{len(M.ph_rom(p))}][2] = {{",
        *[f"    {u128_init(v)}," for v in M.ph_rom(p)],
        "};",
        f"static const uint64_t EXP_ROM[{len(M.exp_rom(p))}][2] = {{",
        *[f"    {u128_init(v)}," for v in M.exp_rom(p)],
        "};",
        "",
        "#endif",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    """Regenerate both language tables, or compare them without writing when CI passes --check."""
    outputs = ((OUT, render()), (OUT_C, render_c()))
    if "--check" in sys.argv:
        stale = [o for o, text in outputs if not o.exists() or o.read_text() != text]
        for o in stale:
            print(f"stale: {o.relative_to(ROOT)}")
        return 1 if stale else 0
    for o, text in outputs:
        o.write_text(text, newline="\n")
        print(f"wrote {o.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
