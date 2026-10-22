// Shared bit-level helper functions used by both packet directions.
package util_pkg;

  // I XOR one byte into the high end, then perform eight polynomial steps.
  // This is CRC-16/CCITT-FALSE: polynomial 0x1021, MSB first, initial FFFF.
  function automatic logic [15:0] crc16_update(input logic [15:0] crc, input logic [7:0] data);
    logic [15:0] c;
    c = crc ^ {data, 8'h00};
    for (int i = 0; i < 8; i++)
      c = c[15] ? ((c << 1) ^ 16'h1021) : (c << 1);
    return c;
  endfunction

  // Ethernet's CRC-32 for one byte. Ethernet sends bits least significant
  // first, so this register shifts right and uses the bit-reversed
  // polynomial 0xEDB88320. The loop unrolls into a block of XOR gates; there
  // is no clocked loop in hardware. Start at FFFFFFFF; the transmitted FCS is
  // the inverted register, low byte first. A receiver that also runs the
  // four FCS bytes through this function ends at the constant DEBB20E3 for
  // every good frame.
  function automatic logic [31:0] crc32_update(input logic [31:0] crc, input logic [7:0] data);
    logic [31:0] c;
    c = crc ^ {24'h0, data};
    for (int i = 0; i < 8; i++)
      c = c[0] ? ((c >> 1) ^ 32'hEDB88320) : (c >> 1);
    return c;
  endfunction

  // Ones'-complement addition used by the IPv4, ICMP and UDP checksums: a
  // carry out of bit 15 wraps around into bit 0.
  function automatic logic [15:0] oc_add(input logic [15:0] a, input logic [15:0] b);
    logic [16:0] s;
    s = {1'b0, a} + {1'b0, b};
    return s[15:0] + {15'h0, s[16]};
  endfunction

endpackage
