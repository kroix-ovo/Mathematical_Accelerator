"""Public entry points for the mathematical accelerator's Python driver.

`connect` selects board or simulator; `Device` exposes the command methods.
The driver moves scratchpad words and runs NOP, FILL and the FPU array kernel.
I keep the same Device methods for UART, UDP and the software simulators;
the chosen transport changes how packets move, not what a command means.
The driver and CLI support all eighteen operations. Phase 3 checks compare
the result bits with the CORDIC model and measure error against the
mathematical reference separately.
"""

from mathaccel.driver.device import Device, connect

__all__ = ["Device", "connect"]
