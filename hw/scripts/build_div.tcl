# Standalone timing build of the divider (my divider experiments).
#   vivado -mode batch -source hw/scripts/build_div.tcl                       (from the repo root)
#   vivado -mode batch -source hw/scripts/build_div.tcl -tclargs DQ=16      (with a parameter)
#
# The full Nexys build (build.tcl) is what decides whether a divider fits: it
# times the divider together with everything else. But a full build's worst
# path can be anywhere, and a different divider moves the placement of
# unrelated logic too, so its WNS doesn't say how much margin the divider
# itself has. This build places and routes the divide unit alone, "out of
# context", on the same part with a 100 MHz clock, the way build_cordic.tcl
# measures the CORDIC unit. In this experiment the divide unit is the shared
# divide and square-root unit, fp_divsqrt. Inputs and outputs get 2 ns of
# delay budget each, as if a
# register sits on the other side with some routing.
#
# I pass NAME=VALUE arguments to fp_divsqrt; DQ changes queue depth, not
# the arithmetic stages. I keep it a power of two. There is no STEPS knob
# in this shared multiplicative pipeline.
# Outputs in build/vivado_div/: timing and utilisation reports.

set root [file normalize [file dirname [info script]]/../..]
set out  $root/build/vivado_div
file mkdir $out

set part xc7a100tcsg324-1

foreach f {hw/rtl/pkg/accel_pkg.sv hw/rtl/fp/fp_pkg.sv hw/rtl/fp/fp_divsqrt.sv} {
    read_verilog -sv $root/$f
}

set generics {}
# cmd.exe splits an unquoted argument at "=", so a quoted one may arrive with its
# quotes; I strip them.
foreach arg $argv { lappend generics -generic [string trim $arg {"}] }
synth_design -top fp_divsqrt -part $part -mode out_of_context {*}$generics

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
