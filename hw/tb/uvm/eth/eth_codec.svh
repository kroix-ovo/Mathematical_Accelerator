// Ethernet, ARP, IPv4, ICMP and UDP in SystemVerilog, for the testbench.
//
// This is the third independent copy of these rules: the RTL streams one byte
// per clock (hw/rtl/eth), Python works on bytes objects (sw/mathaccel/net),
// and this class works on byte queues with plain loops. The SV environment
// predicts every reply byte with it, so a misreading of a standard that
// happened to be shared by the RTL and Python would still show up here.
//
// Header values the board uses for its own packets, from eth_tx.sv: IPv4
// version 4 with a 20-byte header, TOS 0, ID 0, Don't Fragment, TTL 64.

class eth_codec;
  // Addresses as byte queues in wire order.
  static function byte_q mac_q(bit [47:0] m);
    byte_q q;
    for (int i = 5; i >= 0; i--) q.push_back(m[8*i +: 8]);
    return q;
  endfunction
  static function byte_q ip_q(bit [31:0] a);
    byte_q q;
    for (int i = 3; i >= 0; i--) q.push_back(a[8*i +: 8]);
    return q;
  endfunction

  static function void put16be(inout byte_q q, input bit [15:0] v);
    q.push_back(v[15:8]);
    q.push_back(v[7:0]);
  endfunction
  static function void append(inout byte_q q, input byte_q tail);
    foreach (tail[i]) q.push_back(tail[i]);
  endfunction
  static function byte_q slice(byte_q q, int from, int to_excl);
    byte_q r;
    for (int i = from; i < to_excl && i < q.size(); i++) r.push_back(q[i]);
    return r;
  endfunction
  static function bit [15:0] be16(byte_q q, int off);
    return {q[off], q[off + 1]};
  endfunction
  static function bit [31:0] be32(byte_q q, int off);
    return {q[off], q[off + 1], q[off + 2], q[off + 3]};
  endfunction
  static function bit [47:0] be48(byte_q q, int off);
    return {q[off], q[off + 1], q[off + 2], q[off + 3], q[off + 4], q[off + 5]};
  endfunction

  // CRC-32 register, reflected (shifts right, polynomial EDB88320), start FFFFFFFF.
  static function bit [31:0] crc32_raw(byte_q data);
    bit [31:0] c = 32'hFFFF_FFFF;
    foreach (data[i]) begin
      c ^= data[i];
      repeat (8) c = c[0] ? ((c >> 1) ^ 32'hEDB8_8320) : (c >> 1);
    end
    return c;
  endfunction

  // FCS bytes: inverted register, least significant byte first.
  static function byte_q fcs(byte_q frame);
    bit [31:0] c = ~crc32_raw(frame);
    byte_q q;
    for (int i = 0; i < 4; i++) q.push_back(c[8*i +: 8]);
    return q;
  endfunction
  static function bit fcs_ok(byte_q f);
    return f.size() >= 4 && crc32_raw(f) == 32'hDEBB_20E3;
  endfunction

  // Ones'-complement sum of big-endian 16-bit words (RFC 1071), not inverted.
  static function bit [15:0] ones_sum(byte_q data, bit [31:0] start = 0);
    bit [31:0] s = start;
    for (int i = 0; i < data.size(); i += 2)
      s += {data[i], (i + 1 < data.size()) ? data[i + 1] : 8'h00};
    while (s >> 16) s = (s & 32'hFFFF) + (s >> 16);
    return s[15:0];
  endfunction
  static function bit [15:0] checksum(byte_q data, bit [31:0] start = 0);
    return ~ones_sum(data, start);
  endfunction

  // Ethernet II frame, padded to 60 bytes, with FCS.
  static function byte_q ethernet(bit [47:0] dst, bit [47:0] src, bit [15:0] etype, byte_q payload);
    byte_q f = mac_q(dst);
    append(f, mac_q(src));
    put16be(f, etype);
    append(f, payload);
    while (f.size() < 60) f.push_back(8'h00);
    append(f, fcs(f));
    return f;
  endfunction

  static function byte_q arp(bit [15:0] oper, bit [47:0] sha, bit [31:0] spa, bit [47:0] tha, bit [31:0] tpa);
    byte_q q;
    put16be(q, 16'h0001); put16be(q, 16'h0800);
    q.push_back(6); q.push_back(4);
    put16be(q, oper);
    append(q, mac_q(sha)); append(q, ip_q(spa));
    append(q, mac_q(tha)); append(q, ip_q(tpa));
    return q;
  endfunction

  static function byte_q ipv4(bit [31:0] src, bit [31:0] dst, byte unsigned proto, byte_q payload,
                              bit [15:0] flags_frag = 16'h4000, byte unsigned vihl = 8'h45);
    byte_q h;
    bit [15:0] c;
    h.push_back(vihl); h.push_back(0);
    put16be(h, 20 + payload.size());
    put16be(h, 16'h0000);
    put16be(h, flags_frag);
    h.push_back(64); h.push_back(proto);
    put16be(h, 16'h0000);
    append(h, ip_q(src)); append(h, ip_q(dst));
    c = checksum(h);
    h[10] = c[15:8];
    h[11] = c[7:0];
    append(h, payload);
    return h;
  endfunction

  static function byte_q udp(bit [31:0] src_ip, bit [31:0] dst_ip, bit [15:0] sport, bit [15:0] dport,
                             byte_q payload);
    byte_q d, pseudo;
    bit [15:0] len = 8 + payload.size(), c;
    put16be(d, sport); put16be(d, dport); put16be(d, len); put16be(d, 16'h0000);
    append(d, payload);
    append(pseudo, ip_q(src_ip)); append(pseudo, ip_q(dst_ip));
    pseudo.push_back(0); pseudo.push_back(17);
    put16be(pseudo, len);
    append(pseudo, d);
    c = checksum(pseudo);
    if (c == 0) c = 16'hFFFF;
    d[6] = c[15:8];
    d[7] = c[7:0];
    return d;
  endfunction

  static function byte_q icmp_echo(byte unsigned typ, bit [15:0] id, bit [15:0] seq, byte_q data);
    byte_q m;
    bit [15:0] c;
    m.push_back(typ); m.push_back(0);
    put16be(m, 16'h0000);
    put16be(m, id); put16be(m, seq);
    append(m, data);
    c = checksum(m);
    m[2] = c[15:8];
    m[3] = c[7:0];
    return m;
  endfunction

  // Host -> board frames.
  static function byte_q udp_frame(byte_q payload, bit [47:0] src_mac, bit [31:0] src_ip, bit [15:0] sport,
                                   bit [47:0] dst_mac = accel_pkg::ETH_MAC, bit [31:0] dst_ip = accel_pkg::ETH_IP,
                                   bit [15:0] dport = accel_pkg::ETH_UDP_PORT);
    return ethernet(dst_mac, src_mac, 16'h0800, ipv4(src_ip, dst_ip, 17, udp(src_ip, dst_ip, sport, dport, payload)));
  endfunction
  static function byte_q arp_request(bit [47:0] src_mac, bit [31:0] src_ip, bit [31:0] target = accel_pkg::ETH_IP);
    return ethernet(48'hFFFF_FFFF_FFFF, src_mac, 16'h0806, arp(1, src_mac, src_ip, 48'h0, target));
  endfunction
  static function byte_q ping(bit [47:0] src_mac, bit [31:0] src_ip, bit [15:0] id, bit [15:0] seq, byte_q data);
    return ethernet(accel_pkg::ETH_MAC, src_mac, 16'h0800, ipv4(src_ip, accel_pkg::ETH_IP, 1, icmp_echo(8, id, seq, data)));
  endfunction
endclass
