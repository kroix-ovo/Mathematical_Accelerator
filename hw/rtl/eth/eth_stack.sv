// This wraps the whole Ethernet transport so accel_core can treat it almost
// like a second UART. One side has the RMII pins of the LAN8720A. The other
// side has
//   out_valid/out_data  the payload bytes of received UDP command datagrams,
//                       one byte per pulse, for a COBS decoder and frame_rx
//   in_valid/ready/data the COBS-encoded reply bytes from the core, which
//                       become one UDP datagram per reply frame
//
//            clk_rmii (50 MHz)        clk (100 MHz)
//   RMII -> rmii_rx --events--> eth_rx --slots--> replay --> out_* (to COBS decoder)
//                                  |  \--ARP/ping jobs--\
//   RMII <- rmii_tx <--bytes--- eth_tx <------------------ in_* (from COBS encoder)
//
// The replay engine reads a stored datagram out of eth_rx's frame memory.
// After every 0x00 delimiter it waits until the core is idle again and the
// previous UDP reply has been sent, so the command processor never sees a new
// frame while it is busy (frame_rx would drop it). It also adds a 0x00 if the
// sender left it off, so one datagram's last frame can never run into the
// next datagram's first. While I replay a datagram I remember who sent it;
// the reply goes back to that MAC, IP address and port.
module eth_stack
  import accel_pkg::*;
(
    input  logic        clk,
    input  logic        clk_rmii,
    input  logic        rst,
    // RMII pins
    input  logic [1:0]  rmii_rxd,
    input  logic        rmii_crs_dv,
    input  logic        rmii_rx_er,
    output logic [1:0]  rmii_txd,
    output logic        rmii_tx_en,
    // command bytes toward the core
    output logic        out_valid,
    output logic [7:0]  out_data,
    input  logic        core_idle,     // cmd_proc idle and no reply still on its way out
    // reply bytes from the core
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_data,
    // status for LEDs and debug
    output logic [15:0] rx_errors,
    output logic [15:0] rx_drops,
    output logic        rx_frame,      // pulse per received frame
    output logic        tx_frame,      // pulse per sent frame
    // Debug view for LEDs. Bits 2:0 are live: replay engine inside a
    // datagram, reply buffer occupied, transmitter busy. Bits 5:3 latch
    // (until reset) when the same three conditions last longer than 10 ms,
    // which never happens when things work: a reply normally leaves within
    // about 0.1 ms.
    output logic [5:0]  dbg
);
  localparam logic [1:0] K_UDP = 2'd2, K_ICMP = 2'd3;

  // ---------------------------------------------------------------------
  // 50 MHz side.
  logic       ev_tgl;
  logic [1:0] ev_kind;
  logic [7:0] ev_data;
  rmii_rx u_rmii_rx (
      .clk_rmii, .rst, .rxd(rmii_rxd), .crs_dv(rmii_crs_dv), .rx_er(rmii_rx_er),
      .ev_tgl, .ev_kind, .ev_data);

  logic       tx_tgl, tx_last, tx_ack, underrun;
  logic [7:0] tx_data;
  rmii_tx u_rmii_tx (
      .clk_rmii, .rst, .in_tgl(tx_tgl), .in_data(tx_data), .in_last(tx_last), .ack_tgl(tx_ack),
      .txd(rmii_txd), .tx_en(rmii_tx_en), .underrun);

  // ---------------------------------------------------------------------
  // 100 MHz side: receive and transmit halves.
  logic [1:0]  slot_kind [2];
  logic [47:0] slot_mac  [2];
  logic [31:0] slot_ip   [2];
  logic [15:0] slot_port [2], slot_len [2], slot_csum [2];
  logic        first_udp, udp_free, icmp_free;
  logic [11:0] rd_addr_a, rd_addr_b;
  logic [7:0]  rd_data_a, rd_data_b;
  logic        arp_pending, arp_done;
  logic [47:0] arp_eth_dst, arp_sha;
  logic [31:0] arp_spa;

  eth_rx u_rx (
      .clk, .rst, .ev_tgl, .ev_kind, .ev_data,
      .slot_kind, .slot_mac, .slot_ip, .slot_port, .slot_len, .slot_csum,
      .first_udp, .udp_free, .icmp_free,
      .rd_addr_a, .rd_data_a, .rd_addr_b, .rd_data_b,
      .arp_pending, .arp_eth_dst, .arp_sha, .arp_spa, .arp_done,
      .rx_errors, .rx_drops, .rx_frame);

  // At most one slot holds a ping; icmp_slot says which.
  wire icmp_slot    = (slot_kind[1] == K_ICMP);
  wire icmp_pending = (slot_kind[0] == K_ICMP) || (slot_kind[1] == K_ICMP);

  logic        udp_busy, tx_active;
  logic [47:0] rep_mac;
  logic [31:0] rep_ip;
  logic [15:0] rep_port;
  eth_tx u_tx (
      .clk, .rst,
      .arp_pending, .arp_eth_dst, .arp_sha, .arp_spa, .arp_done,
      .icmp_pending, .icmp_slot, .icmp_mac(slot_mac[icmp_slot]), .icmp_ip(slot_ip[icmp_slot]),
      .icmp_tlen(slot_len[icmp_slot]), .icmp_csum(slot_csum[icmp_slot]), .icmp_done(icmp_free),
      .rx_rd_addr(rd_addr_b), .rx_rd_data(rd_data_b),
      .in_valid, .in_ready, .in_data, .udp_mac(rep_mac), .udp_ip(rep_ip), .udp_port(rep_port), .udp_busy,
      .tx_tgl, .tx_data, .tx_last, .tx_ack, .tx_frame, .tx_active);

  // ---------------------------------------------------------------------
  // Replay engine: stored UDP payload -> COBS decoder.
  typedef enum logic [2:0] {R_IDLE, R_ADDR, R_EMIT, R_ZERO, R_GATE} rstate_t;
  rstate_t     rs;
  logic        rslot;
  logic [10:0] ptr;               // payload byte being read
  logic [15:0] rlen;              // payload length of this datagram
  logic        final_gate;        // the gate after the datagram's last frame
  logic [3:0]  gate_cnt;

  // The UDP payload starts at byte 42 of the stored frame.
  assign rd_addr_a = {rslot, 11'd42 + ptr};
  wire   go = core_idle && !udp_busy;
  wire [2:0] live = {rs != R_IDLE, udp_busy, tx_active};
  logic [19:0] long_cnt [3];
  logic [2:0]  stuck;
  always_ff @(posedge clk) begin
    for (int i = 0; i < 3; i++) begin
      if (rst) begin
        long_cnt[i] <= '0;
        stuck[i]    <= 1'b0;
      end else if (!live[i]) begin
        long_cnt[i] <= '0;
      end else if (long_cnt[i] == 20'd1_000_000) begin
        stuck[i] <= 1'b1;            // 10 ms at 100 MHz
      end else begin
        long_cnt[i] <= long_cnt[i] + 1'b1;
      end
    end
  end
  assign dbg = {stuck, live};

  always_ff @(posedge clk) begin
    out_valid <= 1'b0;
    udp_free  <= 1'b0;
    if (rst) begin
      rs <= R_IDLE;
    end else begin
      case (rs)
        // Start the oldest waiting datagram once the core has nothing in flight.
        R_IDLE: if (slot_kind[first_udp] == K_UDP && go && !udp_free) begin
          rslot    <= first_udp;
          rep_mac  <= slot_mac[first_udp];
          rep_ip   <= slot_ip[first_udp];
          rep_port <= slot_port[first_udp];
          rlen     <= slot_len[first_udp];
          ptr      <= '0;
          rs       <= R_ADDR;
        end
        // The address for byte ptr is on the RAM this clock; data next clock.
        R_ADDR: rs <= R_EMIT;
        R_EMIT: begin
          out_valid <= 1'b1;
          out_data  <= rd_data_a;
          ptr       <= ptr + 1'b1;
          gate_cnt  <= '0;
          if (16'(ptr) + 16'd1 == rlen) begin
            // Last byte. Without a closing 0x00 I add one myself.
            if (rd_data_a != 8'h00) rs <= R_ZERO;
            else begin final_gate <= 1'b1; rs <= R_GATE; end
          end else if (rd_data_a == 8'h00) begin
            final_gate <= 1'b0;
            rs <= R_GATE;
          end else begin
            rs <= R_ADDR;
          end
        end
        R_ZERO: begin
          out_valid  <= 1'b1;
          out_data   <= 8'h00;
          final_gate <= 1'b1;
          rs         <= R_GATE;
        end
        // A delimiter has gone out. The decoder, frame_rx and cmd_proc need a
        // few clocks before cmd_proc reports busy, so I wait eight clocks
        // before I trust core_idle, then wait for the command to finish.
        R_GATE: begin
          if (gate_cnt != 4'd8) gate_cnt <= gate_cnt + 1'b1;
          else if (go) begin
            if (final_gate) begin
              udp_free <= 1'b1;
              rs       <= R_IDLE;
            end else begin
              rs <= R_ADDR;
            end
          end
        end
        default: rs <= R_IDLE;
      endcase
    end
  end
endmodule
