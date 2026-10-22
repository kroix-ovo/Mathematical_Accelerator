// This module connects the reusable accelerator to Nexys A7-100T pins.
// I keep the computation in accel_core; this wrapper makes the clocks, turns
// the active-low button into a safe internal reset, starts the Ethernet PHY,
// and displays status on the LEDs.
//
// Clocks. The LAN8720A on this board has no crystal of its own: the FPGA must
// give it a 50 MHz reference clock on ETH_REFCLK, and the RMII data in both
// directions is timed against that clock. One MMCM (a clock synthesizer
// inside the FPGA) makes three clocks from the 100 MHz oscillator:
//   clk       100 MHz  everything in accel_core except the RMII shifters
//   clk_rmii   50 MHz  rmii_rx / rmii_tx and the RMII I/O flip-flops
//   clk_ref    50 MHz  same frequency, shifted by REFCLK_PHASE degrees; it
//                      only drives the ETH_REFCLK pin through an ODDR
// Because all three come from one MMCM, their edges have fixed positions
// relative to each other. Vivado knows those positions and checks every path
// between the clocks, so the 50/100 MHz hand-offs need no synchronizers.
//
// Why the phase shift: the LAN8720A changes RXD between 3 ns and 14 ns after
// it sees a rising REFCLK edge, and the clock itself needs a few ns to leave
// the FPGA. Without a shift my sampling edge would land near the end of the
// PHY's valid window. Shifting the forwarded clock by 321.75 degrees (2 ns
// earlier than my own edges, before the pad delay) puts my sampling edge
// near the middle of that window, with about 1.8 ns to spare on either
// side. I found the value from Vivado's slack on the RMII input paths. The
// transmit side uses the falling edge (see rmii_tx). The XDC describes the
// PHY's timing, and the Vivado timing report shows the margins.
module nexys_top #(
    // PHY reset length is 2^PHY_RST_BITS clocks; the board-level simulation
    // shortens it so the test does not spend a millisecond waiting.
    parameter int PHY_RST_BITS = 17
) (
    input  logic        CLK100MHZ,
    input  logic        CPU_RESETN,
    input  logic        UART_TXD_IN,   // from the FT2232 (host -> FPGA)
    output logic        UART_RXD_OUT,  // to the FT2232   (FPGA -> host)
    // LAN8720A Ethernet PHY (RMII)
    output logic        ETH_REFCLK,
    output logic        ETH_RSTN,
    output logic        ETH_MDC,
    input  logic [1:0]  ETH_RXD,
    input  logic        ETH_CRSDV,
    input  logic        ETH_RXERR,
    output logic [1:0]  ETH_TXD,
    output logic        ETH_TXEN,
    output logic [15:0] LED
);
  localparam real REFCLK_PHASE = 321.75;   // a multiple of the MMCM's 2.25 degree step here

  // ---------------------------------------------------------------------
  // MMCM: 100 MHz in, VCO at 1000 MHz (x10), outputs divided down from it.
  // The feedback goes through a BUFG so the internal clk lines up with the
  // input pin. BUFGs are the dedicated low-skew clock networks.
  logic clkfb, clkfb_buf, clk100_u, clk50_u, clkref_u, locked;
  logic clk, clk_rmii, clk_ref;

  MMCME2_BASE #(
      .CLKIN1_PERIOD   (10.0),
      .DIVCLK_DIVIDE   (1),
      .CLKFBOUT_MULT_F (10.0),
      .CLKOUT0_DIVIDE_F(10.0),
      .CLKOUT1_DIVIDE  (20),
      .CLKOUT2_DIVIDE  (20),
      .CLKOUT2_PHASE   (REFCLK_PHASE)
  ) u_mmcm (
      .CLKIN1(CLK100MHZ), .CLKFBIN(clkfb_buf), .CLKFBOUT(clkfb), .CLKFBOUTB(),
      .CLKOUT0(clk100_u), .CLKOUT0B(), .CLKOUT1(clk50_u), .CLKOUT1B(), .CLKOUT2(clkref_u), .CLKOUT2B(),
      .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
      .LOCKED(locked), .PWRDWN(1'b0), .RST(1'b0));

  BUFG u_bufg_fb  (.I(clkfb),    .O(clkfb_buf));
  BUFG u_bufg_100 (.I(clk100_u), .O(clk));
  BUFG u_bufg_50  (.I(clk50_u),  .O(clk_rmii));
  BUFG u_bufg_ref (.I(clkref_u), .O(clk_ref));

  // An ODDR is a flip-flop in the output pad that can send one value on the
  // rising edge and another on the falling edge. With 1 then 0 it copies its
  // clock to the pin. This is the recommended way to send a clock off chip,
  // because the pin delay is then fixed and known to the timing tools.
  ODDR #(.DDR_CLK_EDGE("SAME_EDGE")) u_refclk_oddr (
      .Q(ETH_REFCLK), .C(clk_ref), .CE(1'b1), .D1(1'b1), .D2(1'b0), .R(1'b0), .S(1'b0));

  // ---------------------------------------------------------------------
  // Core reset. Pressing reset acts immediately, and so does losing MMCM
  // lock. After release, three clock edges pass before the core resumes;
  // this avoids an uneven reset release across logic.
  logic [2:0] rst_sync;
  wire        arst_n = CPU_RESETN && locked;
  always_ff @(posedge clk or negedge arst_n)
    if (!arst_n) rst_sync <= '1;
    else         rst_sync <= {rst_sync[1:0], 1'b0};
  wire rst = rst_sync[2];

  // PHY reset. The LAN8720A reads its configuration straps (on the RXD and
  // CRS_DV pins) when nRST rises, and its reference clock must already be
  // running then. I hold ETH_RSTN low for 2^17 clocks (1.3 ms) after the
  // MMCM locks, which is more than the 100 us the datasheet asks for. The
  // reset button does not restart the PHY, so the link stays up while I
  // reset the accelerator.
  logic [PHY_RST_BITS:0] phy_cnt;
  always_ff @(posedge clk or negedge locked)
    if (!locked)                     phy_cnt <= '0;
    else if (!phy_cnt[PHY_RST_BITS]) phy_cnt <= phy_cnt + 1'b1;
  assign ETH_RSTN = phy_cnt[PHY_RST_BITS];
  // No MDIO management: the PHY's power-on defaults (autonegotiation of all
  // speeds) are what I want, and the RJ45 LEDs already show the link.
  assign ETH_MDC = 1'b0;

  logic busy, err, eth_rx, eth_tx, eth_err, eth_drop;
  logic [6:0] eth_dbg;
  accel_core u_core (
      .clk, .rst, .uart_rx_i(UART_TXD_IN), .uart_tx_o(UART_RXD_OUT),
      .clk_rmii, .rmii_rxd(ETH_RXD), .rmii_crs_dv(ETH_CRSDV), .rmii_rx_er(ETH_RXERR),
      .rmii_txd(ETH_TXD), .rmii_tx_en(ETH_TXEN),
      .busy_o(busy), .err_o(err),
      .eth_rx_o(eth_rx), .eth_tx_o(eth_tx), .eth_err_o(eth_err), .eth_drop_o(eth_drop),
      .eth_dbg_o(eth_dbg));

  // LED0: heartbeat (~1.5 Hz), LED1: command busy (stretched), LED2: link error
  // seen (CRC or framing, either transport), LED3: Ethernet frame received,
  // LED4: Ethernet frame sent (both stretched), LED5: bad Ethernet frame seen,
  // LED6: Ethernet frame dropped, LED7: PHY out of reset, LED15: UART RX activity.
  // LED8-11 show the Ethernet path's state directly, for debugging a stall:
  // LED8 transmitter busy, LED9 UDP reply waiting in the reply buffer, LED10
  // replay engine inside a datagram, LED11 a reply still in the encoder.
  // LED12-14 latch when LED8, LED9 or LED10 stays on for more than 10 ms
  // (LED12 = reply buffer, LED13 = transmitter, LED14 = replay engine).
  logic [25:0] hb;
  logic [21:0] busy_stretch, rx_stretch, erx_stretch, etx_stretch;
  always_ff @(posedge clk) begin
    hb <= hb + 1'b1;
    busy_stretch <= busy ? '1 : (busy_stretch != 0 ? busy_stretch - 1'b1 : '0);
    rx_stretch   <= !UART_TXD_IN ? '1 : (rx_stretch != 0 ? rx_stretch - 1'b1 : '0);
    erx_stretch  <= eth_rx ? '1 : (erx_stretch != 0 ? erx_stretch - 1'b1 : '0);
    etx_stretch  <= eth_tx ? '1 : (etx_stretch != 0 ? etx_stretch - 1'b1 : '0);
  end

  assign LED = {rx_stretch != 0, eth_dbg[6], eth_dbg[4], eth_dbg[5], eth_dbg[3:0], ETH_RSTN, eth_drop, eth_err, etx_stretch != 0, erx_stretch != 0,
                err, busy_stretch != 0, hb[25]};
endmodule
