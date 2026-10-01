"""Bring-up CLI: `mathaccel ping --port COM5`, `mathaccel bench --port sim`, ..."""

from __future__ import annotations

import argparse
import time

import numpy as np

from mathaccel.driver.device import connect


def cmd_ping(dev, _args) -> None:
    info = dev.ping()
    print(f"magic ok, v{info.version[0]}.{info.version[1]}, "
          f"{1 << info.scratch_addr_bits} scratch words, clk {info.clk_hz / 1e6:.0f} MHz")


def cmd_status(dev, _args) -> None:
    print(dev.status())


def cmd_memtest(dev, args) -> None:
    rng = np.random.default_rng(args.seed)
    data = rng.integers(0, 2**32, args.words, dtype=np.uint32)
    t0 = time.perf_counter()
    dev.write_words(args.addr, data)
    back = dev.read_words(args.addr, args.words)
    dt = time.perf_counter() - t0
    bad = int(np.count_nonzero(back != data))
    rate = 2 * 4 * args.words / dt / 1e3
    print(f"{args.words} words round trip in {dt * 1e3:.1f} ms ({rate:.0f} kB/s), mismatches: {bad}")
    if bad:
        raise SystemExit(1)


def cmd_bench(dev, _args) -> None:
    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        dev.nop()
    rtt = (time.perf_counter() - t0) / n
    r = dev.fill(0, 4096, 0xDEADBEEF)
    print(f"NOP round trip: {rtt * 1e6:.0f} us   FILL 4096 words: {r.cycles} cycles ({r.seconds * 1e6:.1f} us)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="mathaccel")
    ap.add_argument("--port", default="sim", help='serial port like COM5, or "sim" (default)')
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    sub.add_parser("status")
    mt = sub.add_parser("memtest")
    mt.add_argument("--addr", type=int, default=0)
    mt.add_argument("--words", type=int, default=4096)
    mt.add_argument("--seed", type=int, default=1)
    sub.add_parser("bench")
    args = ap.parse_args(argv)
    with connect(args.port) as dev:
        {"ping": cmd_ping, "status": cmd_status, "memtest": cmd_memtest, "bench": cmd_bench}[args.cmd](dev, args)


if __name__ == "__main__":
    main()
