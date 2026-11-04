"""Public entry points for the mathematical accelerator's Python driver.

`connect` selects board or simulator; `Device` exposes the command methods.
The driver moves scratchpad words and runs NOP, FILL and the FPU array kernel.
I keep the same Device methods for UART, UDP and the software simulators;
the chosen transport changes how packets move, not what a command means.
The phase 3 operation codes are available through Device.fpu, while the CLI's
exact-bit checks still select the original twelve arithmetic operations.
"""

from mathaccel.driver.device import Device, connect

__all__ = ["Device", "connect"]
