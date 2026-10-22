// Reference model and scoreboard for the Ethernet environment.
//
// eth_ref_model decides, for each frame the DUT received, which frames the
// DUT must send back: an ARP reply, an echo reply, or one UDP datagram per
// command reply. Commands go to top_ref_model, the same SV device model the
// UART environment uses. The scoreboard keeps one eth_ref_model and feeds it
// requests from both transports, so memory and the summed STATUS counters
// are shared exactly as in the hardware.

typedef byte_q byte_q_q[$];     // a queue of frames

// What the model decided about one received frame (for counts and coverage).
typedef enum {K_IGNORED, K_ARP, K_ICMP, K_UDP, K_ERROR, K_DROP} eth_kind_e;

class eth_ref_model extends uvm_object;
  `uvm_object_utils(eth_ref_model)
  localparam bit [47:0] BCAST = 48'hFFFF_FFFF_FFFF;

  top_ref_model dev;
  int unsigned  rx_errors, rx_drops;
  eth_kind_e    last_kind;       // what the last frame was, for coverage

  function new(string name = "eth_ref_model");
    super.new(name);
    dev = top_ref_model::type_id::create("dev");
  endfunction

  // The order of the checks follows eth_rx.sv and board_model.py.
  function byte_q_q handle(byte_q f, bit rx_er);
    byte_q_q   out;
    bit [47:0] dst, src;
    bit [15:0] etype;
    last_kind = K_IGNORED;
    if (rx_er || f.size() < 64 || !eth_codec::fcs_ok(f)) begin
      rx_errors++;
      last_kind = K_ERROR;
      return out;
    end
    if (f.size() > 1522) begin
      rx_drops++;
      last_kind = K_DROP;
      return out;
    end
    dst   = eth_codec::be48(f, 0);
    src   = eth_codec::be48(f, 6);
    etype = eth_codec::be16(f, 12);
    if (dst != accel_pkg::ETH_MAC && dst != BCAST) return out;

    if (etype == 16'h0806) begin
      if (eth_codec::be16(f, 14) == 1 && eth_codec::be16(f, 16) == 16'h0800 && f[18] == 6 && f[19] == 4 &&
          eth_codec::be16(f, 20) == 1 && eth_codec::be32(f, 38) == accel_pkg::ETH_IP) begin
        out.push_back(eth_codec::ethernet(src, accel_pkg::ETH_MAC, 16'h0806,
                      eth_codec::arp(2, accel_pkg::ETH_MAC, accel_pkg::ETH_IP,
                                     eth_codec::be48(f, 22), eth_codec::be32(f, 28))));
        last_kind = K_ARP;
      end
      return out;
    end

    if (etype == 16'h0800) begin
      byte_q     hdr = eth_codec::slice(f, 14, 34), body, payload;
      int        total = eth_codec::be16(f, 16);
      bit [31:0] src_ip = eth_codec::be32(f, 26);
      if (f[14] != 8'h45 || eth_codec::ones_sum(hdr) != 16'hFFFF || (eth_codec::be16(f, 20) & 16'h3FFF) != 0 ||
          total < 20 || 14 + total > f.size() - 4 || eth_codec::be32(f, 30) != accel_pkg::ETH_IP)
        return out;
      body = eth_codec::slice(f, 34, 14 + total);
      if (f[23] == 1 && body.size() >= 8 && body[0] == 8 && body[1] == 0) begin
        // Echo reply: same identifier, sequence and data, type 0, new checksum.
        byte_q     msg = '{0, 0, 0, 0};
        bit [15:0] c;
        eth_codec::append(msg, eth_codec::slice(body, 4, body.size()));
        c = eth_codec::checksum(msg);
        msg[2] = c[15:8];
        msg[3] = c[7:0];
        out.push_back(eth_codec::ethernet(src, accel_pkg::ETH_MAC, 16'h0800,
                      eth_codec::ipv4(accel_pkg::ETH_IP, src_ip, 1, msg)));
        last_kind = K_ICMP;
      end else if (f[23] == 17 && body.size() >= 8) begin
        bit [15:0] sport = eth_codec::be16(body, 0), dport = eth_codec::be16(body, 2);
        int        len   = eth_codec::be16(body, 4);
        byte_q     chunk;
        if (len < 8 || len > body.size() || dport != accel_pkg::ETH_UDP_PORT) return out;
        last_kind = K_UDP;
        payload = eth_codec::slice(body, 8, len);
        // Split at every 0x00; a final frame without its 0x00 still counts.
        payload.push_back(0);
        foreach (payload[i]) begin
          if (payload[i] != 0) chunk.push_back(payload[i]);
          else begin
            byte_q reply;
            if (chunk.size() != 0 && dev.predict(chunk, reply))
              out.push_back(eth_codec::ethernet(src, accel_pkg::ETH_MAC, 16'h0800,
                            eth_codec::ipv4(accel_pkg::ETH_IP, src_ip, 17,
                            eth_codec::udp(accel_pkg::ETH_IP, src_ip, accel_pkg::ETH_UDP_PORT, sport, reply))));
            chunk.delete();
          end
        end
      end
    end
    return out;
  endfunction
endclass

// ---------------------------------------------------------------------------
class eth_scoreboard extends uvm_scoreboard;
  `uvm_component_utils(eth_scoreboard)

  uvm_analysis_imp_erx #(eth_frame_item,  eth_scoreboard) erx_imp;
  uvm_analysis_imp_etx #(eth_frame_item,  eth_scoreboard) etx_imp;
  uvm_analysis_imp_urx #(wire_frame_item, eth_scoreboard) urx_imp;
  uvm_analysis_imp_utx #(wire_frame_item, eth_scoreboard) utx_imp;

  eth_ref_model model;
  byte_q        exp_eth[$], exp_uart[$];
  int unsigned  n_erx, n_etx, n_urx, n_utx, n_errors;
  int unsigned  n_kind[eth_kind_e];

  // What the receive monitor saw, and the PHY behaviour it was sent with.
  eth_kind_e cov_kind;
  int    cov_lead, cov_pre;
  bit    cov_toggle, cov_rx_er;
  covergroup cg_rx;
    cp_kind:   coverpoint cov_kind;      // one bin per eth_kind_e value
    cp_lead:   coverpoint cov_lead { bins none = {0}; bins some = {[1:$]}; }
    cp_pre:    coverpoint cov_pre { bins full = {28}; bins short = {[0:27]}; }
    cp_toggle: coverpoint cov_toggle;
    cp_rx_er:  coverpoint cov_rx_er;
    x_kind_toggle: cross cp_kind, cp_toggle { ignore_bins rare = binsof(cp_kind) intersect {K_DROP, K_ARP}; }
  endgroup

  function new(string name, uvm_component parent);
    super.new(name, parent);
    erx_imp = new("erx_imp", this);
    etx_imp = new("etx_imp", this);
    urx_imp = new("urx_imp", this);
    utx_imp = new("utx_imp", this);
    cg_rx   = new();
  endfunction

  function void build_phase(uvm_phase phase);
    model = eth_ref_model::type_id::create("model");
  endfunction

  function void write_erx(eth_frame_item f);
    byte_q_q replies = model.handle(f.data, f.rx_er);
    n_erx++;
    foreach (replies[i]) exp_eth.push_back(replies[i]);
    n_kind[model.last_kind]++;
    cov_kind   = model.last_kind;
    cov_lead   = f.lead_in;
    cov_pre    = f.pre_dibits;
    cov_toggle = f.toggle_nibbles != 0;
    cov_rx_er  = f.rx_er;
    cg_rx.sample();
  endfunction

  function void write_urx(wire_frame_item f);
    byte_q reply;
    n_urx++;
    if (model.dev.predict(f.bytes, reply)) exp_uart.push_back(reply);
  endfunction

  function void compare(ref byte_q q[$], input byte_q got, input string what);
    byte_q exp;
    if (q.size() == 0) begin
      n_errors++;
      `uvm_error("ETH_SB", $sformatf("unexpected %s %s", what, top_codec::hex(got, 80)))
      return;
    end
    exp = q.pop_front();
    if (got != exp) begin
      n_errors++;
      `uvm_error("ETH_SB", $sformatf("%s mismatch\n  expected %s\n  got      %s", what,
                                     top_codec::hex(exp, 120), top_codec::hex(got, 120)))
    end
  endfunction

  function void write_etx(eth_frame_item f);
    n_etx++;
    compare(exp_eth, f.data, "Ethernet frame");
  endfunction

  function void write_utx(wire_frame_item f);
    byte_q got = f.bytes;
    n_utx++;
    got.push_back(0);
    compare(exp_uart, got, "UART reply");
  endfunction

  function void check_phase(uvm_phase phase);
    if (exp_eth.size() != 0)
      `uvm_error("ETH_SB", $sformatf("%0d predicted Ethernet frames never arrived", exp_eth.size()))
    if (exp_uart.size() != 0)
      `uvm_error("ETH_SB", $sformatf("%0d predicted UART replies never arrived", exp_uart.size()))
  endfunction

  function void report_phase(uvm_phase phase);
    string kinds = "";
    foreach (n_kind[k]) kinds = {kinds, $sformatf(" %s=%0d", k.name(), n_kind[k])};
    `uvm_info("SB", $sformatf("Ethernet %0d in (%s ), %0d out; UART %0d in, %0d out; model rx_errors %0d rx_drops %0d; %0d mismatches; rx coverage %0.1f%%",
                              n_erx, kinds, n_etx, n_urx, n_utx, model.rx_errors, model.rx_drops, n_errors,
                              cg_rx.get_coverage()), UVM_NONE)
  endfunction
endclass
