// This module receives bytes from the LAN8720A PHY over RMII. RMII moves two
// bits (a "dibit") per 50 MHz clock, so one byte takes four clocks at
// 100 Mbit/s. The PHY sends the least significant dibit of each byte first.
//
// A frame on these pins looks like this (one entry per 50 MHz clock):
//
//   crs_dv  0 0 1 1 1 1 ... 1 1 1 1 1 ... 1 0 1 0 1 0 0
//   rxd     x x 0 0 1 1 ... 1 3 d d d ... d d d d d x x
//               ^   ^       ^ ^                   ^
//               |   |       | SFD ends (11)        last data dibit
//               |   preamble 01 01 01 ...
//               carrier sense, data not valid yet (rxd = 00)
//
// CRS_DV ("carrier sense / data valid") goes high when the PHY hears a
// carrier. The preamble is 0x55 bytes (dibits 01 01 01 01) and the start-of-
// frame delimiter 0xD5 ends with the dibit 11, after which every dibit is
// frame data. At the end the PHY may drop CRS_DV while its internal FIFO
// still holds data; it then toggles CRS_DV (low on the first dibit of each
// nibble, high on the second) until the data runs out. I therefore treat a
// dibit as valid while CRS_DV is high now or was high one clock ago, and end
// the frame after two low samples in a row. That rule also takes in one
// extra dibit after the real end, which only ever forms an incomplete byte
// and is thrown away.
//
// This block knows nothing about MAC addresses or CRCs; eth_rx does that in
// the 100 MHz domain. I hand bytes over through one "event" register and a
// toggle bit: every time the toggle changes, ev_kind/ev_data hold a new
// event. The 50 and 100 MHz clocks come from the same MMCM with aligned
// edges, so Vivado can time these paths directly and no synchronizer is
// needed. Events are at least two 50 MHz clocks apart, which is four clocks
// on the 100 MHz side, so none can be missed.
module rmii_rx (
    input  logic       clk_rmii,     // 50 MHz, same clock the PHY uses
    input  logic       rst,
    input  logic [1:0] rxd,
    input  logic       crs_dv,
    input  logic       rx_er,
    output logic       ev_tgl,       // flips once per event
    output logic [1:0] ev_kind,      // EV_DATA, EV_END_OK, EV_END_ERR
    output logic [7:0] ev_data
);
  localparam logic [1:0] EV_DATA = 2'd0, EV_END_OK = 2'd1, EV_END_ERR = 2'd2;

  // Input registers. These are placed in the I/O blocks next to the pins
  // (IOB property in the XDC), so the PHY's timing is measured to a fixed
  // flip-flop instead of to wherever the router puts the logic.
  (* IOB = "TRUE" *) logic [1:0] rxd_q;
  (* IOB = "TRUE" *) logic       crs_q, er_q;
  logic       crs_p;            // crs_q one clock earlier
  logic       in_frame;         // between SFD and end of frame
  logic       pre_seen;         // at least one 01 preamble dibit seen
  logic [1:0] cnt;              // dibits collected in the current byte
  logic [5:0] sh;               // the first three dibits of the byte
  logic       err;              // RX_ER was raised during this frame

  wire carrier = crs_q || crs_p;

  always_ff @(posedge clk_rmii) begin
    rxd_q <= rxd;
    crs_q <= crs_dv;
    er_q  <= rx_er;
    crs_p <= crs_q;
    if (rst) begin
      in_frame <= 1'b0;
      pre_seen <= 1'b0;
      ev_tgl   <= 1'b0;
      ev_kind  <= EV_DATA;
      ev_data  <= '0;
    end else if (!in_frame) begin
      // Hunting for the SFD. Leading 00 dibits (carrier sensed, no data
      // yet) are allowed before the preamble. Anything else resets the hunt.
      if (!carrier)                          pre_seen <= 1'b0;
      else if (rxd_q == 2'b01)               pre_seen <= 1'b1;
      else if (rxd_q == 2'b11 && pre_seen) begin
        in_frame <= 1'b1;
        pre_seen <= 1'b0;
        cnt      <= '0;
        err      <= 1'b0;
      end else if (rxd_q != 2'b00)           pre_seen <= 1'b0;
    end else if (!carrier) begin
      // Two low samples: the frame is over. Any partial byte is dropped;
      // eth_rx's CRC check decides whether the frame was good.
      in_frame <= 1'b0;
      ev_kind  <= err ? EV_END_ERR : EV_END_OK;
      ev_tgl   <= ~ev_tgl;
    end else begin
      // Collect one dibit. The first dibit is bits 1:0 of the byte, so each
      // new dibit goes in at the top and the older ones move down.
      if (er_q) err <= 1'b1;
      cnt <= cnt + 1'b1;
      if (cnt == 2'd3) begin
        ev_kind <= EV_DATA;
        ev_data <= {rxd_q, sh};
        ev_tgl  <= ~ev_tgl;
      end else begin
        sh <= {rxd_q, sh[5:2]};
      end
    end
  end
endmodule
