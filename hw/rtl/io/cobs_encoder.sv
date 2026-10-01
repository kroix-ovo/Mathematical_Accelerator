// COBS packages raw bytes for a wire where zero marks the end of a frame.
// I gather up to 254 nonzero bytes, emit a code byte followed by that block,
// and finally emit a zero delimiter. A raw zero closes its current block.
// The input side pauses while a block is emitted; ready/valid controls each
// transfer so a slow UART cannot cause dropped bytes.
module cobs_encoder (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,
    input  logic       in_last,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
  logic [7:0] blk [254];
  logic [7:0] cnt;        // bytes stored in the open block
  logic [7:0] code;       // code byte of the block being emitted
  logic [7:0] rd;         // emit index: 0 = code byte, 1..code-1 = data
  logic       final_blk;  // the block being emitted is the frame's last
  logic       saw_last;   // in_last consumed; flush open block after emitting

  typedef enum logic [1:0] {FILL, EMIT, DELIM} state_t;
  state_t state;

  assign in_ready  = (state == FILL);
  assign out_valid = (state == EMIT) || (state == DELIM);
  assign out_data  = (state == DELIM) ? 8'h00 : (rd == 0) ? code : blk[rd - 1'b1];

  always_ff @(posedge clk) begin
    // FILL collects raw bytes, EMIT sends a complete coded block, and DELIM
    // sends the final wire zero. in_last belongs to the raw frame, not UART.
    if (rst) begin
      state    <= FILL;
      cnt      <= '0;
      saw_last <= 1'b0;
    end else begin
      case (state)
        FILL: if (in_valid) begin
          if (in_data == 8'h00) begin
            code      <= cnt + 1'b1;
            rd        <= '0;
            final_blk <= 1'b0;
            saw_last  <= in_last;
            state     <= EMIT;
          end else if (cnt == 8'd253) begin
            blk[cnt]  <= in_data;
            code      <= 8'hFF;
            rd        <= '0;
            final_blk <= 1'b0;
            saw_last  <= in_last;
            state     <= EMIT;
          end else begin
            blk[cnt] <= in_data;
            cnt      <= cnt + 1'b1;
            if (in_last) begin
              code      <= cnt + 2'd2;
              rd        <= '0;
              final_blk <= 1'b1;
              state     <= EMIT;
            end
          end
        end
        EMIT: if (out_ready) begin
          if (rd == code - 1'b1) begin
            cnt <= '0;
            if (final_blk) begin
              state <= DELIM;
            end else if (saw_last) begin
              // The last input byte closed a block; COBS still emits a final (empty) block.
              code      <= 8'h01;
              rd        <= '0;
              final_blk <= 1'b1;
              saw_last  <= 1'b0;
            end else begin
              state <= FILL;
            end
          end else begin
            rd <= rd + 1'b1;
          end
        end
        DELIM: if (out_ready) state <= FILL;
        default: state <= FILL;
      endcase
    end
  end
endmodule
