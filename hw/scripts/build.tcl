# Non-project Vivado build for the Nexys A7-100T.
#   vivado -mode batch -source hw/scripts/build.tcl            (run from the repo root)
# Outputs in build/vivado/: nexys_top.bit, timing (including rmii_timing.rpt) and
# utilisation reports. Fails if setup or hold timing is not met.

set root [file normalize [file dirname [info script]]/../..]
set out  $root/build/vivado
file mkdir $out

set part xc7a100tcsg324-1

set fp [open $root/hw/rtl/sources.f r]
foreach line [split [read $fp] "\n"] {
    if {[string trim $line] ne ""} { read_verilog -sv $root/[string trim $line] }
}
close $fp
read_verilog -sv $root/hw/rtl/top/nexys_top.sv
read_xdc $root/hw/constraints/nexys_a7_100t.xdc

synth_design -top nexys_top -part $part
report_utilization -file $out/post_synth_util.rpt
opt_design
place_design
phys_opt_design
route_design

report_timing_summary -max_paths 10 -file $out/timing.rpt
# The RMII pins on their own: setup and hold of every PHY input and output.
report_timing -from [get_ports {ETH_RXD[*] ETH_CRSDV ETH_RXERR}] -delay_type min_max -max_paths 8 -file $out/rmii_timing.rpt
report_timing -to [get_ports {ETH_TXD[*] ETH_TXEN}] -delay_type min_max -max_paths 6 -append -file $out/rmii_timing.rpt
report_utilization -hierarchical -file $out/util_hier.rpt
report_utilization -file $out/util.rpt
write_bitstream -force $out/nexys_top.bit

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
puts "WNS: $wns ns  WHS: $whs ns"
if {$wns < 0 || $whs < 0} {
    puts "ERROR: timing not met"
    exit 1
}
