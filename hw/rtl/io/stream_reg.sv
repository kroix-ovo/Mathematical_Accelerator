// This one-entry register holds a byte and its last marker between two
// ready/valid stages. I use it to shorten the combinational timing path from
// packet generation into COBS encoding. It accepts a new item when empty;
// UART is much slower than this possible one-item-per-two-clocks throughput.
// Think of this as a one-byte register with an occupied bit. This version
// alternates between loading and draining; it retains a byte through a
// stall. I use it between framing and COBS so the byte and its last marker
// remain together even when the encoder stops to send a buffered block.
module stream_reg #(
    parameter int W = 9
) (
    input  logic         clk,                    // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic         rst,                    // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic         in_valid,               // Input valid: upstream presents data; a transfer occurs on a rising edge with input ready also high.
    output logic         in_ready,               // Input ready: high only while the holding register is empty; this implementation cannot drain and refill together.
    input  logic [W-1:0] in_data,                // Input data: complete W-bit item to buffer; the core packs a final-byte marker beside its byte.
    output logic         out_valid,              // Output valid: the selected output item is available and remains stable until accepted.
    input  logic         out_ready,              // Output ready: downstream accepts the current item on a rising edge when output valid is also high.
    output logic [W-1:0] out_data                // Output data: item presented to downstream, meaningful while output valid is high.
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
