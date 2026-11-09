"""Build only this worktree's extension, leaving other variants independent."""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
native = root / "native/c"
table = []
for i in range(256):
    crc = i << 8
    for _ in range(8):
        crc = ((crc << 1) ^ 0x1021) & 0xffff if crc & 0x8000 else (crc << 1) & 0xffff
    table.append(crc)
(native / "crc_table.h").write_text("static const uint16_t ma_crc_table[256] = {\n" +
                                  ",\n".join(", ".join(f"0x{x:04x}" for x in table[i:i+8])
                                               for i in range(0, 256, 8)) + "\n};\n")
start = time.perf_counter()
subprocess.run([sys.executable, "setup.py", "build_ext", "--inplace"], cwd=native, check=True)
for pattern in ("_framing_native*.so", "_framing_native*.pyd"):
    for artifact in native.glob(pattern):
        shutil.copy2(artifact, root / "sw/mathaccel" / artifact.name)
print(f"release build wall seconds: {time.perf_counter() - start:.3f}")
