// UART receive converts a start bit, eight data bits, and a stop bit into a
// one-clock byte-valid pulse. I sample near each bit's center, where the wire
// is less likely to be changing, and discard a byte with a bad stop bit.
module uart_rx #(
    parameter int CLKS_PER_BIT = 50
) (
    input  logic       clk,
    input  logic       rst,
    input  logic       rx,
    output logic       valid,
    output logic [7:0] data
);
  localparam int CW = $clog2(CLKS_PER_BIT + 1);

  logic rx_s0, rx_s1;
  // The wire is asynchronous to clk. Two flip-flops reduce the chance that
  // a changing input reaches the state machine in an unstable state.
  always_ff @(posedge clk) begin
    rx_s0 <= rx;
    rx_s1 <= rx_s0;
  end

  typedef enum logic [1:0] {IDLE, START, DATA, STOP} state_t;
  state_t        state;
  logic [CW-1:0] cnt;
  logic [2:0]    bit_idx;
  logic [7:0]    shreg;

  always_ff @(posedge clk) begin
    valid <= 1'b0;
    if (rst) begin
      state <= IDLE;
      cnt   <= '0;
    end else begin
      case (state)
        IDLE: if (!rx_s1) begin
          state <= START;
          cnt   <= CW'(CLKS_PER_BIT / 2 - 1);
        end
        // Recheck the low start level halfway through its bit period. A brief
        // low glitch returns to IDLE instead of being treated as a character.
        START: if (cnt == 0) begin
          if (!rx_s1) begin
            state   <= DATA;
            cnt     <= CW'(CLKS_PER_BIT - 1);
            bit_idx <= '0;
          end else state <= IDLE;  // glitch
        end else cnt <= cnt - 1'b1;
        // Sampling is now one full bit period apart. The UART sends bit zero
        // first; shifting right makes the eighth sample finish the correct byte.
        DATA: if (cnt == 0) begin
          shreg <= {rx_s1, shreg[7:1]};
          cnt   <= CW'(CLKS_PER_BIT - 1);
          if (bit_idx == 3'd7) state <= STOP;
          bit_idx <= bit_idx + 1'b1;
        end else cnt <= cnt - 1'b1;
        STOP: if (cnt == 0) begin
          state <= IDLE;
          if (rx_s1) begin
            valid <= 1'b1;
            data  <= shreg;
          end
        end else cnt <= cnt - 1'b1;
        default: state <= IDLE;
      endcase
    end
  end
endmodule
