# Optional C framing backend

From the checkout root with Python >=3.11 and a C compiler:

```sh
python -m pip install -e '.[dev]' setuptools==84.2.1 wheel==0.48.0
python tools/build_framing_native.py
PYTHONPATH=sw MATHACCEL_FRAMING=native python -m pytest -q
gcc -std=c11 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer native/c/codec.c native/c/test_codec.c -o build/test_codec
build/test_codec
```

PowerShell: set `$env:PYTHONPATH='sw'` and
`$env:MATHACCEL_FRAMING='native'` before pytest. Windows builds use MSVC `/O2`;
Unix builds use `-O3 -std=c11`. WSL GCC builds and sanitizer tests are validated
here; native Windows builds are not measured. Normal project installation stays
Python-only. Rebuild the extension after changing interpreters or cleaning.

The builder generates the 256-entry CRC table, then compiles `codec.c` and
`bindings.c`, copying the extension into this checkout only. These are new host
codecs, independent of the existing RTL DPI-C references. CRC and COBS are the
same selected functions as Rust, with identical batching and wire semantics.

`codec.h` documents lengths, capacities and error codes. The core uses uint8_t,
uint16_t, uint32_t and size_t, checks capacity and encode-size overflow, and
performs no endian-sensitive casts or floating-point arithmetic. The binding
borrows immutable input while holding the GIL, owns a temporary output buffer,
copies it into Python-owned bytes and frees it on both success and failure.
There are no persistent native buffers or device state. The common Python
adapter normalizes seeds, preserves keyword arguments and translates ValueError
into the public CobsError. The same adapter is used in the Rust branch.

`MATHACCEL_FRAMING=python` forces optimized Python; `native` requires the build;
`auto` uses it when available. Confirm `framing.BACKEND` before benchmarking.
The sanitizer executable covers codec memory accesses; it is not a sanitizer
build of CPython or proof of safety for every possible input.

SymPy, compiler, NumPy, CLI, UART/UDP, request matching, retry handling,
EVU RUN recovery and all fp32 reference code stay in their existing language.
Keeping them avoids additional build, ownership and numerical complexity with
no measured justification for rewriting them.
