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

endpackage
