// This stateful byte selector builds one raw reply packet. I send opcode,
// tag, little-endian length, payload, then little-endian CRC. The downstream
// COBS encoder adds the wire delimiter. idx advances on ready/valid handshakes.
module frame_tx
  import util_pkg::*;
#(
    parameter int BUF_AW = 10
) (
    input  logic              clk,
    input  logic              rst,
    input  logic              start,
    input  logic [7:0]        op,
    input  logic [7:0]        tag,
    input  logic [15:0]       len,
    output logic              busy,
    // tx payload buffer read (combinational)
    output logic [BUF_AW-1:0] rd_addr,
    input  logic [7:0]        rd_data,
    // raw byte stream to the COBS encoder
    output logic              out_valid,
    input  logic              out_ready,
    output logic [7:0]        out_data,
    output logic              out_last
);
  logic [15:0] idx, plen;
  logic [7:0]  r_op, r_tag;
  logic [15:0] crc;

  assign out_valid = busy;
  assign rd_addr   = BUF_AW'(idx - 16'd4);
  assign out_last  = (idx == plen + 16'd5);

  always_comb begin
    // The same idx selects either a header byte, a buffered payload byte,
    // or one of the two CRC bytes.
    if (idx == 0)                 out_data = r_op;
    else if (idx == 1)            out_data = r_tag;
    else if (idx == 2)            out_data = plen[7:0];
    else if (idx == 3)            out_data = plen[15:8];
    else if (idx < plen + 16'd4)  out_data = rd_data;
    else if (idx == plen + 16'd4) out_data = crc[7:0];
    else                          out_data = crc[15:8];
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      busy <= 1'b0;
    end else if (!busy) begin
      if (start) begin
        busy  <= 1'b1;
        idx   <= '0;
        plen  <= len;
        r_op  <= op;
        r_tag <= tag;
        crc   <= 16'hFFFF;
      end
    end else if (out_ready) begin
      // busy supplies out_valid. Update the CRC only when the next stage
      // accepts a header/payload byte, so a stalled byte is not counted twice.
      if (idx < plen + 16'd4) crc <= crc16_update(crc, out_data);
      if (out_last) busy <= 1'b0;
      idx <= idx + 1'b1;
    end
  end
endmodule
