"""Drive the real RTL UART pins from Python and compare complete wire replies.

I use cocotb coroutines to send bytes and monitor replies at the same time.
Unlike SimTransport, this test clocks the HDL and exercises framing, command
execution, and serial bit timing. Shared vectors provide the expected bytes.

Run with `python hw/tb/cocotb/run.py [--sim verilator|icarus]`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, with_timeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vectors import build_vectors  # noqa: E402

CPB = 8  # Must match run.py; fewer clocks per bit make simulation finish sooner.


async def send_byte(dut, b: int) -> None:
    """Drive one UART character: low start, eight LSB-first data bits, high stop."""
    # Each awaited interval advances the simulator, allowing the RTL receiver
    # and the independent reply monitor to run while the host holds this bit.
    for bit in [0, *[(b >> i) & 1 for i in range(8)], 1]:
        dut.uart_rx_i.value = bit
        await ClockCycles(dut.clk, CPB)


async def uart_monitor(dut, out: list[int]) -> None:
    """Collect transmitted bytes continuously, checking each stop bit as well."""
    while True:
        # UART is idle high. Its falling edge marks the start of a character.
        # Half a bit centers us in the start bit, then whole-bit waits sample
        # the centers of the eight data bits and the stop bit.
        await FallingEdge(dut.uart_tx_o)
        await ClockCycles(dut.clk, CPB // 2)
        b = 0
        for i in range(8):
            await ClockCycles(dut.clk, CPB)
            b |= int(dut.uart_tx_o.value) << i  # First wire bit becomes bit zero.
        await ClockCycles(dut.clk, CPB)
        assert int(dut.uart_tx_o.value) == 1, "stop bit"
        out.append(b)


async def reset(dut) -> list[int]:
    """Start a 100 MHz clock, reset the core, and launch its UART reply monitor."""
    Clock(dut.clk, 10, unit="ns").start()
    # The Ethernet side stays idle in this test: a running 50 MHz clock and
    # no carrier. Both clocks start together, so their edges line up as they
    # do when one MMCM makes them on the board.
    Clock(dut.clk_rmii, 20, unit="ns").start()
    dut.rmii_crs_dv.value = 0
    dut.rmii_rxd.value = 0
    dut.rmii_rx_er.value = 0
    dut.uart_rx_i.value = 1
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    rx: list[int] = []
    # The monitor appends into this same list throughout the test. Keeping it
    # alive during sends catches replies that begin before send_byte finishes.
    cocotb.start_soon(uart_monitor(dut, rx))
    return rx


async def wait_bytes(dut, rx: list[int], n: int) -> None:
    """Yield simulation time until the monitor has collected the expected count."""
    while len(rx) < n:
        await ClockCycles(dut.clk, CPB * 10)


@cocotb.test()
async def test_golden_vectors(dut):
    """Replay stateful vectors, require exact replies, and check silence on errors."""
    rx = await reset(dut)
    for i, (req, resp) in enumerate(build_vectors()):
        rx.clear()
        for b in req:
            await send_byte(dut, b)
        if resp is None:
            # A rejected frame must produce no response. Wait several complete
            # UART characters to make late, unwanted replies visible.
            await ClockCycles(dut.clk, 40 * CPB * 10)
            assert not rx, f"vector {i}: unexpected response {bytes(rx).hex()}"
        else:
            # A missing reply fails by timeout instead of hanging the CI run.
            # Extra wait time catches replies with trailing unwanted bytes.
            await with_timeout(wait_bytes(dut, rx, len(resp)), 2, "ms")
            await ClockCycles(dut.clk, 4 * CPB * 10)
            # Compare wire bytes, including COBS/CRC, not just decoded payload.
            assert bytes(rx) == resp, f"vector {i}:\n exp {resp.hex()}\n got {bytes(rx).hex()}"
