"""One isolated host variant, real FPGA only, with correctness-gated timings."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from language_git import git_env


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--port", default="udp:192.168.10.2")
    ap.add_argument("--seconds", type=float, default=6)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.port in ("sim", "udpsim"):
        raise ValueError("This harness requires a physical board")
    root = args.root.resolve()
    sys.path.insert(0, str(root / "sw"))
    import numpy as np
    import sympy as sp

    from mathaccel import evu_model
    from mathaccel._generated import spec
    from mathaccel.compiler.evu_isa import Instr, Program, Run, Table, memory_image
    from mathaccel.compiler.problems import PROBLEMS
    from mathaccel.compiler.quadrature import integral_run
    from mathaccel.compiler.sympy_to_evu import compile_expr
    from mathaccel.driver import framing
    from mathaccel.driver.device import Device, RunTimeout, connect
    from mathaccel.driver.model import fpu_evaluate
    from mathaccel.driver.protocol import Frame
    from mathaccel.driver.transport import UdpTransport

    def golden(run):
        result = evu_model.execute(run)
        return SimpleNamespace(values=np.array([result.sum], dtype=np.uint32) if run.reduce else result.results,
                               flags=np.array([result.sum_flags], dtype=np.uint32) if run.reduce else result.flags,
                               aux=result.aux)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fingerprints, results, validation, recovery = {}, {}, [], {}
    rng = np.random.default_rng(20261111)
    words = rng.integers(0, 2**32, 32768, dtype=np.uint32)
    words[::31] = 0
    words[:8] = [0, 0x80000000, 0x7f800000, 0xff800000, 0x7fc12345, 0x7f812345, 1, 0xffffffff]
    fingerprints["dataset"] = hashlib.sha256(words.tobytes()).hexdigest()

    def measure(name, fn, check=lambda result: None, count=101, seconds=0, payload=0):
        for _ in range(3):
            check(fn())
        samples, phases = [], []
        cpu = time.process_time_ns()
        window = time.perf_counter_ns()
        while len(samples) < count or time.perf_counter_ns() - window < seconds * 1e9:
            start = time.perf_counter_ns()
            value = fn()
            samples.append(time.perf_counter_ns() - start)
            # Read integrity checks are outside operation latency but inside the
            # sustained window/CPU accounting. Write-only windows read back at end.
            check(value)
            if hasattr(value, "write_seconds"):
                phases.append({"upload_ns": value.write_seconds * 1e9,
                               "run_roundtrip_ns": value.run_seconds * 1e9,
                               "readback_ns": value.read_seconds * 1e9,
                               "fpga_cycles": value.cycles})
        wall = time.perf_counter_ns() - window
        cpu = time.process_time_ns() - cpu
        results[name] = {"samples_ns": samples, "count": len(samples),
                         "median_ns": float(np.median(samples)),
                         "p95_ns": float(np.percentile(samples, 95)) if len(samples) >= 100 else None,
                         "p99_ns": float(np.percentile(samples, 99)) if len(samples) >= 1000 else None,
                         "p999_ns": float(np.percentile(samples, 99.9)) if len(samples) >= 10000 else None,
                         "wall_ns": wall, "cpu_ns": cpu, "cpu_percent": cpu / wall * 100,
                         "payload_bytes_per_op": payload, "payload_MBps": payload * len(samples) / wall * 1000,
                         "phases": phases}
        print(f"{name}: {len(samples)} samples, {np.median(samples) / 1000:.1f} us", flush=True)

    x = sp.Symbol("x", real=True)
    with connect(args.port, timeout=1, retries=3) as dev:
        info, before = dev.ping(), dev.status()
        if info.clk_hz != 100000000 or info.version != (0, 4):
            raise AssertionError(f"unexpected board: {info}")
        measure("link/ping", dev.ping, count=201)
        measure("link/status", dev.status, count=201)
        measure("link/nop", dev.nop, count=201)

        def same(expected):
            def check(got):
                np.testing.assert_array_equal(got, expected)
            return check

        for n in (1, 255, 256, 4096):
            data = words[:n]

            def transfer(data=data):
                dev.write_words(0, data)
                return dev.read_words(0, len(data))

            measure(f"memory/roundtrip/{n}", transfer, same(data), count=51, payload=n * 8)

        for n in (4096, 32768):
            data = words[:n]
            measure(f"sustained/write/{n}", lambda data=data: dev.write_words(0, data),
                    count=11, seconds=args.seconds, payload=n * 4)
            same(data)(dev.read_words(0, n))
            measure(f"sustained/read/{n}", lambda n=n: dev.read_words(0, n), same(data),
                    count=11, seconds=args.seconds, payload=n * 4)
            measure(f"sustained/roundtrip/{n}",
                    lambda data=data: (dev.write_words(0, data), dev.read_words(0, len(data)))[1],
                    same(data), count=11, seconds=args.seconds, payload=n * 8)

        # Boundary and exact-bit validation across multiple hardware frames.
        dev.write_words(spec.SCRATCH_WORDS - 256, words[:256])
        same(words[:256])(dev.read_words(spec.SCRATCH_WORDS - 256, 256))
        validation.append("top-of-scratchpad and raw fp32 patterns: pass")

        for op in spec.FpuOp:
            a, b = words[:128], words[128:256]
            expected = [fpu_evaluate(op, int(u), int(v)) for u, v in zip(a, b, strict=True)]
            want = np.array([pair[0] for pair in expected], dtype=np.uint32)
            flags = np.array([pair[1] for pair in expected], dtype=np.uint32)

            def verify_fpu(value, want=want, flags=flags):
                np.testing.assert_array_equal(value[0], want)
                np.testing.assert_array_equal(value[1], flags)
                assert value[2].aux == int(np.bitwise_or.reduce(flags))

            value = dev.fpu(op, a, b)
            verify_fpu(value)
            fingerprints[f"fpu/{op.name}"] = [value[0].tolist(), value[1].tolist(), value[2].cycles, value[2].aux]
            if op in (spec.FpuOp.ADD, spec.FpuOp.DIV, spec.FpuOp.SIN):
                measure(f"fpu/{op.name}", lambda op=op, a=a, b=b: dev.fpu(op, a, b), verify_fpu, count=31)

        for label, expr, size, reduce in (
            ("basic", x*x + 2, 64, False),
            ("map", sp.sin(x) + x*x, 4096, False),
            ("reduce", sp.sin(x)/(x+2), 16384, True),
        ):
            values = np.linspace(0.1, 1, size, dtype=np.float32).view(np.uint32)
            run = Run(compile_expr(expr, {x: 0}), [Table("x", values, 0)], (size,), reduce)
            expected = golden(run)
            image = memory_image(run)
            fingerprints[f"image/{label}"] = hashlib.sha256(image.words.tobytes()).hexdigest()

            def verify(reply, expected=expected):
                np.testing.assert_array_equal(reply.results, expected.values)
                np.testing.assert_array_equal(reply.flags, expected.flags)
                assert reply.aux == expected.aux

            reply = dev.evu_run(run)
            verify(reply)
            fingerprints[f"evu/{label}"] = [reply.results.tolist(), reply.flags.tolist(), reply.cycles, reply.aux]
            measure(f"evu/full/{label}", lambda run=run: dev.evu_run(run), verify, count=31)
            loaded = dev.evu_load(run)
            bounds = evu_model.cycle_bounds(run)
            output_count = 1 if reduce else size

            def repeated(image=loaded, bounds=bounds, n=output_count):
                done = dev.evu_start(image, bounds)
                return (dev.read_words(image.r, n), dev.read_words(image.f, n), done.cycles, done.aux)

            def verify_loaded(value, expected=expected, cycles=reply.cycles):
                np.testing.assert_array_equal(value[0], expected.values)
                np.testing.assert_array_equal(value[1], expected.flags)
                assert value[2] == cycles and value[3] == expected.aux

            measure(f"evu/loaded/{label}", repeated, verify_loaded, count=31)

        # Existing mathematical workloads: every rule, including 2M-point runs.
        for problem in PROBLEMS:
            for k, rule in enumerate(problem.rules):
                start = time.perf_counter_ns()
                run = integral_run(problem.expr, problem.axes(rule))
                compile_ns = time.perf_counter_ns() - start
                expected = golden(run)
                reply = dev.evu_run(run)
                np.testing.assert_array_equal(reply.results, expected.values)
                np.testing.assert_array_equal(reply.flags, expected.flags)
                assert reply.aux == expected.aux
                name = f"class/{problem.name}/{k}"
                fingerprints[name] = [reply.results.tolist(), reply.flags.tolist(), reply.cycles, reply.aux,
                                      hashlib.sha256(memory_image(run).words.tobytes()).hexdigest()]
                validation.append({"case": name, "points": run.points, "compile_ns": compile_ns,
                                   "fpga_cycles": reply.cycles, "fpga_ns": reply.cycles * 10,
                                   "upload_ns": reply.write_seconds * 1e9,
                                   "run_roundtrip_ns": reply.run_seconds * 1e9,
                                   "readback_ns": reply.read_seconds * 1e9, "correct": True})
        after = dev.status()
        assert after.crc_errors == before.crc_errors and after.frame_errors == before.frame_errors

    # Real-board recovery with deterministic loss injection, outside timed cases.
    # No injected RUN is retransmitted. Both the client and the real scratchpad
    # participate; these are synthetic faults, not claims of natural packet loss.
    if not args.quick and args.port.startswith("udp:"):
        host, _, port = args.port[4:].partition(":")

        class Loss(UdpTransport):
            def __init__(self, mode):
                super().__init__(host, int(port) if port else spec.UDP_PORT)
                self.mode, self.runs, self.drop_next = mode, 0, False
                self.injected = False

            def send(self, wire):
                request = Frame.decode(wire[:-1])
                if request.op == spec.Op.RUN and request.payload[0] == spec.Kernel.EVU:
                    self.runs += 1
                    self.drop_next = True
                    if self.mode == "not started":
                        return
                super().send(wire)

            def recv_frame(self, timeout):
                if self.drop_next:
                    self.drop_next = False
                    self.injected = True
                    if self.mode == "finished":
                        frame = super().recv_frame(timeout)
                        assert frame is not None
                    return None
                return super().recv_frame(min(timeout, 0.005) if self.mode == "running" else timeout)

        for mode in ("not started", "finished", "running"):
            transport = Loss(mode)
            with Device(transport, timeout=0.005 if mode == "running" else 1, retries=0) as dev:
                values = np.linspace(0.1, 1, 1024, dtype=np.float32).view(np.uint32)
                program = compile_expr(x*x+2, {x: 0})
                grid = (1024,)
                if mode == "running":
                    # 32 dependent SINs at every point give STATUS time to time out.
                    instructions = [Instr(spec.EVU_OP_LOAD, 0, 0)]
                    instructions += [Instr(spec.FpuOp.SIN, 0, 0, 0, last=k == 31) for k in range(32)]
                    program, grid = Program([i.encode() for i in instructions]), (1024, 1024)
                run = Run(program, [Table("x", values, 0)], grid, True)
                image = dev.evu_load(run)
                try:
                    dev.evu_start(image, evu_model.cycle_bounds(run))
                except RunTimeout as error:
                    assert error.state == mode, (mode, error.state)
                else:
                    raise AssertionError("loss injection did not produce RunTimeout")
                assert transport.runs == 1
                if mode == "running":
                    # A bounded wait for completion. STATUS is safe; no RUN retry.
                    transport.mode = "cleanup"
                    dev.timeout = 2
                    dev.retries = 3  # STATUS can retry safely after a queued query occupied the receive slot.
                    status = dev.status()
                    assert int(dev.read_words(image.f, 1)[0]) != 0xffffffff
                    recovery[mode] = {"run_requests": transport.runs, "completed_cycles": status.last_cycles}
                else:
                    recovery[mode] = {"run_requests": transport.runs}

    def command(*cmd):
        return subprocess.check_output(cmd, env=git_env(root), text=True).strip()

    native_file = None
    try:
        from mathaccel import _framing_native
        native_file = {"path": _framing_native.__file__,
                       "sha256": hashlib.sha256(Path(_framing_native.__file__).read_bytes()).hexdigest()}
    except ImportError:
        pass
    data = {"head": command("git", "-C", str(root), "rev-parse", "HEAD"),
            "backend": getattr(framing, "BACKEND", "original-python"), "port": args.port,
            "platform": platform.platform(), "python": sys.version,
            "dependencies": {d: importlib.metadata.version(d) for d in ("numpy", "sympy", "mpmath", "pyserial")},
            "native_binary": native_file, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "board": vars(info), "status_before": vars(before), "status_after": vars(after),
            "benchmarks": results, "fingerprints": fingerprints, "validation": validation, "recovery": recovery,
            "seed": 20261111, "physical_board": True}
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(f"PHYSICAL BOARD PASS: {data['backend']} -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
