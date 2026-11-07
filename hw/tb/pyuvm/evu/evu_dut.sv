// I wire the real EVU, FPU and memory together for the block tests here.
// I expose observation wires so the Python monitor can check accepted work.
// I keep optional profiling in this wrapper: it measures simulated clocks and
// isn't a set of counters read from the physical board.
//
// What the expression unit will sit between once it is integrated, wired up for
// the block testbench and the standalone timing build: the real FPU and the real
// scratchpad. The testbench reaches the scratchpad through its own port, as
// cmd_proc will, and only while the unit is idle.
//
// The fpu_* and cov_* outputs add no logic. They let the monitors watch the FPU
// ports and see which rule refused a request.
//
// With PROFILE = 1 this wrapper also sorts every busy clock into one category,
// using the counters at the end of this file. They watch evu_core from outside,
// so the unit being measured is the same RTL that's on the board, and nothing in
// a board build changes. PROFILE is 0 by default, which leaves no counters at all.
module evu_dut
  import accel_pkg::*;
#(
    parameter int CONTEXTS = EVU_CONTEXTS,       // contexts in use
    parameter int FD = 64,                       // the FPU's CORDIC FIFO depth
    parameter bit PROFILE = 1'b0,                // 1 builds the clock counters
    parameter int PROF_N = 16                    // how many categories prof holds
) (
    input  logic                  clk,           // Clock: I update the registers on its rising edge; the board runs this at 100 MHz.
    input  logic                  rst,           // Reset: I sample this active-high signal on the clock edge to clear control state.
    input  logic                  start,         // Start: I accept this one-clock pulse in idle to begin a run.
    input  logic [SCRATCH_AW-1:0] desc_addr,     // Descriptor address: I sample this scratchpad word index with start, then copy the descriptor from it.
    output logic                  busy,          // Busy: I hold this high while this unit is occupied.
    output logic                  done,          // Done: I pulse this for one clock once the writes are committed and status is valid.
    output logic [31:0]           aux,           // Auxiliary result: I OR every point's instruction flags into this run-level summary.
    output logic [7:0]            status,        // Run status: I report ST_OK or the refusal reason here when done is high.
    input  logic                  tb_en,         // Testbench memory enable: an access on the rising edge, only while not busy.
    input  logic                  tb_we,         // Testbench memory write enable.
    input  logic [SCRATCH_AW-1:0] tb_addr,       // Testbench memory address.
    input  logic [31:0]           tb_wdata,      // Testbench memory write data.
    output logic [31:0]           tb_rdata,      // Testbench memory read data, valid the clock after tb_en.
    output logic                  b_we,          // Scratchpad write enable, as driven by the unit.
    output logic [SCRATCH_AW-1:0] b_addr,        // Scratchpad write address, as driven by the unit.
    output logic [31:0]           b_wdata,       // Scratchpad write data, as driven by the unit.
    output logic                  fpu_req_valid, // FPU request valid: I offer this clock's instruction here; if refused I retry its context later.
    output logic                  fpu_req_ready, // FPU request ready: I treat valid and ready together as acceptance on the rising edge.
    output logic [4:0]            fpu_req_op,    // FPU request operation.
    output logic [31:0]           fpu_req_a,     // FPU request operand A.
    output logic [31:0]           fpu_req_b,     // FPU request operand B.
    output logic [7:0]            fpu_req_tag,   // FPU request tag: I send the context number so I can return the result to its registers.
    output logic                  fpu_rsp_valid, // FPU result valid.
    output logic [7:0]            fpu_rsp_tag,   // FPU result tag: I use the returned context number to find the destination and next instruction.
    output logic                  cov_ds_busy,   // Coverage: the divider is taking no new work.
    output logic                  cov_old,       // Coverage: the FPU's age rule is holding requests back.
    output logic                  cov_full,      // Coverage: the CORDIC FIFO has no slot to reserve.
    output logic [5:0]            cov_pending,   // Coverage: points between start and finished.
    output logic [3:0]            cov_phase,     // Coverage: the unit's run phase, so a test can see a run was accepted.
    output logic [48*PROF_N-1:0]  prof           // Profile: one 48-bit clock count per category, all zero unless PROFILE is 1.
);
  logic                  m_en;
  logic [SCRATCH_AW-1:0] m_addr;
  logic [31:0]           m_rdata;
  logic [31:0]           fpu_rsp_result;
  logic [4:0]            fpu_rsp_flags;
  logic                  mem_en, mem_we;
  logic [SCRATCH_AW-1:0] mem_addr;

  assign mem_en   = busy ? m_en   : tb_en;
  assign mem_we   = !busy && tb_we;
  assign mem_addr = busy ? m_addr : tb_addr;
  assign tb_rdata = m_rdata;

  scratch_mem #(.AW(SCRATCH_AW)) u_mem (
      .clk, .en(mem_en), .we(mem_we), .addr(mem_addr), .wdata(tb_wdata), .rdata(m_rdata),
      .b_we, .b_addr, .b_wdata);

  evu_core #(.CONTEXTS(CONTEXTS)) u_evu (
      .clk, .rst, .start, .desc_addr, .busy, .done, .aux, .status,
      .m_en, .m_addr, .m_rdata, .b_we, .b_addr, .b_wdata,
      .fpu_req_valid, .fpu_req_ready, .fpu_req_op, .fpu_req_a, .fpu_req_b, .fpu_req_tag,
      .fpu_rsp_valid, .fpu_rsp_result, .fpu_rsp_flags, .fpu_rsp_tag);

  fpu #(.TW(8), .FD(FD)) u_fpu (
      .clk, .rst,
      .req_valid(fpu_req_valid), .req_ready(fpu_req_ready), .req_op(fpu_req_op),
      .req_a(fpu_req_a), .req_b(fpu_req_b), .req_tag(fpu_req_tag),
      .rsp_valid(fpu_rsp_valid), .rsp_result(fpu_rsp_result), .rsp_flags(fpu_rsp_flags), .rsp_tag(fpu_rsp_tag));

  assign cov_ds_busy = !u_fpu.ds_in_ready;
  assign cov_old     = u_fpu.f_old;
  assign cov_full    = !u_fpu.c_room || u_fpu.f_full;
  assign cov_pending = u_evu.pending;
  assign cov_phase   = 4'(u_evu.phase);

  // ---- where the clocks go ---------------------------------------------------
  // Every clock of a run lands in exactly one counter, so the counters add up to
  // the run's length, and the test checks that they do.
  //
  // While the unit runs points, I look at the FPU's request port, because it takes
  // at most one instruction per clock and so it's where the time is spent. On each
  // clock there are three possibilities: the FPU took an instruction, it refused
  // one, or nothing was on offer. A refusal says its own reason. An empty clock
  // doesn't, because that slot was lost six clocks earlier at S0, when no context
  // could be picked. So I work out the reason at S0 and send it down a six-stage
  // shift register, to arrive with the empty slot it explains.
  localparam int P_SETUP = 0, P_ARITH = 1, P_LOAD = 2, P_REF_DIV = 3, P_REF_CORDIC = 4, P_REF_OTHER = 5,
                 P_WAIT_DIV = 6, P_WAIT_CORDIC = 7, P_DEP_DIV = 8, P_DEP_CORDIC = 9, P_DEP_FAST = 10,
                 P_OUTPUT = 11, P_DRAIN = 12, P_FILL = 13, P_FINISH = 14;
  // evu_core's phase_t, in order. Its enum names are local to that module.
  localparam logic [3:0] PH_IDLE = 4'd0, PH_DESC = 4'd1, PH_CHECK = 4'd2, PH_PROG = 4'd3, PH_KONST = 4'd4, PH_RUN = 4'd5;

  generate
    if (PROFILE) begin : g_prof
      logic [47:0] count [PROF_N];
      logic [3:0]  slot [6];                     // why each S0 slot of the last six clocks was empty
      logic [3:0]  why, s0_why;                  // why this clock's S6 is empty; the reason being worked out at S0
      logic [31:0] wait_div, wait_cordic;        // contexts going round the retry queue, by what refused them
      logic [31:0] fly_div, fly_cordic;          // contexts with this kind of op inside the FPU
      logic        is_ds, is_cordic, s0_used, offered, took;
      logic [4:0]  ctx, back;

      // The op on offer, and which context it belongs to.
      assign is_ds     = (fpu_req_op == FOP_DIV) || (fpu_req_op == FOP_SQRT);
      assign is_cordic = (fpu_req_op >= FOP_SIN) && (fpu_req_op <= FOP_LN);
      assign offered   = fpu_req_valid;
      assign took      = fpu_req_valid && fpu_req_ready;
      assign ctx       = fpu_req_tag[4:0];
      assign back      = fpu_rsp_tag[4:0];

      // This mirrors the term in evu_core that decides whether S1 gets anything.
      assign s0_used = u_evu.take_t || u_evu.take_n || (u_evu.take_r && !(u_evu.rq_fin && u_evu.g_done));

      // Why nobody could be picked. No more points to start comes first, because
      // then the empty slots are the end of the run. After that, a slot held back
      // by the output side, a context going round on a refusal, or else every
      // context simply waiting for the result of its own last instruction. That
      // last case is a dependency stall, and I split it by what's in the FPU.
      always_comb begin
        if (s0_used)                                    s0_why = 4'(P_ARITH);    // not empty: never read
        else if (u_evu.g_done)                          s0_why = 4'(P_DRAIN);
        else if (u_evu.rq_v && u_evu.rq_wait)           s0_why = 4'(P_OUTPUT);
        else if (wait_div != '0)                        s0_why = 4'(P_WAIT_DIV);
        else if (wait_cordic != '0)                     s0_why = 4'(P_WAIT_CORDIC);
        else if (fly_div != '0)                         s0_why = 4'(P_DEP_DIV);
        else if (fly_cordic != '0)                      s0_why = 4'(P_DEP_CORDIC);
        else                                            s0_why = 4'(P_DEP_FAST);
      end
      assign why = slot[5];

      always_ff @(posedge clk) begin
        // Until the first S0 decision reaches S6, an empty slot is just the
        // pipeline filling.
        slot[0] <= (cov_phase == PH_RUN) ? s0_why : 4'(P_FILL);
        for (int k = 1; k < 6; k++) slot[k] <= slot[k - 1];

        if (took) begin
          wait_div[ctx]    <= 1'b0;
          wait_cordic[ctx] <= 1'b0;
          if (is_ds)     fly_div[ctx]    <= 1'b1;
          if (is_cordic) fly_cordic[ctx] <= 1'b1;
        end else if (offered) begin
          if (is_ds && cov_ds_busy) wait_div[ctx] <= 1'b1;
          else                      wait_cordic[ctx] <= 1'b1;
        end
        if (fpu_rsp_valid) begin
          fly_div[back]    <= 1'b0;
          fly_cordic[back] <= 1'b0;
        end

        // One category per busy clock. The first decision is which phase it is.
        case (cov_phase)
          PH_IDLE: ;
          PH_DESC, PH_CHECK, PH_PROG, PH_KONST: count[P_SETUP] <= count[P_SETUP] + 1'b1;
          PH_RUN: begin
            if (took && fpu_req_op == 5'd31)  count[P_LOAD]        <= count[P_LOAD] + 1'b1;
            else if (took)                    count[P_ARITH]       <= count[P_ARITH] + 1'b1;
            else if (offered && is_ds && cov_ds_busy)
                                              count[P_REF_DIV]     <= count[P_REF_DIV] + 1'b1;
            else if (offered && (cov_old || (is_cordic && cov_full)))
                                              count[P_REF_CORDIC]  <= count[P_REF_CORDIC] + 1'b1;
            else if (offered)                 count[P_REF_OTHER]   <= count[P_REF_OTHER] + 1'b1;
            else                              count[why]           <= count[why] + 1'b1;
          end
          default: count[P_FINISH] <= count[P_FINISH] + 1'b1;   // SUM, WRITE_F, FIN
        endcase

        if (start || rst) begin
          for (int k = 0; k < PROF_N; k++) count[k] <= '0;
          for (int k = 0; k < 6; k++) slot[k] <= 4'(P_FILL);
          wait_div <= '0;  wait_cordic <= '0;  fly_div <= '0;  fly_cordic <= '0;
        end
      end

      always_comb for (int k = 0; k < PROF_N; k++) prof[48*k +: 48] = count[k];
    end else begin : g_no_prof
      assign prof = '0;
    end
  endgenerate
endmodule
