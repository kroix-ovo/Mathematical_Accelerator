// COBS turns zero-free wire bytes back into packet bytes. A code byte says
// how many nonzero data bytes follow it. Short blocks stand for an extra zero
// between blocks; 0xFF blocks do not. I emit that implied zero when the next
// code arrives, so the delimiter itself never adds a trailing packet byte.
// An input 0x00 ends the frame and checks whether the block was complete.
module cobs_decoder (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    input  logic [7:0] in_data,
    output logic       out_valid,
    output logic [7:0] out_data,
    output logic       frame_end,  // pulse on the delimiter
    output logic       frame_err   // qualifies frame_end: block overran the frame
);
  logic       expect_code;   // next byte is a code byte
  logic [7:0] remaining;     // data bytes left in the current block
  logic       zero_pending;  // emit 0x00 before the next block
  logic       cur_ff;        // current block is a 0xFF block (no implicit zero)

  always_ff @(posedge clk) begin
    // A valid pulse marks one decoded byte. frame_end is a separate pulse
    // because the final wire delimiter is not part of the decoded payload.
    out_valid <= 1'b0;
    frame_end <= 1'b0;
    if (rst) begin
      expect_code  <= 1'b1;
      zero_pending <= 1'b0;
      frame_err    <= 1'b0;
    end else if (in_valid) begin
      if (in_data == 8'h00) begin
        frame_end    <= 1'b1;
        frame_err    <= !expect_code;
        expect_code  <= 1'b1;
        zero_pending <= 1'b0;
      end else if (expect_code) begin
        if (zero_pending) begin
          out_valid <= 1'b1;
          out_data  <= 8'h00;
        end
        cur_ff <= (in_data == 8'hFF);
        if (in_data == 8'h01) begin
          zero_pending <= 1'b1;  // empty block
        end else begin
          remaining    <= in_data - 1'b1;
          expect_code  <= 1'b0;
          zero_pending <= 1'b0;
        end
      end else begin
        out_valid <= 1'b1;
        out_data  <= in_data;
        remaining <= remaining - 1'b1;
        if (remaining == 8'd1) begin
          expect_code  <= 1'b1;
          zero_pending <= !cur_ff;
        end
      end
    end
  end
endmodule
