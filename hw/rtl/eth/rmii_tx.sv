// This module sends bytes to the LAN8720A over RMII, two bits per 50 MHz
// clock, least significant dibit first. It is only a serializer: eth_tx in
// the 100 MHz domain supplies every byte of the frame, including the seven
// 0x55 preamble bytes and the 0xD5 start-of-frame delimiter, and marks the
// last FCS byte with in_last. TX_EN is high exactly while frame dibits are on
// TXD.
//
// The byte hand-off mirrors rmii_rx: eth_tx puts a byte on in_data and flips
// in_tgl; when I copy the byte into my shift register I set ack_tgl equal to
// in_tgl, which tells eth_tx the slot is free for the next byte. I take the
// next byte on the same clock that the fourth dibit of the current one goes
// out, so dibits leave without gaps. eth_tx has about six of its own clocks
// to prepare each byte, which is plenty.
//
// After the last byte I keep TX_EN low for at least 48 clocks: 96 bit times,
// the minimum inter-frame gap Ethernet requires between two frames.
//
// The pins change on the falling edge of clk_rmii, half a period after the
// logic decides each dibit. The PHY samples on its rising REFCLK edge, and
// nexys_top shifts that clock late enough to center my receive window; with
// rising-edge outputs the PHY's 1.5 ns hold time would then have almost no
// margin. Launching on the falling edge moves the transmit data to the middle
// of the PHY's window instead. The XDC lets Vivado check both.
// The toggle/ack pair is a byte handoff between the core and RMII logic.
// A changed toggle announces a new byte; the acknowledgement lets the sender
// reuse its holding register. I serialize each accepted byte into four
// two-bit symbols, including the last byte before ending TX_EN.
module rmii_tx (
    input  logic       clk_rmii,                 // Reduced media independent interface clock: 50 MHz, phase-related to the 100 MHz core clock. Rising edges prepare data; falling edges launch it onto the pins.
    input  logic       rst,                      // Reset: active high, sampled on the 50 MHz rising edge to stop transmission and clear the byte handshake.
    input  logic       in_tgl,                   // Input byte toggle: a change relative to acknowledgement announces a new byte held by the 100 MHz sender.
    input  logic [7:0] in_data,                  // Input byte data: preamble, frame or checksum byte held stable until its toggle is acknowledged.
    input  logic       in_last,                  // Input last-byte marker: high beside the frame's final byte requests the transmit gap after serialization.
    output logic       ack_tgl,                  // Acknowledgement toggle: copies the input toggle when the offered byte is captured, allowing the sender to advance.
    output logic [1:0] txd,                      // Transmit data: two reduced-media-interface bits launched on falling clock edges for the physical-layer device.
    output logic       tx_en,                    // Transmit enable: high while transmit data carries frame symbols; low during the inter-frame gap.
    output logic       underrun                  // Transmit underrun: one-clock pulse when the next required byte was not ready, forcing the frame to end early.
);
  localparam int IFG_CLKS = 48;

  // Output registers in the I/O blocks, like the inputs of rmii_rx. The
  // logic below fills txd_r/tx_en_r on the rising edge; the pad flip-flops
  // copy them on the falling edge.
  logic [1:0] txd_r;
  logic       tx_en_r;
  (* IOB = "TRUE" *) logic [1:0] txd_q;
  (* IOB = "TRUE" *) logic       tx_en_q;
  always_ff @(negedge clk_rmii) begin
    txd_q   <= txd_r;
    tx_en_q <= tx_en_r;
  end
  assign txd   = txd_q;
  assign tx_en = tx_en_q;

  logic       sending;
  logic [7:0] sh;          // byte being sent; shifts right by one dibit per clock
  logic       last;        // sh holds the frame's final byte
  logic [1:0] cnt;         // dibits of sh already sent
  logic [5:0] ifg;         // gap clocks still to wait

  always_ff @(posedge clk_rmii) begin
    underrun <= 1'b0;
    if (rst) begin
      sending <= 1'b0;
      ack_tgl <= 1'b0;
      ifg     <= '0;
      txd_r   <= '0;
      tx_en_r <= 1'b0;
    end else if (!sending) begin
      txd_r   <= '0;
      tx_en_r <= 1'b0;
      if (ifg != 0) begin
        ifg <= ifg - 1'b1;
      end else if (in_tgl != ack_tgl) begin
        // First byte of a frame (the first preamble byte).
        sh      <= in_data;
        last    <= in_last;
        ack_tgl <= in_tgl;
        cnt     <= '0;
        sending <= 1'b1;
      end
    end else begin
      // Prepare one dibit; the pad registers show it half a clock later.
      txd_r   <= sh[1:0];
      tx_en_r <= 1'b1;
      sh      <= sh >> 2;
      cnt     <= cnt + 1'b1;
      if (cnt == 2'd3) begin
        if (last) begin
          sending <= 1'b0;
          ifg     <= 6'(IFG_CLKS);
        end else if (in_tgl != ack_tgl) begin
          sh      <= in_data;
          last    <= in_last;
          ack_tgl <= in_tgl;
        end else begin
          // eth_tx did not keep up. Stop the frame; the PHY ends it and the
          // receiver throws it away because its FCS is incomplete.
          underrun <= 1'b1;
          sending  <= 1'b0;
          ifg      <= 6'(IFG_CLKS);
        end
      end
    end
  end
endmodule
