"""Bring up the Ethernet transport on the board, one layer at a time.

    python hw/scripts/eth_bringup.py                 # Ethernet checks only
    python hw/scripts/eth_bringup.py --uart COM3     # also compare against the UART
    python hw/scripts/eth_bringup.py --fptest 5000   # longer FPU check

Program the board first (vivado -mode batch -source hw/scripts/program.tcl)
and give the laptop's Ethernet port the static address 192.168.10.1/24.

I run the checks from the bottom of the stack up and stop at the first one
that fails, with a hint about which layer to look at:

  1. laptop  the Ethernet adapter is up and has 192.168.10.1
  2. ARP     Windows learned the board's MAC (02-4d-41-43-43-01)
  3. ICMP    ping 192.168.10.2 gets replies
  4. UDP     PING and STATUS commands through UdpTransport
  5. data    full-scratchpad memtest, one FPU division, seeded fptest
  6. timing  NOP round-trip times (and the same over the UART with --uart)
  7. errors  STATUS link counters are still zero at the end

Everything printed also goes to build/eth_bringup_<time>.md so the numbers
can go straight into docs/verification.md.
    python hw/scripts/eth_bringup.py --port udpsim   # dry run against the Python model
"""

from __future__ import annotations

import argparse
import datetime as dt
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw"))

from mathaccel import fp32ref  # noqa: E402
from mathaccel._generated import spec  # noqa: E402
from mathaccel._generated.spec import FpuOp  # noqa: E402
from mathaccel.driver.device import connect  # noqa: E402
from mathaccel.fpgen import stream  # noqa: E402

BOARD_MAC_WIN = spec.ETH_MAC.lower().replace(":", "-")
LOG: list[str] = []


def say(text: str = "") -> None:
    print(text)
    LOG.append(text)


def fail(step: str, hint: str) -> None:
    say(f"\nFAILED at {step}.\nHint: {hint}")
    finish(ok=False)


def finish(ok: bool) -> None:
    out = ROOT / "build" / f"eth_bringup_{dt.datetime.now():%Y%m%d_%H%M%S}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(LOG) + "\n", encoding="utf-8")
    say(f"\n{'ALL CHECKS PASSED' if ok else 'STOPPED'}; log written to {out.relative_to(ROOT)}")
    sys.exit(0 if ok else 1)


def run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout + res.stderr


def check_adapter() -> None:
    """Windows only: find the adapter holding HOST_IP and show its link speed."""
    if platform.system() != "Windows":
        say("1. laptop: skipped (not Windows)")
        return
    ps = ("Get-NetIPAddress -AddressFamily IPv4 -IPAddress " + spec.HOST_IP +
          " -ErrorAction SilentlyContinue | ForEach-Object { $a = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex;"
          " \"$($a.Name)|$($a.Status)|$($a.LinkSpeed)|$($_.PrefixLength)\" }")
    line = run(["powershell", "-NoProfile", "-Command", ps]).strip()
    if not line:
        fail("1. laptop", f"no adapter has {spec.HOST_IP}. As administrator run:\n"
             f'  netsh interface ipv4 set address name="Ethernet" static {spec.HOST_IP} 255.255.255.0')
    name, status, speed, prefix = line.splitlines()[0].split("|")
    say(f"1. laptop: adapter '{name}' {status}, {speed}, {spec.HOST_IP}/{prefix}")
    if status != "Up":
        fail("1. laptop", "the cable link is down: check the cable, that the board is powered and programmed "
             "(LED7 on = PHY out of reset), and the RJ45 link LED.")
    if "100 Mbps" not in speed:
        say(f"   note: link speed is {speed}; the PHY only does 10/100, so expect 100 Mbps.")


def check_ping() -> None:
    """ping fills the ARP table as a side effect, so ICMP runs before the ARP check."""
    flag = "-n" if platform.system() == "Windows" else "-c"
    out = run(["ping", flag, "4", spec.ETH_IP])
    replies = [ln for ln in out.splitlines() if "TTL=" in ln.upper()]
    say(f"3. ICMP: {len(replies)}/4 ping replies")
    for ln in replies[:4]:
        say(f"   {ln.strip()}")
    arp = run(["arp", "-a", spec.ETH_IP]).lower()
    have_mac = BOARD_MAC_WIN in arp or spec.ETH_MAC.lower() in arp
    say(f"2. ARP: board MAC {'found' if have_mac else 'NOT found'} in the ARP table")
    if not have_mac:
        fail("2. ARP", "the board never answered ARP. Watch LED3 (frame received) while pinging: dark means no "
             "frames arrive (link, PHY reset or strap problem); LED3 flashing with LED5 on means frames arrive "
             "with bad CRC (RX timing: REFCLK phase in nexys_top.sv); LED3 flashing, LED5 off and LED4 dark "
             "means received fine but nothing sent (TX path).")
    if not replies:
        fail("3. ICMP", "ARP works but ping does not. Windows Firewall may block echo replies on a Public "
             "network; mathaccel does not need ping, so you can skip ahead with --skip-ping.")


def bench_nop(dev, n: int) -> list[float]:
    """Round-trip times of n NOP kernel requests, in microseconds."""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        dev.nop()
        times.append((time.perf_counter() - t0) * 1e6)
    return times


def describe(times: list[float]) -> str:
    q = np.percentile(times, [50, 99])
    return (f"mean {statistics.mean(times):.0f} us, median {q[0]:.0f} us, 99th pct {q[1]:.0f} us, "
            f"min {min(times):.0f} us")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=spec.ETH_IP)
    ap.add_argument("--uart", help="serial port (e.g. COM3) for the UART comparison")
    ap.add_argument("--fptest", type=int, default=1000, help="vectors per FPU op (0 to skip)")
    ap.add_argument("--nop", type=int, default=500, help="NOP requests for the latency numbers")
    ap.add_argument("--skip-ping", action="store_true", help="skip the ICMP/ARP checks")
    ap.add_argument("--port", help="connect() string instead of udp:HOST; 'udpsim' dry-runs the script "
                    "without a board (skips the laptop and ping checks)")
    args = ap.parse_args()

    say(f"# Ethernet bring-up {dt.datetime.now():%Y-%m-%d %H:%M}")
    say()
    if not args.port:
        check_adapter()
        if not args.skip_ping:
            check_ping()

    try:
        dev = connect(args.port or f"udp:{args.host}", timeout=0.5, retries=1)
        info = dev.ping()
    except Exception as e:  # noqa: BLE001 - any failure here means the same thing
        fail("4. UDP", f"{e!r}. ARP/ping work, so the MAC and IP layers are fine; look at UDP: port "
             f"{spec.UDP_PORT}, a firewall blocking the reply, or LED6 (dropped frames).")
    say(f"4. UDP: PING ok, v{info.version[0]}.{info.version[1]}, clk {info.clk_hz / 1e6:.0f} MHz; "
        f"STATUS {dev.status()}")

    with dev:
        # Full scratchpad, like the UART board test on 2026-10-13.
        words = 1 << spec.SCRATCH_ADDR_BITS
        data = np.random.default_rng(1).integers(0, 2**32, words, dtype=np.uint32)
        t0 = time.perf_counter()
        dev.write_words(0, data)
        back = dev.read_words(0, words)
        t = time.perf_counter() - t0
        bad = int(np.count_nonzero(back != data))
        say(f"5. memtest: {words} words written and read in {t * 1e3:.0f} ms "
            f"({2 * 4 * words / t / 1e3:.0f} kB/s payload), {bad} mismatches")
        if bad:
            fail("5. memtest", "data errors with intact CRCs would be a core problem, not Ethernet.")

        res, fl, run_ = dev.fpu(FpuOp.DIV, [0x3F800000], [0x40400000])
        ok = (int(res[0]), int(fl[0])) == fp32ref.evaluate(FpuOp.DIV, 0x3F800000, 0x40400000)
        say(f"   fp div 1 3: 0x{int(res[0]):08X} flags {int(fl[0]):05b}, {run_.cycles} cycles, "
            f"{'matches' if ok else 'DIFFERS FROM'} the model")
        if not ok:
            fail("5. fp", "FPU result differs from the Python model.")

        if args.fptest:
            total_bad = 0
            t0 = time.perf_counter()
            for op in FpuOp:
                vecs = list(stream(1 + int(op), args.fptest, int(op)))
                for i in range(0, len(vecs), 1024):
                    part = vecs[i:i + 1024]
                    a, b = [v[1] for v in part], [v[2] for v in part]
                    r, f, _ = dev.fpu(op, a, b)
                    total_bad += sum((int(r[k]), int(f[k])) != fp32ref.evaluate(op, x, y)
                                     for k, (x, y) in enumerate(zip(a, b, strict=True)))
            say(f"   fptest: {args.fptest} vectors x 12 ops in {time.perf_counter() - t0:.1f} s, "
                f"{total_bad} mismatches")
            if total_bad:
                fail("5. fptest", "FPU mismatches; compare with the UART run to see if it is transport related.")

        udp_times = bench_nop(dev, args.nop)
        say(f"6. NOP round trip over UDP ({args.nop} requests): {describe(udp_times)}")
        st = dev.status()
        say(f"7. STATUS at the end: {st}")

    if args.uart:
        with connect(args.uart) as udev:
            udev.ping()
            uart_times = bench_nop(udev, args.nop)
            say(f"   NOP round trip over UART {args.uart}: {describe(uart_times)}")
            say(f"   median ratio UART/UDP: {np.median(uart_times) / np.median(udp_times):.1f}x")
            say(f"   STATUS over UART: {udev.status()} (same summed counters as over UDP)")

    if st.crc_errors or st.frame_errors:
        fail("7. errors", "link errors were counted during the run; check LED5/LED6 and repeat.")
    finish(ok=True)


if __name__ == "__main__":
    main()
