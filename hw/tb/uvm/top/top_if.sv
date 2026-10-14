// Pins of accel_core as seen by the top-level UVM environment: just the
// clock, reset and the two UART wires. Everything the testbench does to the
// accelerator goes through these two wires, exactly like the real board.
interface top_if (input logic clk);
  logic rst;
  logic uart_rx;     // host -> DUT (testbench drives it)
  logic uart_tx;     // DUT -> host (testbench only watches it)
  logic busy, err;   // status outputs, only used for debug and coverage

  longint unsigned cycle = 0;
  always @(posedge clk) cycle <= cycle + 1;

  // UART lines idle high. Once out of reset the DUT's line must never be X.
  a_tx_known: assert property (@(posedge clk) disable iff (rst) !$isunknown(uart_tx))
    else $error("uart_tx is X");
endinterface
