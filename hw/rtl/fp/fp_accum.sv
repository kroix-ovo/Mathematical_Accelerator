// I put this after the expression unit because points can finish out of order.
// I need their order to leave the answer unchanged. I add finite fp32 terms as
// integers in units of 2^-149, keep special-value state separately, and round
// only when the run is done. The default is ten 32-bit segments, or 320 bits.
//
// Exact accumulator: adds fp32 values with no rounding until the very end.
//
// Why it exists: adding many values one at a time in fp32 rounds on every add,
// and once the running total is large, small terms get rounded away. On my class
// integrals that reached about 17,800 ULP at a million points, and the answer
// changed with the order of the terms (docs/phase4-plan.md). Here the total is a
// long fixed-point number, so every add is exact and order can't matter.
//
// How the number is held: every finite fp32 value is a whole multiple of 2^-149,
// because value = +-m * 2^shift * 2^-149, with m the 24-bit significand and
// shift = max(e, 1) - 1 in 0..253. So the total is one big two's complement
// integer, in units of 2^-149. The largest term ends at bit 277 and the top
// segment leaves room for 2^42 of them, far more than the EVU can run.
//
// Adding at 100 MHz: a carry can't cross 320 bits in one clock, so the number
// is split into N segments of 32 bits, each with its own carry flip-flop. A
// carry out of segment s is added into segment s+1 on the next clock. Until the
// carries have settled the segments don't show the total, but none is lost:
//   total = sum over s of (seg[s] + cy[s] * 2^32) * 2^(32 s)      modulo 2^320
// That is what lets a new term go in on every clock.
//
// A term covers at most two segments. A negative term is made two's complement
// first, so the segments above it each add all ones (its sign extension). That
// way each segment does one plain add with a carry in, and a carry is always
// 0 or 1. I avoid a separate subtract and a two-way carry that way.
//
//   stage 1  capture the term, note special values, negate if negative
//   stage 2  shift by the low 5 bits of shift; decode which segments it covers
//   stage 3  every segment adds its part and the carry from the segment below
//
// Finishing: after finish, three steps with fixed lengths, so the time from
// finish to out_valid is always LAT_FINISH clocks:
//   SETTLE  two clocks for the last term to reach the segments, then N clocks
//           adding nothing. A carry moves up one segment per clock and a new
//           one can only come from an old one, so N clocks clear them all.
//   SCAN    read the segments from lowest to highest through one 32-bit port,
//           taking the magnitude as they go (the sign is the top bit). This
//           keeps only what rounding needs: the highest nonzero segment, the
//           one below it, and whether anything lower was nonzero.
//   NORM    find the leading 1, shift it to the top, round once, as the FPU's
//           other units do (round_pre, round_post).
//
// Infinities and NaN can't go in a fixed-point register, so they set state bits.
// The results are what repeated ADD gives: +inf and -inf together, or an sNaN,
// give NaN with NV. An exact zero total is +0. A subnormal total is always exact
// (it's a multiple of 2^-149), so it raises neither UF nor NX.
//
// Using it: pulse clear, present terms with in_valid (any gaps), then pulse
// finish, on or after the clock of the last term. out_valid pulses LAT_FINISH
// clocks later. Terms presented between finish and the next clear are ignored.
//
// sw/mathaccel/evu_model.py (exact_sum) predicts every result bit for bit.
module fp_accum
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int N = 10          // segments of 32 bits
) (
    input  logic        clk,      // Clock: I update the registers on its rising edge; the board runs this at 100 MHz.
    input  logic        rst,      // Reset: I sample this active-high signal on the clock edge to clear control state.
    input  logic        clear,    // Clear: I pulse this for one clock to start a fresh sum at zero.
    input  logic        in_valid, // Input valid: I add in_value on this clock when the accumulator is accepting terms.
    input  logic [31:0] in_value, // Input value: I receive one raw fp32 term here, including any infinity or NaN.
    input  logic        finish,   // Finish: I pulse this with or after the last term to settle carries and round the sum.
    output logic        busy,     // Busy: I hold this high while this unit is occupied.
    output logic        out_valid,  // Output valid: I pulse this for one clock when the rounded result and flags are ready.
    output logic [31:0] out_result, // Output result: I return the exact total rounded once to fp32 here.
    output logic [4:0]  out_flags   // Output exception flags: I report invalid, divide by zero, overflow, underflow and inexact in bits 4..0.
);
  localparam int SA = $clog2(N);          // bits needed to name a segment

  typedef enum logic [2:0] {RUN, SETTLE, SCAN, NORM1, NORM2, NORM3, DONE} state_t;
  state_t state;
  logic [SA:0] count;                     // clocks left in SETTLE, next segment in SCAN

  assign busy = (state != RUN);

  // ---- stage 1: capture, special values, negate ---------------------------
  // The term is +-m. I hold it as a signed 25-bit number so the shift in stage 2
  // can extend its sign. A special value adds nothing and only sets state bits.
  logic        t1_v;
  logic [7:0]  t1_shift;
  logic signed [24:0] t1_m;
  logic        seen_pinf, seen_ninf, seen_nan, seen_snan;
  logic        special;
  logic [23:0] m;
  assign special = (in_value[30:23] == 8'hFF);
  assign m       = mant24(in_value);

  always_ff @(posedge clk) begin
    t1_v <= 1'b0;
    if (rst || clear) begin
      seen_pinf <= 1'b0;
      seen_ninf <= 1'b0;
      seen_nan  <= 1'b0;
      seen_snan <= 1'b0;
    end else if (in_valid && state == RUN) begin
      t1_v     <= !special;
      t1_shift <= (in_value[30:23] == 8'h00) ? 8'd0 : (in_value[30:23] - 8'd1);
      t1_m     <= in_value[31] ? -25'(m) : 25'(m);
      if (is_inf(in_value) && !in_value[31]) seen_pinf <= 1'b1;
      if (is_inf(in_value) &&  in_value[31]) seen_ninf <= 1'b1;
      if (is_nan(in_value))                  seen_nan  <= 1'b1;
      if (is_snan(in_value))                 seen_snan <= 1'b1;
    end
  end

  // ---- stage 2: shift, and decide which segments the term covers -----------
  // shift[7:5] picks the segment of the term's low part and shift[4:0] is the
  // shift within it. A 25-bit number shifted by up to 31 fits two segments.
  logic         t2_v;
  logic [63:0]  t2_word;
  logic [N-1:0] t2_lo, t2_hi, t2_fill;    // for each segment: gets low word, high word, sign fill

  always_ff @(posedge clk) begin
    logic signed [63:0] extended;
    extended = 64'(t1_m);                 // sign extends, since t1_m is signed
    t2_v    <= t1_v && !(rst || clear);
    t2_word <= extended << t1_shift[4:0];
    for (int s = 0; s < N; s++) begin
      t2_lo[s]   <= (s == int'(t1_shift[7:5]));
      t2_hi[s]   <= (s == int'(t1_shift[7:5]) + 1);
      t2_fill[s] <= (s >  int'(t1_shift[7:5]) + 1) && t1_m[24];
    end
  end

  // ---- stage 3: the segments ----------------------------------------------
  logic [31:0]  seg [N];
  logic [N-1:0] cy;                       // carry out of each segment, waiting to go up
  logic [N-1:0] cy_in;
  assign cy_in = {cy[N-2:0], 1'b0};

  for (genvar s = 0; s < N; s++) begin : g_seg
    // This segment's part of the term. Below the term it is 0, and above a
    // negative term it is all ones, the sign extension.
    logic [31:0] part;
    logic [32:0] sum;
    always_comb begin
      part = '0;
      if (t2_v) begin
        if (t2_lo[s])        part = t2_word[31:0];
        else if (t2_hi[s])   part = t2_word[63:32];
        else if (t2_fill[s]) part = '1;
      end
    end
    assign sum = {1'b0, seg[s]} + {1'b0, part} + 33'(cy_in[s]);

    always_ff @(posedge clk) begin
      if (rst || clear) begin
        seg[s] <= '0;
        cy[s]  <= 1'b0;
      end else begin
        {cy[s], seg[s]} <= sum;
      end
    end
  end

  // ---- SCAN: magnitude, and what rounding needs ----------------------------
  // Negating a two's complement number from the low end: invert every word and
  // add 1 at the bottom, carrying upward. neg_c is that carry.
  logic        negative;
  assign negative = seg[N-1][31];

  logic [31:0] rd;                        // the segment read last clock
  logic        rd_v;                      // rd holds a segment to process
  logic        neg_c;
  logic [31:0] prev;                      // magnitude of the segment below the one in rd
  logic        below;                     // anything nonzero lower than prev
  logic [31:0] top_hi, top_lo;            // highest nonzero segment so far, and the one below it
  logic        top_sticky;                // anything nonzero lower than top_lo
  logic [SA:0] top_i;                     // which segment top_hi is
  logic        top_v;                     // some segment was nonzero
  logic [SA:0] rd_i;

  logic [32:0] mag;
  assign mag = negative ? {1'b0, ~rd} + 33'(neg_c) : {1'b0, rd};

  // ---- NORM: leading 1, then round ----------------------------------------
  logic [5:0]           n_lz;
  logic [31:0]          n_hi, n_lo;
  logic                 n_sticky;
  logic signed [EW-1:0] n_exp;
  logic [25:0]          n2_sig;
  logic signed [EW-1:0] n2_exp;
  round_mid_t           n3_mid;

  // ---- control ------------------------------------------------------------
  always_ff @(posedge clk) begin
    logic [36:0] rp;
    out_valid <= 1'b0;
    rd_v      <= 1'b0;
    if (rst || clear) begin
      state <= RUN;
    end else begin
      case (state)
        RUN: if (finish) begin
          state <= SETTLE;
          count <= (SA+1)'(N + 2);
        end

        SETTLE: begin
          count <= count - 1'b1;
          if (count == 1) begin
            state      <= SCAN;
            count      <= '0;
            neg_c      <= 1'b1;
            prev       <= '0;
            below      <= 1'b0;
            top_v      <= 1'b0;
            top_sticky <= 1'b0;
          end
        end

        SCAN: begin
          // One segment per clock through one read path. The last read is
          // processed on the clock after count reaches N.
          if (count != (SA+1)'(N)) begin
            rd    <= seg[SA'(count)];
            rd_i  <= count;
            rd_v  <= 1'b1;
            count <= count + 1'b1;
          end
          if (rd_v) begin
            neg_c <= mag[32];
            prev  <= mag[31:0];
            below <= below | (prev != '0);
            if (mag[31:0] != '0) begin
              top_hi     <= mag[31:0];
              top_lo     <= prev;
              top_sticky <= below;
              top_i      <= rd_i;
              top_v      <= 1'b1;
            end
          end else if (count == (SA+1)'(N)) begin
            state <= NORM1;
          end
        end

        // Bit p of top_hi is worth 2^(32 * top_i + p - 149), so its biased
        // exponent is 32 * top_i + p - 22, with p = 31 - lz.
        NORM1: begin
          n_lz     <= lzc32(top_hi);
          n_hi     <= top_hi;
          n_lo     <= top_lo;
          n_sticky <= top_sticky;
          n_exp    <= EW'(top_i) * EW'(32) + EW'(9) - EW'(lzc32(top_hi));
          state    <= NORM2;
        end

        NORM2: begin
          logic [63:0] sh;
          sh      = {n_hi, n_lo} << n_lz;
          n2_sig <= {sh[63:39], (sh[38:0] != '0) | n_sticky};
          n2_exp <= n_exp;
          state  <= NORM3;
        end

        NORM3: begin
          n3_mid <= round_pre(negative, n2_exp, n2_sig);
          state  <= DONE;
        end

        // The result. Only finite totals use the rounded value.
        default: begin
          rp        = round_post(n3_mid);
          out_valid <= 1'b1;
          state     <= RUN;
          if (seen_nan || (seen_pinf && seen_ninf)) begin
            out_result <= CANON_NAN;
            out_flags  <= (seen_snan || (seen_pinf && seen_ninf)) ? FFLAG_NV : 5'b0;
          end else if (seen_pinf || seen_ninf) begin
            out_result <= {seen_ninf, POS_INF[30:0]};
            out_flags  <= '0;
          end else if (!top_v) begin
            out_result <= '0;                    // an exact zero total is +0
            out_flags  <= '0;
          end else begin
            out_result <= rp[31:0];
            out_flags  <= rp[36:32];
          end
        end
      endcase
    end
  end
endmodule
