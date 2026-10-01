// UART transmit turns each accepted byte into a start bit, eight data bits
// (least significant first), and a stop bit. ready is high only when idle.
// CLKS_PER_BIT determines how long every bit stays on the output pin.
module uart_tx #(
    parameter int CLKS_PER_BIT = 50
) (
    input  logic       clk,
    input  logic       rst,
    input  logic       valid,
    output logic       ready,
    input  logic [7:0] data,
    output logic       tx
);
  localparam int CW = $clog2(CLKS_PER_BIT + 1);

  logic [8:0]    shreg;      // {stop, data[7:0]} still to send
  logic [3:0]    bits_left;  // bits (incl. the one on the line) not yet finished
  logic [CW-1:0] cnt;

  assign ready = (bits_left == 0);

  always_ff @(posedge clk) begin
    // Loading drives the start bit immediately. Each timer expiration shifts
    // the next data or stop bit onto tx for a whole bit period.
    if (rst) begin
      tx        <= 1'b1;
      bits_left <= '0;
    end else if (bits_left == 0) begin
      if (valid) begin
        tx        <= 1'b0;  // start bit
        shreg     <= {1'b1, data};
        bits_left <= 4'd10;
        cnt       <= CW'(CLKS_PER_BIT - 1);
      end
    end else if (cnt != 0) begin
      cnt <= cnt - 1'b1;
    end else if (bits_left == 1) begin
      bits_left <= '0;  // stop bit done
    end else begin
      tx        <= shreg[0];
      shreg     <= {1'b1, shreg[8:1]};
      bits_left <= bits_left - 1'b1;
      cnt       <= CW'(CLKS_PER_BIT - 1);
    end
  end
endmodule
