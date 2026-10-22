// Top module for the Ethernet UVM test. The DUT is accel_core with a fast
// UART (8 clocks per bit). The PHY model rmii_phy_if is clocked by the same
// 50 MHz clock as the DUT's RMII logic; this is the "ideal board" view where
// the forwarded clock has no delay. The board-level test tb_nexys_eth covers
// the MMCM, the ODDR and the phase shift.
module tb_eth_uvm;
  import uvm_pkg::*;
  import eth_uvm_pkg::*;

  // Both clocks start at time 0, so every rising edge of clk_rmii lines up
  // with a rising edge of clk, as when one MMCM makes them on the board.
  logic clk = 1'b0, clk_rmii = 1'b0;
  always #5  clk      = ~clk;
  always #10 clk_rmii = ~clk_rmii;

  // The DUT's TXD changes on the falling edge, so the PHY model's 4 ns setup
  // and 1.5 ns hold checks apply here too (with 10 ns to spare each).
  top_if      bus (clk);
  rmii_phy_if phy (clk_rmii);

  accel_core #(.CLKS_PER_BIT(8)) dut (
      .clk, .rst(bus.rst), .uart_rx_i(bus.uart_rx), .uart_tx_o(bus.uart_tx),
      .clk_rmii, .rmii_rxd(phy.rxd), .rmii_crs_dv(phy.crs_dv), .rmii_rx_er(phy.rx_er),
      .rmii_txd(phy.txd), .rmii_tx_en(phy.tx_en),
      .busy_o(bus.busy), .err_o(bus.err), .eth_rx_o(), .eth_tx_o(), .eth_err_o(), .eth_drop_o());

  initial begin
    bus.rst     = 1'b1;
    bus.uart_rx = 1'b1;
    repeat (10) @(posedge clk);
    bus.rst <= 1'b0;
  end

  // Once out of reset the DUT must never drive X onto the PHY.
  a_tx_known: assert property (@(posedge clk_rmii) disable iff (bus.rst) !$isunknown({phy.tx_en, phy.txd}))
    else $error("RMII TX pins are X");

  initial begin
    uvm_config_db #(virtual top_if)::set(null, "*", "vif", bus);
    uvm_config_db #(virtual rmii_phy_if)::set(null, "*", "rmii_vif", phy);
    run_test();
  end

  final if (phy.tx_timing_errors != 0) $error("%0d RMII TX timing violations", phy.tx_timing_errors);
endmodule
