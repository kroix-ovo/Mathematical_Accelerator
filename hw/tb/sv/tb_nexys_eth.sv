// Board-level test of the Ethernet path in xsim. The DUT is nexys_top itself,
// with Xilinx's simulation models of the MMCM, BUFGs and ODDR (the unisim
// library), so this is the only test that runs the real clock generation,
// the forwarded 50 MHz ETH_REFCLK, its 321.75 degree phase shift, and the PHY
// reset sequence.
//
// The PHY model is clocked by ETH_REFCLK as it leaves the FPGA, delayed to
// stand in for the output pad and the board trace. The transmit pins reach
// the PHY with the same kind of delay, and the PHY's outputs reach the FPGA
// a little later still. Those delays are rough numbers, not a timing
// analysis (that is Vivado's job with the XDC constraints); what this test
// shows is that with realistic delays and the chosen phase, the PHY's setup
// and hold checks (4 ns / 1.5 ns) pass and every frame arrives intact in
// both directions.
//
// Steps: wait for the MMCM to lock and ETH_RSTN to rise, check the REFCLK
// period, then ARP, a ping, a UDP PING command and a UDP FILL command, and
// finally a PING over the real 2 Mbaud UART to show the fallback still works.
`timescale 1ns / 1ps
module tb_nexys_eth;
  import uvm_pkg::*;
  import top_uvm_pkg::*;
  import eth_uvm_pkg::*;

  localparam realtime T_OUT  = 4.5ns;   // FPGA output pad + trace to the PHY
  localparam realtime T_IN   = 1.0ns;   // trace + input buffer back into the FPGA
  localparam int      CPB    = 50;      // 2 Mbaud at 100 MHz, as on the board

  logic clk100 = 1'b0;
  always #5 clk100 = ~clk100;
  logic resetn = 1'b0;
  logic uart_in = 1'b1, uart_out;
  logic eth_refclk, eth_rstn, eth_mdc, eth_txen;
  logic [1:0] eth_txd;
  logic [15:0] led;

  // Delayed copies: what the PHY sees, and what the FPGA sees from the PHY.
  logic       phy_refclk, phy_txen;
  logic [1:0] phy_txd;
  assign #(T_OUT) phy_refclk = eth_refclk;
  assign #(T_OUT) phy_txen   = eth_txen;
  assign #(T_OUT) phy_txd    = eth_txd;

  rmii_phy_if phy (phy_refclk);
  assign phy.txd   = phy_txd;
  assign phy.tx_en = phy_txen;
  logic [1:0] fpga_rxd;
  logic       fpga_crs, fpga_rxer;
  assign #(T_IN) fpga_rxd  = phy.rxd;
  assign #(T_IN) fpga_crs  = phy.crs_dv;
  assign #(T_IN) fpga_rxer = phy.rx_er;

  nexys_top #(.PHY_RST_BITS(8)) dut (
      .CLK100MHZ(clk100), .CPU_RESETN(resetn), .UART_TXD_IN(uart_in), .UART_RXD_OUT(uart_out),
      .ETH_REFCLK(eth_refclk), .ETH_RSTN(eth_rstn), .ETH_MDC(eth_mdc),
      .ETH_RXD(fpga_rxd), .ETH_CRSDV(fpga_crs), .ETH_RXERR(fpga_rxer),
      .ETH_TXD(eth_txd), .ETH_TXEN(eth_txen), .LED(led));

  localparam bit [47:0] HMAC = 48'hD8BB_C1A0_B0C0;
  localparam bit [31:0] HIP  = 32'hC0A8_0A01;
  int errors = 0;
  eth_ref_model model;

  // A receiver runs for the whole test and queues every frame the board
  // sends. (A fork inside a foreach inside a fork crashed xsim 2023.2, so the
  // exchange task below only sends and then waits on this queue.)
  byte_q_q got_frames;
  initial forever begin
    byte_q f;
    bit    ok;
    phy.recv_frame(f, ok);
    if (!ok) begin
      errors++;
      $error("received a frame with a bad preamble or partial byte");
    end
    got_frames.push_back(f);
  end

  // Send one frame to the board and compare every frame it sends back.
  task automatic exchange(input byte_q frame, input string what);
    byte_q_q exp = model.handle(frame, 0);
    got_frames.delete();
    phy.send_frame(frame, 2, 28, 2);
    for (int t = 0; t < 50_000 && got_frames.size() < exp.size(); t++) @(posedge clk100);
    repeat (2_000) @(posedge clk100);       // and nothing extra arrives
    if (got_frames.size() != exp.size()) begin
      errors++;
      $error("%s: %0d frames back, expected %0d", what, got_frames.size(), exp.size());
    end
    foreach (exp[i]) begin
      if (i >= got_frames.size()) break;
      if (got_frames[i] != exp[i]) begin
        errors++;
        $error("%s: reply %0d wrong\n  expected %s\n  got      %s", what, i, top_codec::hex(exp[i], 80),
               top_codec::hex(got_frames[i], 80));
      end else $display("  [%s] reply %0d ok (%0d bytes)", what, i, exp[i].size());
    end
  endtask

  task automatic uart_byte(input byte unsigned b);
    uart_in = 1'b0;
    repeat (CPB) @(posedge clk100);
    for (int i = 0; i < 8; i++) begin uart_in = b[i]; repeat (CPB) @(posedge clk100); end
    uart_in = 1'b1;
    repeat (CPB) @(posedge clk100);
  endtask

  task automatic uart_get(output byte unsigned b);
    @(negedge uart_out);
    repeat (CPB / 2) @(posedge clk100);
    for (int i = 0; i < 8; i++) begin repeat (CPB) @(posedge clk100); b[i] = uart_out; end
    repeat (CPB) @(posedge clk100);
  endtask

  initial begin
    realtime t0, t1, rst_rise;
    byte_q none, data, p, w, rep, exp;
    model = new("model");
    phy.tco = 10ns;                       // inside the LAN8720A's 2..16 ns range

    #(200ns) resetn = 1'b1;
    // The MMCM needs some microseconds to lock; then ETH_RSTN follows.
    wait (eth_rstn === 1'b1);
    rst_rise = $realtime;
    $display("  ETH_RSTN released at %0t", rst_rise);
    @(posedge eth_refclk) t0 = $realtime;
    repeat (10) @(posedge eth_refclk);
    t1 = $realtime;
    if (t1 - t0 < 199.9ns || t1 - t0 > 200.1ns) begin
      errors++;
      $error("ETH_REFCLK period %0t, expected 20 ns", (t1 - t0) / 10);
    end
    repeat (200) @(posedge clk100);

    exchange(eth_codec::arp_request(HMAC, HIP), "ARP");
    for (int i = 0; i < 32; i++) data.push_back(8'h61 + i % 23);
    exchange(eth_codec::ping(HMAC, HIP, 16'h0001, 16'h0007, data), "ping");
    w = top_codec::cobs_encode(top_codec::frame(accel_pkg::OP_PING, 8'h11, none));
    w.push_back(0);
    exchange(eth_codec::udp_frame(w, HMAC, HIP, 16'd55555), "UDP PING");
    p = '{accel_pkg::K_FILL};
    cmd_base_seq::put32(p, 0); cmd_base_seq::put32(p, 64); cmd_base_seq::put32(p, 32'h3F80_0000);
    w = top_codec::cobs_encode(top_codec::frame(accel_pkg::OP_RUN, 8'h12, p));
    w.push_back(0);
    exchange(eth_codec::udp_frame(w, HMAC, HIP, 16'd55555), "UDP FILL");

    // The UART still answers, and with the same device state.
    w = top_codec::cobs_encode(top_codec::frame(accel_pkg::OP_PING, 8'h13, none));
    w.push_back(0);
    void'(model.dev.predict(top_codec::cobs_encode(top_codec::frame(accel_pkg::OP_PING, 8'h13, none)), exp));
    fork
      foreach (w[i]) uart_byte(w[i]);
      begin
        byte unsigned b;
        do begin uart_get(b); rep.push_back(b); end while (b != 0);
      end
    join
    if (rep != exp) begin
      errors++;
      $error("UART PING reply %s, expected %s", top_codec::hex(rep), top_codec::hex(exp));
    end else $display("  [UART PING] reply ok");

    if (phy.tx_timing_errors != 0) errors += phy.tx_timing_errors;
    if (led[5] || led[6]) begin errors++; $error("Ethernet error/drop LED is on"); end
    if (errors == 0) $display("BOARD_ETH_PASS");
    else             $display("BOARD_ETH_FAIL (%0d errors)", errors);
    $finish;
  end
endmodule
