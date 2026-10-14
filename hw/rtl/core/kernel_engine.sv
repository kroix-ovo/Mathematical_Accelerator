// This state machine performs one requested kernel at a time. The caller
// (cmd_proc) checks kernel IDs, argument values and address ranges before
// asserting start, so nothing here has to reject a request.
//
//   NOP   no memory access, 1 cycle
//   FILL  write one scratchpad word per clock, count + 1 cycles
//   FPU   r[i], f[i] = fop(a[i], b[i]) for i = 0 .. count-1, where a, b, r
//         and f are word arrays in the scratchpad. r gets the 32-bit result,
//         f gets that element's 5 exception flags (in the low bits).
//
// `cycles` counts every clock from start to completion, including the final
// FIN clock. `aux` is a kernel-specific extra result: for FPU it is the OR of
// all elements' flags (like a sticky floating-point status register), for
// NOP and FILL it is 0.
//
// FPU timing: each element takes four clocks to fetch and issue, because
// the scratchpad's read port returns one word per clock and each element
// needs two operands:
//   RA     read a[i]                     (data arrives next clock)
//   RB     read b[i], capture a[i]
//   RC     capture b[i]
//   ISSUE  send a[i], b[i] to the FPU from registers, and wait here while
//          the FPU is not ready (divide/sqrt busy)
// RC exists for timing only. Block RAM data appears late in the clock
// period, and feeding it straight into the FPU's first stage made that path
// about 1.5 ns too slow for 100 MHz. Capturing it in a register first gives
// the FPU a full clock period.
// Results are written through the RAM's second port as they come back, in
// two clocks: r[j] in the clock the result arrives, f[j] on the next clock.
// Results return in issue order, because one kernel run only ever uses one
// operation and so only one FPU unit.
module kernel_engine
  import accel_pkg::*;
(
    input  logic                  clk,
    input  logic                  rst,
    input  logic                  start,
    input  logic [7:0]            kernel,
    input  logic [8*21-1:0]       args,      // RUN payload bytes after the kernel ID
    output logic                  busy,
    output logic                  done,
    output logic [31:0]           cycles,
    output logic [31:0]           aux,
    // scratchpad port A (read/write)
    output logic                  m_en,
    output logic                  m_we,
    output logic [SCRATCH_AW-1:0] m_addr,
    output logic [31:0]           m_wdata,
    input  logic [31:0]           m_rdata,
    // scratchpad port B (write)
    output logic                  b_we,
    output logic [SCRATCH_AW-1:0] b_addr,
    output logic [31:0]           b_wdata
);
  // Little-endian 32-bit argument starting at byte `off` of args.
  function automatic logic [31:0] arg32(input int off);
    return args[8*off +: 32];
  endfunction

  typedef enum logic [2:0] {IDLE, FILL, RA, RB, RC, ISSUE, DRAIN, FIN} state_t;
  state_t state;

  logic [31:0]           cyc, i, count, value;
  logic [SCRATCH_AW-1:0] base;                  // FILL base
  logic [SCRATCH_AW-1:0] a_base, b_base, r_base, f_base;
  logic [3:0]            fop;
  logic [31:0]           a_q, b_q;              // captured a[i], b[i]
  logic [31:0]           j;                     // results fully written
  logic                  flag_pend;             // f[j] write due this clock
  logic [4:0]            flag_q, flags_or;

  // ---- FPU ------------------------------------------------------------------
  logic        req_valid, req_ready, rsp_valid;
  logic [31:0] rsp_result;
  logic [4:0]  rsp_flags;
  logic [7:0]  rsp_tag;

  assign req_valid = (state == ISSUE);
  fpu #(.TW(8)) u_fpu (
      .clk, .rst,
      .req_valid, .req_ready, .req_op(fop), .req_a(a_q), .req_b(b_q), .req_tag(i[7:0]),
      .rsp_valid, .rsp_result, .rsp_flags, .rsp_tag);

  // ---- memory ports -----------------------------------------------------------
  assign busy    = (state != IDLE);
  assign m_en    = (state == FILL) || (state == RA) || (state == RB);
  assign m_we    = (state == FILL);
  assign m_addr  = (state == FILL) ? base + SCRATCH_AW'(i) :
                   (state == RA)   ? a_base + SCRATCH_AW'(i) : b_base + SCRATCH_AW'(i);
  assign m_wdata = value;
  // Port B: result word when it arrives, flag word one clock later.
  assign b_we    = rsp_valid || flag_pend;
  assign b_addr  = rsp_valid ? r_base + SCRATCH_AW'(j) : f_base + SCRATCH_AW'(j);
  assign b_wdata = rsp_valid ? rsp_result : {27'b0, flag_q};

  always_ff @(posedge clk) begin
    // done is a pulse, not a sticky flag: clear it on every clock by default.
    done      <= 1'b0;
    flag_pend <= 1'b0;
    if (rst) begin
      state <= IDLE;
    end else begin
      // Result write-back runs alongside the fetch states below.
      if (rsp_valid) begin
        flag_q    <= rsp_flags;
        flags_or  <= flags_or | rsp_flags;
        flag_pend <= 1'b1;
      end
      if (flag_pend) j <= j + 1'b1;

      if (state != IDLE && state != FIN) cyc <= cyc + 1'b1;

      case (state)
        IDLE: if (start) begin
          cyc      <= '0;
          i        <= '0;
          j        <= '0;
          flags_or <= '0;
          if (kernel == K_FILL) begin
            base  <= SCRATCH_AW'(arg32(0));
            count <= arg32(4);
            value <= arg32(8);
            if (arg32(4) != 0) state <= FILL;
            else               state <= FIN;
          end else if (kernel == K_FPU) begin
            fop    <= args[3:0];
            count  <= arg32(1);
            a_base <= SCRATCH_AW'(arg32(5));
            b_base <= SCRATCH_AW'(arg32(9));
            r_base <= SCRATCH_AW'(arg32(13));
            f_base <= SCRATCH_AW'(arg32(17));
            if (arg32(1) != 0) state <= RA;
            else               state <= FIN;
          end else begin
            state <= FIN;                         // NOP
          end
        end

        FILL: begin
          i <= i + 1'b1;
          if (i == count - 1) state <= FIN;
        end

        RA: state <= RB;
        RB: begin
          a_q   <= m_rdata;                       // a[i], read during RA
          state <= RC;
        end
        RC: begin
          b_q   <= m_rdata;                       // b[i], read during RB
          state <= ISSUE;
        end
        ISSUE: if (req_ready) begin               // FPU took a[i], b[i]
          i <= i + 1'b1;
          if (i == count - 1) state <= DRAIN;
          else                state <= RA;
        end

        // All requests are issued; wait for the last flag word to be written.
        DRAIN: if (flag_pend && j == count - 1) state <= FIN;

        // cycles becomes valid with done. Before completion, callers should
        // wait rather than read a result left over from a previous kernel.
        FIN: begin
          cycles <= cyc + 1'b1;
          aux    <= (kernel == K_FPU) ? {27'b0, flags_or} : '0;
          done   <= 1'b1;
          state  <= IDLE;
        end
        default: state <= IDLE;
      endcase
    end
  end
endmodule
