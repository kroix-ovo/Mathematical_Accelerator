// UART transmit turns each accepted byte into a start bit, eight data bits
// (least significant first), and a stop bit. ready is high only when idle.
// CLKS_PER_BIT determines how long every bit stays on the output pin.
// I load one whole byte before serialization. valid is acted on only while
// ready is high; leaving it high for a stalled byte does not restart the
// transmitter. tx stays high between characters, and the final stop bit gets
// a full bit period before ready allows the next character to begin.
module uart_tx #(
    parameter int CLKS_PER_BIT = 50
) (
    input  logic       clk,                      // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic       rst,                      // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic       valid,                    // Transmit-byte valid: caller offers a character; accepted on a rising edge only while ready is high.
    output logic       ready,                    // Transmit-byte ready: high when idle and able to capture the next character.
    input  logic [7:0] data,                     // Transmit-byte data: eight-bit character captured on the valid/ready handshake and sent least-significant bit first.
    output logic       tx                        // Universal asynchronous receiver/transmitter transmit output: serial start, data and stop bits, idle high.
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
