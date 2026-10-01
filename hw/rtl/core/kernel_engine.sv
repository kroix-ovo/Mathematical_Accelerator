// This state machine performs one requested kernel at a time. I use IDLE to
// capture the command, FILL to write one scratchpad word per clock, and FIN to
// pulse done and report the cycle count. NOP skips FILL. The caller checks
// kernel IDs and address ranges before asserting start.
// `cycles` is one for NOP and count + one for FILL, including FIN.
module kernel_engine
  import accel_pkg::*;
(
    input  logic                  clk,
    input  logic                  rst,
    input  logic                  start,
    input  logic [7:0]            kernel,
    input  logic [31:0]           arg0,
    input  logic [31:0]           arg1,
    input  logic [31:0]           arg2,
    output logic                  busy,
    output logic                  done,
    output logic [31:0]           cycles,
    // scratchpad port
    output logic                  m_en,
    output logic                  m_we,
    output logic [SCRATCH_AW-1:0] m_addr,
    output logic [31:0]           m_wdata
);
  typedef enum logic [1:0] {IDLE, FILL, FIN} state_t;
  state_t      state;
  logic [31:0] cyc, i, count, value;
  logic [SCRATCH_AW-1:0] base;

  assign busy    = (state != IDLE);
  // The memory sees a write only during FILL. i starts at zero, so the first
  // clock writes base and each later clock advances by one word.
  assign m_en    = (state == FILL);
  assign m_we    = (state == FILL);
  assign m_addr  = base + SCRATCH_AW'(i);
  assign m_wdata = value;

  always_ff @(posedge clk) begin
    // done is a pulse, not a sticky flag: clear it on every clock by default.
    done <= 1'b0;
    if (rst) begin
      state <= IDLE;
    end else begin
      case (state)
        IDLE: if (start) begin
          cyc   <= '0;
          i     <= '0;
          base  <= SCRATCH_AW'(arg0);
          count <= arg1;
          value <= arg2;
          state <= (kernel == K_FILL && arg1 != 0) ? FILL : FIN;
        end
        FILL: begin
          cyc <= cyc + 1'b1;
          i   <= i + 1'b1;
          if (i == count - 1) state <= FIN;
        end
        // cycles becomes valid with done. Before completion, callers should
        // wait rather than read a result left over from a previous kernel.
        FIN: begin
          cycles <= cyc + 1'b1;
          done   <= 1'b1;
          state  <= IDLE;
        end
        default: state <= IDLE;
      endcase
    end
  end
endmodule
