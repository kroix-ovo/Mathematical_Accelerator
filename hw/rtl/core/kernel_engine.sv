// This state machine performs one requested kernel at a time. The caller
// (cmd_proc) checks kernel IDs, argument values and address ranges before
// asserting start, so nothing here has to reject a request. The EVU is the one
// exception: its descriptor is in the scratchpad, so only evu_core can read and
// check it, and it can come back with a status other than ST_OK.
//
//   NOP   no memory access, 1 cycle
//   FILL  write one scratchpad word per clock, count + 1 cycles
//   FPU   r[i], f[i] = fop(a[i], b[i]) for i = 0 .. count-1, where a, b, r
//         and f are word arrays in the scratchpad. r gets the 32-bit result,
//         f gets that element's 5 exception flags (in the low bits).
//   EVU   run a program over a grid of points; evu_core does all the work.
//
// There is one FPU and it lives here. Only one kernel runs at a time, so I hand
// the FPU and both scratchpad ports to evu_core while state is EVU, and the FPU
// kernel's logic below ignores whatever results go past in that time.
//
// `cycles` counts every clock from start to completion, including the final
// FIN clock. `aux` is a kernel-specific extra result: for FPU it is the OR of
// all elements' flags (like a sticky floating-point status register), for
// NOP and FILL it is 0, and for EVU it is the OR of all points' flags.
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
// I keep fetching and write-back independent: the state machine feeds new
// operands while an earlier result can use memory port B. ISSUE holds a_q,
// b_q and the element index steady until req_ready says the FPU accepted them.
// DRAIN is needed even after the last input was accepted: a CORDIC result is
// still travelling through 52 internal stages and the FPU's output buffering.
// Each input takes at least four clocks to issue, leaving time to write its
// result word and then its flag word before another result of the same op.
module kernel_engine
  import accel_pkg::*;
(
    input  logic                  clk,           // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic                  rst,           // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic                  start,         // Kernel start: high in idle captures one validated kernel request on the rising edge.
    input  logic [7:0]            kernel,        // Kernel identifier: selects no-operation, scratchpad fill, floating-point array processing or expression evaluation.
    input  logic [8*21-1:0]       args,          // Kernel arguments: packed RUN bytes after the kernel identifier; specify the operation, count and addresses.
    output logic                  busy,          // Kernel busy: high from request capture through the final completion state.
    output logic                  done,          // Kernel done: one-clock pulse marking completion and valid cycle-count/auxiliary outputs.
    output logic [31:0]           cycles,        // Kernel cycle count: core clocks used by the last completed kernel, including its final completion clock.
    output logic [31:0]           aux,           // Kernel auxiliary result: bitwise OR of all element exception flags for the floating-point and expression kernels; otherwise zero.
    output logic [7:0]            status,        // Kernel status: ST_OK, or why the expression unit refused its descriptor; valid with done.
    // scratchpad port A (read/write)
    output logic                  m_en,          // Memory port A enable: requests a scratchpad access on the rising clock edge.
    output logic                  m_we,          // Memory port A write enable: high writes a word when memory enable is also high.
    output logic [SCRATCH_AW-1:0] m_addr,        // Memory port A address: scratchpad word index for the requested read or write, not a byte address.
    output logic [31:0]           m_wdata,       // Memory port A write data: 32-bit word stored when both memory enable and write enable are high.
    input  logic [31:0]           m_rdata,       // Memory port A read data: registered scratchpad word returned after the enabled read edge.
    // scratchpad port B (write)
    output logic                  b_we,          // Memory port B write enable: high commits the result or flag word on the rising clock edge.
    output logic [SCRATCH_AW-1:0] b_addr,        // Memory port B address: scratchpad word index selected for result or flag write-back.
    output logic [31:0]           b_wdata        // Memory port B write data: 32-bit result word or zero-extended five-bit exception flags.
);
  // Little-endian 32-bit argument starting at byte `off` of args.
  function automatic logic [31:0] arg32(input int off); // off: byte offset of the little-endian 32-bit argument inside the packed RUN payload.
    return args[8*off +: 32];
  endfunction

  typedef enum logic [3:0] {IDLE, FILL, RA, RB, RC, ISSUE, DRAIN, EVU, FIN} state_t;
  state_t state;

  logic [31:0]           cyc, i, count, value;
  logic [SCRATCH_AW-1:0] base;                  // FILL base
  logic [SCRATCH_AW-1:0] a_base, b_base, r_base, f_base;
  logic [4:0]            fop;
  logic [31:0]           a_q, b_q;              // captured a[i], b[i]
  logic [31:0]           j;                     // results fully written
  logic                  flag_pend;             // f[j] write due this clock
  logic [4:0]            flag_q, flags_or;

  // I give the EVU exclusive ownership of the shared FPU and both memory
  // ports for a whole run. The old array kernel keeps its own state path;
  // I only switch the request/data muxes while state is EVU.
  // ---- expression unit ---------------------------------------------------------
  logic                  in_evu, evu_start, evu_done, evu_busy;
  logic [31:0]           evu_aux;
  logic [7:0]            evu_status;
  logic                  evu_m_en, evu_b_we;
  logic [SCRATCH_AW-1:0] evu_desc, evu_m_addr, evu_b_addr;
  logic [31:0]           evu_b_wdata;
  logic                  evu_req_valid, req_ready, rsp_valid;
  logic [4:0]            evu_req_op;
  logic [31:0]           evu_req_a, evu_req_b;
  logic [7:0]            evu_req_tag;
  logic [31:0]           rsp_result;
  logic [4:0]            rsp_flags;
  logic [7:0]            rsp_tag;

  assign in_evu    = (state == EVU);
  assign evu_start = (state == IDLE) && start && (kernel == K_EVU);
  // A slice and not arg32(0): a function in a continuous assignment only reacts
  // to its arguments, so a simulator would never see args change.
  assign evu_desc  = args[SCRATCH_AW-1:0];

  evu_core u_evu (
      .clk, .rst, .start(evu_start), .desc_addr(evu_desc),
      .busy(evu_busy), .done(evu_done), .aux(evu_aux), .status(evu_status),
      .m_en(evu_m_en), .m_addr(evu_m_addr), .m_rdata,
      .b_we(evu_b_we), .b_addr(evu_b_addr), .b_wdata(evu_b_wdata),
      .fpu_req_valid(evu_req_valid), .fpu_req_ready(req_ready), .fpu_req_op(evu_req_op),
      .fpu_req_a(evu_req_a), .fpu_req_b(evu_req_b), .fpu_req_tag(evu_req_tag),
      .fpu_rsp_valid(rsp_valid), .fpu_rsp_result(rsp_result), .fpu_rsp_flags(rsp_flags), .fpu_rsp_tag(rsp_tag));

  // ---- FPU, shared by the two kernels -------------------------------------------
  logic        fpu_valid;
  logic [4:0]  fpu_op;
  logic [31:0] fpu_a, fpu_b;
  logic [7:0]  fpu_tag;
  logic        own_rsp;                           // a result that belongs to the FPU kernel

  assign fpu_valid = in_evu ? evu_req_valid : (state == ISSUE);
  assign fpu_op    = in_evu ? evu_req_op    : fop;
  assign fpu_a     = in_evu ? evu_req_a     : a_q;
  assign fpu_b     = in_evu ? evu_req_b     : b_q;
  assign fpu_tag   = in_evu ? evu_req_tag   : i[7:0];
  // I only count responses for the array kernel when it owns the FPU.
  // EVU tags name contexts, whereas array tags name element indices.
  assign own_rsp   = rsp_valid && !in_evu;

  fpu #(.TW(8)) u_fpu (
      .clk, .rst,
      .req_valid(fpu_valid), .req_ready, .req_op(fpu_op), .req_a(fpu_a), .req_b(fpu_b), .req_tag(fpu_tag),
      .rsp_valid, .rsp_result, .rsp_flags, .rsp_tag);

  // ---- memory ports -----------------------------------------------------------
  assign busy    = (state != IDLE);
  assign m_en    = in_evu ? evu_m_en : (state == FILL) || (state == RA) || (state == RB);
  assign m_we    = (state == FILL);               // the expression unit only reads through port A
  assign m_addr  = in_evu          ? evu_m_addr :
                   (state == FILL) ? base + SCRATCH_AW'(i) :
                   (state == RA)   ? a_base + SCRATCH_AW'(i) : b_base + SCRATCH_AW'(i);
  assign m_wdata = value;
  // Port B: result word when it arrives, flag word one clock later.
  assign b_we    = in_evu ? evu_b_we    : own_rsp || flag_pend;
  assign b_addr  = in_evu ? evu_b_addr  : own_rsp ? r_base + SCRATCH_AW'(j) : f_base + SCRATCH_AW'(j);
  assign b_wdata = in_evu ? evu_b_wdata : own_rsp ? rsp_result : {27'b0, flag_q};

  always_ff @(posedge clk) begin
    // done is a pulse, not a sticky flag: clear it on every clock by default.
    done      <= 1'b0;
    flag_pend <= 1'b0;
    if (rst) begin
      state <= IDLE;
    end else begin
      // Result write-back runs alongside the fetch states below.
      if (own_rsp) begin
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
            // Phase 3 needs five op bits (0..17). cmd_proc has already
            // checked the whole wire byte, so narrowing it is safe here.
            fop    <= args[4:0];
            count  <= arg32(1);
            a_base <= SCRATCH_AW'(arg32(5));
            b_base <= SCRATCH_AW'(arg32(9));
            r_base <= SCRATCH_AW'(arg32(13));
            f_base <= SCRATCH_AW'(arg32(17));
            if (arg32(1) != 0) state <= RA;
            else               state <= FIN;
          end else if (kernel == K_EVU) begin
            state <= EVU;                         // evu_start is high on this clock
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

        // evu_core is running the show until it says it has finished.
        EVU: if (evu_done) state <= FIN;

        // cycles becomes valid with done. Before completion, callers should
        // wait rather than read a result left over from a previous kernel.
        FIN: begin
          cycles <= cyc + 1'b1;
          aux    <= (kernel == K_FPU) ? {27'b0, flags_or} : (kernel == K_EVU) ? evu_aux : '0;
          status <= (kernel == K_EVU) ? evu_status : ST_OK;
          done   <= 1'b1;
          state  <= IDLE;
        end
        default: state <= IDLE;
      endcase
    end
  end
endmodule
