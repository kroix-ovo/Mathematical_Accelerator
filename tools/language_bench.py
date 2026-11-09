"""Measure a selected checkout without installing or editing its tracked sources.

Invoke with the same interpreter and --root for every variant. No board I/O.
Each invocation is an independent round; alternate order outside this process.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.metadata
import json
import os
import platform
import pstats
import random
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(root / "sw"))
    import numpy as np
    import sympy as sp
    from sympy.core.cache import clear_cache

    from mathaccel._generated import spec
    from mathaccel.compiler.evu_isa import Run, Table, memory_image
    from mathaccel.compiler.problems import PROBLEMS
    from mathaccel.compiler.quadrature import integral_run
    from mathaccel.compiler.sympy_to_evu import compile_expr
    from mathaccel.driver import framing
    from mathaccel.driver.device import connect
    from mathaccel.driver.protocol import Frame

    rng = random.Random(20261109)
    datasets = {n: bytes(rng.randrange(256) for _ in range(n)) for n in (0, 16, 256, 1024)}
    cases = {}
    fingerprints = {}
    for n, data in datasets.items():
        encoded = framing.cobs_encode(data)
        wire = Frame(2, 7, data).encode()[:-1]
        cases[f"crc/{n}"] = lambda d=data: framing.crc16(d)
        cases[f"encode/{n}"] = lambda d=data: framing.cobs_encode(d)
        cases[f"decode/{n}"] = lambda d=encoded: framing.cobs_decode(d)
        cases[f"packet_encode/{n}"] = lambda d=data: Frame(2, 7, d).encode()
        cases[f"packet_decode/{n}"] = lambda d=wire: Frame.decode(d)
        fingerprints[f"wire/{n}"] = wire.hex()

    x = sp.Symbol("x", real=True)
    expressions = {
        "small": x * x + 2,
        "repeated": (sp.sin(x) + sp.cos(x)) ** 2 + sp.sin(x) + sp.cos(x),
        "transcendental": sp.exp(-x * x) + sp.sin(x) / (x + 2),
        "large": sp.Add(*(sp.sin(x + sp.Rational(k, 100)) for k in range(40))),
        "near_limit": sp.Add(*(sp.sin(x + sp.Rational(k, 100)) for k in range(80))),
        "over_limit": sp.Add(*(sp.sin(x + sp.Rational(k, 100)) for k in range(90))),
        "unsupported": sp.erf(x),
        "nonfinite": sp.oo,
    }

    def compile_record(expr, tables):
        try:
            p = compile_expr(expr, tables)
            return [p.words, p.constants]
        except ValueError as e:
            return [type(e).__name__, str(e)]

    for name, expr in expressions.items():
        fingerprints[f"compile/{name}"] = compile_record(expr, {x: 0})
        cases[f"compile_warm/{name}"] = lambda e=expr: compile_record(e, {x: 0})

        def cold(e=expr):
            clear_cache()
            return compile_record(e, {x: 0})

        cases[f"compile_cold/{name}"] = cold
    # Force many live intermediate values through an unevaluated expression.
    terms = [sp.sin(x + sp.Rational(k, 100)) for k in range(16)]
    pressure = sp.Add(*(sp.Mul(a, b, evaluate=False) for a, b in zip(terms, terms[1:] + terms[:1])))
    fingerprints["compile/pressure"] = compile_record(pressure, {x: 0})
    cases["compile_warm/pressure"] = lambda: compile_record(pressure, {x: 0})
    for problem in PROBLEMS:
        def build(p=problem):
            return integral_run(p.expr, p.axes(p.rules[0]))
        run = build()
        image = memory_image(run)
        fingerprints[f"image/{problem.name}"] = hashlib.sha256(image.words.tobytes()).hexdigest()
        cases[f"class_compile/{problem.name}"] = build
        cases[f"image/{problem.name}"] = lambda r=run: memory_image(r)

    # Transfer raw IEEE-754 patterns; compilation must never canonicalize these.
    bits = np.array([0, 0x80000000, 0x7f800000, 0xff800000, 0x7fc12345,
                     0x7f812345, 1, 0x7f7fffff], dtype=np.uint32)
    map_run = Run(compile_expr(x, {x: 0}), [Table("x", bits, 0)], (len(bits),), False)
    fingerprints["image/special"] = memory_image(map_run).words.tolist()
    handles = []
    for port in ("sim", "udpsim"):
        dev = connect(port)
        handles.append(dev)
        cases[f"{port}/ping"] = dev.ping
        cases[f"{port}/status"] = dev.status
        cases[f"{port}/nop"] = dev.nop
        for n in (1, 255, 256, 4096):
            words = np.resize(bits, n)
            def transfer(d=dev, w=words):
                d.write_words(0, w)
                result = d.read_words(0, len(w))
                if not np.array_equal(w, result):
                    raise AssertionError("transfer bit mismatch")
            cases[f"{port}/transfer/{n}"] = transfer
        for kind, expr in (("map", x), ("cordic", sp.sin(x)), ("division", 1 / (x + 2))):
            values = np.linspace(0.1, 1, 16, dtype=np.float32).view(np.uint32)
            run = Run(compile_expr(expr, {x: 0}), [Table("x", values, 0)], (16,), kind != "map")
            reply = dev.evu_run(run)
            fingerprints[f"{port}/evu/{kind}"] = [reply.results.tolist(), reply.flags.tolist(), reply.cycles]
            cases[f"{port}/evu_full/{kind}"] = lambda r=run, d=dev: d.evu_run(r)
            # Give each preloaded image a separate span so other cases cannot overwrite it.
            base = {"map": 10000, "cordic": 12000, "division": 14000}[kind]
            image = dev.evu_load(run, base)
            cases[f"{port}/evu_loaded/{kind}"] = lambda d=dev, i=image, b=reply.bounds: d.evu_start(i, b)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.profile:
        prof = cProfile.Profile()
        prof.enable()
        for name, fn in cases.items():
            if "cold" not in name:
                for _ in range(3):
                    fn()
        prof.disable()
        prof.dump_stats(str(args.output.with_suffix(".prof")))
        with args.output.with_suffix(".profile.txt").open("w") as stream:
            pstats.Stats(prof, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(65)

    results = {}
    for name, fn in cases.items():
        for _ in range(3):
            fn()
        # Long compiler cases need fewer samples. Do not publish unsupported tail estimates.
        count = min(args.samples, 11) if "compile" in name else args.samples
        samples = []
        cpu = time.process_time_ns()
        wall = time.perf_counter_ns()
        for _ in range(count):
            start = time.perf_counter_ns()
            fn()
            samples.append(time.perf_counter_ns() - start)
        wall = time.perf_counter_ns() - wall
        cpu = time.process_time_ns() - cpu
        results[name] = {"samples_ns": samples, "median_ns": float(np.median(samples)),
                         "p95_ns": float(np.percentile(samples, 95)) if count >= 100 else None,
                         "p99_ns": float(np.percentile(samples, 99)) if count >= 1000 else None,
                         "p999_ns": float(np.percentile(samples, 99.9)) if count >= 10000 else None,
                         "operations_per_second": count * 1e9 / wall,
                         "process_cpu_percent": cpu / wall * 100}
    for dev in handles:
        dev.close()
    native = getattr(framing, "BACKEND", "original-python")
    try:
        import resource
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        import psutil
        peak_rss = psutil.Process().memory_info().peak_wset / 1024
    def version(command):
        try:
            return subprocess.check_output(command, stderr=subprocess.STDOUT, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            return "unavailable"
    data = {"root": str(root), "head": version(["git", "-C", str(root), "rev-parse", "HEAD"]),
            "backend": native, "python": sys.version, "platform": platform.platform(),
            "cpu": Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else platform.processor(),
            "logical_cpus": os.cpu_count(), "rust": version(["rustc", "--version"]),
            "cc": version(["gcc", "--version"]),
            "dependencies": {d: importlib.metadata.version(d) for d in ("numpy", "sympy", "mpmath", "pyserial")},
            "seed": 20261109, "clock_hz": spec.CLK_HZ, "physical_board": "not measured",
            "peak_rss_kib": peak_rss,
            "fingerprints": fingerprints, "benchmarks": results}
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(f"{native}: {len(results)} cases -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
