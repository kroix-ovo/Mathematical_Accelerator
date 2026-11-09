"""Build a release extension locally; do not install it over other variants."""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / "build" / "framing-wheels"
out.mkdir(parents=True, exist_ok=True)
start = time.perf_counter()
subprocess.run([sys.executable, "-m", "maturin", "build", "--release", "--locked",
                "--features", "extension-module", "--interpreter", sys.executable,
                "--manifest-path", str(root / "native/rust/Cargo.toml"), "--out", str(out)], check=True)
wheel = max(out.glob("*.whl"), key=lambda p: p.stat().st_mtime)
with zipfile.ZipFile(wheel) as archive:
    for entry in archive.namelist():
        if entry.endswith((".so", ".pyd")):
            target = root / "sw/mathaccel" / Path(entry).name
            with archive.open(entry) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)
            print(target)
print(f"release build wall seconds: {time.perf_counter() - start:.3f}")
