# Optional Rust framing backend

From the checkout root, with Python >=3.11 and a Rust toolchain:

```sh
python -m pip install -e '.[dev]' maturin==1.16.0
python tools/build_framing_native.py
PYTHONPATH=sw MATHACCEL_FRAMING=native python -m pytest -q
PYO3_PYTHON="$(command -v python)" cargo test --locked --manifest-path native/rust/Cargo.toml
```

On PowerShell set `$env:PYTHONPATH='sw'` and
`$env:MATHACCEL_FRAMING='native'` before pytest. A native Windows build also
needs the MSVC build tools; this experiment validates builds on WSL Linux.
`Cargo.lock` pins transitive dependencies. The builder invokes maturin with
`--release --locked --features extension-module` and copies the extension from
the wheel into this checkout only. It never installs a backend into another
variant. Normal setuptools installation of the project remains Python-only;
rebuild the extension after a clean checkout or interpreter change.

`codec.rs` contains pure CRC and COBS operations, with typed `DecodeError`
implementing Display and Error. `lib.rs` borrows immutable input slices through
PyO3 and copies owned results into Python bytes. No unsafe Rust is present in
this crate; PyO3 implements the FFI. One Vec and one Python bytes allocation
are used for COBS, matching the C backend's temporary-buffer/result ownership.
The GIL stays held for these bounded packet operations in both implementations.

The common Python adapter preserves public function signatures and CobsError.
`MATHACCEL_FRAMING=python` forces the shared optimized Python implementation;
`native` fails if the extension is missing; `auto` uses a local extension if
available, otherwise Python. Inspect `framing.BACKEND` before timing.

SymPy, compiler lowering/allocation, NumPy, CLI, device orchestration, transports,
timeouts, request tags, EVU RUN recovery and DPI reference models stay in Python
or their existing implementation. The measured bottleneck justified framing;
it did not justify moving those components or changing floating-point code.

Binding API references: [PyO3 functions](https://pyo3.rs/v0.27.2/function)
and [modules](https://pyo3.rs/v0.27.2/module).
