// I keep the program on the board and send independent grid points through it.
// I call one partly evaluated point a context. Its tag follows the FPU request
// back to its own registers, so a long sine doesn't make every point wait.
// I describe the load, schedule, execute and write-back paths below in that order.
//
// Expression evaluation unit: runs a small program once per grid point.
//
// A program is a straight line of FPU instructions (no branches), so a point
// always finishes. docs/phase4-plan.md has the instruction set, and
// sw/mathaccel/evu_model.py predicts every result this module writes.
//
// A context is one point partway through the program. It owns 64 registers and
// has at most one instruction in flight, so the FPU tag only has to name the
// context. Many contexts run at once, which is how a slow FPU op (52 clocks inside
// CORDIC, plus the surrounding request/result registers) can hold up its own point and nobody else's.
//
// One instruction's trip through the pipeline, with one new instruction able to
// enter on every clock:
//
//   S0  pick a context: from the ready queue, the retry queue, or a new one
//   S1  read the instruction from program memory
//   S2  the instruction word comes out of the block RAM
//   S3  LOAD only: look up the table and add the base address to the index
//   S4  read both registers and, for LOAD, the scratchpad
//   S5  the block RAM outputs arrive, late in the clock, so I capture them
//   S6  offer the instruction to the FPU
//   ... the FPU works ...
//   W0  a result arrives; look up where it goes, using the tag
//   W1  write the register; put the context back in the ready queue
//
// S3 only matters for LOAD, but every instruction takes the same path, because
// then all of them reach the FPU in order and nothing has to choose between
// two arrivals.
//
// The FPU may refuse: the divider is busy, or one of its result rules is holding
// new work back. Nothing stalls behind a refusal. The instruction is dropped at
// S6 and its context goes to the retry queue to try again later. That means this
// pipeline has no stall signal at all. I break the FPU's "hold the request
// steady" rule on purpose here: it exists for callers that wait, and an offer
// that isn't accepted leaves no trace inside the FPU.
//
// Two queues, because two things can want one on the same clock: a result
// coming back at W1 and a refusal at S6. A FIFO built from LUT RAM has one write
// port, so each gets its own. Every context sits in at most one place, so a
// depth of 32 can't overflow.
//
// A finished context goes through the ready queue as well, marked fin. When it
// reaches the front it takes the next grid point, or stops if there are none.
// So there's no separate list of free contexts. At the start of a run the unused
// context numbers are handed out by a counter.
//
// The program counter travels with the context, through the queues and the
// table of instructions in flight, so it needs no table of its own.
//
// Registers are 2,048 words, 64 for each of 32 banks. Two reads and one write
// per clock come from two block RAM copies that take the same writes. Contexts
// use banks 0-30, and bank 31 holds the constants, so a source operand is one
// read of the same RAM either way. That is why there are 31 contexts and not 32.
//
// The accumulator is inside this module. The FPU and the scratchpad are outside,
// because kernel_engine shares them with the FPU kernel.
//
// A run: start with desc_addr, the unit copies the descriptor out of the
// scratchpad, checks it, copies the program and constants, runs every point,
// writes the results, and pulses done. In reduce mode the sum goes to r and its
// flags to f. In map mode r[i], f[i] are written for each point i in grid order,
// with i counting the last dimension fastest. aux is the OR of every point's
// flags.
//
// A descriptor comes from the host, so nothing in it is trusted. Before any
// instruction runs I check every field and every address span, and each
// instruction word as it is copied. A bad run writes nothing to the scratchpad:
// it ends with status set to why, and cmd_proc sends that as the reply. The order
// of the checks is part of the protocol, so docs/protocol.md lists it and the
// Python and C models follow it.
//
// A bad program can't hang this either: it has no branches, and the instruction
// at the end of the program length counts as last whether it says so or not.
module evu_core
  import accel_pkg::*;
#(
    parameter int CONTEXTS = EVU_CONTEXTS        // contexts in use, 1 to 31
) (
    input  logic                  clk,           // Clock: I update the registers on its rising edge; the board runs this at 100 MHz.
    input  logic                  rst,           // Reset: I sample this active-high signal on the clock edge to clear control state.
    input  logic                  start,         // Start: I accept this one-clock pulse in idle to begin a run.
    input  logic [SCRATCH_AW-1:0] desc_addr,     // Descriptor address: I sample this scratchpad word index with start, then copy the descriptor from it.
    output logic                  busy,          // Busy: I hold this high while this unit is occupied.
    output logic                  done,          // Done: I pulse this for one clock once the writes are committed and status is valid.
    output logic [31:0]           aux,           // Auxiliary result: I OR every point's instruction flags into this run-level summary.
    output logic [7:0]            status,        // Run status: I report ST_OK or the refusal reason here when done is high.
    // scratchpad port A, reads only
    output logic                  m_en,          // Memory read enable: I request m_addr on this rising edge when this is high.
    output logic [SCRATCH_AW-1:0] m_addr,        // Memory read address: I put a scratchpad word index here, not a byte address.
    input  logic [31:0]           m_rdata,       // Memory read data: I capture the registered scratchpad word after an enabled read.
    // scratchpad port B, writes only
    output logic                  b_we,          // Memory write enable: I commit b_wdata to b_addr on the rising edge when this is high.
    output logic [SCRATCH_AW-1:0] b_addr,        // Memory write address: I select the scratchpad word for the result or its flags here.
    output logic [31:0]           b_wdata,       // Memory write data: I put result bits or zero-extended exception flags here.
    // FPU request and result ports
    output logic                  fpu_req_valid, // FPU request valid: I offer this clock's instruction here; if refused I retry its context later.
    input  logic                  fpu_req_ready, // FPU request ready: I treat valid and ready together as acceptance on the rising edge.
    output logic [4:0]            fpu_req_op,    // FPU request operation: I select an arithmetic op, or the internal bit-preserving move for LOAD.
    output logic [31:0]           fpu_req_a,     // FPU request operand A.
    output logic [31:0]           fpu_req_b,     // FPU request operand B.
    output logic [7:0]            fpu_req_tag,   // FPU request tag: I send the context number so I can return the result to its registers.
    input  logic                  fpu_rsp_valid, // FPU result valid: I must capture the result on this clock because there is no response-ready port.
    input  logic [31:0]           fpu_rsp_result,// FPU result value.
    input  logic [4:0]            fpu_rsp_flags, // FPU result exception flags: invalid, divide by zero, overflow, underflow, inexact.
    input  logic [7:0]            fpu_rsp_tag    // FPU result tag: I use the returned context number to find the destination and next instruction.
);
  localparam logic [4:0] CONST_BANK = 5'd31;      // the bank that holds the constants

  localparam int QW  = 5 + 8 + 1;                 // queue entry: context, pc, fin
  localparam int OW  = SCRATCH_AW + 32 + 5;       // output entry: point, result, flags

  // ---- how I keep a run separate from loading and final writes ----
  typedef enum logic [3:0] {IDLE, DESC, CHECK, PROG, KONST, RUN, SUM, WRITE_F, FIN} phase_t;
  phase_t phase;
  logic   running;
  assign  running = (phase == RUN);
  assign  busy    = (phase != IDLE);

  // The descriptor, kept here for the whole run. cfg_m0..2 hold n - 1 for each
  // dimension, which is what the point generator compares with.
  logic                  cfg_reduce;
  logic [SCRATCH_AW-1:0] cfg_prog, cfg_const, cfg_r, cfg_f;
  logic [8:0]            cfg_prog_len;
  logic [7:0]            cfg_last_pc;
  logic [6:0]            cfg_const_len;
  logic [15:0]           cfg_m0, cfg_m1, cfg_m2;
  logic [SCRATCH_AW-1:0] tbl_base [8];            // 8, a power of two, so a 3-bit table number needs no extra logic
  logic [1:0]            tbl_dim  [8];

  // ---- how I copy the descriptor, program and constants ----
  // The scratchpad gives one word per clock. A read goes through three steps:
  // c1 the address is out, c2 the RAM is reading, c3 the word is in rd_q. I put
  // rd_q in the way for the same reason kernel_engine has its RC state: RAM data
  // arrives late in the clock and needs a clock of its own to go anywhere.
  logic [SCRATCH_AW-1:0] c_addr;
  logic [8:0]            c_left, c_idx;
  logic                  c1_v, c2_v, c3_v;
  logic [8:0]            c1_idx, c2_idx, c3_idx;
  logic [31:0]           rd_q;
  logic                  c_read, c_idle;
  assign c_read = (phase == DESC || phase == PROG || phase == KONST) && (c_left != '0);
  assign c_idle = (c_left == '0) && !c1_v && !c2_v && !c3_v;

  always_ff @(posedge clk) begin
    c1_v <= c_read;   c1_idx <= c_idx;
    c2_v <= c1_v;     c2_idx <= c1_idx;
    c3_v <= c2_v;     c3_idx <= c2_idx;
    rd_q <= m_rdata;
    if (rst) begin
      c1_v <= 1'b0;  c2_v <= 1'b0;  c3_v <= 1'b0;
    end
  end

  // The descriptor's fields, as the words go past. Addresses are kept as 16 bits,
  // so this is the only place that can see the upper bits are wrong. d_bad_arg and
  // d_bad_addr stay set until the next run.
  logic [2:0] c3_table;
  assign c3_table = 3'(c3_idx - 9'(EVU_DESC_TABLE));

  logic d_bad_arg, d_bad_addr, c3_bad_arg, c3_bad_addr, rd_high;
  assign rd_high = |rd_q[31:16];

  always_comb begin
    c3_bad_arg  = 1'b0;
    c3_bad_addr = 1'b0;
    if (c3_idx >= 9'(EVU_DESC_TABLE)) begin
      c3_bad_arg = (rd_q[EVU_DESC_DIM_SHIFT +: 2] == 2'd3) || (|rd_q[31:EVU_DESC_DIM_SHIFT + 2]);
    end else begin
      case (int'(c3_idx))
        EVU_DESC_MODE:      c3_bad_arg = |rd_q[31:1];
        EVU_DESC_PROG_LEN:  c3_bad_arg = (rd_q == 32'd0) || (rd_q > 32'(EVU_PROGRAM_LEN));
        EVU_DESC_CONST_LEN: c3_bad_arg = rd_q > 32'(EVU_CONSTANTS);
        EVU_DESC_GRID, EVU_DESC_GRID + 1, EVU_DESC_GRID + 2:
                            c3_bad_arg = rd_high || (rd_q[15:0] == 16'd0);
        EVU_DESC_PROG, EVU_DESC_CONST, EVU_DESC_R, EVU_DESC_F:
                            c3_bad_addr = rd_high;
        default: ;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    if (phase == IDLE) begin
      d_bad_arg  <= 1'b0;
      d_bad_addr <= 1'b0;
    end else if (c3_v && phase == DESC) begin
      d_bad_arg  <= d_bad_arg  || c3_bad_arg;
      d_bad_addr <= d_bad_addr || c3_bad_addr;
    end
  end

  always_ff @(posedge clk) begin
    if (c3_v && phase == DESC) begin
      if (c3_idx >= 9'(EVU_DESC_TABLE)) begin
        tbl_base[c3_table] <= rd_q[SCRATCH_AW-1:0];
        tbl_dim[c3_table]  <= rd_q[EVU_DESC_DIM_SHIFT +: 2];
      end else begin
        case (int'(c3_idx))
          EVU_DESC_MODE:      cfg_reduce    <= rd_q[0];
          EVU_DESC_PROG:      cfg_prog      <= rd_q[SCRATCH_AW-1:0];
          EVU_DESC_PROG_LEN:  begin
            cfg_prog_len <= rd_q[8:0];
            cfg_last_pc  <= rd_q[7:0] - 8'd1;
          end
          EVU_DESC_CONST:     cfg_const     <= rd_q[SCRATCH_AW-1:0];
          EVU_DESC_CONST_LEN: cfg_const_len <= rd_q[6:0];
          EVU_DESC_GRID:      cfg_m0        <= rd_q[15:0] - 16'd1;
          EVU_DESC_GRID + 1:  cfg_m1        <= rd_q[15:0] - 16'd1;
          EVU_DESC_GRID + 2:  cfg_m2        <= rd_q[15:0] - 16'd1;
          EVU_DESC_R:         cfg_r         <= rd_q[SCRATCH_AW-1:0];
          EVU_DESC_F:         cfg_f         <= rd_q[SCRATCH_AW-1:0];
          default: ;
        endcase
      end
    end
  end

  // ---- CHECK: how I reject impossible shapes before execution ----
  // First I need the number of points, n0 * n1 * n2. That runs once per run, so
  // it doesn't deserve a DSP block: shift and add, 16 clocks for each multiply.
  // mul_lost remembers a 1 shifted off the top of mul_a, and mul_over a sum that
  // no longer fits. Either way the true product is far over the limit.
  //
  //   chk_i 0      load n0 and n1          chk_i 17     load the product and n2
  //   chk_i 1-16   first multiply          chk_i 18-33  second multiply
  //   chk_i 34     judge the point count
  //   chk_i 35-44  one address span per clock
  //   chk_i 46     the last answer is in: refuse the run or go on
  localparam logic [5:0] CHK_LOAD2 = 6'd17, CHK_JUDGE = 6'd34, CHK_SPAN = 6'd35, CHK_TABLE = 6'd39,
                         CHK_SPANS_END = 6'd44, CHK_DECIDE = 6'd46;
  logic [5:0]  chk_i;
  logic [32:0] mul_a, mul_s;
  logic [15:0] mul_b;
  logic [33:0] mul_sum;
  logic        mul_lost, mul_over;
  assign mul_sum = {1'b0, mul_s} + {1'b0, mul_a};

  logic chk_many, chk_map_big;
  assign chk_many    = mul_over || (mul_s[EVU_MAX_POINTS_LOG2] && (|mul_s[EVU_MAX_POINTS_LOG2 - 1:0]));
  assign chk_map_big = chk_many || (mul_s > (33'd1 << SCRATCH_AW));   // one word each, at the very most

  // A span is first + (count - 1), which keeps every count in 16 bits. If that
  // addition carries, the span runs off the end of the scratchpad. Taking one off
  // a count is safe here: only an empty constant pool has a count of 0, and that
  // becomes a span of one word that lies inside the scratchpad anyway. In a reduce
  // run r and f are single words, and chk_span stays 0.
  logic [SCRATCH_AW-1:0] chk_x, chk_y, chk_x_d, chk_y_d, chk_span;
  logic [2:0]            chk_table;
  logic                  chk_go, chk_bad_arg, chk_bad_addr;
  logic [16:0]           chk_end;
  assign chk_table = 3'(chk_i - CHK_TABLE);
  assign chk_end   = {1'b0, chk_x} + {1'b0, chk_y};

  always_comb begin
    case (chk_i)
      CHK_SPAN:        begin chk_x_d = cfg_prog;  chk_y_d = SCRATCH_AW'(cfg_last_pc); end
      CHK_SPAN + 6'd1: begin chk_x_d = cfg_const;
                             chk_y_d = (cfg_const_len == '0) ? '0 : SCRATCH_AW'(cfg_const_len - 7'd1); end
      CHK_SPAN + 6'd2: begin chk_x_d = cfg_r;     chk_y_d = chk_span; end
      CHK_SPAN + 6'd3: begin chk_x_d = cfg_f;     chk_y_d = chk_span; end
      default: begin
        chk_x_d = tbl_base[chk_table];
        case (tbl_dim[chk_table])
          2'd0:    chk_y_d = cfg_m0;
          2'd1:    chk_y_d = cfg_m1;
          default: chk_y_d = cfg_m2;
        endcase
      end
    endcase
  end

  always_ff @(posedge clk) begin
    chk_i  <= (phase == CHECK) ? chk_i + 6'd1 : '0;
    chk_x  <= chk_x_d;
    chk_y  <= chk_y_d;
    chk_go <= (phase == CHECK) && (chk_i >= CHK_SPAN) && (chk_i <= CHK_SPANS_END);
    if (chk_go && chk_end[16]) chk_bad_addr <= 1'b1;

    if (chk_i == '0 || chk_i == CHK_LOAD2) begin
      mul_a    <= (chk_i == '0) ? 33'(cfg_m0) + 33'd1 : mul_s;
      mul_b    <= ((chk_i == '0) ? cfg_m1 : cfg_m2) + 16'd1;
      mul_s    <= '0;
      mul_lost <= 1'b0;
      if (chk_i == '0) mul_over <= 1'b0;
    end else if (chk_i < CHK_JUDGE) begin
      if (mul_b[0]) begin
        mul_s <= mul_sum[32:0];
        if (mul_sum[33] || mul_lost) mul_over <= 1'b1;
      end
      mul_lost <= mul_lost || mul_a[32];
      mul_a    <= mul_a << 1;
      mul_b    <= mul_b >> 1;
    end else if (chk_i == CHK_JUDGE) begin
      chk_bad_arg  <= d_bad_arg || chk_many;
      chk_bad_addr <= d_bad_addr || (!cfg_reduce && chk_map_big);
      chk_span     <= cfg_reduce ? '0 : SCRATCH_AW'(mul_s) - 1'b1;
    end
  end

  // Each instruction word as it goes past on its way into program memory. I check
  // every word up to the length, even those after a last bit, so one rule covers
  // the whole span: every word there must be a real instruction. A LOAD's a field
  // is a table number, and every other source that names a constant must name one
  // that exists. Unary ops ignore b, but I hold it to the same rule, because that
  // needs no list of which ops are unary.
  logic [4:0] p_op;
  logic [6:0] p_a, p_b;
  logic       p_load, p_bad, i_bad;
  assign p_op   = rd_q[EVU_OP_SHIFT +: 5];
  assign p_a    = rd_q[EVU_A_SHIFT +: 7];
  assign p_b    = rd_q[EVU_B_SHIFT +: 7];
  assign p_load = (p_op == 5'(EVU_OP_LOAD));
  assign p_bad  = (!p_load && p_op >= 5'(FPU_OP_COUNT))
               || (p_load ? (p_a >= 7'(EVU_TABLES))
                          : (p_a[6] && 7'(p_a[5:0]) >= cfg_const_len))
               || (p_b[6] && 7'(p_b[5:0]) >= cfg_const_len)
               || (|rd_q[5:0]);                   // the spare bits must stay zero for future use

  always_ff @(posedge clk) begin
    if (phase != PROG)    i_bad <= 1'b0;
    else if (c3_v && p_bad) i_bad <= 1'b1;
  end

  // ---- where I keep the bytecode for all contexts to share ----
  // One block RAM: words are written once while copying and read once per clock.
  logic        s1_v;
  logic [4:0]  s1_ctx;
  logic [7:0]  s1_pc;
  logic [31:0] prog_mem [EVU_PROGRAM_LEN];
  logic [31:0] ins;                               // the word read for S2

  always @(posedge clk) begin
    if (c3_v && phase == PROG) prog_mem[8'(c3_idx)] <= rd_q;
    ins <= prog_mem[s1_pc];
  end

  // ---- why I keep ready work and refused work in separate queues ----
  logic          rq_push, tq_push, rq_pop, tq_pop, rq_v, tq_v;
  logic [QW-1:0] rq_in, tq_in, rq_head, tq_head;
  logic          queue_clear;
  assign queue_clear = (phase == KONST);          // everything per-run restarts here
  logic  fifo_rst;                                // a named wire: Icarus gives X for an expression in a port
  assign fifo_rst = rst || queue_clear;

  evu_fifo #(.W(QW)) u_ready (.clk, .rst(fifo_rst), .push(rq_push), .in(rq_in),
                              .pop(rq_pop), .head(rq_head), .nonempty(rq_v));
  evu_fifo #(.W(QW)) u_retry (.clk, .rst(fifo_rst), .push(tq_push), .in(tq_in),
                              .pop(tq_pop), .head(tq_head), .nonempty(tq_v));

  // ---- how I walk the grid without expanding it into an array ----
  // An odometer: the last dimension turns fastest, like numpy's unravel_index.
  logic [15:0]           g_i0, g_i1, g_i2;
  logic [SCRATCH_AW-1:0] g_point;
  logic                  g_done;                  // every point has been started
  logic [5:0]            fresh;                   // contexts handed out so far
  logic [5:0]            pending;                 // points started and not yet finished
  logic                  take_point, one_finished;

  // In map mode a finished point waits in the output FIFO, and its context is
  // free to start another. So the limit that protects that FIFO is on points,
  // not contexts: at most 32 can be between start and written. A reduce run
  // never reaches it, because a finished point leaves straight away.
  logic room;
  assign room = (pending != 6'd32);

  always_ff @(posedge clk) begin
    if (fifo_rst) begin
      g_i0    <= '0;
      g_i1    <= '0;
      g_i2    <= '0;
      g_point <= '0;
      g_done  <= 1'b0;
      pending <= '0;
    end else begin
      pending <= pending + 6'(take_point) - 6'(one_finished);
      if (take_point) begin
        g_point <= g_point + 1'b1;
        if (g_i2 != cfg_m2) begin
          g_i2 <= g_i2 + 1'b1;
        end else begin
          g_i2 <= '0;
          if (g_i1 != cfg_m1) begin
            g_i1 <= g_i1 + 1'b1;
          end else begin
            g_i1 <= '0;
            if (g_i0 != cfg_m0) g_i0 <= g_i0 + 1'b1;
            else                g_done <= 1'b1;
          end
        end
      end
    end
  end

  // ---- S0: how I choose the next context fairly ----
  // When both queues have work they take turns, so contexts waiting for the
  // divider can't be shut out, and can't take every slot either. A new context
  // only starts when nothing else wants the clock. At the front of the ready
  // queue, a finished context waits if the output side is full; that clears
  // within two clocks.
  logic       rq_fin, rq_wait, take_r, take_t, take_n, turn;
  logic [4:0] rq_ctx, tq_ctx;
  logic [7:0] rq_pc, tq_pc;
  assign {rq_ctx, rq_pc, rq_fin} = rq_head;
  assign {tq_ctx, tq_pc}         = tq_head[QW-1:1];

  assign rq_wait = rq_fin && !g_done && !room;
  assign take_r  = running && rq_v && !rq_wait && (!tq_v || !turn);
  assign take_t  = running && tq_v && !take_r;
  assign take_n  = running && !rq_v && !tq_v && !g_done && room && (fresh != 6'(CONTEXTS));

  assign rq_pop  = take_r;
  assign tq_pop  = take_t;
  assign take_point = (take_r && rq_fin && !g_done) || take_n;

  // A new point's grid indices and number go into context tables at S1.
  logic                  s1_start;
  logic [47:0]           s1_index;                // {i2, i1, i0}
  logic [SCRATCH_AW-1:0] s1_point;

  always_ff @(posedge clk) begin
    s1_v     <= running && (take_t || take_n || (take_r && !(rq_fin && g_done)));
    s1_ctx   <= take_t ? tq_ctx : take_n ? fresh[4:0] : rq_ctx;
    s1_pc    <= take_t ? tq_pc  : take_n ? 8'd0       : rq_pc;
    s1_start <= running && take_point;
    s1_index <= {g_i2, g_i1, g_i0};
    s1_point <= g_point;
    turn     <= take_r;
    if (fifo_rst)     fresh <= '0;
    else if (take_n)  fresh <= fresh + 1'b1;
    if (rst) begin
      s1_v     <= 1'b0;
      s1_start <= 1'b0;
    end
  end

  // ---- where I keep the point indices and flags for each context ----
  // Small memories built from LUTs: they read without a clock, so a table can be
  // looked up and used within one stage.
  logic [47:0]           idx_mem [32];            // each context's grid indices, {i2, i1, i0}
  logic [SCRATCH_AW-1:0] pt_mem  [32];            // each context's point number

  always_ff @(posedge clk) begin
    if (s1_start) begin
      idx_mem[s1_ctx] <= s1_index;
      pt_mem[s1_ctx]  <= s1_point;
    end
  end

  // ---- S2 and S3: how I decode and address a table LOAD ----
  logic        s2_v;
  logic [4:0]  s2_ctx;
  logic [7:0]  s2_pc;
  logic        s3_v, s3_last;
  logic [4:0]  s3_ctx, s3_op;
  logic [7:0]  s3_pc;
  logic [5:0]  s3_dst;
  logic [6:0]  s3_a, s3_b;

  logic [2:0]            s3_table;
  logic [1:0]            s3_dim;
  logic [47:0]           s3_indices;
  logic [15:0]           s3_index;
  assign s3_table   = s3_a[2:0];
  assign s3_dim     = tbl_dim[s3_table];
  assign s3_indices = idx_mem[s3_ctx];
  always_comb begin
    case (s3_dim)
      2'd0:    s3_index = s3_indices[15:0];
      2'd1:    s3_index = s3_indices[31:16];
      default: s3_index = s3_indices[47:32];
    endcase
  end

  logic        s4_v, s4_last;
  logic [4:0]  s4_ctx, s4_op;
  logic [7:0]  s4_pc;
  logic [5:0]  s4_dst;
  logic [10:0] ra_addr, rb_addr;                  // {bank, register}

  // A source field is a register of this context, or a constant in bank 31.
  function automatic logic [10:0] reg_addr(input logic [4:0] ctx, input logic [6:0] src); // ctx: context number. src: source field.
    return {src[6] ? CONST_BANK : ctx, src[5:0]};
  endfunction

  always_ff @(posedge clk) begin
    s2_v <= s1_v;  s2_ctx <= s1_ctx;  s2_pc <= s1_pc;

    s3_v     <= s2_v;
    s3_ctx   <= s2_ctx;
    s3_pc    <= s2_pc;
    s3_op    <= ins[EVU_OP_SHIFT +: 5];
    s3_last  <= ins[EVU_LAST_SHIFT] || (s2_pc == cfg_last_pc);
    s3_dst   <= ins[EVU_DST_SHIFT +: 6];
    s3_a     <= ins[EVU_A_SHIFT +: 7];
    s3_b     <= ins[EVU_B_SHIFT +: 7];

    s4_v     <= s3_v;
    s4_ctx   <= s3_ctx;
    s4_pc    <= s3_pc;
    s4_op    <= s3_op;
    s4_last  <= s3_last;
    s4_dst   <= s3_dst;
    ra_addr  <= reg_addr(s3_ctx, s3_a);
    rb_addr  <= reg_addr(s3_ctx, s3_b);

    // Port A has one user at a time: the copy steps, then LOAD instructions.
    m_en   <= c_read || (s3_v && s3_op == 5'(EVU_OP_LOAD));
    m_addr <= running ? tbl_base[s3_table] + SCRATCH_AW'(s3_index) : c_addr;

    if (rst) begin
      s2_v <= 1'b0;  s3_v <= 1'b0;  s4_v <= 1'b0;  m_en <= 1'b0;
    end
  end

  // ---- where I keep each context separate from the constant bank ----
  logic        rf_we;
  logic [10:0] rf_waddr;
  logic [31:0] rf_wdata;
  logic [31:0] ra_mem [2048];
  logic [31:0] rb_mem [2048];
  logic [31:0] ra_q, rb_q;

  // Zeros, as with the scratchpad: a unary op reads a b that may never have been
  // written, and ignores it, but simulators with X would not.
  initial for (int i = 0; i < 2048; i++) begin
    ra_mem[i] = '0;
    rb_mem[i] = '0;
  end

  always @(posedge clk) begin
    if (rf_we) ra_mem[rf_waddr] <= rf_wdata;
    ra_q <= ra_mem[ra_addr];
  end
  always @(posedge clk) begin
    if (rf_we) rb_mem[rf_waddr] <= rf_wdata;
    rb_q <= rb_mem[rb_addr];
  end

  // ---- S5 and S6: how I capture operands and offer an instruction ----
  logic        s5_v, s5_last, s5_load;
  logic [4:0]  s5_ctx, s5_op;
  logic [7:0]  s5_pc;
  logic [5:0]  s5_dst;

  logic        s6_v, s6_last;
  logic [4:0]  s6_ctx, s6_op;
  logic [7:0]  s6_pc;
  logic [5:0]  s6_dst;
  logic [31:0] s6_a, s6_b;

  always_ff @(posedge clk) begin
    s5_v   <= s4_v;    s5_ctx <= s4_ctx;  s5_pc <= s4_pc;
    s5_op  <= s4_op;   s5_last <= s4_last;  s5_dst <= s4_dst;
    s5_load <= (s4_op == 5'(EVU_OP_LOAD));

    s6_v   <= s5_v;    s6_ctx <= s5_ctx;  s6_pc <= s5_pc;
    s6_op  <= s5_op;   s6_last <= s5_last;  s6_dst <= s5_dst;
    s6_a   <= s5_load ? m_rdata : ra_q;
    s6_b   <= rb_q;
    if (rst) begin
      s5_v <= 1'b0;  s6_v <= 1'b0;
    end
  end

  assign fpu_req_valid = s6_v;
  assign fpu_req_op    = s6_op;
  assign fpu_req_a     = s6_a;
  assign fpu_req_b     = s6_b;
  assign fpu_req_tag   = 8'(s6_ctx);

  logic fire;
  assign fire    = s6_v && fpu_req_ready;
  assign tq_push = s6_v && !fpu_req_ready;
  assign tq_in   = {s6_ctx, s6_pc, 1'b0};

  // Instructions in flight: what to do with the result when it comes back. The
  // tag says which context, and a context has only one in flight.
  logic [5:0] fl_dst_mem [32];
  logic       fl_last_mem [32];
  logic [7:0] fl_npc_mem [32];
  always_ff @(posedge clk) begin
    if (fire) begin
      fl_dst_mem[s6_ctx]  <= s6_dst;
      fl_last_mem[s6_ctx] <= s6_last;
      fl_npc_mem[s6_ctx]  <= s6_pc + 8'd1;
    end
  end

  // ---- W0 and W1: how I return a tagged result to its context ----
  // Each context ORs together the flags of its instructions. The table has no
  // reset: the first instruction of a point (next pc is 1) ignores what's in it.
  logic [4:0]            flag_mem [32];
  logic [4:0]            r_ctx;
  logic [7:0]            r_npc;
  assign r_ctx = fpu_rsp_tag[4:0];
  assign r_npc = fl_npc_mem[r_ctx];

  logic                  w_v, w_last;
  logic [4:0]            w_ctx, w_flags;
  logic [7:0]            w_npc;
  logic [SCRATCH_AW-1:0] w_point;
  logic [5:0]            w_dst;
  logic [31:0]           w_result;

  always_ff @(posedge clk) begin
    w_v      <= fpu_rsp_valid && !rst;
    w_ctx    <= r_ctx;
    w_dst    <= fl_dst_mem[r_ctx];
    w_last   <= fl_last_mem[r_ctx];
    w_npc    <= r_npc;
    w_point  <= pt_mem[r_ctx];
    w_result <= fpu_rsp_result;
    w_flags  <= fpu_rsp_flags | ((r_npc == 8'd1) ? 5'b0 : flag_mem[r_ctx]);
    if (w_v) flag_mem[w_ctx] <= w_flags;
  end

  // The register file's one write port takes results while running, and
  // constants while copying, which is what puts constants in bank 31.
  logic k_we;
  assign k_we     = c3_v && (phase == KONST);
  assign rf_we    = w_v || k_we;
  assign rf_waddr = k_we ? {CONST_BANK, 6'(c3_idx)} : {w_ctx, w_dst};
  assign rf_wdata = k_we ? rd_q : w_result;

  // A finished context carries pc 0, ready for its next point.
  assign rq_push = w_v;
  assign rq_in   = {w_ctx, w_last ? 8'd0 : w_npc, w_last};

  // ---- reduce: where I feed one completed term to the exact sum ----
  logic        acc_finish, acc_valid;
  logic [31:0] acc_result;
  logic [4:0]  acc_flags;
  logic [4:0]  sum_flags;
  logic        acc_in_valid, acc_busy;
  assign acc_in_valid = w_v && w_last && cfg_reduce;

  fp_accum u_accum (
      .clk, .rst, .clear(queue_clear), .in_valid(acc_in_valid), .in_value(w_result),
      .finish(acc_finish), .busy(acc_busy),
      .out_valid(acc_valid), .out_result(acc_result), .out_flags(acc_flags));

  // ---- map: how I buffer results for two scratchpad writes per point ----
  logic          o_push, o_pop, o_v, o_second;
  logic [OW-1:0] o_in, o_head;
  logic [SCRATCH_AW-1:0] o_point;
  logic [31:0]   o_result;
  logic [4:0]    o_flags;
  assign o_push = w_v && w_last && !cfg_reduce;
  assign o_in   = {w_point, w_result, w_flags};
  assign o_pop  = o_v && o_second;
  assign {o_point, o_result, o_flags} = o_head;

  evu_fifo #(.W(OW)) u_out (.clk, .rst(fifo_rst), .push(o_push), .in(o_in),
                            .pop(o_pop), .head(o_head), .nonempty(o_v));

  // A point is finished when it leaves the accumulator's input or its flag word
  // has been written.
  assign one_finished = cfg_reduce ? acc_in_valid : o_pop;

  // ---- how I move from loading through execution to completion ----
  always_ff @(posedge clk) begin
    done       <= 1'b0;
    b_we       <= 1'b0;
    acc_finish <= 1'b0;
    if (w_v && w_last) aux[4:0] <= aux[4:0] | w_flags;

    if (c_read) begin
      c_addr <= c_addr + 1'b1;
      c_left <= c_left - 1'b1;
      c_idx  <= c_idx + 1'b1;
    end

    case (phase)
      IDLE: if (start) begin
        phase  <= DESC;
        c_addr <= desc_addr;
        c_left <= 9'(EVU_DESC_WORDS);
        c_idx  <= '0;
        aux    <= '0;
        status <= ST_OK;
      end

      DESC: if (c_idle) phase <= CHECK;

      // Argument errors win over address errors, and a program is only read once
      // the descriptor is good. Nothing has been written yet, so a refused run
      // just goes straight to FIN.
      CHECK: if (chk_i == CHK_DECIDE) begin
        if (chk_bad_arg) begin
          status <= ST_BAD_ARG;
          phase  <= FIN;
        end else if (chk_bad_addr) begin
          status <= ST_BAD_ADDR;
          phase  <= FIN;
        end else begin
          phase  <= PROG;
          c_addr <= cfg_prog;
          c_left <= cfg_prog_len;
          c_idx  <= '0;
        end
      end

      PROG: if (c_idle) begin
        if (i_bad) begin
          status <= ST_BAD_ARG;
          phase  <= FIN;
        end else begin
          phase  <= KONST;
          c_addr <= cfg_const;
          c_left <= 9'(cfg_const_len);
          c_idx  <= '0;
        end
      end

      KONST: if (c_idle) phase <= RUN;

      // Map writes run alongside everything else here. When every point has
      // finished, a map run is complete and a reduce run has its sum to finish.
      RUN: begin
        o_second <= o_v && !o_second;
        if (o_v) begin
          b_we    <= 1'b1;
          b_addr  <= (o_second ? cfg_f : cfg_r) + o_point;
          b_wdata <= o_second ? {27'b0, o_flags} : o_result;
        end
        if (g_done && pending == '0) begin
          if (cfg_reduce) begin
            phase      <= SUM;
            acc_finish <= 1'b1;
          end else begin
            phase <= FIN;
          end
        end
      end

      SUM: if (acc_valid) begin
        phase     <= WRITE_F;
        sum_flags <= acc_flags;
        b_we      <= 1'b1;
        b_addr    <= cfg_r;
        b_wdata   <= acc_result;
      end

      WRITE_F: begin
        phase   <= FIN;
        b_we    <= 1'b1;
        b_addr  <= cfg_f;
        b_wdata <= {27'b0, sum_flags};
      end

      default: begin                              // FIN
        phase <= IDLE;
        done  <= 1'b1;
      end
    endcase

    if (rst) begin
      phase    <= IDLE;
      c_left   <= '0;
      o_second <= 1'b0;
      aux      <= '0;
      status   <= ST_OK;
    end
  end
endmodule

// A first-in, first-out queue of 32 entries in LUT RAM. head shows the oldest
// entry without a clock, and pop removes it. Callers never push when full,
// because of how many entries can exist at once. The pointers have an extra bit,
// which tells a full queue from an empty one when they are equal otherwise.
module evu_fifo #(
    parameter int W = 8                          // entry width in bits
) (
    input  logic         clk,                    // Clock: I update the registers on its rising edge; the board runs this at 100 MHz.
    input  logic         rst,                    // Reset: I sample this active-high signal on the clock edge to clear control state.
    input  logic         push,                   // Push: I store the entry on this rising edge.
    input  logic [W-1:0] in,                     // Input entry.
    input  logic         pop,                    // Pop: I remove the oldest entry on this rising edge.
    output logic [W-1:0] head,                   // Head: I expose the oldest entry here while nonempty is high.
    output logic         nonempty                // Nonempty: I assert this while the queue holds at least one entry.
);
  logic [W-1:0] mem [32];
  logic [5:0]   wp, rp;
  assign head     = mem[rp[4:0]];
  assign nonempty = (wp != rp);

  always_ff @(posedge clk) begin
    if (push) mem[wp[4:0]] <= in;
    if (rst) begin
      wp <= '0;
      rp <= '0;
    end else begin
      if (push) wp <= wp + 1'b1;
      if (pop)  rp <= rp + 1'b1;
    end
  end
endmodule
