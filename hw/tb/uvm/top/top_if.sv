// Pins of accel_core as seen by the top-level UVM environment: just the
// clock, reset and the two UART wires. Everything the testbench does to the
// accelerator goes through these two wires, exactly like the real board.
interface top_if (input logic clk); // Clock input: rising edge advances the accelerator and timestamps UART observations.
  logic rst;     // Reset: testbench drives active high to return the accelerator's control logic to idle.
  logic uart_rx; // Universal asynchronous receiver/transmitter receive line: testbench drives host command bits into the design.
  logic uart_tx; // Universal asynchronous receiver/transmitter transmit line: testbench observes reply bits coming from the design.
  logic busy;    // Command busy status: design reports command execution or raw reply generation; used for debug and coverage.
  logic err;     // Command error status: design reports nonzero checksum/framing counters; used for debug and coverage.

  longint unsigned cycle = 0; // Clock cycle count: free-running timestamp for the command and UART monitors.
  always @(posedge clk) cycle <= cycle + 1;

  // UART lines idle high. Once out of reset the DUT's line must never be X.
  a_tx_known: assert property (@(posedge clk) disable iff (rst) !$isunknown(uart_tx))
    else $error("uart_tx is X");
endinterface
