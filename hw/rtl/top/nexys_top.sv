// This module connects the reusable accelerator to Nexys A7-100T pins.
// I keep the computation in accel_core; this wrapper supplies the board clock,
// turns the active-low button into a safe internal reset, and displays status.
module nexys_top (
    input  logic        CLK100MHZ,
    input  logic        CPU_RESETN,
    input  logic        UART_TXD_IN,   // from the FT2232 (host -> FPGA)
    output logic        UART_RXD_OUT,  // to the FT2232   (FPGA -> host)
    output logic [15:0] LED
);
  logic clk;
  assign clk = CLK100MHZ;

  // Pressing reset acts immediately. After release, three clock edges pass
  // before the core resumes; this avoids an uneven reset release across logic.
  logic [2:0] rst_sync;
  always_ff @(posedge clk or negedge CPU_RESETN)
    if (!CPU_RESETN) rst_sync <= '1;
    else             rst_sync <= {rst_sync[1:0], 1'b0};
  wire rst = rst_sync[2];

  logic busy, err;
  accel_core u_core (
      .clk, .rst, .uart_rx_i(UART_TXD_IN), .uart_tx_o(UART_RXD_OUT), .busy_o(busy), .err_o(err));

  // LED0: heartbeat (~1.5 Hz), LED1: command busy (stretched), LED2: link error seen,
  // LED15: host activity on RX.
  logic [25:0] hb;
  logic [21:0] busy_stretch, rx_stretch;
  always_ff @(posedge clk) begin
    hb <= hb + 1'b1;
    busy_stretch <= busy ? '1 : (busy_stretch != 0 ? busy_stretch - 1'b1 : '0);
    rx_stretch   <= !UART_TXD_IN ? '1 : (rx_stretch != 0 ? rx_stretch - 1'b1 : '0);
  end

  assign LED = {rx_stretch != 0, 12'b0, err, busy_stretch != 0, hb[25]};
endmodule
