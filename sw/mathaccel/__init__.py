"""Public entry points for the mathematical accelerator's Python driver.

`connect` selects board or simulator; `Device` exposes the command methods.
The phase 1 package moves words and runs NOP/FILL. Future fp32 arithmetic will
use this communication layer, but it is not implemented by this package yet.
"""

from mathaccel.driver.device import Device, connect

__all__ = ["Device", "connect"]
