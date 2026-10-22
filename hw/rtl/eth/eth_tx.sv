// This is the transmit half of the Ethernet block (100 MHz domain). It builds
// three kinds of frames, one at a time, with this priority:
//   ARP reply   "192.168.10.2 is at 02:4D:41:43:43:01", fields from eth_rx
//   ICMP echo   ping reply; the data bytes are read back out of the stored
//               request in eth_rx's frame memory
//   UDP reply   one reply command frame (COBS bytes ending in 0x00) that the
//               core's encoder wrote into my 2 KiB reply buffer
//
// Every frame goes out as: 7 x 0x55 preamble, 0xD5 delimiter, the frame bytes,
// zero padding up to 60 bytes, and the four FCS bytes. I fill the header into
// a 42-byte register array first (the PREP state, which also adds up the IPv4
// and UDP checksums over about 20 clocks), then hand bytes to rmii_tx one by
// one. byte_at(k) below picks frame byte k from the header, the payload
// memory, padding, or the FCS.
//
// Header values I choose for my own IPv4 packets: version 4 with a 20-byte
// header (45), TOS 0, identification 0 with Don't Fragment set (4000), TTL 64.
// The UDP checksum covers a pseudo-header (both IP addresses, protocol 17 and
// the UDP length), the UDP header and the payload; a result of 0 is sent as
// FFFF because 0 would mean "no checksum". The same rules are in
// sw/mathaccel/net/frames.py.
module eth_tx
  import accel_pkg::*;
  import util_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    // ARP job from eth_rx
    input  logic        arp_pending,
    input  logic [47:0] arp_eth_dst,
    input  logic [47:0] arp_sha,
    input  logic [31:0] arp_spa,
    output logic        arp_done,
    // ICMP job from eth_rx: the stored request is in slot icmp_slot
    input  logic        icmp_pending,
    input  logic        icmp_slot,
    input  logic [47:0] icmp_mac,
    input  logic [31:0] icmp_ip,
    input  logic [15:0] icmp_tlen,
    input  logic [15:0] icmp_csum,
    output logic        icmp_done,
    output logic [11:0] rx_rd_addr,      // eth_rx frame memory, port b
    input  logic [7:0]  rx_rd_data,
    // UDP reply bytes from the core's COBS encoder, and where they go
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_data,
    input  logic [47:0] udp_mac,
    input  logic [31:0] udp_ip,
    input  logic [15:0] udp_port,
    output logic        udp_busy,        // reply buffer not empty yet
    // byte hand-off to rmii_tx (50 MHz domain, edge-aligned with clk)
    output logic        tx_tgl,
    output logic [7:0]  tx_data,
    output logic        tx_last,
    input  logic        tx_ack,
    output logic        tx_frame,        // pulse when a frame has been handed over
    output logic        tx_active        // a frame is being prepared or sent (debug LED)
);
  // ---------------------------------------------------------------------
  // Reply buffer. Bytes arrive one per handshake until the 0x00 delimiter.
  // The payload checksum is summed on the way in: even positions are the
  // high byte of a 16-bit word, odd positions the low byte.
  logic [7:0]  rbuf [2048];
  logic [10:0] wptr;
  logic        full;              // a complete datagram is waiting or being sent
  logic [15:0] psum;              // ones'-complement sum of the payload words
  logic [7:0]  phi;
  logic [10:0] plen;              // payload length including the delimiter
  logic [10:0] rbuf_addr;
  logic [7:0]  rbuf_q;

  assign in_ready = !full;
  assign udp_busy = full || (wptr != 0);

  logic udp_sent;                 // pulse from the sender below
  always_ff @(posedge clk) begin
    if (in_valid && in_ready) rbuf[wptr] <= in_data;
    rbuf_q <= rbuf[rbuf_addr];
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      wptr <= '0;
      full <= 1'b0;
      psum <= '0;
    end else if (udp_sent) begin
      wptr <= '0;
      full <= 1'b0;
      psum <= '0;
    end else if (in_valid && in_ready) begin
      if (!wptr[0]) phi <= in_data;
      // An odd-position byte completes a word. The delimiter is 0, so when it
      // lands on an even position it adds nothing and I can stop right there.
      if (wptr[0]) psum <= oc_add(psum, {phi, in_data});
      // A reply always ends with 0x00. The size check is only a guard: real
      // replies are at most about 1040 bytes.
      if (in_data == 8'h00 || wptr == 11'd1471) begin
        full <= 1'b1;
        plen <= wptr + 1'b1;
        if (!wptr[0] && in_data != 8'h00) psum <= oc_add(psum, {in_data, 8'h00});
      end
      wptr <= wptr + 1'b1;
    end
  end

  // ---------------------------------------------------------------------
  // Frame sender.
  typedef enum logic [2:0] {IDLE, PREP, PRE, DATA, WAIT, COOL} state_t;
  typedef enum logic [1:0] {J_ARP, J_ICMP, J_UDP} job_t;
  state_t      state;
  job_t        job;
  logic [7:0]  hdr [42];          // Ethernet + IPv4 + UDP/ICMP header bytes
  logic [5:0]  hdr_len;           // header bytes taken from hdr[]
  logic [11:0] pay_end;           // first byte after the payload (14 + IP total length)
  logic [11:0] data_end;          // first FCS byte (pay_end, or 60 for short frames)
  logic [11:0] k;                 // frame byte index (0 = first destination MAC byte)
  logic [3:0]  pre;               // preamble byte index 0..7
  logic [4:0]  step;              // checksum step in PREP
  logic [15:0] ip_sum, udp_sum;
  logic [31:0] crc;
  logic [15:0] ulen;
  logic [31:0] dst_ip;
  logic [15:0] dport;
  logic        settle;            // memory read for byte k has had its clock

  // Byte k of the frame as it should go on the wire.
  function automatic logic [7:0] byte_at(input logic [11:0] i);
    if (i < 12'(hdr_len))  return hdr[i[5:0]];
    if (i < pay_end)       return (job == J_UDP) ? rbuf_q : rx_rd_data;
    if (i < data_end)      return 8'h00;
    // FCS: the inverted CRC register, low byte first.
    case (i - data_end)
      12'd0:   return ~crc[7:0];
      12'd1:   return ~crc[15:8];
      12'd2:   return ~crc[23:16];
      default: return ~crc[31:24];
    endcase
  endfunction

  // Memory addresses follow k so the byte is ready one clock later. The
  // stored ping request has the same layout as the reply, so the data bytes
  // sit at the same offsets.
  assign rx_rd_addr = {icmp_slot, k[10:0]};
  assign rbuf_addr  = 11'(k - 12'd42);

  wire consumed = (tx_ack == tx_tgl);
  assign tx_active = (state != IDLE);

  // The next IPv4 job's addresses and lengths. A waiting ping goes first.
  wire [47:0] j_mac  = icmp_pending ? icmp_mac : udp_mac;
  wire [31:0] j_ip   = icmp_pending ? icmp_ip  : udp_ip;
  wire [15:0] j_ulen = 16'd8 + 16'(plen);
  wire [15:0] j_tlen = icmp_pending ? icmp_tlen : 16'd28 + 16'(plen);

  // One IPv4 header word or one UDP checksum term per PREP step.
  function automatic logic [15:0] ip_word(input logic [4:0] s);
    return {hdr[14 + 2*s], hdr[15 + 2*s]};
  endfunction

  always_ff @(posedge clk) begin
    arp_done  <= 1'b0;
    icmp_done <= 1'b0;
    udp_sent  <= 1'b0;
    tx_frame  <= 1'b0;
    if (rst) begin
      state  <= IDLE;
      tx_tgl <= 1'b0;
      tx_last <= 1'b0;
    end else begin
      case (state)
        IDLE: begin
          // Common header parts: source MAC, IPv4 header template.
          for (int i = 0; i < 6; i++) hdr[6 + i] <= ETH_MAC[47 - 8*i -: 8];
          step    <= '0;
          ip_sum  <= '0;
          crc     <= 32'hFFFFFFFF;
          k       <= '0;
          pre     <= '0;
          if (arp_pending) begin
            job <= J_ARP;
            for (int i = 0; i < 6; i++) hdr[i] <= arp_eth_dst[47 - 8*i -: 8];
            {hdr[12], hdr[13]} <= 16'h0806;
            {hdr[14], hdr[15], hdr[16], hdr[17]} <= 32'h0001_0800;
            {hdr[18], hdr[19], hdr[20], hdr[21]} <= 32'h0604_0002;
            for (int i = 0; i < 6; i++) hdr[22 + i] <= ETH_MAC[47 - 8*i -: 8];
            for (int i = 0; i < 4; i++) hdr[28 + i] <= ETH_IP[31 - 8*i -: 8];
            for (int i = 0; i < 6; i++) hdr[32 + i] <= arp_sha[47 - 8*i -: 8];
            for (int i = 0; i < 4; i++) hdr[38 + i] <= arp_spa[31 - 8*i -: 8];
            hdr_len  <= 6'd42;
            pay_end  <= 12'd42;
            data_end <= 12'd60;
            state    <= PRE;          // ARP has no checksum to prepare
          end else if (icmp_pending || full) begin
            // IPv4 frames: the parts that differ between ICMP and UDP.
            if (icmp_pending) begin
              job <= J_ICMP;
              hdr[23] <= 8'd1;
              {hdr[34], hdr[35], hdr[36], hdr[37]} <= {16'h0000, icmp_csum};
              hdr_len <= 6'd38;
            end else begin
              job     <= J_UDP;
              hdr[23] <= 8'd17;
              {hdr[34], hdr[35]} <= ETH_UDP_PORT;
              {hdr[36], hdr[37]} <= udp_port;
              {hdr[38], hdr[39]} <= j_ulen;
              {hdr[40], hdr[41]} <= 16'h0000;   // filled in after PREP
              hdr_len <= 6'd42;
            end
            dst_ip <= j_ip;
            dport  <= udp_port;
            ulen   <= j_ulen;
            for (int i = 0; i < 6; i++) hdr[i] <= j_mac[47 - 8*i -: 8];
            {hdr[12], hdr[13]} <= 16'h0800;
            {hdr[14], hdr[15]} <= 16'h4500;
            {hdr[16], hdr[17]} <= j_tlen;
            {hdr[18], hdr[19], hdr[20], hdr[21]} <= 32'h0000_4000;
            hdr[22] <= 8'd64;
            {hdr[24], hdr[25]} <= 16'h0000;
            for (int i = 0; i < 4; i++) hdr[26 + i] <= ETH_IP[31 - 8*i -: 8];
            for (int i = 0; i < 4; i++) hdr[30 + i] <= j_ip[31 - 8*i -: 8];
            pay_end  <= 12'd14 + 12'(j_tlen);
            data_end <= (12'd14 + 12'(j_tlen) < 12'd60) ? 12'd60 : 12'd14 + 12'(j_tlen);
            state    <= PREP;
          end
        end

        // Add the ten IPv4 header words (checksum field still 0), and for UDP
        // the pseudo-header, the UDP header and the payload sum.
        PREP: begin
          step <= step + 1'b1;
          if (step < 10) ip_sum <= oc_add(ip_sum, ip_word(step));
          case (step)
            5'd0:  udp_sum <= oc_add(psum, ETH_IP[31:16]);
            5'd1:  udp_sum <= oc_add(udp_sum, ETH_IP[15:0]);
            5'd2:  udp_sum <= oc_add(udp_sum, dst_ip[31:16]);
            5'd3:  udp_sum <= oc_add(udp_sum, dst_ip[15:0]);
            5'd4:  udp_sum <= oc_add(udp_sum, 16'd17);
            5'd5:  udp_sum <= oc_add(udp_sum, ulen);       // pseudo-header length
            5'd6:  udp_sum <= oc_add(udp_sum, ETH_UDP_PORT);
            5'd7:  udp_sum <= oc_add(udp_sum, dport);
            5'd8:  udp_sum <= oc_add(udp_sum, ulen);       // UDP header length
            default: ;
          endcase
          if (step == 5'd10) begin
            {hdr[24], hdr[25]} <= ~ip_sum;
            if (job == J_UDP) {hdr[40], hdr[41]} <= (~udp_sum == 16'h0000) ? 16'hFFFF : ~udp_sum;
            state <= PRE;
          end
        end

        // Seven 0x55 bytes and 0xD5. These are not part of the CRC.
        PRE: if (consumed) begin
          tx_data <= (pre == 4'd7) ? 8'hD5 : 8'h55;
          tx_last <= 1'b0;
          tx_tgl  <= ~tx_tgl;
          pre     <= pre + 1'b1;
          settle  <= 1'b1;            // k = 0 was set in IDLE long ago
          if (pre == 4'd7) state <= DATA;
        end

        // Frame bytes. After each hand-off I advance k and wait one clock so
        // the block RAM output belongs to the new k before I use it.
        DATA: begin
          settle <= 1'b1;
          if (consumed && settle) begin
            tx_data <= byte_at(k);
            tx_last <= (k == data_end + 12'd3);
            tx_tgl  <= ~tx_tgl;
            if (k < data_end) crc <= crc32_update(crc, byte_at(k));
            k       <= k + 1'b1;
            settle  <= 1'b0;
            if (k == data_end + 12'd3) state <= WAIT;
          end
        end

        // Wait until rmii_tx has taken the last byte, then release the job.
        WAIT: if (consumed) begin
          tx_frame <= 1'b1;
          case (job)
            J_ARP:  arp_done  <= 1'b1;
            J_ICMP: icmp_done <= 1'b1;
            default: udp_sent <= 1'b1;
          endcase
          state <= COOL;
        end

        // The done pulse needs one clock to clear the job's pending flag
        // (in eth_rx, or full above). Waiting here keeps IDLE from seeing the
        // old flag and sending the same frame twice.
        COOL: if (!arp_done && !icmp_done && !udp_sent) state <= IDLE;

        default: state <= IDLE;
      endcase
    end
  end
endmodule
