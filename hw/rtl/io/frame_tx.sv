// This stateful byte selector builds one raw reply packet. I send opcode,
// tag, little-endian length, payload, then little-endian CRC. The downstream
// COBS encoder adds the wire delimiter. idx advances on ready/valid handshakes.
// I latch op/tag/len at start so they cannot change halfway through a reply.
// The payload remains in the caller's buffer and rd_addr selects its next
// byte. The output multiplexer can change combinationally, but idx moves only
// after acceptance, keeping data stable while the COBS encoder is paused.
module frame_tx
  import util_pkg::*;
#(
    parameter int BUF_AW = 10
) (
    input  logic              clk,               // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic              rst,               // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic              start,             // Transmit start: pulse while idle captures the reply header and starts raw packet generation.
    input  logic [7:0]        op,                // Reply operation code: command identifier with its response bit already set by the caller.
    input  logic [7:0]        tag,               // Reply request tag: host identifier copied into the generated packet header.
    input  logic [15:0]       len,               // Reply payload length: number of bytes to read from the payload buffer before appending the checksum.
    output logic              busy,              // Packet builder busy: high while header, payload and cyclic redundancy check bytes remain to be accepted.
    // tx payload buffer read (combinational)
    output logic [BUF_AW-1:0] rd_addr,           // Transmit-buffer read address: selects the reply payload byte needed by the raw packet builder.
    input  logic [7:0]        rd_data,           // Transmit-buffer read data: combinational reply payload byte selected by the buffer address.
    // raw byte stream to the COBS encoder
    output logic              out_valid,         // Output byte valid: high while a raw packet byte is available; waits for output ready.
    input  logic              out_ready,         // Output byte ready: downstream encoder accepts the selected raw packet byte on the rising edge.
    output logic [7:0]        out_data,          // Output byte data: selected reply header, payload or cyclic redundancy check byte.
    output logic              out_last           // Output last-byte marker: high with the final checksum byte; sampled with the byte's valid/ready transfer.
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
