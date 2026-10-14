// Top module for the FPU UVM test. It only makes the clock and reset, wires
// the fpu to the interface, publishes the interface to UVM, and starts the
// test chosen with +UVM_TESTNAME. All stimulus and checking live in classes.
module tb_fpu_uvm;
  import uvm_pkg::*;
  import fpu_uvm_pkg::*;

  logic clk = 1'b0;
  always #5 clk = ~clk;                 // 100 MHz

  fpu_if bus (clk);

  fpu dut (
      .clk, .rst(bus.rst),
      .req_valid(bus.req_valid), .req_ready(bus.req_ready), .req_op(bus.req_op),
      .req_a(bus.req_a), .req_b(bus.req_b), .req_tag(bus.req_tag),
      .rsp_valid(bus.rsp_valid), .rsp_result(bus.rsp_result), .rsp_flags(bus.rsp_flags),
      .rsp_tag(bus.rsp_tag));

  initial begin
    bus.rst = 1'b1;
    repeat (5) @(posedge clk);
    bus.rst <= 1'b0;
  end

  initial begin
    // "*" makes the handle visible to every component in the hierarchy.
    uvm_config_db #(virtual fpu_if)::set(null, "*", "vif", bus);
    run_test();
  end
endmodule
