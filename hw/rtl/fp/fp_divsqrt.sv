// One pipelined unit for fp32 divide and square root (experiment S3).
//
// fp_div (my reciprocal divider, experiment E) and the multiplicative square
// root (experiment S2) are built the same way: a seed table, a multiply by the
// seed, an error term and its square, a refining multiply, a final multiply and
// a remainder check. Here they share one pipeline and one result queue. Every
// clock, the operation in a stage is either a divide or a square root, and a
// bit beside it (sq) picks which numbers that stage's multiplier and adder
// see. I can mix roots and divides on consecutive clocks; sharing doesn't
// mean I finish one whole operation before starting the other. My FPU only
// accepts one request per clock, so one combined issue lane matches it today.
// Queue pressure and result-port collisions can still stall that lane.
// I save a second queue and some multipliers versus independent E plus S2,
// and keep the mixed CORDIC bound at 128 instead of their 160 clocks.
//
// The arithmetic of each operation is exactly the one in its own experiment,
// with the same widths and floors, so the results are bit-identical to fp_div
// (experiment E) and to S2's fp_sqrt. I only re-timed the two schedules so the
// shared steps line up. (sw/tests/test_div_multiplicative.py and
// sw/tests/test_sqrt_multiplicative.py check the two datapaths for every
// divisor and every normalized square-root significand/exponent-parity class.)
// Those are exhaustive integer-model checks. My RTL regressions separately
// check rounding, flags, subnormals, special values, tags, and mixed traffic.
// I don't treat passing the integer model alone as proof of the whole FPU.
//
// My path through the module is:
//   request -> C classify -> P normalize -> s1..s19 estimate/correct
//           -> K pack -> R1/R2 round -> result queue -> FPU result register
// I keep the operation bit and tag beside the math so a root and a divide
// can sit in different stages without borrowing each other's metadata.
// The free-port latency is 26 clocks at the FPU output for either operation.
// That's a latency, not the acceptance interval: I can accept one combined
// DIV-or-SQRT request per clock while the queue has an unreserved slot.
//
// The shared schedule, s<n> being n clocks after P:
//   s1        seed table: 1/d (14 bits) or 1/sqrt(a) (16 bits)
//   s2, s3    M1: D * Y0                       | na * Y0 = S0
//   s4, s5    M2: (idle)                       | S0 * Y0 = T
//   s6        e = 1 - d*y0                     | e = 1 - a*y0^2
//   s7, s8    M3: Eh * Eh                      | Eh * Eh
//   s9        S = e + e^2                      | C = e/2 + 3e^2/8
//   s10, s11  M4: Y0 * S                       | (S0 >> 16) * C
//   s12       y1                               | R0
//   s13, s14  M5: X * Y1t                      | R0 * R0 (low 29 bits)
//   s15       Q0                               | rem = N - R0^2
//   s16, s17  M6: Q0 * D                       | (idle)
//   s18       R0 = X * 2^26 - Q0 * D           | (rem waits)
//   s19       correct by -1, 0 or +1: against D | against 2R0 + 1
// M2 and M6 each serve one operation only; M1, M3, M4 and M5 serve both, with
// their inputs picked by sq. A divide takes two clocks longer than in fp_div,
// and a square root three longer than in S2. My 31 EVU contexts hide most of
// that latency in the measured workloads, though dipole_power does pick up
// about 0.1 clock per point from the two extra divide clocks.
//
// I use X and D for normalized 24-bit dividend/divisor significands, with
// their leading 1 at bit 23. For a root I call that significand na, and p
// is the normalized unbiased exponent's parity. My exact integer targets are
//   divide: Q = floor(X * 2^26 / D), sticky = (remainder != 0)
//   root:   R = isqrt(N), N = na << (27+p), sticky = (N - R*R != 0).
// The estimate only gets me close. The final remainder comparison is what
// makes these targets exact before I use the common rounding functions.
//
// My scale notes (an integer at scale 2^-n represents integer * 2^-n):
//                 divide                         square root
// Y0              14 bits, 2^-14                  16 bits, 2^-16
// M1              D*Y0, 2^-37                     na*Y0, 2^-(39-p)
// E before cuts   signed, 2^-37                   signed, 2^-55
// Eh              E>>>11, 2^-26                   E>>>30, 2^-25
// linear          E>>>4 gives e at 2^-33           E>>>16 gives e/2 at 2^-40
// factor          e + e^2 at 2^-33                 e/2 + 3e^2/8 at 2^-40
// s12 value       Y1t at 2^-32                     integer estimate R0
// I floor signed shifts with >>>; I don't silently round those intermediates.
// Register widths are part of the arithmetic, not just implementation detail.
// The model tests keep the same cuts and check that the required values fit.
module fp_divsqrt_core
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,                   // I update registers on this rising edge; the board runs at 100 MHz.
    input  logic          rst,                   // I clear stage validity on reset; old payload bits are then ignored.
    input  logic          in_valid,              // I take this operation on the edge; the wrapper has reserved its result slot.
    input  logic          in_sqrt,               // I select sqrt(A) with 1 and A/B with 0, and delay that choice beside the math.
    input  logic [31:0]   in_a,                  // I read A as the dividend or radicand.
    input  logic [31:0]   in_b,                  // I read B as the divisor; I ignore it for roots.
    input  logic [TW-1:0] in_tag,                // I carry this request identifier through unchanged.
    output logic          out_valid,             // I present a core result for this clock; the reserved queue slot must take it.
    output logic [31:0]   out_result,            // I return the binary32 quotient or root.
    output logic [4:0]    out_flags,             // I return flags [4:0] as invalid, divide-by-zero, overflow, underflow, inexact.
    output logic [TW-1:0] out_tag,               // I return the tag belonging to this operation.
    output logic          busy_div,              // I report divides in the arithmetic stages for the overlap probe.
    output logic          busy_sqrt              // I report roots in the arithmetic stages for the overlap probe.
);
  // Clocks from P's output to the stage before the correction; the side-band
  // delay line below must be exactly this long.
  localparam int ARITH = 18;

  // ---- C: capture and classify ------------------------------------------------
  // The divide classification is fp_div's, and the square-root one is fp_sqrt's;
  // sq picks which one counts. I keep special answers on the normal timing
  // path too: invalid arithmetic in an unused estimate stage never becomes
  // the answer, because the delayed special bit selects sp_res and sp_fl.
  // I test zero before the negative-root case so sqrt(-0) keeps its sign.
  logic                 c_v, c_sq, c_sign, c_special;
  logic [TW-1:0]        c_tag;
  logic [31:0]          c_sp_res;
  logic [4:0]           c_sp_fl;
  logic [23:0]          c_ma, c_mb;
  logic [5:0]           c_lza, c_lzb;
  logic signed [EW-1:0] c_ea, c_eb;

  always_ff @(posedge clk) begin
    c_v <= rst ? 1'b0 : in_valid;
    c_sq    <= in_sqrt;
    c_tag   <= in_tag;
    c_ma    <= mant24(in_a);
    c_mb    <= mant24(in_b);
    c_lza   <= lzc24(mant24(in_a));
    c_lzb   <= lzc24(mant24(in_b));
    c_ea    <= exp_eff(in_a);
    c_eb    <= exp_eff(in_b);
    c_sign  <= in_sqrt ? 1'b0 : (in_a[31] ^ in_b[31]);
    c_special <= 1'b1;
    c_sp_fl   <= '0;
    c_sp_res  <= '0;
    if (in_sqrt) begin
      if (is_nan(in_a)) begin
        c_sp_res <= CANON_NAN;  c_sp_fl <= is_snan(in_a) ? FFLAG_NV : '0;
      end else if (is_zero(in_a)) begin
        c_sp_res <= in_a;                           // sqrt(+-0) = +-0
      end else if (in_a[31]) begin
        c_sp_res <= CANON_NAN;  c_sp_fl <= FFLAG_NV;  // negative input
      end else if (is_inf(in_a)) begin
        c_sp_res <= POS_INF;
      end else begin
        c_special <= 1'b0;
      end
    end else begin
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
  end

  // ---- P: normalize -------------------------------------------------------------
  // For a divide, opA is the divisor D and x is the dividend X. For a square
  // root, opA is the radicand's significand na and par is its exponent's
  // parity. opA is what the seed table and M1 look at in both cases.
  logic        p_v, p_sq, p_par;
  logic [23:0] p_x, p_opa;
  localparam int SBW = 1 + 1 + 32 + 5 + TW + EW;
  logic [SBW-1:0] sb_line [ARITH+1];           // sign, special, special answer, tag, exponent

  always_ff @(posedge clk) begin
    logic signed [EW-1:0] ea_n, eb_n, e_unb, ex;
    ea_n  = c_ea - EW'(c_lza);
    eb_n  = c_eb - EW'(c_lzb);
    e_unb = ea_n - EW'(127);
    ex    = c_sq ? EW'(EW'(127) + (e_unb >>> 1)) : EW'(ea_n - eb_n + EW'(127));
    p_v   <= rst ? 1'b0 : c_v;
    p_sq  <= c_sq;
    p_par <= e_unb[0];
    p_x   <= c_ma << c_lza;
    p_opa <= c_sq ? (c_ma << c_lza) : (c_mb << c_lzb);
    sb_line[0] <= {c_sign, c_special, c_sp_res, c_sp_fl, c_tag, ex};
    // The side band only waits; a delay line with no reset or enable becomes
    // shift-register LUTs. I only reset validity, so old payload bits after
    // reset are harmless bubbles. I must keep this delay aligned with s19;
    // changing a math stage without its metadata would mix unrelated requests.
    for (int k = 1; k <= ARITH; k++) sb_line[k] <= sb_line[k-1];
  end

  // ---- the two seed tables (fp_div's and S2's, unchanged) ---------------------
  // DSEED[i] = round(2^24 / (1025 + 2i)) ~ 2^14 / d at the middle of interval i.
  // SSEED[p, i] = round(sqrt(2^42 / ((p + 1)(1025 + 2i)))) ~ 2^16 / sqrt(a).
  // I still have two sets of table contents: 512 divide seeds and 1,024 root
  // seeds. I share their scheduled read/selection, not one universal formula.
  // Root parity selects [1,2) or [2,4), then the next nine bits pick an interval.
  // These loops run during elaboration to fill constant ROM contents. I am
  // not building a runtime integer divider or a slow root loop for the seed.
  function automatic logic [15:0] sseed_entry(int p, int i);
    longint unsigned x, r, b;
    x = (64'd1 << 44) / (64'(p + 1) * 64'(1025 + 2 * i));
    r = 0;
    for (int k = 20; k >= 0; k--) begin        // isqrt, one bit at a time
      b = r | (64'd1 << k);
      if (b * b <= x) r = b;
    end
    return 16'((r + 1) >> 1);
  endfunction

  logic [13:0] dseed [512];
  logic [15:0] sseed [1024];
  initial begin
    for (int i = 0; i < 512; i++) dseed[i] = 14'((((1 << 25) / (1025 + 2 * i)) + 1) >> 1);
    for (int p = 0; p < 2; p++)
      for (int i = 0; i < 512; i++) sseed[p * 512 + i] = sseed_entry(p, i);
  end

  // ---- the arithmetic pipeline ------------------------------------------------
  // I read index k as stage s<k>, not as a separate operation's array index.
  // Nonblocking assignments read the old register values on this clock edge,
  // so the right-hand side's index must match the multiplier's input stage.
  // The extra product register lets Vivado use the DSP pipeline registers.
  // M2 and M6 still calculate on unused operation slots; sq decides whether
  // I consume their results. I haven't added clock gating or measured power.
  logic                v    [ARITH+1];          // valid bit per stage
  logic                sq_l [ARITH+1];          // 1: this stage holds a square root
  logic                p_l  [ARITH+1];          // square root: exponent parity
  logic [23:0]         x_l  [ARITH+1];          // divide: X
  logic [23:0]         a_l  [ARITH+1];          // D or na
  logic [15:0]         y0_l [ARITH+1];          // Y0 (a divide's 14 bits sit at the bottom)
  logic [39:0]         m1_l [ARITH+1];          // M1's product, from s3: P = D*Y0 or S0 = na*Y0
  logic [39:0]         s2_m;
  logic [55:0]         s4_t, s5_t;              // M2: T = S0 * Y0
  logic signed [17:0]  s6_eh;                   // the error, shortened for M3
  logic signed [31:0]  s6_lin, s7_lin, s8_lin;  // divide: e at 2^-33; root: e/2 at 2^-40
  logic [35:0]         s7_e2, s8_e2;            // M3: Eh * Eh
  logic signed [31:0]  s9_sc;                   // divide: S = e + e^2; root: C = e/2 + 3e^2/8
  logic signed [56:0]  s10_pc, s11_pc;          // M4
  logic [31:0]         s12_v;                   // divide: Y1t; root: R0
  logic [58:0]         s13_m5, s14_m5;          // M5
  logic [28:0]         s15_a, s16_a, s17_a;     // divide: Q0; root: rem (signed)
  logic [26:0]         s15_b, s16_b, s17_b, s18_b;  // the estimate the correction adjusts: Q0 or R0
  logic [26:0]         s13_r0, s14_r0;          // a root's R0, waiting from s12 until s15
  logic [50:0]         s16_qd, s17_qd;          // M6: Q0 * D
  logic signed [28:0]  s18_r;                   // the remainder to check
  logic [26:0]         s19_q;                   // corrected quotient or root
  logic                s19_st;                  // sticky: remainder nonzero

  always_ff @(posedge clk) begin
    v[1]    <= rst ? 1'b0 : p_v;
    sq_l[1] <= p_sq;
    p_l[1]  <= p_par;
    x_l[1]  <= p_x;
    a_l[1]  <= p_opa;
    for (int k = 2; k <= ARITH; k++) begin
      v[k]    <= rst ? 1'b0 : v[k-1];
      sq_l[k] <= sq_l[k-1];
      p_l[k]  <= p_l[k-1];
      x_l[k]  <= x_l[k-1];
      a_l[k]  <= a_l[k-1];
      y0_l[k] <= y0_l[k-1];
    end
    for (int k = 4; k <= ARITH; k++) m1_l[k] <= m1_l[k-1];
    // s1: both tables read with the 9 bits after opA's leading 1; sq picks one.
    y0_l[1] <= p_sq ? sseed[{p_par, p_opa[22:14]}] : {2'b00, dseed[p_opa[22:14]]};
    // s2, s3: M1.
    s2_m    <= a_l[1] * y0_l[1];
    m1_l[3] <= s2_m;
    // s4, s5: M2, for square roots: T = S0 * Y0.
    s4_t <= m1_l[3] * y0_l[3];
    s5_t <= s4_t;
    // s6: the error term.
    if (sq_l[5]) begin
      // I want E = 2^55 - (T << p), but the true small error fits signed
      // 48 bits. Since 2^55 is zero modulo 2^48, I can subtract the low
      // 48 product bits from zero and recover that same signed error.
      // The exhaustive width check matters here; arbitrary wraparound would
      // not preserve a value outside that signed range.
      logic signed [47:0] e;
      logic [47:0]        tl;
      tl = 48'(s5_t << p_l[5]);
      e  = 48'sd0 - $signed(tl);
      s6_eh  <= 18'(e >>> 30);
      s6_lin <= 32'(e >>> 16);
    end else begin
      // Divide: e = 1 - d*y0 at scale 2^-37, from P = D * Y0 (see fp_div).
      logic signed [38:0] e;
      e = 39'sd1 <<< 37;
      e = e - $signed({1'b0, m1_l[5][37:0]});
      s6_eh  <= 18'(e >>> 11);
      s6_lin <= 32'(e >>> 4);
    end
    // s7, s8: M3, e^2, with the linear term waiting beside it.
    s7_e2  <= 36'(s6_eh * s6_eh);
    s8_e2  <= s7_e2;
    s7_lin <= s6_lin;
    s8_lin <= s7_lin;
    // s9: the refining factor.
    if (sq_l[8]) s9_sc <= s8_lin + $signed({1'b0, 31'((({2'b00, s8_e2} + {1'b0, s8_e2, 1'b0})) >> 13)});
    else         s9_sc <= s8_lin + $signed({7'b0, 25'(s8_e2 >> 19)});
    // s10, s11: M4. Root: top 24 bits of S0 times C. Divide: Y0 times S.
    begin
      logic [23:0] ma;
      ma = sq_l[9] ? m1_l[9][39:16] : {8'b0, y0_l[9]};
      s10_pc <= $signed({1'b0, ma}) * s9_sc;
    end
    s11_pc <= s10_pc;
    // s12: I turn the correction into an estimate of the final quantity.
    // For divide I keep y0 + y0*S at scale 2^-32. For a root I align S0
    // and its correction using parity, keep four extra fractional bits in
    // g, then floor those away to get integer R0. Exact correction is later.
    if (sq_l[11]) begin
      logic signed [32:0] g;
      g = $signed({1'b0, 32'(m1_l[11] >> (5'd10 - p_l[11]))}) + 33'(s11_pc >>> (6'd34 - p_l[11]));
      s12_v <= 32'(27'(g >>> 4));
    end else begin
      logic signed [48:0] y1;
      y1 = $signed({2'b00, y0_l[11][13:0], 33'b0}) + 49'(s11_pc);
      s12_v <= 32'(y1 >>> 15);
    end
    // s13, s14: M5. Divide: X * Y1t. Root: R0 * R0, of which the low 29 bits
    // are the same as in S2's 29-bit product.
    begin
      logic [26:0] ma;
      ma = sq_l[12] ? s12_v[26:0] : {3'b000, x_l[12]};
      s13_m5 <= ma * s12_v;
    end
    s14_m5 <= s13_m5;
    // s15: divide: Q0 = (X * Y1t) >> 29. Root: rem = N - R0^2 modulo 2^29, where
    // N's low 29 bits are na's lowest one or two bits. I only need the low
    // product bits because the actual small signed remainder fits 29 bits.
    // This isn't a claim that the entire square or radicand fits that width.
    if (sq_l[14]) begin
      s15_a <= (p_l[14] ? {a_l[14][0], 28'b0} : {a_l[14][1:0], 27'b0}) - s14_m5[28:0];
      s15_b <= s14_r0;
    end else begin
      s15_a <= {2'b00, 27'(s14_m5 >> 29)};
      s15_b <= 27'(s14_m5 >> 29);
    end
    s13_r0 <= s12_v[26:0];
    s14_r0 <= s13_r0;
    s16_a <= s15_a;
    s17_a <= s16_a;
    s16_b <= s15_b;
    s17_b <= s16_b;
    s18_b <= s17_b;
    // s16, s17: M6, for divides: Q0 * D, of which only the low 27 bits matter.
    s16_qd <= s15_a[26:0] * a_l[15];
    s17_qd <= s16_qd;
    // s18: the remainder. Divide: X * 2^26 - Q0 * D modulo 2^27, sign-extended.
    // Root: rem from s15, which waited.
    if (sq_l[17]) begin
      s18_r <= $signed(s17_a);
    end else begin
      logic signed [26:0] r;
      r = $signed({x_l[17][0], 26'b0} - s17_qd[26:0]);
      s18_r <= 29'(r);
    end
  end

  // s19: pick the estimate - 1, the estimate or the estimate + 1. A divide wants
  // 0 <= R < D; a square root wants 0 <= rem < 2R0 + 1, and when it steps down
  // the remainder grows by 2R0 - 1.
  // I get those root thresholds from (R0+1)^2 - R0^2 = 2R0+1 and
  // R0^2 - (R0-1)^2 = 2R0-1. For divide the corresponding step is just D.
  // A negative remainder means I guessed too high; reaching lim means too
  // low. After adjusting once, I test the corrected remainder for sticky.
  // The exhaustive model verifies that this single correction succeeds on
  // every normalized input class; the estimate is not my rounded answer.
  always_ff @(posedge clk) begin
    logic signed [28:0] lim, add_lo;
    if (sq_l[ARITH]) begin
      lim    = $signed({1'b0, s18_b, 1'b1});           // 2 * R0 + 1
      add_lo = lim - 29'sd2;                            // 2 * R0 - 1
    end else begin
      lim    = $signed({5'b0, a_l[ARITH]});            // D
      add_lo = lim;
    end
    if (s18_r < 0) begin
      s19_q  <= s18_b - 1'b1;
      s19_st <= (s18_r + add_lo) != 0;
    end else if (s18_r >= lim) begin
      s19_q  <= s18_b + 1'b1;
      s19_st <= (s18_r - lim) != 0;
    end else begin
      s19_q  <= s18_b;
      s19_st <= s18_r != 0;
    end
  end

  // I expose these observations for the overlap probe, not for admission.
  // They scan the arithmetic delay line, so they aren't a complete busy flag
  // for classification, rounding or queued results. The wrapper's cnt is the
  // reservation count that decides whether I may accept another request.
  always_comb begin
    busy_div = 1'b0;
    busy_sqrt = 1'b0;
    for (int k = 1; k <= ARITH; k++) begin
      busy_div  |= v[k] && !sq_l[k];
      busy_sqrt |= v[k] && sq_l[k];
    end
  end

  // ---- K, R1, R2: pack and round --------------------------------------------------
  // I keep the corrected high bits plus guard/sticky information, then let
  // round_pre/round_post apply the shared ties-to-even and exception rules.
  // A divide can have a significand below 1, so I shift it and reduce its
  // exponent in that case. The root was already normalized by parity.
  // I select a saved special answer at the end, with its own flags and tag.
  logic                 s19_v, s19_sq;
  logic [SBW-1:0]       s19_sb;
  logic                 k_v, r1_v;
  logic [25:0]          k_sig;
  logic signed [EW-1:0] k_exp, n_exp;
  logic [SBW-EW-1:0]    k_sb, r1_sb;
  round_mid_t           r1_mid;

  always_ff @(posedge clk) begin
    s19_v  <= rst ? 1'b0 : v[ARITH];
    s19_sq <= sq_l[ARITH];
    s19_sb <= sb_line[ARITH];
  end
  assign n_exp = s19_sb[EW-1:0];

  always_ff @(posedge clk) begin
    k_v  <= rst ? 1'b0 : s19_v;
    r1_v <= rst ? 1'b0 : k_v;
    out_valid <= rst ? 1'b0 : r1_v;
    if (s19_sq) begin                         // root: 25 bits, then sticky
      k_sig <= {s19_q[25:1], s19_q[0] | s19_st};
      k_exp <= n_exp;
    end else if (s19_q[26]) begin             // quotient in [1, 2)
      k_sig <= {s19_q[26:2], s19_q[1] | s19_q[0] | s19_st};
      k_exp <= n_exp;
    end else begin                            // quotient in (0.5, 1)
      k_sig <= {s19_q[25:1], s19_q[0] | s19_st};
      k_exp <= n_exp - 1'b1;
    end
    k_sb   <= s19_sb[SBW-1:EW];
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


// The shared divide and square-root unit for the FPU (experiment S3): the
// pipeline in fp_divsqrt_core plus one promised-place result queue, the same
// queue fp_div and S2's fp_sqrt each had. cnt counts operations in the core
// plus results stored, and out_urgent rises when the oldest stored result has
// waited AGE clocks; fpu.sv then stops new requests until it leaves.
//
// I reserve a queue slot at acceptance, not when the result arrives. The core
// can't pause halfway through a multiply, so checking only finished entries
// would let too much work in and eventually overwrite a result.
//   cnt   = accepted operations whose results haven't left yet
//   q_cnt = finished results physically stored in the queue
// An operation moving from core to queue changes q_cnt but not cnt. A pop
// frees both. DIV and SQRT share these credits and preserve their relative
// order here; other FPU units can still finish out of order using their tags.
module fp_divsqrt
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW  = 8,
    parameter int DQ  = 32,                      // result queue depth, a power of 2
    parameter int AGE = 8                        // clocks a result may wait before out_urgent rises
) (
    input  logic          clk,                   // I update registers on this rising edge; the board runs at 100 MHz.
    input  logic          rst,                   // I discard pipeline and queue validity when reset is high.
    input  logic          in_valid,              // I accept this offer only when in_ready is also high on the edge.
    input  logic          in_sqrt,               // I select sqrt(A) with 1 and A/B with 0, and delay that choice beside the math.
    output logic          in_ready,              // I can promise a result slot while this is high.
    input  logic [31:0]   in_a,                  // I read A as the dividend or radicand.
    input  logic [31:0]   in_b,                  // I read B as the divisor; I ignore it for roots.
    input  logic [TW-1:0] in_tag,                // I keep this identifier beside the operands so the caller can match the answer.
    output logic          out_valid,             // I show the oldest queued result while this is high.
    output logic          out_urgent,            // I ask the FPU to stop issuing so this waiting answer can reach the result port.
    input  logic          out_ack,               // I remove the shown answer when valid and this ack are high on the edge.
    output logic [31:0]   out_result,            // I expose the binary32 answer here; it counts only with out_valid.
    output logic [4:0]    out_flags,             // I return flags [4:0] as invalid, divide-by-zero, overflow, underflow, inexact.
    output logic [TW-1:0] out_tag,               // I return the original tag for the queued answer.
    output logic          busy_div,              // I expose the core's divide observation for the testbench.
    output logic          busy_sqrt              // I expose the core's root observation for the testbench.
);
  localparam int QA = $clog2(DQ);

  logic          fire, core_v;
  logic [31:0]   core_r;
  logic [4:0]    core_f;
  logic [TW-1:0] core_t;
  assign fire = in_valid && in_ready;

  fp_divsqrt_core #(.TW(TW)) u_core (
      .clk, .rst, .in_valid(fire), .in_sqrt, .in_a, .in_b, .in_tag,
      .out_valid(core_v), .out_result(core_r), .out_flags(core_f), .out_tag(core_t),
      .busy_div, .busy_sqrt);

  // A circular buffer in LUT RAM, read without a clock like fpu.sv's CORDIC
  // FIFO. Each entry stores the low 8 bits of a clock counter from when it
  // arrived, so the oldest entry's age is now - stamp, modulo 256.
  // I only interpret the exposed word while out_valid is high. Empty queue
  // memory can contain old bits; resetting pointers and counts makes them
  // invalid without resetting the whole RAM. DQ must stay a power of two.
  // At full capacity I don't accept a replacement on the same edge as a pop;
  // that conservative ready rule can cost one clock, but never overbooks.
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
      // so it rises on the clock the oldest result turns AGE. I age from
      // queue arrival, not initial request acceptance. The FPU stops issuing
      // new work on urgency, lets the short pipeline drain, then grants this
      // queue the port. I don't drop a result just because it waited too long.
      out_urgent <= out_valid && !pop && (q_age >= 8'(AGE - 1));
    end
  end
endmodule
