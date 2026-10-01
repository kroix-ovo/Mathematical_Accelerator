"""Generate the SystemVerilog package and Python constants from spec/accel.toml.

Usage: python tools/gen_spec.py [--check]
--check exits non-zero if the generated files are out of date (used in CI).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec" / "accel.toml"
SV_OUT = ROOT / "hw" / "rtl" / "pkg" / "accel_pkg.sv"
PY_OUT = ROOT / "sw" / "mathaccel" / "_generated" / "spec.py"

HEADER = "AUTO-GENERATED from spec/accel.toml by tools/gen_spec.py -- do not edit."


def render_sv(spec: dict) -> str:
    d = spec["device"]
    lines = [f"// {HEADER}", "package accel_pkg;", ""]
    lines.append(f"  localparam logic [31:0] MAGIC         = 32'h{d['magic']:08X};")
    lines.append(f"  localparam int          VERSION_MAJOR = {d['version_major']};")
    lines.append(f"  localparam int          VERSION_MINOR = {d['version_minor']};")
    lines.append(f"  localparam int          CLK_HZ        = {d['clk_hz']};")
    lines.append(f"  localparam int          BAUD          = {d['baud']};")
    lines.append(f"  localparam int          SCRATCH_AW    = {d['scratch_addr_bits']};")
    lines.append(f"  localparam int          MAX_PAYLOAD   = {d['max_payload']};")
    for section, prefix in (("opcodes", "OP_"), ("status", "ST_"), ("kernels", "K_")):
        lines.append("")
        for name, val in spec[section].items():
            lines.append(f"  localparam logic [7:0] {prefix}{name} = 8'h{val:02X};")
    lines += ["", "endpackage", ""]
    return "\n".join(lines)


def render_py(spec: dict) -> str:
    d = spec["device"]
    lines = [f'"""{HEADER}"""', "", "from enum import IntEnum", ""]
    for key in ("magic", "version_major", "version_minor", "clk_hz", "baud",
                "scratch_addr_bits", "max_payload"):
        val = d[key]
        lines.append(f"{key.upper()} = {hex(val) if key == 'magic' else val}")
    lines.append("SCRATCH_WORDS = 1 << SCRATCH_ADDR_BITS")
    for section, cls in (("opcodes", "Op"), ("status", "Status"), ("kernels", "Kernel")):
        lines += ["", "", f"class {cls}(IntEnum):"]
        for name, val in spec[section].items():
            lines.append(f"    {name} = 0x{val:02X}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    spec = tomllib.loads(SPEC.read_text())
    outputs = {SV_OUT: render_sv(spec), PY_OUT: render_py(spec)}
    if "--check" in sys.argv:
        stale = [p for p, text in outputs.items() if not p.exists() or p.read_text() != text]
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if stale else 0
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, newline="\n")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
