// Pipelined fp32 divide by reciprocal multiplication (experiment E).
//
// Instead of finding quotient bits one or two at a time, I estimate 1/b with a
// small table and one Newton-Raphson-style refinement, multiply a by that
// reciprocal, and then fix the last bit exactly with a remainder check. Every
// step is a multiply or an add with its own pipeline registers, so a new
// divide can enter every clock, like experiment B, but the work is done by
// the FPGA's DSP multipliers instead of 14 or 27 subtractor stages.
//
// The numbers, with X and D the 24-bit significands (leading 1 at bit 23) and
// d = D / 2^23 in [1, 2):
//   1. y0 = SEED[top 9 bits of d after the leading 1] ~ 1/d. The table entry is
//      2/(lo + hi) for the 1/512-wide interval [lo, hi), which minimizes the
//      worst |e| over it; rounded to 14 fraction bits. Then |e| < 2^-9.97,
//      where e = 1 - d*y0.
//   2. y1 = y0 (1 + e + e^2). Exactly, 1/d - y1 = e^3/d, so this third-order
//      step cubes the error instead of squaring it as plain Newton-Raphson
//      y0 (1 + e) would: about 2^-30 instead of 2^-20.
//   3. Q0 = floor(X * y1 * 2^3), an estimate of t = X * 2^26 / D.
//   4. R0 = X * 2^26 - Q0 * D, and then I pick Q0 - 1, Q0 or Q0 + 1, whichever
//      leaves 0 <= R < D. That gives exactly Q = floor(X * 2^26 / D) and
//      sticky = (R != 0), the same two things the baseline's 27-step loop
//      produces, so the same packing and rounding give bit-identical results.
// Step 4 is only safe if |Q0 - t| < 1. I checked that for every one of the
// 2^23 possible divisors with the exact integer arithmetic below
// (sw/tests/test_div_multiplicative.py): the scaled reciprocal Y1t is never
// more than 6.1 units from 2^55 / D, against the 32 units that |Q0 - t| < 1
// allows when X < 2^24.
//
// Fixed-point widths (each value is an integer; "scale 2^-n" means the real
// number is the integer times 2^-n):
//   Y0   14 bits unsigned, scale 2^-14         SEED entry
//   P    38 bits, scale 2^-37                  D * Y0
//   E    39 bits signed, scale 2^-37           2^37 - P, exactly e
//   Eh   18 bits signed, scale 2^-26           E shifted right 11 (floor)
//   Es   25 bits signed, scale 2^-33           E shifted right 4 (floor)
//   S    25 bits signed, scale 2^-33           Es + (Eh * Eh) / 2^19, i.e. e + e^2
//   Y1t  32 bits unsigned, scale 2^-32         (Y0 * 2^33 + Y0 * S) / 2^15, i.e. y1
//   Q0   27 bits                               (X * Y1t) / 2^29
//   R0   27 bits signed                        low 27 bits of X * 2^26 - Q0 * D
// Every shift right is a floor. R0 only needs its low 27 bits because the true
// value lies in [-D, 2D), well inside +-2^26.
//
// Multiplier sizes, against a DSP48E1's 25 x 18 signed multiplier:
// D*Y0 24x14, Eh*Eh 18x18, Y0*S 14x25 take one DSP each; X*Y1t 24x32 and
// Q0*D 27x24 take two each, so about 7 DSPs. Each multiply has a register
// after it and another after that, which Vivado folds into the DSP's own
// product and output registers.
module fp_div_core
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,                   // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic          rst,                   // Reset: active high; clears the valid bit of every stage.
    input  logic          in_valid,              // Input valid: a divide enters the pipeline on this clock edge.
    input  logic [31:0]   in_a,                  // Input operand A: floating-point dividend.
    input  logic [31:0]   in_b,                  // Input operand B: floating-point divisor.
    input  logic [TW-1:0] in_tag,                // Input request tag: carried through unchanged.
    output logic          out_valid,             // Output valid: a finished divide is on the outputs for this one clock.
    output logic [31:0]   out_result,            // Output result: 32-bit encoded quotient.
    output logic [4:0]    out_flags,             // Output exception flags: bits 4..0 mean invalid, divide by zero, overflow, underflow and inexact.
    output logic [TW-1:0] out_tag                // Output request tag of the finished divide.
);
  // Clocks from P's output to the correction stage's output; the side-band
  // delay line below must be exactly this long.
  localparam int ARITH = 16;

  // ---- C: capture and classify (as in the baseline's IDLE state) ------------
  logic                 c_v, c_sign, c_special;
  logic [TW-1:0]        c_tag;
  logic [31:0]          c_sp_res;
  logic [4:0]           c_sp_fl;
  logic [23:0]          c_ma, c_mb;
  logic [5:0]           c_lza, c_lzb;
  logic signed [EW-1:0] c_ea, c_eb;

  always_ff @(posedge clk) begin
    c_v <= rst ? 1'b0 : in_valid;
    c_tag   <= in_tag;
    c_ma    <= mant24(in_a);
    c_mb    <= mant24(in_b);
    c_lza   <= lzc24(mant24(in_a));
    c_lzb   <= lzc24(mant24(in_b));
    c_ea    <= exp_eff(in_a);
    c_eb    <= exp_eff(in_b);
    c_sign  <= in_a[31] ^ in_b[31];
    c_special <= 1'b1;
    c_sp_fl   <= '0;
    c_sp_res  <= '0;
    if (is_nan(in_a) || is_nan(in_b)) begin
      c_sp_res <= CANON_NAN;
      c_sp_fl  <= (is_snan(in_a) || is_snan(in_b)) ? FFLAG_NV : '0;
    end else if ((is_inf(in_a) && is_inf(in_b)) || (is_zero(in_a) && is_zero(in_b))) begin
      c_sp_res <= CANON_NAN;  c_sp_fl <= FFLAG_NV;
    end else if (is_inf(in_a)) begin
      c_sp_res <= {in_a[31] ^ in_b[31], POS_INF[30:0]};
    end else if (is_inf(in_b) || is_zero(in_a)) begin
      c_sp_res <= {in_a[31] ^ in_b[31], 31'b0};
    end else if (is_zero(in_b)) begin
      c_sp_res <= {in_a[31] ^ in_b[31], POS_INF[30:0]};
      c_sp_fl  <= FFLAG_DZ;
    end else begin
      c_special <= 1'b0;
    end
  end

  // ---- P: normalize ---------------------------------------------------------
  logic        p_v;
  logic [23:0] p_x, p_d;                       // X and D, leading 1 at bit 23
  localparam int SBW = 1 + 1 + 32 + 5 + TW + EW;
  logic [SBW-1:0] sb_line [ARITH+1];           // sign, special, special answer, tag, exponent

  always_ff @(posedge clk) begin
    logic signed [EW-1:0] ea_n, eb_n;
    ea_n = c_ea - EW'(c_lza);
    eb_n = c_eb - EW'(c_lzb);
    p_v  <= rst ? 1'b0 : c_v;
    p_x  <= c_ma << c_lza;
    p_d  <= c_mb << c_lzb;
    sb_line[0] <= {c_sign, c_special, c_sp_res, c_sp_fl, c_tag, EW'(ea_n - eb_n + EW'(127))};
    // The side band only waits; a delay line with no reset or enable becomes
    // shift-register LUTs. (One always_ff writes the whole array: SystemVerilog
    // doesn't let two processes share a variable written with a loop index.)
    for (int k = 1; k <= ARITH; k++) sb_line[k] <= sb_line[k-1];
  end

  // ---- the reciprocal seed table ----------------------------------------------
  // SEED[i] = round(2^14 * 2 / (lo + hi)) with lo = 1 + i/512, hi = lo + 1/512,
  // which is round(2^24 / (1025 + 2i)). The fraction is never exactly one half
  // (the denominator is odd), so "add one half and floor" is exact rounding.
  // The loop runs when the design is built, not in hardware: it only fills in
  // the ROM's contents.
  logic [13:0] seed [512];
  initial begin
    for (int i = 0; i < 512; i++) seed[i] = 14'((((1 << 25) / (1025 + 2 * i)) + 1) >> 1);
  end

  // ---- the arithmetic pipeline ------------------------------------------------
  // Each register's name says which stage it belongs to; s<n> is n clocks after
  // P. X and D ride along beside the numbers that need them later. Index k of
  // each delay line is stage s<k>; index 0 is unused (P's own registers p_*).
  logic                v   [ARITH+1];           // valid bit per stage
  logic [23:0]         x_l [ARITH+1];           // X delayed
  logic [23:0]         d_l [ARITH+1];           // D delayed
  logic [13:0]         y0_l [ARITH+1];          // Y0 delayed (valid from s1)
  logic [37:0]         s2_p, s3_p;              // D * Y0
  logic signed [17:0]  s4_eh;
  logic signed [24:0]  s4_es, s5_es, s6_es;     // Es waits beside the square
  logic [35:0]         s5_e2, s6_e2;            // Eh * Eh, never negative
  logic signed [24:0]  s7_s;                    // e + e^2
  logic signed [39:0]  s8_ys, s9_ys;            // Y0 * S
  logic [31:0]         s10_y1;                  // y1 at scale 2^-32
  logic [55:0]         s11_a, s12_a;            // X * Y1t
  logic [26:0]         s13_q0, s14_q0, s15_q0, s16_q0;
  logic [50:0]         s14_qd, s15_qd;          // Q0 * D
  logic signed [26:0]  s16_r0;
  logic [26:0]         s17_q;                   // corrected quotient
  logic                s17_st;                  // sticky: remainder nonzero

  always_ff @(posedge clk) begin
    v[1]   <= rst ? 1'b0 : p_v;
    x_l[1] <= p_x;
    d_l[1] <= p_d;
    for (int k = 2; k <= ARITH; k++) begin
      v[k]   <= rst ? 1'b0 : v[k-1];
      x_l[k] <= x_l[k-1];
      d_l[k] <= d_l[k-1];
    end
    for (int k = 2; k <= ARITH; k++) y0_l[k] <= y0_l[k-1];
    // s1: read the table with the 9 bits after the divisor's leading 1.
    y0_l[1] <= seed[p_d[22:14]];
    // s2, s3: D * Y0.
    s2_p <= d_l[1] * y0_l[1];
    s3_p <= s2_p;
    // s4: e = 1 - d*y0, then the two truncated copies.
    begin
      logic signed [38:0] e;
      e = 39'sd1 <<< 37;
      e = e - $signed({1'b0, s3_p});
      s4_eh <= 18'(e >>> 11);
      s4_es <= 25'(e >>> 4);
    end
    // s5, s6: e^2, with Es waiting beside it.
    s5_e2 <= 36'(s4_eh * s4_eh);
    s6_e2 <= s5_e2;
    s5_es <= s4_es;
    s6_es <= s5_es;
    // s7: S = e + e^2, both at scale 2^-33.
    s7_s  <= s6_es + 25'(s6_e2 >> 19);
    // s8, s9: Y0 * S.
    s8_ys <= $signed({1'b0, y0_l[7]}) * s7_s;
    s9_ys <= s8_ys;
    // s10: y1 = y0 + y0 * S at scale 2^-47, kept at scale 2^-32.
    begin
      logic signed [48:0] y1;
      y1 = $signed({2'b00, y0_l[9], 33'b0}) + 49'(s9_ys);
      s10_y1 <= 32'(y1 >>> 15);
    end
    // s11, s12: X * Y1t.
    s11_a <= x_l[10] * s10_y1;
    s12_a <= s11_a;
    // s13: Q0, the estimate of floor(X * 2^26 / D).
    s13_q0 <= 27'(s12_a >> 29);
    // s14, s15: Q0 * D, of which only the low 27 bits matter.
    s14_qd <= s13_q0 * d_l[13];
    s15_qd <= s14_qd;
    s14_q0 <= s13_q0;
    s15_q0 <= s14_q0;
    // s16: R0 = X * 2^26 - Q0 * D, modulo 2^27. X * 2^26 modulo 2^27 is just
    // X's lowest bit moved to bit 26.
    s16_r0 <= $signed({x_l[15][0], 26'b0} - s15_qd[26:0]);
    s16_q0 <= s15_q0;
  end

  // s17: pick Q0 - 1, Q0 or Q0 + 1 so that 0 <= R < D. D for this divide is
  // in d_l[16], beside s16_r0.
  always_ff @(posedge clk) begin
    logic signed [26:0] dd;
    dd = $signed({3'b000, d_l[ARITH]});
    if (s16_r0 < 0) begin
      s17_q  <= s16_q0 - 1'b1;
      s17_st <= (s16_r0 + dd) != 0;
    end else if (s16_r0 >= dd) begin
      s17_q  <= s16_q0 + 1'b1;
      s17_st <= (s16_r0 - dd) != 0;
    end else begin
      s17_q  <= s16_q0;
      s17_st <= s16_r0 != 0;
    end
  end

  // ---- K, R1, R2: pack and round, as in the baseline ---------------------------
  logic                 s17_v;
  logic [SBW-1:0]       s17_sb;
  logic                 k_v, r1_v;
  logic [25:0]          k_sig;
  logic signed [EW-1:0] k_exp, n_exp;
  logic [SBW-EW-1:0]    k_sb, r1_sb;
  round_mid_t           r1_mid;

  always_ff @(posedge clk) begin
    s17_v  <= rst ? 1'b0 : v[ARITH];
    s17_sb <= sb_line[ARITH];
  end
  assign n_exp = s17_sb[EW-1:0];

  always_ff @(posedge clk) begin
    k_v  <= rst ? 1'b0 : s17_v;
    r1_v <= rst ? 1'b0 : k_v;
    out_valid <= rst ? 1'b0 : r1_v;
    if (s17_q[26]) begin                      // quotient in [1, 2)
      k_sig <= {s17_q[26:2], s17_q[1] | s17_q[0] | s17_st};
      k_exp <= n_exp;
    end else begin                            // quotient in (0.5, 1)
      k_sig <= {s17_q[25:1], s17_q[0] | s17_st};
      k_exp <= n_exp - 1'b1;
    end
    k_sb   <= s17_sb[SBW-1:EW];
    r1_mid <= round_pre(k_sb[SBW-EW-1], k_exp, k_sig);
    r1_sb  <= k_sb;
    begin
      logic [36:0] rp;
      rp = round_post(r1_mid);
      out_result <= r1_sb[SBW-EW-2] ? r1_sb[TW+5 +: 32] : rp[31:0];
      out_flags  <= r1_sb[SBW-EW-2] ? r1_sb[TW +: 5]    : rp[36:32];
      out_tag    <= r1_sb[TW-1:0];
    end
  end
endmodule


// fp32 divide for the FPU (experiment E): the multiplicative pipeline in
// fp_div_core plus the same promised-place result queue as experiment B.
//
// fp_div_core can't pause, so I keep up to DQ finished divides here and only
// accept a divide when a queue place can be promised to it: cnt counts divides
// still in the core plus divides stored. out_urgent rises when the oldest
// stored result has waited AGE clocks; fpu.sv then stops new requests until it
// leaves. That is the same arrangement as experiment B's queue, so the two
// pipelined candidates differ only in their arithmetic.
module fp_div
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW  = 8,
    parameter int DQ  = 32,                      // result queue depth, a power of 2
    parameter int AGE = 8                        // clocks a result may wait before out_urgent rises
) (
    input  logic          clk,                   // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic          rst,                   // Reset: active high; empties the pipeline and the queue.
    input  logic          in_valid,              // Input valid: a divide is accepted only when input ready is also high on the rising edge.
    output logic          in_ready,              // Input ready: high while a queue place can still be promised to a new divide.
    input  logic [31:0]   in_a,                  // Input operand A: floating-point dividend.
    input  logic [31:0]   in_b,                  // Input operand B: floating-point divisor.
    input  logic [TW-1:0] in_tag,                // Input request tag: identifier carried beside the operands so the caller can match the result.
    output logic          out_valid,             // Output valid: the oldest finished divide is shown on the result outputs.
    output logic          out_urgent,            // Output urgent: the shown result has waited AGE clocks; the FPU must stop new requests until it leaves.
    input  logic          out_ack,               // Output acknowledgement: high takes the shown result on the rising edge.
    output logic [31:0]   out_result,            // Output result: 32-bit encoded quotient; meaningful only while output valid is high.
    output logic [4:0]    out_flags,             // Output exception flags: bits 4..0 mean invalid, divide by zero, overflow, underflow and inexact.
    output logic [TW-1:0] out_tag                // Output request tag: unchanged identifier of the divide that produced this result.
);
  localparam int QA = $clog2(DQ);

  logic          fire, core_v;
  logic [31:0]   core_r;
  logic [4:0]    core_f;
  logic [TW-1:0] core_t;
  assign fire = in_valid && in_ready;

  fp_div_core #(.TW(TW)) u_core (
      .clk, .rst, .in_valid(fire), .in_a, .in_b, .in_tag,
      .out_valid(core_v), .out_result(core_r), .out_flags(core_f), .out_tag(core_t));

  // A circular buffer in LUT RAM, read without a clock like fpu.sv's CORDIC
  // FIFO. Each entry stores the low 8 bits of a clock counter from when it
  // arrived, so the oldest entry's age is now - stamp, modulo 256.
  logic [32+5+TW+8-1:0] q_mem [DQ];
  logic [QA-1:0]        q_wp, q_rp;
  logic [QA:0]          q_cnt;               // results stored, 0..DQ
  logic [QA:0]          cnt;                 // places promised: in the core or stored, 0..DQ
  logic [7:0]           now, q_ts, q_age;
  logic                 pop;
  assign in_ready  = (cnt != (QA+1)'(DQ));
  assign out_valid = (q_cnt != '0);
  assign pop       = out_valid && out_ack;
  assign {out_result, out_flags, out_tag, q_ts} = q_mem[q_rp];
  assign q_age     = now - q_ts;

  always_ff @(posedge clk) begin
    if (core_v) q_mem[q_wp] <= {core_r, core_f, core_t, now};
    if (rst) begin
      q_wp <= '0;  q_rp <= '0;  q_cnt <= '0;  cnt <= '0;  now <= '0;
      out_urgent <= 1'b0;
    end else begin
      now <= now + 1'b1;
      if (core_v) q_wp <= q_wp + 1'b1;
      if (pop)    q_rp <= q_rp + 1'b1;
      q_cnt <= q_cnt + (QA+1)'(core_v) - (QA+1)'(pop);
      cnt   <= cnt + (QA+1)'(fire) - (QA+1)'(pop);
      // Registered so the compare stays off req_ready's path; one clock early
      // so it rises on the clock the oldest result turns AGE.
      out_urgent <= out_valid && !pop && (q_age >= 8'(AGE - 1));
    end
  end
endmodule
