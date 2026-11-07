# I build the accumulator on its own here to inspect its resource and timing cost.
# I use the same RTL as the full design; this build does not test the board.
# Standalone timing build of the exact accumulator (phase 4 milestone 2 gate).
#   vivado -mode batch -source hw/scripts/build_accum.tcl      (run from the repo root)
#
# This answers whether fp_accum can take one term per clock at 100 MHz, before
# anything depends on it. It builds the module "out of context": no top level and
# no pins, on the same xc7a100t part as the real build, with a 100 MHz clock.
# The inputs and outputs get 2 ns of delay budget each, as if a register sits on
# the other side with some routing.
#
# Outputs in build/vivado_accum/: timing and utilisation reports.

set root [file normalize [file dirname [info script]]/../..]
set out  $root/build/vivado_accum
file mkdir $out

set part xc7a100tcsg324-1

foreach f {hw/rtl/pkg/accel_pkg.sv hw/rtl/fp/fp_pkg.sv hw/rtl/fp/fp_accum.sv} {
    read_verilog -sv $root/$f
}

synth_design -top fp_accum -part $part -mode out_of_context

create_clock -name clk -period 10.000 [get_ports clk]
set ins [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_input_delay  -clock clk 2.0 $ins
set_output_delay -clock clk 2.0 [get_ports -filter {DIRECTION == OUT}]

report_utilization -file $out/post_synth_util.rpt
opt_design
place_design
phys_opt_design
route_design

report_timing_summary -max_paths 10 -file $out/timing.rpt
report_utilization -file $out/util.rpt

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
puts "WNS: $wns ns  WHS: $whs ns"
if {$wns < 0 || $whs < 0} {
    puts "ERROR: timing not met"
    exit 1
}
