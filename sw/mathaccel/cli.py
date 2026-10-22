"""Command-line checks for the communication system and the FPU.

I use this entry point to identify the board, inspect link errors, test memory,
compare request latency with reported kernel time, and check the FPU on real
hardware. Put the global --port option before the command:
`mathaccel --port COM5 ping`, `--port udp:192.168.10.2 bench`,
`--port COM5 fptest`, `--port sim fp div 1 3`.
The simulator uses the same driver calls but does not measure FPGA hardware.
"""

from __future__ import annotations

import argparse
import struct
import time

import numpy as np

from mathaccel import fp32ref
from mathaccel._generated.spec import FpuFlag, FpuOp
from mathaccel.driver.device import connect
from mathaccel.fpgen import stream


def cmd_ping(dev, _args) -> None:
    """Display the configuration returned after Device checks the identity magic."""
    info = dev.ping()
    print(f"magic ok, v{info.version[0]}.{info.version[1]}, "
          f"{1 << info.scratch_addr_bits} scratch words, clk {info.clk_hz / 1e6:.0f} MHz")


def cmd_status(dev, _args) -> None:
    """Show the link counters and most recent kernel cycle count."""
    print(dev.status())


def cmd_memtest(dev, args) -> None:
    """Write reproducible random words, read them back, and count mismatches.

    This overwrites the requested scratchpad span. The measured wall time
    includes packet transfers and host work as well as memory operations.
    """
    rng = np.random.default_rng(args.seed)
    data = rng.integers(0, 2**32, args.words, dtype=np.uint32)
    t0 = time.perf_counter()
    dev.write_words(args.addr, data)
    back = dev.read_words(args.addr, args.words)
    dt = time.perf_counter() - t0
    bad = int(np.count_nonzero(back != data))
    # Both a write and a read move four bytes per word. This payload rate
    # excludes framing overhead, although the elapsed time includes it.
    rate = 2 * 4 * args.words / dt / 1e3
    print(f"{args.words} words round trip in {dt * 1e3:.1f} ms ({rate:.0f} kB/s), mismatches: {bad}")
    if bad:
        # A failing memory check must also fail a shell command or CI step.
        raise SystemExit(1)


def cmd_bench(dev, _args) -> None:
    """Measure NOP request latency, then display device-reported FILL time."""
    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        dev.nop()
    rtt = (time.perf_counter() - t0) / n
    # rtt includes host and link costs. r.seconds below converts only the
    # kernel's reported busy clocks; these are different measurements.
    r = dev.fill(0, 4096, 0xDEADBEEF)
    print(f"NOP round trip: {rtt * 1e6:.0f} us   FILL 4096 words: {r.cycles} cycles ({r.seconds * 1e6:.1f} us)")


def f32_bits(text: str) -> int:
    """Parse an operand: a decimal number (1.5, -2e-40, inf, nan) or raw bits (0x3fc00000)."""
    if text.lower().startswith("0x"):
        return int(text, 16) & 0xFFFFFFFF
    return struct.unpack("<I", struct.pack("<f", float(text)))[0]


def describe(bits: int) -> str:
    return f"{struct.unpack('<f', struct.pack('<I', bits))[0]!r:>16} (0x{bits:08X})"


def flag_names(flags: int) -> str:
    return "|".join(f.name for f in FpuFlag if flags & f) or "-"


def cmd_fp(dev, args) -> None:
    """Run one FPU operation on the device and show the result next to the model's."""
    op = FpuOp[args.op.upper()]
    a = int(args.a, 0) & 0xFFFFFFFF if op == FpuOp.I2F else f32_bits(args.a)
    b = f32_bits(args.b) if args.b is not None else 0
    res, fl, run = dev.fpu(op, [a], [b])
    exp_r, exp_f = fp32ref.evaluate(op, a, b)
    as_int = op in (FpuOp.F2I, FpuOp.EQ, FpuOp.LT, FpuOp.LE)
    shown = f"{int(np.int32(res[0]))} (0x{int(res[0]):08X})" if as_int else describe(int(res[0]))
    print(f"{op.name}: {shown}  flags {flag_names(int(fl[0]))}  [{run.cycles} cycles]")
    if (int(res[0]), int(fl[0])) != (exp_r, exp_f):
        print(f"MISMATCH: model says 0x{exp_r:08X} flags {flag_names(exp_f)}")
        raise SystemExit(1)


def cmd_fptest(dev, args) -> None:
    """Run seeded FPU vectors on the device and compare every result and flag
    word with the Python reference model. Exits non-zero on any mismatch."""
    ops = list(FpuOp) if args.op == "all" else [FpuOp[args.op.upper()]]
    chunk = 1024                     # elements per FPU kernel run
    bad_total = 0
    for op in ops:
        vecs = list(stream(args.seed + int(op), args.n, int(op)))
        bad = 0
        cycles = 0
        t0 = time.perf_counter()
        for i in range(0, len(vecs), chunk):
            part = vecs[i:i + chunk]
            a = [v[1] for v in part]
            b = [v[2] for v in part]
            res, fl, run = dev.fpu(op, a, b)
            cycles += run.cycles
            for k, (x, y) in enumerate(zip(a, b, strict=True)):
                want = fp32ref.evaluate(op, x, y)
                if (int(res[k]), int(fl[k])) != want:
                    bad += 1
                    if bad <= 5:
                        print(f"  {op.name} a=0x{x:08X} b=0x{y:08X}: got 0x{int(res[k]):08X}/{int(fl[k]):05b}"
                              f" want 0x{want[0]:08X}/{want[1]:05b}")
        dt = time.perf_counter() - t0
        per = cycles / len(vecs)
        print(f"{op.name:5s} {len(vecs)} vectors, {bad} mismatches, {per:.1f} kernel cycles/element, "
              f"{dt:.2f} s wall time")
        bad_total += bad
    if bad_total:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    """Parse a subcommand and keep its connection open for the whole operation."""
    ap = argparse.ArgumentParser(prog="mathaccel")
    ap.add_argument("--port", default="sim",
                    help='serial port like COM5, udp:192.168.10.2 for Ethernet, "udpsim", or "sim" (default)')
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    sub.add_parser("status")
    mt = sub.add_parser("memtest")
    mt.add_argument("--addr", type=int, default=0)
    mt.add_argument("--words", type=int, default=4096)
    mt.add_argument("--seed", type=int, default=1)
    sub.add_parser("bench")
    fp = sub.add_parser("fp", help="one FPU operation, e.g. `fp div 1 3` or `fp sqrt 2`")
    fp.add_argument("op", choices=[o.name.lower() for o in FpuOp])
    fp.add_argument("a")
    fp.add_argument("b", nargs="?")
    ft = sub.add_parser("fptest", help="seeded FPU vectors checked against the Python model")
    ft.add_argument("--op", default="all", help="op name or 'all'")
    ft.add_argument("--n", type=int, default=2000, help="vectors per op")
    ft.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    # The context manager closes a physical serial port even if a command
    # raises. A dictionary selects the handler with no separate CLI branches.
    with connect(args.port) as dev:
        handlers = {"ping": cmd_ping, "status": cmd_status, "memtest": cmd_memtest, "bench": cmd_bench,
                    "fp": cmd_fp, "fptest": cmd_fptest}
        handlers[args.cmd](dev, args)


if __name__ == "__main__":
    main()
