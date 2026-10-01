# Program the board over JTAG (volatile).
#   vivado -mode batch -source hw/scripts/program.tcl
set root [file normalize [file dirname [info script]]/../..]
open_hw_manager
connect_hw_server
open_hw_target
set dev [lindex [get_hw_devices xc7a100t*] 0]
current_hw_device $dev
set_property PROGRAM.FILE $root/build/vivado/nexys_top.bit $dev
program_hw_devices $dev
close_hw_manager
