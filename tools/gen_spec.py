"""Generate the SystemVerilog package and Python constants from spec/accel.toml.

Usage: python tools/gen_spec.py [--check]
--check exits non-zero if the generated files are out of date (used in CI).

I keep protocol numbers in one TOML file rather than maintaining separate HDL
and Python copies. The renderers below generate both languages, including their
explanatory comments. CI compares the generated text without changing files.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Resolve from this script, so invocation does not depend on the working folder.
SPEC = ROOT / "spec" / "accel.toml"
SV_OUT = ROOT / "hw" / "rtl" / "pkg" / "accel_pkg.sv"
PY_OUT = ROOT / "sw" / "mathaccel" / "_generated" / "spec.py"

# (TOML section, SystemVerilog name prefix, bit width, Python enum class name)
SECTIONS = (
    ("opcodes", "OP_", 8),
    ("status", "ST_", 8),
    ("kernels", "K_", 8),
    ("fpu_ops", "FOP_", 4),
    ("fpu_flags", "FFLAG_", 5),
)
PY_CLASSES = {"opcodes": "Op", "status": "Status", "kernels": "Kernel",
              "fpu_ops": "FpuOp", "fpu_flags": "FpuFlag"}

HEADER = "AUTO-GENERATED from spec/accel.toml by tools/gen_spec.py -- do not edit."


def mac_int(text: str) -> int:
    """Turn "02:4D:41:43:43:01" into one 48-bit integer, first byte highest."""
    return int(text.replace(":", ""), 16)


def ip_int(text: str) -> int:
    """Turn dotted IPv4 notation into one 32-bit integer, first byte highest."""
    a, b, c, d = (int(x) for x in text.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def render_sv(spec: dict) -> str:
    """Render constant declarations consumed by the synthesizable RTL modules."""
    d = spec["device"]
    lines = [
        f"// {HEADER}",
        "// Shared device settings and one-byte command IDs. I generate this",
        "// package with the Python constants so both ends use the same protocol.",
        "// localparam fixes each value at compile time; this package adds no registers.",
        "package accel_pkg;", "",
        "  // Identity, version, clock/baud settings, and buffer limits.",
        "  // SCRATCH_AW counts word-address bits; each scratchpad word is 32 bits.",
    ]
    lines.append(f"  localparam logic [31:0] MAGIC         = 32'h{d['magic']:08X};")
    lines.append(f"  localparam int          VERSION_MAJOR = {d['version_major']};")
    lines.append(f"  localparam int          VERSION_MINOR = {d['version_minor']};")
    lines.append(f"  localparam int          CLK_HZ        = {d['clk_hz']};")
    lines.append(f"  localparam int          BAUD          = {d['baud']};")
    lines.append(f"  localparam int          SCRATCH_AW    = {d['scratch_addr_bits']};")
    lines.append(f"  localparam int          MAX_PAYLOAD   = {d['max_payload']};")
    # The Ethernet identity is written as packed hex so the RTL can compare a
    # received byte with one slice of it, e.g. ETH_MAC[47 -: 8] is the first
    # byte on the wire. Dotted and colon notation stay in the TOML for people.
    e = spec["ethernet"]
    lines += ["", "  // Ethernet/UDP transport: board MAC and IPv4 address, UDP command port.",
              "  // The first byte on the wire is the most significant byte here."]
    lines.append(f"  localparam logic [47:0] ETH_MAC       = 48'h{mac_int(e['mac']):012X};")
    lines.append(f"  localparam logic [31:0] ETH_IP        = 32'h{ip_int(e['ip']):08X};")
    lines.append(f"  localparam logic [15:0] ETH_UDP_PORT  = 16'd{e['udp_port']};")
    # A section's prefix keeps opcode, reply status, and kernel names distinct.
    descriptions = {
        "opcodes": "Request opcodes; a reply sets bit 7 of the same opcode.",
        "status": "First byte of every reply payload; zero means success.",
        "kernels": "Kernel IDs carried in the first byte of a RUN request payload.",
        "fpu_ops": "FPU operation codes, four bits wide (fpu.sv op input).",
        "fpu_flags": "IEEE exception flag masks, five bits wide (RISC-V fflags order).",
    }
    # Each section gets its own name prefix and bit width. The width makes the
    # constant the same size as the port it is compared with, so tools do not
    # warn about silently truncated constants.
    for section, prefix, width in SECTIONS:
        lines += ["", f"  // {descriptions[section]}"]
        for name, val in spec[section].items():
            lines.append(f"  localparam logic [{width - 1}:0] {prefix}{name} = {width}'h{val:0{(width + 3) // 4}X};")
    lines += ["", "endpackage", ""]
    return "\n".join(lines)


def render_py(spec: dict) -> str:
    """Render equivalent host constants and integer enums from the same input."""
    d = spec["device"]
    lines = [
        '"""' + HEADER,
        "",
        "Device settings and packet IDs shared with hw/rtl/pkg/accel_pkg.sv.",
        "I generate both copies together; edit the TOML and regenerate them.",
        '"""', "", "from enum import IntEnum", "",
        "# Device identity, version, clock, baud, and packet/memory sizes.",
    ]
    for key in ("magic", "version_major", "version_minor", "clk_hz", "baud",
                "scratch_addr_bits", "max_payload"):
        val = d[key]
        lines.append(f"{key.upper()} = {hex(val) if key == 'magic' else val}")
    lines += [
        "# N address bits select 2**N words, each containing 32 data bits.",
        "SCRATCH_WORDS = 1 << SCRATCH_ADDR_BITS",
    ]
    e = spec["ethernet"]
    lines += [
        "",
        "# Ethernet/UDP transport: board identity, laptop address, command port.",
        f"ETH_MAC = {e['mac']!r}",
        f"ETH_IP = {e['ip']!r}",
        f"HOST_IP = {e['host_ip']!r}",
        f"UDP_PORT = {e['udp_port']}",
    ]
    descriptions = {
        "Op": "Request IDs; replies use these values with bit 7 set.",
        "Status": "First reply payload byte; OK is the successful result.",
        "Kernel": "RUN payload IDs selecting a hardware kernel.",
        "FpuOp": "FPU operation codes used by the FPU kernel and fpu.sv.",
        "FpuFlag": "IEEE exception flag bit masks (RISC-V fflags order).",
    }
    for section, _prefix, _width in SECTIONS:
        cls = PY_CLASSES[section]
        # IntEnum keeps readable names while still packing as integer fields.
        lines += ["", "", f"class {cls}(IntEnum):", f'    """{descriptions[cls]}"""', ""]
        for name, val in spec[section].items():
            lines.append(f"    {name} = 0x{val:02X}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Compare outputs in check mode, or write both generated files together."""
    spec = tomllib.loads(SPEC.read_text())
    outputs = {SV_OUT: render_sv(spec), PY_OUT: render_py(spec)}
    if "--check" in sys.argv:
        # Text equality also catches stale generated comments. No writes happen
        # in this path, so CI reports missing regeneration without repairing it.
        stale = [p for p, text in outputs.items() if not p.exists() or p.read_text() != text]
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if stale else 0
    for path, text in outputs.items():
        # Fixed line endings keep generated contents stable across platforms.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, newline="\n")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
