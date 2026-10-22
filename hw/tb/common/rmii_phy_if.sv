// A simulation model of the LAN8720A's RMII side, written as an interface so
// both the UVM agent (through a virtual interface) and the plain board-level
// testbench can call its tasks.
//
// An interface groups wires and can also hold tasks and checks. A virtual
// interface is a class-side handle to one, which is how UVM classes, which
// cannot own wires, reach the pins.
//
// Receive direction (PHY -> FPGA). send_frame() drives RXD, CRS_DV and RX_ER
// the way the PHY does: each change happens tco after a rising ref_clk edge
// (the PHY's output delay, `tco`), CRS_DV may rise a few dibits before the preamble
// with RXD = 00, the preamble may be shorter than 7 bytes, CRS_DV may toggle
// during the last nibbles (low on the first dibit of each nibble, high on the
// second, which the LAN8720A does while its FIFO drains after the carrier is
// gone), and RX_ER may be raised for one dibit.
//
// Transmit direction (FPGA -> PHY). The PHY samples TXD and TX_EN on each
// rising ref_clk edge and needs them stable tx_setup before and tx_hold
// after it. The two always blocks at the bottom measure that for every edge
// and every change and count violations in tx_timing_errors. recv_frame()
// waits for TX_EN and returns the frame bytes after checking the preamble.
//
// The three timing values are variables rather than parameters, because a
// virtual interface handle must have exactly the parameters of the instance
// it points to; a testbench changes them with e.g. `phy.tx_hold = 0ns;`.
interface rmii_phy_if (input logic ref_clk);
  realtime tco      = 6ns;      // RX output delay after ref_clk
  realtime tx_setup = 4ns;      // LAN8720A datasheet, REF_CLK-in mode
  realtime tx_hold  = 1.5ns;

  logic [1:0] rxd    = 2'b00;
  logic       crs_dv = 1'b0;
  logic       rx_er  = 1'b0;
  logic [1:0] txd;
  logic       tx_en;

  int unsigned tx_timing_errors = 0;
  int unsigned frames_sent = 0;

  // One dibit, tco after the next rising edge.
  task automatic put(input logic crs, input logic [1:0] d, input logic er = 1'b0);
    @(posedge ref_clk);
    #(tco);
    crs_dv = crs;
    rxd    = d;
    rx_er  = er;
  endtask

  // Send one frame (destination MAC .. FCS). rx_er_at < 0 means no error.
  task automatic send_frame(input byte unsigned data[$], input int lead_in = 0, input int pre_dibits = 28,
                            input int toggle_nibbles = 0, input int rx_er_at = -1, input int gap = 48);
    int n = 4 * data.size();
    int toggle_from = n - 2 * toggle_nibbles;
    repeat (lead_in) put(1'b1, 2'b00);
    repeat (pre_dibits) put(1'b1, 2'b01);
    put(1'b1, 2'b01); put(1'b1, 2'b01); put(1'b1, 2'b01); put(1'b1, 2'b11);   // SFD 0xD5
    for (int i = 0; i < n; i++) begin
      logic [1:0] d = data[i / 4] >> (2 * (i % 4));
      logic       crs = !(i >= toggle_from && i % 2 == 0);
      put(crs, d, i == rx_er_at);
    end
    put(1'b0, 2'b00);
    repeat ((gap < 48 ? 48 : gap) - 1) put(1'b0, 2'b00);
    frames_sent++;
  endtask

  // Wait for the next transmitted frame and return its bytes after the SFD.
  // ok is 0 if the preamble was wrong or the frame was not whole bytes.
  task automatic recv_frame(output byte unsigned frame[$], output bit ok);
    logic [1:0] dib[$];
    byte unsigned raw[$];
    frame.delete();
    @(posedge ref_clk iff tx_en === 1'b1);
    while (tx_en === 1'b1) begin
      dib.push_back(txd);
      @(posedge ref_clk);
    end
    for (int k = 0; k + 3 < dib.size(); k += 4)
      raw.push_back({dib[k + 3], dib[k + 2], dib[k + 1], dib[k]});
    ok = (dib.size() % 4 == 0) && raw.size() > 8;
    for (int i = 0; ok && i < 8; i++) ok = (raw[i] == (i == 7 ? 8'hD5 : 8'h55));
    for (int i = 8; i < raw.size(); i++) frame.push_back(raw[i]);
  endtask

  // Setup/hold checks on the transmit pins, measured at this interface.
  realtime last_edge = 0, last_change = 0;
  always @(posedge ref_clk) begin
    if (tx_en === 1'b1 && $realtime > 0 && $realtime - last_change < tx_setup) begin
      tx_timing_errors++;
      $error("RMII TX setup violation: data changed %0t before ref_clk", $realtime - last_change);
    end
    last_edge = $realtime;
  end
  // Every change of the transmit pins is checked, also the ones that start
  // and end a frame; between frames the pins do not move.
  always @(txd or tx_en) begin
    if ($realtime > 0 && $realtime - last_edge < tx_hold) begin
      tx_timing_errors++;
      $error("RMII TX hold violation: data changed %0t after ref_clk", $realtime - last_edge);
    end
    last_change = $realtime;
  end
endinterface
