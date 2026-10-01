// This is the receive packet gate. I collect decoded bytes in a buffer and
// release a one-clock frame_valid pulse only after the delimiter, length, and
// CRC all agree. Layout is op, tag, two length bytes, payload, two CRC bytes.
// A request arriving while the command processor is busy is dropped.
module frame_rx
  import accel_pkg::*;
  import util_pkg::*;
#(
    parameter int BUF_AW = 11
) (
    input  logic              clk,
    input  logic              rst,
    input  logic              in_valid,
    input  logic [7:0]        in_data,
    input  logic              in_end,
    input  logic              in_err,
    input  logic              hold,
    // accepted frame
    output logic              frame_valid,
    output logic [7:0]        frame_op,
    output logic [7:0]        frame_tag,
    output logic [15:0]       frame_len,
    // payload buffer read (combinational)
    input  logic [BUF_AW-1:0] rd_addr,
    output logic [7:0]        rd_data,
    // statistics
    output logic [15:0]       crc_errors,
    output logic [15:0]       frame_errors
);
  localparam int MAX_RAW = MAX_PAYLOAD + 6;

  logic [7:0]  buffer [2**BUF_AW];
  logic [15:0] n;          // bytes received in this frame
  logic [15:0] crc;        // CRC of all bytes except the last two seen
  logic [7:0]  b1, b2;     // delay two bytes so CRC never includes its own field
  logic        overflow, dropping;

  assign rd_data = buffer[rd_addr];

  always_ff @(posedge clk) begin
    // A length or CRC error never reaches the command processor. Counters
    // remain available through STATUS for link diagnosis.
    if (in_valid && !dropping && n < MAX_RAW) buffer[n[BUF_AW-1:0]] <= in_data;
  end

  always_ff @(posedge clk) begin
    frame_valid <= 1'b0;
    if (rst) begin
      n            <= '0;
      crc          <= 16'hFFFF;
      overflow     <= 1'b0;
      dropping     <= 1'b0;
      crc_errors   <= '0;
      frame_errors <= '0;
    end else if (in_end) begin
      if (n != 0) begin
        if (dropping || in_err || overflow || n < 6 || frame_len > MAX_PAYLOAD || n != frame_len + 16'd6)
          frame_errors <= frame_errors + 1'b1;
        else if (crc != {b1, b2})
          crc_errors <= crc_errors + 1'b1;
        else
          frame_valid <= 1'b1;
      end
      n        <= '0;
      crc      <= 16'hFFFF;
      overflow <= 1'b0;
      dropping <= 1'b0;
    end else if (in_valid) begin
      if (n == 0) dropping <= hold;
      if (n == MAX_RAW) overflow <= 1'b1;
      else n <= n + 1'b1;
      case (n)
        16'd0: frame_op <= in_data;
        16'd1: frame_tag <= in_data;
        16'd2: frame_len[7:0] <= in_data;
        16'd3: frame_len[15:8] <= in_data;
        default: ;
      endcase
      if (n >= 2) crc <= crc16_update(crc, b2);
      b2 <= b1;
      b1 <= in_data;
    end
  end
endmodule
