// This is the receive half of the Ethernet block, in the 100 MHz domain. It
// takes the bytes rmii_rx collected, stores each frame, and decides at the
// end of the frame whether the board has to do something with it.
//
// Byte positions in a frame (after the preamble), which the parser below
// uses directly as case labels:
//
//    0.. 5  destination MAC        6..11 source MAC       12..13 EtherType
//   ARP body (EtherType 0806):
//   14..21  htype, ptype, hlen, plen, oper (must be 0001 0800 06 04 0001)
//   22..27  sender MAC  28..31 sender IP  32..37 target MAC  38..41 target IP
//   IPv4 header (EtherType 0800):
//   14 version/length (45)  15 TOS  16..17 total length  18..19 ID
//   20..21 flags/fragment   22 TTL  23 protocol  24..25 header checksum
//   26..29 source IP        30..33 destination IP
//   34..    ICMP (type, code, checksum, ...) or UDP (ports, length, checksum)
//   42..    UDP payload, which holds the COBS command frames
//   last 4  FCS (CRC-32)
//
// I parse the fields while the bytes go by and keep a copy of the whole
// frame in one of two 2 KiB slots of block RAM. Only UDP command datagrams
// and ping requests need their bytes later; their slot stays occupied until
// eth_stack (UDP) or eth_tx (ICMP) releases it. ARP replies only need a few
// fields, which I keep in registers. When a frame ends I check, in order:
//   1. RX_ER from the PHY, a bad FCS, or a runt (< 64 bytes): discard, count
//      in rx_errors.
//   2. Longer than 1522 bytes: discard, count in rx_drops.
//   3. Not for our MAC and not broadcast: ignore.
//   4. ARP request for our IP: queue an ARP reply (drop if one is queued).
//   5. IPv4 to our IP with a good header: ping -> queue an echo reply;
//      UDP to our port with a payload -> commit the slot for the command
//      path. Either one needs a free slot (and only one ping may wait at a
//      time); otherwise the frame is dropped and counted.
//   6. Anything else (IPv6, other ports, multicast...): ignore.
// The Python model sw/mathaccel/net/board_model.py makes the same decisions.
module eth_rx
  import accel_pkg::*;
  import util_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    // events from rmii_rx (50 MHz domain, edge-aligned with clk)
    input  logic        ev_tgl,
    input  logic [1:0]  ev_kind,
    input  logic [7:0]  ev_data,
    // slot state: 0 free, 1 being written, 2 UDP datagram, 3 ping request
    output logic [1:0]  slot_kind [2],
    output logic [47:0] slot_mac  [2],   // Ethernet source of the request
    output logic [31:0] slot_ip   [2],   // IPv4 source of the request
    output logic [15:0] slot_port [2],   // UDP source port
    output logic [15:0] slot_len  [2],   // UDP payload bytes, or IPv4 total length for a ping
    output logic [15:0] slot_csum [2],   // checksum to put in the echo reply
    output logic        first_udp,       // the older of two waiting UDP slots
    input  logic        udp_free,        // pulse: release slot first_udp
    input  logic        icmp_free,       // pulse: release the ping slot
    // stored frames, two synchronous read ports: address {slot, byte}
    input  logic [11:0] rd_addr_a,
    output logic [7:0]  rd_data_a,
    input  logic [11:0] rd_addr_b,
    output logic [7:0]  rd_data_b,
    // ARP reply job
    output logic        arp_pending,
    output logic [47:0] arp_eth_dst,     // Ethernet source of the request
    output logic [47:0] arp_sha,         // sender MAC inside the ARP body
    output logic [31:0] arp_spa,         // sender IP inside the ARP body
    input  logic        arp_done,
    // statistics
    output logic [15:0] rx_errors,
    output logic [15:0] rx_drops,
    output logic        rx_frame         // pulse at the end of every frame
);
  localparam logic [1:0] EV_DATA = 2'd0, EV_END_OK = 2'd1, EV_END_ERR = 2'd2;
  localparam logic [1:0] K_FREE = 2'd0, K_WRITE = 2'd1, K_UDP = 2'd2, K_ICMP = 2'd3;
  localparam logic [31:0] CRC_RESIDUE = 32'hDEBB20E3;

  // ---------------------------------------------------------------------
  // Event input. ev_tgl changes once per event; comparing it with the
  // value seen last time gives a one-clock "new event" pulse.
  logic ev_seen;
  wire  ev_new = (ev_tgl != ev_seen);
  wire  is_data = ev_new && (ev_kind == EV_DATA);
  wire  is_end  = ev_new && (ev_kind != EV_DATA);
  wire [7:0] b  = ev_data;

  // ---------------------------------------------------------------------
  // Frame store: 2 slots x 2 KiB. Two read processes on one array make
  // Vivado build two block RAM copies, each with its own read address.
  logic [7:0] mem [4096];
  logic [11:0] n;             // bytes received so far in this frame
  logic        wslot;         // slot being written
  logic        wvalid;        // a slot was free when this frame started
  always_ff @(posedge clk) begin
    if (is_data && wvalid && n < 12'd2048) mem[{wslot, n[10:0]}] <= b;
    rd_data_a <= mem[rd_addr_a];
    rd_data_b <= mem[rd_addr_b];
  end

  // ---------------------------------------------------------------------
  // Fields gathered while the frame streams in.
  logic [31:0] crc;
  logic        dst_ours, dst_bcast;
  logic [47:0] src_mac;
  logic [15:0] etype;
  logic        arp_hdr_ok, arp_tpa_ok;
  logic [47:0] sha;
  logic [31:0] spa;
  logic        ip_vihl_ok, ip_frag_ok, ip_dst_ok;
  logic [15:0] ip_tlen;
  logic [16:0] l4_end;        // 14 + total length: first byte after the datagram
  logic [7:0]  ip_proto;
  logic [31:0] ip_src;
  logic [15:0] ip_sum;        // running ones'-complement sum of the IP header
  logic [7:0]  ip_hi;         // high byte waiting for its low partner
  logic [15:0] udp_sport, udp_dport, udp_len;
  logic [7:0]  icmp_type, icmp_code;
  logic [15:0] icmp_sum;      // sum of the reply message (type and checksum as 0)
  logic [7:0]  icmp_hi;

  // The 48-bit MAC and 32-bit IP constants, one byte per wire position.
  function automatic logic [7:0] mac_byte(input logic [11:0] i);
    return ETH_MAC[47 - 8*i[2:0] -: 8];
  endfunction
  function automatic logic [7:0] ip_byte(input logic [11:0] i);
    return ETH_IP[31 - 8*i[1:0] -: 8];
  endfunction
  // Bytes 14..21 of an ARP request: 0001 0800 06 04 0001.
  function automatic logic [7:0] arp_fixed(input logic [11:0] i);
    case (i)
      12'd15, 12'd21: return 8'h01;
      12'd16:         return 8'h08;
      12'd18:         return 8'h06;
      12'd19:         return 8'h04;
      default:        return 8'h00;
    endcase
  endfunction

  // Values used only at the end of the frame. The final ICMP sum adds the
  // last high byte when the message has an odd length.
  wire [15:0] icmp_final = ip_tlen[0] ? oc_add(icmp_sum, {icmp_hi, 8'h00}) : icmp_sum;
  wire frame_bad  = (ev_kind == EV_END_ERR) || (crc != CRC_RESIDUE) || (n < 12'd64);
  wire frame_long = (n > 12'd1522);
  wire for_us     = dst_ours || dst_bcast;
  wire is_arp     = (etype == 16'h0806) && arp_hdr_ok && arp_tpa_ok;
  wire ip_ok      = (etype == 16'h0800) && ip_vihl_ok && (ip_sum == 16'hFFFF) && ip_frag_ok &&
                    ip_dst_ok && (ip_tlen >= 16'd20) && (l4_end + 17'd4 <= {5'h0, n});
  wire is_icmp    = ip_ok && (ip_proto == 8'd1) && (ip_tlen >= 16'd28) &&
                    (icmp_type == 8'd8) && (icmp_code == 8'd0);
  wire is_udp     = ip_ok && (ip_proto == 8'd17) && (ip_tlen >= 16'd28) && (udp_dport == ETH_UDP_PORT) &&
                    (udp_len > 16'd8) && (udp_len <= ip_tlen - 16'd20);
  wire icmp_busy  = (slot_kind[0] == K_ICMP) || (slot_kind[1] == K_ICMP);
  wire icmp_slot  = (slot_kind[1] == K_ICMP);

  always_ff @(posedge clk) begin
    rx_frame <= 1'b0;
    if (rst) begin
      ev_seen     <= 1'b0;
      n           <= '0;
      wvalid      <= 1'b0;
      slot_kind[0] <= K_FREE;   // two assignments: Icarus cannot take '{..} on an array port
      slot_kind[1] <= K_FREE;
      first_udp   <= 1'b0;
      arp_pending <= 1'b0;
      rx_errors   <= '0;
      rx_drops    <= '0;
    end else begin
      ev_seen <= ev_tgl;
      if (arp_done) arp_pending <= 1'b0;

      // Release requests from the consumers. A released UDP slot hands the
      // "oldest" role to the other slot if that one also holds a datagram.
      if (udp_free) begin
        slot_kind[first_udp] <= K_FREE;
        if (slot_kind[~first_udp] == K_UDP) first_udp <= ~first_udp;
      end
      if (icmp_free) slot_kind[icmp_slot] <= K_FREE;

      if (is_data) begin
        // The first byte of a frame claims a free slot, if there is one.
        if (n == 0) begin
          wvalid <= (slot_kind[0] == K_FREE) || (slot_kind[1] == K_FREE);
          wslot  <= (slot_kind[0] != K_FREE);
          if (slot_kind[0] == K_FREE)      slot_kind[0] <= K_WRITE;
          else if (slot_kind[1] == K_FREE) slot_kind[1] <= K_WRITE;
          crc        <= crc32_update(32'hFFFFFFFF, b);
          dst_ours   <= (b == mac_byte(0));
          dst_bcast  <= (b == 8'hFF);
          arp_hdr_ok <= 1'b1;
          arp_tpa_ok <= 1'b1;
          ip_sum     <= '0;
          icmp_sum   <= '0;
          udp_dport  <= '0;
          icmp_type  <= '0;
          ip_tlen    <= '0;
          l4_end     <= '0;
        end else begin
          crc <= crc32_update(crc, b);
        end
        if (n != 12'hFFF) n <= n + 1'b1;

        // Ethernet header.
        if (n >= 1 && n <= 5) begin
          dst_ours  <= dst_ours && (b == mac_byte(n));
          dst_bcast <= dst_bcast && (b == 8'hFF);
        end
        if (n >= 6 && n <= 11) src_mac <= {src_mac[39:0], b};
        if (n == 12) etype[15:8] <= b;
        if (n == 13) etype[7:0]  <= b;

        // ARP: the fixed part (Ethernet/IPv4, sizes 6 and 4, operation 1 =
        // request), then the addresses.
        if (n >= 14 && n <= 21) arp_hdr_ok <= arp_hdr_ok && (b == arp_fixed(n));
        if (n >= 22 && n <= 27) sha <= {sha[39:0], b};
        if (n >= 28 && n <= 31) spa <= {spa[23:0], b};
        if (n >= 38 && n <= 41) arp_tpa_ok <= arp_tpa_ok && (b == ip_byte(n - 12'd38));

        // IPv4 header fields.
        if (n == 14) ip_vihl_ok <= (b == 8'h45);
        if (n == 16) ip_tlen[15:8] <= b;
        if (n == 17) begin
          ip_tlen[7:0] <= b;
          l4_end       <= 17'd14 + {1'b0, ip_tlen[15:8], b};
        end
        if (n == 20) ip_frag_ok <= (b[5:0] == 6'd0);      // MF = 0, offset high bits = 0
        if (n == 21) ip_frag_ok <= ip_frag_ok && (b == 8'h00);
        if (n == 23) ip_proto <= b;
        if (n >= 26 && n <= 29) ip_src <= {ip_src[23:0], b};
        if (n == 30) ip_dst_ok <= (b == ip_byte(0));
        if (n >= 31 && n <= 33) ip_dst_ok <= ip_dst_ok && (b == ip_byte(n - 12'd30));
        // Header checksum: pair bytes into big-endian 16-bit words.
        if (n >= 14 && n <= 33) begin
          if (!n[0]) ip_hi <= b;
          else       ip_sum <= oc_add(ip_sum, {ip_hi, b});
        end

        // UDP header.
        if (n == 34) udp_sport[15:8] <= b;
        if (n == 35) udp_sport[7:0]  <= b;
        if (n == 36) udp_dport[15:8] <= b;
        if (n == 37) udp_dport[7:0]  <= b;
        if (n == 38) udp_len[15:8]   <= b;
        if (n == 39) udp_len[7:0]    <= b;

        // ICMP: type and code, and the checksum of the reply I will send.
        // The reply is this message with type 0 and a fresh checksum, so I
        // add the bytes with the type and checksum fields counted as zero.
        if (n == 34) icmp_type <= b;
        if (n == 35) icmp_code <= b;
        if (n >= 34 && {5'h0, n} < l4_end) begin
          if (!n[0]) icmp_hi <= (n == 34 || n == 36) ? 8'h00 : b;
          else icmp_sum <= oc_add(icmp_sum, {icmp_hi, (n == 37) ? 8'h00 : b});
        end
      end

      if (is_end) begin
        rx_frame <= 1'b1;
        n        <= '0;
        // The written slot is released unless it is committed below.
        if (wvalid && n != 0) slot_kind[wslot] <= K_FREE;
        wvalid <= 1'b0;
        if (n != 0) begin
          if (frame_bad)                     rx_errors <= rx_errors + 1'b1;
          else if (frame_long)               rx_drops  <= rx_drops + 1'b1;
          else if (!for_us)                  ;
          else if (is_arp) begin
            if (arp_pending && !arp_done)    rx_drops  <= rx_drops + 1'b1;
            else begin
              arp_pending <= 1'b1;
              arp_eth_dst <= src_mac;
              arp_sha     <= sha;
              arp_spa     <= spa;
            end
          end else if (is_icmp) begin
            if (!wvalid || icmp_busy)        rx_drops  <= rx_drops + 1'b1;
            else begin
              slot_kind[wslot] <= K_ICMP;
              slot_mac[wslot]  <= src_mac;
              slot_ip[wslot]   <= ip_src;
              slot_len[wslot]  <= ip_tlen;
              slot_csum[wslot] <= ~icmp_final;
            end
          end else if (is_udp) begin
            if (!wvalid)                     rx_drops  <= rx_drops + 1'b1;
            else begin
              slot_kind[wslot] <= K_UDP;
              slot_mac[wslot]  <= src_mac;
              slot_ip[wslot]   <= ip_src;
              slot_port[wslot] <= udp_sport;
              slot_len[wslot]  <= udp_len - 16'd8;
              // This datagram is the oldest unless the other slot already
              // holds one that is still waiting.
              if (!(slot_kind[~wslot] == K_UDP && !(udp_free && first_udp == ~wslot)))
                first_udp <= wslot;
            end
          end
        end
      end
    end
  end
endmodule
