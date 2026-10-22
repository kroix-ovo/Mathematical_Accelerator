// Top module for the accelerator-level UVM test. The DUT is accel_core with
// a fast UART (8 clocks per bit) so simulation finishes in reasonable time.
module tb_top_uvm;
  import uvm_pkg::*;
  import top_uvm_pkg::*;

  logic clk = 1'b0;
  always #5 clk = ~clk;
  // The Ethernet side is idle here (hw/tb/uvm/eth tests it). Its 50 MHz
  // clock has rising edges on every other clk edge, as on the board.
  logic clk_rmii = 1'b0;
  always #10 clk_rmii = ~clk_rmii;

  top_if bus (clk);

  accel_core #(.CLKS_PER_BIT(8)) dut (
      .clk, .rst(bus.rst), .uart_rx_i(bus.uart_rx), .uart_tx_o(bus.uart_tx),
      .clk_rmii, .rmii_rxd(2'b00), .rmii_crs_dv(1'b0), .rmii_rx_er(1'b0), .rmii_txd(), .rmii_tx_en(),
      .busy_o(bus.busy), .err_o(bus.err), .eth_rx_o(), .eth_tx_o(), .eth_err_o(), .eth_drop_o());

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
