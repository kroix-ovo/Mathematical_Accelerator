// This one-entry register holds a byte and its last marker between two
// ready/valid stages. I use it to shorten the combinational timing path from
// packet generation into COBS encoding. It accepts a new item when empty;
// UART is much slower than this possible one-item-per-two-clocks throughput.
module stream_reg #(
    parameter int W = 9
) (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [W-1:0] in_data,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [W-1:0] out_data
);
  assign in_ready = !out_valid;
  // out_valid doubles as the occupied flag. This version cannot drain and
  // refill on the same clock: a full slot must first become empty.

  always_ff @(posedge clk) begin
    if (rst) out_valid <= 1'b0;
    else if (out_valid) begin
      if (out_ready) out_valid <= 1'b0;
    end else if (in_valid) begin
      out_valid <= 1'b1;
      out_data  <= in_data;
    end
  end
endmodule
