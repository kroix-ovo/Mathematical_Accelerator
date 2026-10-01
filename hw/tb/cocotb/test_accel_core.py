"""cocotb tests for accel_core: replay the golden-model vectors over the UART pins.

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

CPB = 8  # must match the CLKS_PER_BIT parameter passed by run.py


async def send_byte(dut, b: int) -> None:
    for bit in [0, *[(b >> i) & 1 for i in range(8)], 1]:
        dut.uart_rx_i.value = bit
        await ClockCycles(dut.clk, CPB)


async def uart_monitor(dut, out: list[int]) -> None:
    while True:
        await FallingEdge(dut.uart_tx_o)
        await ClockCycles(dut.clk, CPB // 2)
        b = 0
        for i in range(8):
            await ClockCycles(dut.clk, CPB)
            b |= int(dut.uart_tx_o.value) << i
        await ClockCycles(dut.clk, CPB)
        assert int(dut.uart_tx_o.value) == 1, "stop bit"
        out.append(b)


async def reset(dut) -> list[int]:
    Clock(dut.clk, 10, unit="ns").start()
    dut.uart_rx_i.value = 1
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    rx: list[int] = []
    cocotb.start_soon(uart_monitor(dut, rx))
    return rx


async def wait_bytes(dut, rx: list[int], n: int) -> None:
    while len(rx) < n:
        await ClockCycles(dut.clk, CPB * 10)


@cocotb.test()
async def test_golden_vectors(dut):
    rx = await reset(dut)
    for i, (req, resp) in enumerate(build_vectors()):
        rx.clear()
        for b in req:
            await send_byte(dut, b)
        if resp is None:
            await ClockCycles(dut.clk, 40 * CPB * 10)
            assert not rx, f"vector {i}: unexpected response {bytes(rx).hex()}"
        else:
            await with_timeout(wait_bytes(dut, rx, len(resp)), 2, "ms")
            await ClockCycles(dut.clk, 4 * CPB * 10)
            assert bytes(rx) == resp, f"vector {i}:\n exp {resp.hex()}\n got {bytes(rx).hex()}"
