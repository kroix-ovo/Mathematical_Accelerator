# I build the EVU wrapper with the real FPU and scratchpad to check timing.
# I keep profiling disabled for this implementation build.
# Standalone timing build of the expression unit (phase 4 milestone 3 gate).
#   vivado -mode batch -source hw/scripts/build_evu.tcl      (run from the repo root)
#
# This builds hw/tb/pyuvm/evu/evu_dut.sv: the unit with the real FPU, accumulator
# and scratchpad, so the paths into the FPU and the scratchpad's many block RAMs
# are real ones. It is "out of context": no top level and no pins, on the same
# xc7a100t part as the real build, with a 100 MHz clock. The inputs and outputs
# get 2 ns of delay budget each, as if a register sits on the other side.
#
# Outputs in build/vivado_evu/: timing and utilisation reports, the second one
# broken down by module so I can see what the unit itself costs.

set root [file normalize [file dirname [info script]]/../..]
set out  $root/build/vivado_evu
file mkdir $out

set part xc7a100tcsg324-1

foreach f {hw/rtl/pkg/accel_pkg.sv hw/rtl/fp/fp_pkg.sv hw/rtl/fp/cordic_pkg.sv hw/rtl/fp/fp_addsub.sv
           hw/rtl/fp/fp_mul.sv hw/rtl/fp/fp_misc.sv hw/rtl/fp/fp_divsqrt.sv hw/rtl/fp/fp_cordic.sv
           hw/rtl/fp/fpu.sv hw/rtl/fp/fp_accum.sv hw/rtl/core/scratch_mem.sv hw/rtl/evu/evu_core.sv
           hw/tb/pyuvm/evu/evu_dut.sv} {
    read_verilog -sv $root/$f
}

synth_design -top evu_dut -part $part -mode out_of_context -flatten_hierarchy rebuilt

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
report_utilization -hierarchical -hierarchical_depth 2 -file $out/util.rpt

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
puts "WNS: $wns ns  WHS: $whs ns"
if {$wns < 0 || $whs < 0} {
    puts "ERROR: timing not met"
    exit 1
}
