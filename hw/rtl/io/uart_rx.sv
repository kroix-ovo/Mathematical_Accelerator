// UART receive converts a start bit, eight data bits, and a stop bit into a
// one-clock byte-valid pulse. I sample near each bit's center, where the wire
// is less likely to be changing, and discard a byte with a bad stop bit.
// CLKS_PER_BIT counts FPGA clocks, not transmitted bits. The counter spaces
// the samples; bit_idx chooses which of the eight data samples comes next.
// The two synchronizer registers reduce metastability risk, while the START
// state's half-bit wait separately rejects short low pulses on the cable.
module uart_rx #(
    parameter int CLKS_PER_BIT = 50
) (
    input  logic       clk,                      // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic       rst,                      // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic       rx,                       // Universal asynchronous receiver/transmitter receive input: asynchronous host serial line, idle high.
    output logic       valid,                    // Received-byte valid: one-clock pulse after eight data bits and a correct high stop bit are sampled.
    output logic [7:0] data                      // Received-byte data: reconstructed eight-bit character, meaningful when received-byte valid is high.
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
