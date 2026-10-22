// Sequences, the reply path for command sequences, the environment, and the
// virtual sequences for the Ethernet tests.

localparam bit [47:0] HOST_MAC  = 48'hD8BB_C1A0_B0C0;
localparam bit [31:0] HOST_IP   = 32'hC0A8_0A01;    // 192.168.10.1
localparam bit [15:0] HOST_PORT = 16'd51515;

// ---------------------------------------------------------------------------
// Layering sequence: one command item from the command sequencer becomes
// one UDP datagram holding its COBS/CRC wire frame, sent with random PHY
// details. The same corruptions as cmd_to_uart_seq are applied first.
class cmd_to_eth_seq extends uvm_sequence #(eth_frame_item);
  `uvm_object_utils(cmd_to_eth_seq)
  cmd_sequencer up;
  function new(string name = "cmd_to_eth_seq"); super.new(name); endfunction

  task body();
    accel_cmd_item cmd;
    byte_q raw, wire_bytes;
    forever begin
      eth_frame_item f;
      up.get_next_item(cmd);
      raw = top_codec::frame(cmd.op, cmd.tag, cmd.payload);
      case (cmd.corrupt)
        CORRUPT_CRC:   raw[raw.size() - 1] ^= 8'h5A;
        CORRUPT_SHORT: while (raw.size() > 3) void'(raw.pop_back());
        default: ;
      endcase
      wire_bytes = top_codec::cobs_encode(raw);
      wire_bytes.push_back(0);
      f = eth_frame_item::type_id::create("cmd");
      start_item(f);
      if (!f.randomize()) `uvm_fatal("RAND", "eth_frame_item randomize failed")
      f.data = eth_codec::udp_frame(wire_bytes, HOST_MAC, HOST_IP, HOST_PORT);
      finish_item(f);
      up.item_done();
    end
  endtask
endclass

// Reply datagrams from the DUT, unpacked into wire frames for the command
// sequencer's reply FIFO, so command sequences see exactly what they would
// see over the UART.
class udp_reply_extractor extends uvm_subscriber #(eth_frame_item);
  `uvm_component_utils(udp_reply_extractor)
  uvm_analysis_port #(wire_frame_item) ap;
  function new(string name, uvm_component parent);
    super.new(name, parent);
    ap = new("ap", this);
  endfunction

  function void write(eth_frame_item t);
    byte_q f = t.data, chunk;
    int    total;
    if (f.size() < 46 || eth_codec::be16(f, 12) != 16'h0800 || f[23] != 17) return;
    total = eth_codec::be16(f, 16);
    for (int i = 42; i < 14 + total; i++) begin
      if (f[i] != 0) chunk.push_back(f[i]);
      else if (chunk.size() != 0) begin
        wire_frame_item w = wire_frame_item::type_id::create("w");
        w.bytes = chunk;
        ap.write(w);
        chunk.delete();
      end
    end
  endfunction
endclass

// A list of complete frames for the RMII sequencer. Each is sent with
// random PHY details unless `fixed` says to keep the item's own knobs.
class raw_eth_seq extends uvm_sequence #(eth_frame_item);
  `uvm_object_utils(raw_eth_seq)
  byte_q_q frames;
  int      rx_er_at = -1;
  bit      fixed;
  function new(string name = "raw_eth_seq"); super.new(name); endfunction
  task body();
    foreach (frames[i]) begin
      eth_frame_item f = eth_frame_item::type_id::create("raw");
      start_item(f);
      if (!fixed && !f.randomize()) `uvm_fatal("RAND", "eth_frame_item randomize failed")
      f.data     = frames[i];
      f.rx_er_at = rx_er_at;
      finish_item(f);
    end
  endtask
endclass

// One command with an explicit payload, for the mixed-transport checks.
class cmd_one_seq extends cmd_base_seq;
  `uvm_object_utils(cmd_one_seq)
  byte unsigned op;
  byte_q        payload;
  function new(string name = "cmd_one_seq"); super.new(name); endfunction
  task body();
    byte_q r;
    send(op, payload, r);
  endtask
endclass

// ---------------------------------------------------------------------------
class eth_vsequencer extends uvm_sequencer;
  `uvm_component_utils(eth_vsequencer)
  cmd_sequencer  eth_cmd, uart_cmd;
  rmii_sequencer rmii;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass

class eth_env extends uvm_env;
  `uvm_component_utils(eth_env)
  rmii_agent          rmii;
  uart_agent          uart;
  cmd_sequencer       eth_cmd, uart_cmd;
  udp_reply_extractor replies;
  eth_scoreboard      sb;
  eth_vsequencer      vsqr;

  function new(string name, uvm_component parent); super.new(name, parent); endfunction

  function void build_phase(uvm_phase phase);
    rmii     = rmii_agent::type_id::create("rmii", this);
    uart     = uart_agent::type_id::create("uart", this);
    eth_cmd  = cmd_sequencer::type_id::create("eth_cmd", this);
    uart_cmd = cmd_sequencer::type_id::create("uart_cmd", this);
    replies  = udp_reply_extractor::type_id::create("replies", this);
    sb       = eth_scoreboard::type_id::create("sb", this);
    vsqr     = eth_vsequencer::type_id::create("vsqr", this);
  endfunction

  function void connect_phase(uvm_phase phase);
    rmii.rx_mon.ap.connect(sb.erx_imp);
    rmii.tx_mon.ap.connect(sb.etx_imp);
    rmii.tx_mon.ap.connect(replies.analysis_export);
    replies.ap.connect(eth_cmd.rsp_fifo.analysis_export);
    uart.rx_mon.ap.connect(sb.urx_imp);
    uart.tx_mon.ap.connect(sb.utx_imp);
    uart.tx_mon.ap.connect(uart_cmd.rsp_fifo.analysis_export);
    vsqr.eth_cmd  = eth_cmd;
    vsqr.uart_cmd = uart_cmd;
    vsqr.rmii     = rmii.sqr;
  endfunction

  // Both layering sequences run for the whole test.
  task run_phase(uvm_phase phase);
    cmd_to_eth_seq  el = cmd_to_eth_seq::type_id::create("eth_layer");
    cmd_to_uart_seq ul = cmd_to_uart_seq::type_id::create("uart_layer");
    el.up = eth_cmd;
    ul.up = uart_cmd;
    fork
      el.start(rmii.sqr);
      ul.start(uart.sqr);
    join
  endtask
endclass

// ---------------------------------------------------------------------------
class eth_base_vseq extends uvm_sequence;
  `uvm_object_utils(eth_base_vseq)
  `uvm_declare_p_sequencer(eth_vsequencer)
  function new(string name = "eth_base_vseq"); super.new(name); endfunction

  task wait_clks(int n);
    repeat (n) @(posedge p_sequencer.eth_cmd.vif.clk);
  endtask

  task send_raw(byte_q_q frames, int rx_er_at = -1);
    raw_eth_seq s = raw_eth_seq::type_id::create("raw");
    s.frames   = frames;
    s.rx_er_at = rx_er_at;
    s.start(p_sequencer.rmii);
  endtask

  // Two frames back to back with the minimum gap, sent without waiting.
  task send_back_to_back(byte_q a, byte_q b);
    raw_eth_seq s = raw_eth_seq::type_id::create("b2b");
    s.frames.push_back(a);
    s.frames.push_back(b);
    s.fixed = 1;
    s.start(p_sequencer.rmii);
  endtask

  task one(cmd_sequencer sqr, byte unsigned op, byte_q payload);
    cmd_one_seq s = cmd_one_seq::type_id::create("one");
    s.op      = op;
    s.payload = payload;
    s.start(sqr);
  endtask

  static function byte_q cmd_wire(byte unsigned op, byte unsigned tag, byte_q payload, bit delimiter = 1);
    byte_q w = top_codec::cobs_encode(top_codec::frame(op, tag, payload));
    if (delimiter) w.push_back(0);
    return w;
  endfunction
endclass

class eth_smoke_vseq extends eth_base_vseq;
  `uvm_object_utils(eth_smoke_vseq)
  function new(string name = "eth_smoke_vseq"); super.new(name); endfunction
  task body();
    byte_q_q f;
    byte_q   data;
    cmd_smoke_seq smoke = cmd_smoke_seq::type_id::create("smoke");
    for (int i = 0; i < 32; i++) data.push_back(i);
    f.push_back(eth_codec::arp_request(HOST_MAC, HOST_IP));
    f.push_back(eth_codec::ping(HOST_MAC, HOST_IP, 16'h0001, 16'h0001, data));
    send_raw(f);
    wait_clks(20_000);
    smoke.start(p_sequencer.eth_cmd);
  endtask
endclass

class eth_regress_vseq extends eth_base_vseq;
  `uvm_object_utils(eth_regress_vseq)
  function new(string name = "eth_regress_vseq"); super.new(name); endfunction

  // Traffic a Windows laptop sends on a new link, plus broken frames.
  function byte_q_q junk();
    byte_q_q j;
    byte_q   none, w, z40, z50, z56, z1520, good;
    w = cmd_wire(accel_pkg::OP_PING, 8'hEE, none);
    repeat (40) z40.push_back(0);
    repeat (50) z50.push_back(0);
    repeat (56) z56.push_back(0);
    repeat (1520) z1520.push_back(0);
    good = eth_codec::udp_frame(w, HOST_MAC, HOST_IP, HOST_PORT);
    // mDNS to a multicast MAC, an IPv6 frame, broadcast NetBIOS.
    j.push_back(eth_codec::ethernet(48'h0100_5E00_00FB, HOST_MAC, 16'h0800,
                eth_codec::ipv4(HOST_IP, 32'hE000_00FB, 17, eth_codec::udp(HOST_IP, 32'hE000_00FB, 5353, 5353, z40))));
    j.push_back(eth_codec::ethernet(48'h3333_0000_0002, HOST_MAC, 16'h86DD, z56));
    j.push_back(eth_codec::ethernet(48'hFFFF_FFFF_FFFF, HOST_MAC, 16'h0800,
                eth_codec::ipv4(HOST_IP, 32'hC0A8_0AFF, 17, eth_codec::udp(HOST_IP, 32'hC0A8_0AFF, 137, 137, z50))));
    // Our IP on the wrong port; our port on the wrong IP.
    j.push_back(eth_codec::udp_frame(w, HOST_MAC, HOST_IP, HOST_PORT, accel_pkg::ETH_MAC, accel_pkg::ETH_IP,
                                     accel_pkg::ETH_UDP_PORT + 1));
    j.push_back(eth_codec::udp_frame(w, HOST_MAC, HOST_IP, HOST_PORT, accel_pkg::ETH_MAC, 32'hC0A8_0A03));
    // ARP for another address, and a gratuitous ARP reply.
    j.push_back(eth_codec::arp_request(HOST_MAC, HOST_IP, 32'hC0A8_0A4D));
    j.push_back(eth_codec::ethernet(48'hFFFF_FFFF_FFFF, HOST_MAC, 16'h0806, eth_codec::arp(2, HOST_MAC, HOST_IP,
                                                                                         HOST_MAC, HOST_IP)));
    // IPv4 options (IHL 6) and a fragment: ignored.
    j.push_back(eth_codec::ethernet(accel_pkg::ETH_MAC, HOST_MAC, 16'h0800, eth_codec::ipv4(HOST_IP, accel_pkg::ETH_IP,
                17, eth_codec::udp(HOST_IP, accel_pkg::ETH_IP, HOST_PORT, accel_pkg::ETH_UDP_PORT, w), 16'h4000, 8'h46)));
    j.push_back(eth_codec::ethernet(accel_pkg::ETH_MAC, HOST_MAC, 16'h0800, eth_codec::ipv4(HOST_IP, accel_pkg::ETH_IP,
                17, eth_codec::udp(HOST_IP, accel_pkg::ETH_IP, HOST_PORT, accel_pkg::ETH_UDP_PORT, w), 16'h2000)));
    // Bad FCS and a runt count as receive errors; an oversized frame is a drop.
    begin
      byte_q bad = good, runt = eth_codec::slice(good, 0, 30);
      bad[bad.size() - 1] ^= 8'h01;
      j.push_back(bad);
      eth_codec::append(runt, eth_codec::fcs(runt));
      j.push_back(runt);
    end
    j.push_back(eth_codec::ethernet(accel_pkg::ETH_MAC, HOST_MAC, 16'h0800, z1520));
    return j;
  endfunction

  task body();
    byte_q_q      f;
    byte_q        none, p, data, two;
    cmd_smoke_seq smoke = cmd_smoke_seq::type_id::create("smoke");
    cmd_mem_seq   mem   = cmd_mem_seq::type_id::create("mem");
    cmd_fpu_seq   fpu   = cmd_fpu_seq::type_id::create("fpu");
    cmd_error_seq err   = cmd_error_seq::type_id::create("err");
    cmd_mem_seq   umem  = cmd_mem_seq::type_id::create("umem");
    cmd_error_seq uerr  = cmd_error_seq::type_id::create("uerr");
    int           sizes[] = '{0, 1, 32, 333, 1472};

    f.push_back(eth_codec::arp_request(HOST_MAC, HOST_IP));
    send_raw(f);
    foreach (sizes[k]) begin
      data.delete();
      repeat (sizes[k]) data.push_back($urandom);
      f.delete();
      f.push_back(eth_codec::ping(HOST_MAC, HOST_IP, 16'h1234, sizes[k], data));
      send_raw(f);
      wait_clks(5_000 + 20 * sizes[k]);
    end

    smoke.start(p_sequencer.eth_cmd);
    mem.start(p_sequencer.eth_cmd);
    fpu.start(p_sequencer.eth_cmd);
    err.start(p_sequencer.eth_cmd);

    send_raw(junk());
    // RX_ER halfway through an otherwise good command frame.
    f.delete();
    f.push_back(eth_codec::udp_frame(cmd_wire(accel_pkg::OP_PING, 8'hEF, none), HOST_MAC, HOST_IP, HOST_PORT));
    send_raw(f, 200);
    wait_clks(20_000);

    // Two frames in one datagram, the second without its 0x00.
    two = cmd_wire(accel_pkg::OP_PING, 8'hA1, none);
    p = '{accel_pkg::K_NOP};
    eth_codec::append(two, cmd_wire(accel_pkg::OP_RUN, 8'hA2, p, 0));
    f.delete();
    f.push_back(eth_codec::udp_frame(two, HOST_MAC, HOST_IP, HOST_PORT));
    send_raw(f);
    wait_clks(20_000);
    // Two datagrams with the minimum gap: both receive slots fill, both answer.
    send_back_to_back(eth_codec::udp_frame(cmd_wire(accel_pkg::OP_PING, 8'hB1, none), HOST_MAC, HOST_IP, 40001),
                      eth_codec::udp_frame(cmd_wire(accel_pkg::OP_STATUS, 8'hB2, none), HOST_MAC, HOST_IP, 40002));
    wait_clks(20_000);

    // Mixed transports: memory written on one is read on the other.
    umem.start(p_sequencer.uart_cmd);
    p.delete(); cmd_base_seq::put32(p, 123); cmd_base_seq::put32(p, 32'hCAFE_F00D); cmd_base_seq::put32(p, 32'h1234_5678);
    one(p_sequencer.eth_cmd, accel_pkg::OP_WRITE_MEM, p);
    p.delete(); cmd_base_seq::put32(p, 123); cmd_base_seq::put16(p, 2);
    one(p_sequencer.uart_cmd, accel_pkg::OP_READ_MEM, p);
    p.delete(); cmd_base_seq::put32(p, 500); cmd_base_seq::put32(p, 32'h0BAD_BEEF);
    one(p_sequencer.uart_cmd, accel_pkg::OP_WRITE_MEM, p);
    p.delete(); cmd_base_seq::put32(p, 500); cmd_base_seq::put16(p, 1);
    one(p_sequencer.eth_cmd, accel_pkg::OP_READ_MEM, p);
    // STATUS over UDP now includes the UART's CRC and framing errors too.
    uerr.start(p_sequencer.uart_cmd);
    one(p_sequencer.eth_cmd, accel_pkg::OP_STATUS, none);
    smoke.start(p_sequencer.eth_cmd);
  endtask
endclass
