// Frame encoding in SystemVerilog: CRC-16/CCITT-FALSE and COBS.
//
// These are written separately from the RTL (which streams one byte per
// clock) and from the Python driver, as plain loops over byte queues. A
// queue (byte_q, declared as "byte unsigned q[$]") is SV's growable array;
// push_back appends, q.size() is the length.
typedef byte unsigned byte_q[$];

class top_codec;
  // CRC over a whole byte queue, MSB first, poly 0x1021, start 0xFFFF.
  static function bit [15:0] crc16(byte_q data);
    bit [15:0] c = 16'hFFFF;
    foreach (data[i]) begin
      c ^= {data[i], 8'h00};
      repeat (8) c = c[15] ? ((c << 1) ^ 16'h1021) : (c << 1);
    end
    return c;
  endfunction

  // Raw frame: op, tag, len (LE), payload, crc (LE).
  static function byte_q frame(byte unsigned op, byte unsigned tag, byte_q payload);
    byte_q f;
    bit [15:0] c;
    f.push_back(op);
    f.push_back(tag);
    f.push_back(payload.size() & 8'hFF);
    f.push_back(payload.size() >> 8);
    foreach (payload[i]) f.push_back(payload[i]);
    c = crc16(f);
    f.push_back(c[7:0]);
    f.push_back(c[15:8]);
    return f;
  endfunction

  // COBS: replace every zero with a pointer to the next zero, so the encoded
  // frame contains no zeros and a 0x00 can mark its end. A code byte k means
  // "k-1 data bytes follow, then (unless k == 255) an implied zero".
  static function byte_q cobs_encode(byte_q data);
    byte_q out, blk;
    foreach (data[i]) begin
      if (data[i] == 0) begin
        out.push_back(blk.size() + 1);
        foreach (blk[j]) out.push_back(blk[j]);
        blk.delete();
      end else begin
        blk.push_back(data[i]);
        if (blk.size() == 254) begin
          out.push_back(255);
          foreach (blk[j]) out.push_back(blk[j]);
          blk.delete();
        end
      end
    end
    out.push_back(blk.size() + 1);
    foreach (blk[j]) out.push_back(blk[j]);
    return out;
  endfunction

  // Returns 0 on malformed input (zero inside, or a block running past the end).
  static function bit cobs_decode(byte_q data, output byte_q out);
    int i = 0;
    out.delete();
    while (i < data.size()) begin
      int code = data[i];
      if (code == 0) return 0;
      i++;
      if (i + code - 1 > data.size()) return 0;
      for (int k = 0; k < code - 1; k++) begin
        if (data[i + k] == 0) return 0;
        out.push_back(data[i + k]);
      end
      i += code - 1;
      if (code != 255 && i < data.size()) out.push_back(0);
    end
    return 1;
  endfunction

  static function string hex(byte_q q, int max = 24);
    string s = "";
    foreach (q[i]) begin
      if (i == max) return {s, "..."};
      s = {s, $sformatf("%02h", q[i])};
    end
    return s;
  endfunction
endclass
