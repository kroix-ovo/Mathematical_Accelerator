// Top module for the accelerator-level UVM test. The DUT is accel_core with
// a fast UART (8 clocks per bit) so simulation finishes in reasonable time.
module tb_top_uvm;
  import uvm_pkg::*;
  import top_uvm_pkg::*;

  logic clk = 1'b0;
  always #5 clk = ~clk;

  top_if bus (clk);

  accel_core #(.CLKS_PER_BIT(8)) dut (
      .clk, .rst(bus.rst), .uart_rx_i(bus.uart_rx), .uart_tx_o(bus.uart_tx),
      .busy_o(bus.busy), .err_o(bus.err));

  initial begin
    bus.rst     = 1'b1;
    bus.uart_rx = 1'b1;
    repeat (10) @(posedge clk);
    bus.rst <= 1'b0;
  end

  initial begin
    uvm_config_db #(virtual top_if)::set(null, "*", "vif", bus);
    run_test();
  end
endmodule
