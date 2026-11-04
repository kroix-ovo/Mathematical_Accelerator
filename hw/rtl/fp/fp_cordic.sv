// Pipelined CORDIC unit for the phase 3 functions: one operation per clock,
// fixed latency LAT = CN + 10 clocks (52 with the current widths), whatever
// the op.
//
// It implements all six phase 3 ops: SIN, COS (circular rotation), ATAN2,
// HYPOT (circular vectoring), EXP (hyperbolic rotation) and LN (hyperbolic
// vectoring), plus linear vectoring (division) for the tiny-result cases of
// ATAN2 and LN. Any other op code is treated as SIN.
//
// General results are approximations and can differ from the correctly
// rounded mathematical answer. What is exact is the match against the bit-accurate
// Python model sw/mathaccel/cordic_model.py, which the testbench checks on
// every result. That model's error against mpmath (tools/measure_ulp.py) is
// what says how accurate this unit is. The widths and constant tables live in
// cordic_pkg.sv, generated from the same model.
//
// How sin/cos of an fp32 x are computed (angles in quarter turns, pi/2 each):
//   1. |x| < 2^-12: sin x rounds to x and cos x rounds to 1, so those inputs
//      skip everything else (the "tiny" path, decided in stage 1).
//   2. Payne-Hanek reduction. x = M * 2^E with a 24-bit M. A ROM indexed by
//      the exponent gives PH_L bits of 2/pi; M times that window, modulo
//      2^PH_L, is x * 2/pi modulo 4. Its top 2 bits are the quadrant q and
//      the other PH_F bits are the fraction f of a quarter turn.
//   3. Fold: if f >= 1/2, use f' = 1 - f and swap sin and cos, so the angle
//      t = f' quarter turns is at most pi/4.
//   4. CN rotation stages turn the vector (K, 0) by +-atan(2^-i) until the
//      leftover angle z is ~0. The vector ends at (cos t, sin t).
//   5. If f' < 2^-SMALL_T, sin t is too small for fixed point to give it with
//      full relative precision. In parallel with the CORDIC, a second path
//      computes sin t = t as normalized f' times pi/2 (one 32 x 32 multiply).
//   6. Pick cos t or sin t and the sign from the quadrant, normalize, round.
//
// How atan2(y, x) and hypot(x, y) are computed (vectoring mode, z in radians):
//   1. Special values (NaN, zeros, infinities) by rule in stage 1.
//   2. Normalize both significands (subnormals get their leading 1 shifted up)
//      and sort the two inputs into the larger magnitude B and smaller S, with
//      exponent gap d. For atan2, "swap" means |y| > |x|.
//   3. Multiply both significands by K/2 (the CORDIC gain correction, halved
//      to leave headroom), and shift S right by d: the vector (B, S).
//   4. CN vectoring stages turn the vector toward the x axis, steering y to
//      zero and adding up the angles turned in z. x ends at sqrt(B^2 + S^2)
//      (hypot) and z at atan(S/B). For atan2, z starts at the quadrant's base
//      angle (0, pi/2 or pi) and the angle steps are negated when the result
//      is base - angle, so z ends at the final answer.
//   5. When the atan2 result would be tiny (x > 0, |y| < |x|, d >= ATAN_D),
//      fixed point would lose its relative precision, so the same stages run
//      in linear mode instead: a shift-and-subtract division leaving S/B in
//      z. atan(r) is r to within 0.1 ULP there. The exponent -d is put back
//      when the result is packed.
//   6. Normalize x or z, round.
//
// How exp x is computed (hyperbolic rotation, z in units of ln2):
//   1. |x| < 2^-25 gives 1, |x| >= 128 overflows (+inf) or underflows (+0),
//      by rule in stage 1, with NaN and the infinities.
//   2. The sin/cos multiplier, fed from a second small ROM, computes
//      t = |x|/ln2 with 86 fraction bits. k = round(t) and f = t - k, in
//      [-1/2, 1/2], so e^x = 2^k * e^(f ln2). f stays in units of ln2 and the
//      atanh table is scaled to match, so nothing is multiplied by ln2.
//   3. Hyperbolic rotation of (G/2, G/2) by f ln2 ends at e^(f ln2)/2 in x
//      (cosh + sinh = exp; G undoes the stages' length change, the halving
//      leaves headroom). k + 1 goes into the exponent.
//
// How ln x is computed (hyperbolic vectoring, z in natural units):
//   1. NaN, zero (-inf, DZ), negative (NaN, NV), +inf and 1 by rule.
//   2. Within 2^-LN_T of 1, d = x - 1 is exact and ln(1+d) = 2 atanh(d/(2+d))
//      is 2 d/(2+d) to within 0.1 ULP: linear mode divides normalized d by
//      (1 + x), and d's exponent goes back in when the result is packed.
//   3. Otherwise x = m * 2^e with m in [sqrt(1/2), sqrt(2)). Vectoring
//      ((m+1)/4, (m-1)/4) leaves atanh((m-1)/(m+1)) = ln(m)/2 in z, which
//      starts at |e| * ln2/2 (one DSP multiply in stages 4-5). For x < 1 the
//      steps are negated, so z ends at |ln x|/2 >= 0, and ln x = -+2z.
//
//   stage 1        unpack both operands, special values, tiny inputs, 2/pi or
//                  1/ln2 ROM
//   stages 2-3     sin/cos/exp: 24 x 96 multiply as four 24 x 24 DSP products
//                  atan2/hypot/ln: normalize significands, then sort by size,
//                  fold ln's significand at sqrt(2), normalize ln's d
//   stage 4        sin/cos/exp: add the partial products in pairs
//                  atan2/hypot: multiply both significands by K (DSP, reg 1)
//                  ln: multiply |e| by ln2/2 (DSP, reg 1)
//   stage 5        sin/cos/exp: final add (quadrant and fraction, or t = x/ln2)
//                  atan2/hypot/ln: DSP register 2
//   stage 6        load the CORDIC: fold f / align S / split t, choose mode
//   stages 7..     CN CORDIC stages (small-angle path runs alongside)
//   stage CN+7     pick x, y or z, count leading zeros
//   stage CN+8     normalize to a 26-bit significand
//   stage CN+9     first half of rounding (subnormal shift)
//   stage CN+10    second half of rounding, output register
// A few HDL details help when reading the stages below. logic [23:0] is a
// bundle of 24 bits; logic signed gives the bundle a two's-complement meaning.
// A fixed-point value with CW fraction bits represents stored_integer / 2^CW.
// There is no physical binary point: the shifts and scale factors agree on
// where it belongs. This lets adders work on fractions as integer bit vectors.
//
// In always_ff, <= updates a register after the edge, using its previous
// inputs. So s3 <= s2 and s2 <= s1 move two different requests one stage each;
// they do not move one request through both stages in one clock. Local =
// assignments calculate temporary values used to load those registers.
// A for (genvar ...) builds CN copies of the stage circuit at synthesis time.
// It is a pipeline of adders and registers, not one adder looping CN times.
//
// I carry the early special-case answer through the same stages as ordinary
// work. done selects that saved answer at the output; it does not make the
// request finish early. Keeping one latency makes tags and valid bits line up.
// Reset clears validity rather than every data register. Ignore out_result,
// out_flags and out_tag whenever out_valid is low.
module fp_cordic
  import accel_pkg::*;
  import fp_pkg::*;
  import cordic_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,
    input  logic          rst,
    input  logic          in_valid,
    input  logic [4:0]    in_op,
    input  logic [31:0]   in_a,       // x for SIN/COS, y for ATAN2, first input of HYPOT
    input  logic [31:0]   in_b,       // x for ATAN2, second input of HYPOT, unused otherwise
    input  logic [TW-1:0] in_tag,
    output logic          out_valid,
    output logic [31:0]   out_result,
    output logic [4:0]    out_flags,
    output logic [TW-1:0] out_tag
);
  localparam int LAT = CN + 10;
  localparam logic [31:0] ONE    = 32'h3F80_0000;
  // Correctly rounded pi, pi/2, pi/4 and 3pi/4, for the atan2 rule results.
  localparam logic [31:0] PI_F   = 32'h4049_0FDB;
  localparam logic [31:0] PIO2_F = 32'h3FC9_0FDB;
  localparam logic [31:0] PIO4_F = 32'h3F49_0FDB;
  localparam logic [31:0] P3O4_F = 32'h4016_CBE4;

  // Stage modes: circular rotation with z in quarter turns, circular
  // vectoring with z in radians, linear vectoring (division), hyperbolic
  // rotation with z in units of ln2, hyperbolic vectoring in natural units.
  localparam logic [2:0] M_ROT = 3'd0, M_VEC = 3'd1, M_LIN = 3'd2, M_HROT = 3'd3, M_HVEC = 3'd4;
  // Which value the back end turns into the result.
  localparam logic [1:0] SEL_X = 2'd0, SEL_Y = 2'd1, SEL_Z = 2'd2, SEL_SMALL = 2'd3;

  // Valid bits. A request entering on one clock edge comes out LAT edges
  // later, whatever path it takes, so the valid bit simply walks down a
  // shift register: vpipe[k] is "stage k+1 holds a real request", and the
  // output register takes the last bit.
  // It is the only state that needs a reset; everything else may hold
  // garbage while its valid bit is 0.
  logic [LAT-2:0] vpipe;

  always_ff @(posedge clk) vpipe <= rst ? '0 : {vpipe[LAT-3:0], in_valid};

  // Everything else that travels down the front end beside the numbers. A
  // packed struct is just a bit vector with named fields, so copying it from
  // stage to stage is one assignment.
  //   done    the result was decided in stage 1 (special value, tiny input)
  //   res/fl  that decided result and its flags
  //   cos     the op is COS (otherwise SIN), for rotation mode
  //   vec     the op is ATAN2 or HYPOT (vectoring mode)
  //   hyp     the op is HYPOT
  //   exp     the op is EXP
  //   ln      the op is LN
  //   sign    sign of in_a (the sign of sin x's input, atan2's y, exp's x)
  //   xneg    sign of in_b (atan2's x)
  typedef struct packed {
    logic [TW-1:0] tag;
    logic          done;
    logic [31:0]   res;
    logic [4:0]    fl;
    logic          cos;
    logic          vec;
    logic          hyp;
    logic          exp;
    logic          ln;
    logic          sign;
    logic          xneg;
  } head_t;

  // ---------------------------------------------------------------- stage 1
  // Decide the special cases now, and start both front ends. Both always run;
  // stage 6 uses whichever matches the op. The 2/pi ROM address is the raw
  // exponent field; inputs that never use the window read a harmless entry.
  head_t                s1;
  logic [23:0]          s1_m;
  logic [PH_L-1:0]      s1_win;
  logic [23:0]          s1_mu, s1_mw;       // significands of in_a and in_b
  logic signed [EW-1:0] s1_eu, s1_ew;       // their exponents (subnormals count as 1)
  logic [5:0]           s1_lzu, s1_lzw;     // leading zeros (0 for normal numbers)
  logic [23:0]          s1_dmag;            // ln near 1: |x - 1| in units of 2^-23 or 2^-24
  logic                 s1_near, s1_dbelow; // ln near 1, and x < 1 there

  always_ff @(posedge clk) begin
    logic        cos_op, vec_op, hyp_op, exp_op, ln_op;
    logic        done;
    logic [31:0] res;
    logic [4:0]  fl;
    logic [23:0] dm;
    cos_op = (in_op == FOP_COS);
    hyp_op = (in_op == FOP_HYPOT);
    vec_op = (in_op == FOP_ATAN2) || hyp_op;
    exp_op = (in_op == FOP_EXP);
    ln_op  = (in_op == FOP_LN);

    s1.tag  <= in_tag;
    s1.cos  <= cos_op;
    s1.vec  <= vec_op;
    s1.hyp  <= hyp_op;
    s1.exp  <= exp_op;
    s1.ln   <= ln_op;
    s1.sign <= in_a[31];
    s1.xneg <= in_b[31];
    s1_m    <= mant24(in_a);
    // The multiplier in stages 2-3 serves sin/cos and exp: it gets 2/pi or
    // 1/ln2 bits, picked by the op.
    s1_win  <= exp_op ? exp_window(in_a[30:23]) : ph_window(in_a[30:23]);
    s1_mu   <= mant24(in_a);
    s1_mw   <= mant24(in_b);
    s1_eu   <= exp_eff(in_a);
    s1_ew   <= exp_eff(in_b);
    s1_lzu  <= lzc24(mant24(in_a));
    s1_lzw  <= lzc24(mant24(in_b));
    // ln near 1: for x in [1, 2), x - 1 is the fraction field in units of
    // 2^-23; for x in [1/2, 1), 1 - x is 2^24 minus the significand in units
    // of 2^-24. Both are exact. "Near" means |x - 1| < 2^-LN_T.
    if (in_a[30:23] == 8'd127) begin
      dm = {1'b0, in_a[22:0]};
      s1_near   <= (dm < 24'(1 << (23 - LN_T)));
      s1_dbelow <= 1'b0;
    end else begin
      dm = 24'(25'h100_0000 - {1'b0, mant24(in_a)});
      s1_near   <= (in_a[30:23] == 8'd126) && (dm < 24'(1 << (24 - LN_T)));
      s1_dbelow <= 1'b1;
    end
    s1_dmag <= dm;

    done = 1'b1;
    res  = '0;
    fl   = '0;
    if (hyp_op) begin
      // hypot: an infinity wins even over a NaN (an sNaN still raises NV);
      // a zero input makes the result the other input's magnitude, exactly.
      if (is_inf(in_a) || is_inf(in_b)) begin
        res = POS_INF;
        fl  = (is_snan(in_a) || is_snan(in_b)) ? FFLAG_NV : '0;
      end else if (is_nan(in_a) || is_nan(in_b)) begin
        res = CANON_NAN;
        fl  = (is_snan(in_a) || is_snan(in_b)) ? FFLAG_NV : '0;
      end else if (is_zero(in_b)) begin
        res = {1'b0, in_a[30:0]};
      end else if (is_zero(in_a)) begin
        res = {1'b0, in_b[30:0]};
      end else begin
        done = 1'b0;
      end
    end else if (exp_op) begin
      // exp: e^+inf = +inf and e^-inf = +0 exactly, e^+-0 = 1 exactly. For
      // |x| < 2^-25, e^x = 1 + x + ... is within half an ULP of 1, so 1 is the
      // correctly rounded result. |x| >= 128 is far past both ends of the
      // range: +inf with OF, or +0 with UF.
      if (is_nan(in_a)) begin
        res = CANON_NAN;
        fl  = is_snan(in_a) ? FFLAG_NV : '0;
      end else if (is_inf(in_a)) begin
        res = in_a[31] ? 32'h0 : POS_INF;
      end else if (is_zero(in_a)) begin
        res = ONE;
      end else if (in_a[30:23] < 8'(EXP_TINY_E)) begin
        res = ONE;
        fl  = FFLAG_NX;
      end else if (in_a[30:23] >= 8'(EXP_BIG_E)) begin
        res = in_a[31] ? 32'h0 : POS_INF;
        fl  = in_a[31] ? (FFLAG_UF | FFLAG_NX) : (FFLAG_OF | FFLAG_NX);
      end else begin
        done = 1'b0;
      end
    end else if (ln_op) begin
      // ln: ln(+-0) = -inf with DZ, ln of a negative number (including -inf)
      // is NaN with NV, ln(+inf) = +inf and ln(1) = +0 exactly.
      if (is_nan(in_a)) begin
        res = CANON_NAN;
        fl  = is_snan(in_a) ? FFLAG_NV : '0;
      end else if (is_zero(in_a)) begin
        res = 32'hFF80_0000;
        fl  = FFLAG_DZ;
      end else if (in_a[31]) begin
        res = CANON_NAN;
        fl  = FFLAG_NV;
      end else if (is_inf(in_a)) begin
        res = POS_INF;
      end else if (in_a == ONE) begin
        res = 32'h0;
      end else begin
        done = 1'b0;
      end
    end else if (vec_op) begin
      // atan2(y = in_a, x = in_b), following C99 Annex F. Exact results are
      // +-0 (y = +-0 with x >= +0, or finite y with x = +inf); the angles
      // pi, pi/2, pi/4, 3pi/4 are rounded constants and raise NX.
      if (is_nan(in_a) || is_nan(in_b)) begin
        res = CANON_NAN;
        fl  = (is_snan(in_a) || is_snan(in_b)) ? FFLAG_NV : '0;
      end else if (is_inf(in_a)) begin
        res = {in_a[31], is_inf(in_b) ? (in_b[31] ? P3O4_F[30:0] : PIO4_F[30:0]) : PIO2_F[30:0]};
        fl  = FFLAG_NX;
      end else if (is_zero(in_a) || is_inf(in_b)) begin
        res = {in_a[31], in_b[31] ? PI_F[30:0] : 31'b0};
        fl  = in_b[31] ? FFLAG_NX : '0;
      end else if (is_zero(in_b)) begin
        res = {in_a[31], PIO2_F[30:0]};
        fl  = FFLAG_NX;
      end else begin
        done = 1'b0;
      end
    end else begin
      if (is_nan(in_a)) begin
        res = CANON_NAN;
        fl  = is_snan(in_a) ? FFLAG_NV : '0;
      end else if (is_inf(in_a)) begin
        res = CANON_NAN;                  // sin and cos of infinity have no value
        fl  = FFLAG_NV;
      end else if (is_zero(in_a)) begin
        res = cos_op ? ONE : in_a;        // exact: sin(+-0) = +-0, cos(+-0) = 1
      end else if (in_a[30:23] < 8'(TINY_E)) begin
        // |x| < 2^-12. sin x = x - x^3/6 is within half an ULP of x, and
        // cos x = 1 - x^2/2 within half an ULP of 1, so these are the
        // correctly rounded results. They are inexact, and a subnormal x is
        // also tiny, so sin raises UF for it.
        res = cos_op ? ONE : in_a;
        fl  = FFLAG_NX | ((!cos_op && in_a[30:23] == 8'h00) ? FFLAG_UF : 5'b0);
      end else begin
        done = 1'b0;
      end
    end
    s1.done <= done;
    s1.res  <= res;
    s1.fl   <= fl;
  end

  // ------------------------------------------------------- stages 2 and 3
  // sin/cos: M (24 bits) times the 96-bit window. I split the window into four
  // 24-bit pieces so each product is the same 24 x 24 shape as in fp_mul,
  // which Vivado maps onto two DSP48 blocks and runs at 100 MHz when followed
  // by two registers. pp[k] has weight 2^(24k) in the full product.
  //
  // atan2/hypot: stage 2 shifts each significand's leading 1 up to bit 23
  // and lowers its exponent to match (subnormals only; normal numbers have
  // lz = 0). Stage 3 sorts them: B is the larger magnitude, S the smaller,
  // and d = e_B - e_S >= 0 is how far S must move right to line up with B.
  head_t       s2, s3;
  logic [47:0] s2_pp [4];
  logic [47:0] s3_pp [4];

  logic [23:0]          s2_mu, s2_mw;
  logic signed [EW-1:0] s2_eu, s2_ew;
  logic [23:0]          s3_mb, s3_ms;
  logic signed [EW-1:0] s3_eb, s3_d;
  logic                 s3_swap, s3_lin;

  always_ff @(posedge clk) begin
    logic                 swap;
    logic signed [EW-1:0] d;
    s2   <= s1;
    for (int k = 0; k < 4; k++) s2_pp[k] <= s1_m * s1_win[24*k +: 24];
    s2_mu <= s1_mu << s1_lzu;
    s2_mw <= s1_mw << s1_lzw;
    s2_eu <= s1_eu - EW'(s1_lzu);
    s2_ew <= s1_ew - EW'(s1_lzw);

    s3   <= s2;
    for (int k = 0; k < 4; k++) s3_pp[k] <= s2_pp[k];
    // |a| > |b|: compare exponents first, then significands. Both are
    // normalized now, so this is an exact magnitude comparison.
    swap    = (s2_eu > s2_ew) || (s2_eu == s2_ew && s2_mu > s2_mw);
    d       = swap ? (s2_eu - s2_ew) : (s2_ew - s2_eu);
    s3_swap <= swap;
    s3_mb   <= swap ? s2_mu : s2_mw;
    s3_ms   <= swap ? s2_mw : s2_mu;
    s3_eb   <= swap ? s2_eu : s2_ew;
    s3_d    <= d;
    // The y/x path: atan2 with x > 0, |y| <= |x| and a gap of ATAN_D or more,
    // where the result is about y/x < 2^-13.
    s3_lin  <= !s2.hyp && !swap && !s2.xneg && d >= EW'(ATAN_D);
  end

  // ------------------------------------------------------ ln, stages 2-5
  // Stage 2: count the leading zeros of d (near-1 path). Stage 3: shift d's
  // leading 1 up to bit 23, so |d| = dn * 2^-23 * 2^-ds; and fold the
  // normalized significand: if m >= sqrt(2), halve it and add one to the
  // exponent, so m is in [sqrt(1/2), sqrt(2)) and x = m * 2^ep. Stages 4-5:
  // |ep| * ln2/2 on a DSP, z's start value. ep <= 0 exactly when x < 1, so
  // the sign of the result is "x < 1" and only |ep| is needed.
  logic [23:0]          l2_dmag, l3_dn, l4_dn, l5_dn, l3_dmag, l4_dmag, l5_dmag;
  logic [4:0]           l2_lzd;
  logic                 l2_near, l3_near, l4_near, l5_near;
  logic                 l2_dbelow, l3_dbelow, l4_dbelow, l5_dbelow;
  logic [5:0]           l3_ds, l4_ds, l5_ds;
  logic                 l3_big, l4_big, l5_big;
  logic                 l3_below, l4_below, l5_below;
  logic [23:0]          l3_mn, l4_mn, l5_mn;
  logic [7:0]           l3_aep;
  logic [58:0]          l4_z0, l5_z0;

  always_ff @(posedge clk) begin
    logic                 big;
    logic signed [EW-1:0] ep;
    l2_dmag   <= s1_dmag;
    l2_lzd    <= 5'(lzc24(s1_dmag));
    l2_near   <= s1_near;
    l2_dbelow <= s1_dbelow;

    l3_dn     <= l2_dmag << l2_lzd;
    l3_ds     <= 6'(l2_lzd) + 6'(l2_dbelow);
    l3_dmag   <= l2_dmag;
    l3_near   <= l2_near;
    l3_dbelow <= l2_dbelow;
    big        = (s2_mu >= SQRT2_M);
    ep         = s2_eu - EW'(127) + EW'(big);
    l3_big    <= big;
    l3_mn     <= s2_mu;
    l3_below  <= (s2_eu < EW'(127));
    l3_aep    <= ep[EW-1] ? 8'(-ep) : 8'(ep);

    l4_z0 <= l3_aep * LN2H;                      // DSP, register 1
    l4_dn <= l3_dn;  l4_ds <= l3_ds;  l4_dmag <= l3_dmag;  l4_near <= l3_near;
    l4_dbelow <= l3_dbelow;  l4_big <= l3_big;  l4_mn <= l3_mn;  l4_below <= l3_below;

    l5_z0 <= l4_z0;                              // DSP register 2
    l5_dn <= l4_dn;  l5_ds <= l4_ds;  l5_dmag <= l4_dmag;  l5_near <= l4_near;
    l5_dbelow <= l4_dbelow;  l5_big <= l4_big;  l5_mn <= l4_mn;  l5_below <= l4_below;
  end

  // ---------------------------------------------------------------- stage 4
  // sin/cos: add the partial products in pairs. Only the product modulo 2^96
  // matters (higher bits are whole turns), so the upper pair is only needed
  // to 48 bits: it will be shifted up by 48 places, and the sum wraps around.
  //
  // atan2/hypot: multiply both significands by K (32 fraction bits). Like the
  // other multiplies, two registers follow so Vivado can use the DSP's own.
  head_t                s4;
  logic [71:0]          s4_lo;
  logic [47:0]          s4_hi;
  logic [55:0]          s4_pb, s4_ps;
  logic signed [EW-1:0] s4_eb, s4_d;
  logic                 s4_swap, s4_lin;

  always_ff @(posedge clk) begin
    s4      <= s3;
    s4_lo   <= {24'b0, s3_pp[0]} + {s3_pp[1], 24'b0};
    s4_hi   <= s3_pp[2] + {s3_pp[3][23:0], 24'b0};
    s4_pb   <= s3_mb * KV;
    s4_ps   <= s3_ms * KV;
    s4_eb   <= s3_eb;
    s4_d    <= s3_d;
    s4_swap <= s3_swap;
    s4_lin  <= s3_lin;
  end

  // ---------------------------------------------------------------- stage 5
  // sin/cos: x * 2/pi modulo 4, as a fixed-point number with PH_F fraction
  // bits: bits [95:94] are the quadrant, bits [93:0] the fraction f.
  head_t                s5;
  logic [1:0]           s5_q;
  logic [PH_F-1:0]      s5_f;
  logic [55:0]          s5_pb, s5_ps;
  logic signed [EW-1:0] s5_eb, s5_d;
  logic                 s5_swap, s5_lin;

  always_ff @(posedge clk) begin
    logic [PH_L-1:0] prod;
    s5      <= s4;
    prod     = {24'b0, s4_lo} + {s4_hi, 48'b0};  // wraps modulo 2^96
    s5_q    <= prod[PH_L-1 -: 2];
    s5_f    <= prod[PH_F-1:0];
    s5_pb   <= s4_pb;                            // second DSP register
    s5_ps   <= s4_ps;
    s5_eb   <= s4_eb;
    s5_d    <= s4_d;
    s5_swap <= s4_swap;
    s5_lin  <= s4_lin;
  end

  // ---------------------------------------------------------------- stage 6
  // Load the first CORDIC stage.
  //
  // sin/cos: fold f into [0, 1/2]. Past half a quarter turn, sin and cos trade
  // places (sin(pi/2 - t) = cos t), so I use f' = 1 - f and remember the swap.
  // In PH_F-bit fixed point, 1 - f is the two's complement negation of f.
  // Then work out which CORDIC output this op needs and with what sign:
  //   sin|x| is +sin, +cos, -sin, -cos of the unfolded angle in quadrants 0-3
  //   cos|x| is +cos, -sin, -cos, +sin
  // and sin is odd, so sin x also flips with the sign of x.
  //
  // atan2/hypot: the products are (significand / 2^23) * K with 55 fraction
  // bits. Dropping 56 - CW bits gives B * K / 2 with CW fraction bits; S also
  // moves right by d (63 is as far as it can usefully go: past CW bits it is
  // zero anyway). The y/x path keeps S unshifted, so z ends at S/B in (1/2, 2).
  //
  // The side band travels with the vector through every stage:
  //   mode   circular, linear or hyperbolic rotation/vectoring for this request
  //   zneg   negate the angle steps (atan2 results of the form base - angle)
  //   sel    which value becomes the result (SEL_X, SEL_Y, SEL_Z, SEL_SMALL)
  //   neg    negate the result
  //   ebase  biased exponent of the value 1.0 (bit CW): the result is
  //          v * 2^(ebase - 127) for the selected v
  typedef struct packed {
    logic [TW-1:0]        tag;
    logic                 done;
    logic [31:0]          res;
    logic [4:0]           fl;
    logic [2:0]           mode;
    logic                 zneg;
    logic [1:0]           sel;
    logic                 neg;
    logic signed [EW-1:0] ebase;
  } side_t;

  logic signed [CW+1:0] cx [CN+1];   // x, y with CW fraction bits, 1 integer bit, sign
  logic signed [CW+1:0] cy [CN+1];
  logic signed [ZW-1:0] cz [CN+1];   // angle, CZ fraction bits, 6 integer bits (|ln x|/2), sign
  side_t                cs [CN+1];   // side band, one copy per stage
  logic [63:0]          sp_win;      // f' bits for the small-angle path

  always_ff @(posedge clk) begin
    logic            swap, is_small, want_cos, take_x;
    logic [PH_F-1:0] fp;
    logic [5:0]      dcl;
    side_t           side;
    logic [PH_L-1:0]      t;
    logic [9:0]           kmag;
    logic signed [EW-1:0] k;
    logic signed [85:0]   f;
    logic [CW+1:0]        msh;
    swap     = s5_f[PH_F-1];                     // f >= 1/2
    fp       = swap ? (PH_F'(0) - s5_f) : s5_f;  // f' = 1 - f, or f
    // f' < 2^-SMALL_T when its top SMALL_T bits are all zero.
    is_small = (fp[PH_F-1 -: SMALL_T] == '0);
    want_cos = s5.cos ? !s5_q[0] : s5_q[0];
    take_x   = want_cos ^ swap;
    dcl      = (s5_d > EW'(63)) ? 6'd63 : s5_d[5:0];

    // I fill a local struct and then store it with one assignment. Writing
    // the fields of cs[0] one by one is legal SV, but Icarus Verilog rejects
    // field writes to an element of an array of structs.
    side.tag  = s5.tag;
    side.done = s5.done;
    side.res  = s5.res;
    side.fl   = s5.fl;
    if (s5.exp) begin
      // t = |x|/ln2 with 86 fraction bits. Round to the nearest integer k;
      // the fraction bits read as a signed number are then exactly t - k
      // (a fraction of 1/2 or more becomes negative, borrowing from k). For
      // negative x both change sign.
      t     = {s5_q, s5_f};
      kmag  = t[95:86] + 10'(t[85]);
      f     = t[85:0];
      k     = s5.sign ? -EW'(kmag) : EW'(kmag);
      if (s5.sign) f = -f;
      side.mode  = M_HROT;
      side.zneg  = 1'b0;
      side.sel   = SEL_X;
      side.neg   = 1'b0;
      side.ebase = EW'(128) + k;                 // x = e^r/2: one more power of two
      cx[0] <= HYP_G2;                           // (G/2, G/2)
      cy[0] <= HYP_G2;
      cz[0] <= ZW'(f >>> (86 - CZ));             // f in units of ln2, CZ fraction bits
    end else if (s5.ln) begin
      side.sel  = SEL_Z;
      side.zneg = 1'b0;
      if (l5_near) begin
        // ln(1 + d) = +-2 * (|d| / (1 + x)): divide (dn/2^23)/4 by (1 + x)/4,
        // which is 1/2 +- the exact d/4. The result is z * 2^(1 - ds).
        side.mode  = M_LIN;
        side.neg   = l5_dbelow;
        side.ebase = EW'(128) - EW'(l5_ds);
        cx[0] <= l5_dbelow ? ((CW+2)'(1) << (CW - 1)) - ((CW+2)'(l5_dmag) << (CW - 26))
                           : ((CW+2)'(1) << (CW - 1)) + ((CW+2)'(l5_dmag) << (CW - 25));
        cy[0] <= (CW+2)'(l5_dn) << (CW - 25);
        cz[0] <= '0;
      end else begin
        // ((m+1)/4, (m-1)/4), with m = mn * 2^-23, or mn * 2^-24 when folded.
        msh        = l5_big ? (CW+2)'(l5_mn) << (CW - 26) : (CW+2)'(l5_mn) << (CW - 25);
        side.mode  = M_HVEC;
        side.neg   = l5_below;
        side.zneg  = l5_below;                   // x < 1: z ends at -ln(x)/2
        side.ebase = EW'(128);                   // ln x = +-2z
        cx[0] <= msh + ((CW+2)'(1) << (CW - 2));
        cy[0] <= msh - ((CW+2)'(1) << (CW - 2));
        cz[0] <= ZW'(l5_z0 >> 8);                // |ep| * ln2/2, CZ fraction bits
      end
    end else if (!s5.vec) begin
      side.mode  = M_ROT;
      side.zneg  = 1'b0;
      side.sel   = take_x ? SEL_X : (is_small ? SEL_SMALL : SEL_Y);
      side.neg   = s5.cos ? (s5_q == 2'd1 || s5_q == 2'd2) : (s5_q[1] ^ s5.sign);
      side.ebase = EW'(127);
      cx[0] <= CORDIC_K;                         // (K, 0): the start vector
      cy[0] <= '0;
      cz[0] <= ZW'(fp[PH_F-1 -: CZ]);            // f' with CZ fraction bits, >= 0
    end else begin
      side.mode = s5_lin ? M_LIN : M_VEC;
      side.neg  = s5.hyp ? 1'b0 : s5.sign;       // atan2 takes the sign of y
      if (s5.hyp) begin
        // x ends at (B/2) * sqrt(1 + (S/B)^2), times 2^(e_B - 127): one
        // power of two more than B's own exponent.
        side.sel   = SEL_X;
        side.ebase = s5_eb + EW'(1);
      end else begin
        side.sel   = SEL_Z;
        // The y/x result S/B is z * 2^-d.
        side.ebase = s5_lin ? (EW'(127) - s5_d) : EW'(127);
      end
      cx[0] <= (CW+2)'(s5_pb >> (56 - CW));
      cy[0] <= s5_lin ? (CW+2)'(s5_ps >> (56 - CW)) : (CW+2)'((s5_ps >> (56 - CW)) >> dcl);
      // atan2 base angle and direction for {swap, x < 0}:
      //   no swap, x > 0   angle            z0 = 0
      //   swap,    x > 0   pi/2 - angle     z0 = pi/2, steps negated
      //   no swap, x < 0   pi - angle       z0 = pi,   steps negated
      //   swap,    x < 0   pi/2 + angle     z0 = pi/2
      // hypot and the y/x path start at 0 with plain steps.
      if (s5.hyp || s5_lin) begin
        cz[0]     <= '0;
        side.zneg  = 1'b0;
      end else begin
        cz[0]     <= (s5_swap || !s5.xneg) ? (s5_swap ? PIO2_Z : '0) : PI_Z;
        side.zneg  = s5_swap ^ s5.xneg;
      end
    end
    cs[0] <= side;
    // The 64 bits of f' just below the SMALL_T bits that are zero on the
    // small path. Its leading 1 is somewhere in the top 32 (f' >= 2^-30).
    sp_win <= fp[PH_F-SMALL_T-1 -: 64];
  end

  // ------------------------------------------------------- CORDIC stages
  // Stage i turns the vector by atan(2^-i). Turning (x, y) by an angle a with
  // tan(a) = 2^-i is, apart from a length factor, x - y*2^-i and y + x*2^-i:
  // a shift and an add, no multiplier. Which way to turn depends on the mode:
  //   rotation   whichever way brings the leftover angle z toward zero
  //   vectoring  whichever way brings y toward zero (z adds up the angle)
  //   linear     the same, but x stays fixed and z adds up +-2^-i, so the
  //              stages do a binary division: z ends at y0 / x0
  // ">>>" is an arithmetic shift (it copies the sign bit in from the left), so
  // negative x or y shift correctly. Each stage is one clock, so a new value
  // can enter every clock, and each carries its own mode.
  //
  // Hyperbolic steps differ from circular ones in two ways: x moves the same
  // way as y (x + y*2^-s instead of x - y*2^-s), and the shift s follows the
  // schedule 1, 2, 3, 4, 4, 5, ..., 13, 13, 14, ... from hshift(), because
  // hyperbolic CORDIC only converges with shifts 4 and 13 done twice. Stages
  // 4-13 use the same shift either way; the others pick one of two shifted
  // copies of x and y.
  //
  // I write the mode differences so that each bit of x and z stays one adder
  // bit: linear mode gates the y term to zero rather than choosing between
  // two results, and the hyperbolic x direction and atan2/ln's negated steps
  // flip the add/subtract choice rather than negating a value. (Muxing whole
  // results here made the unit about 5k LUTs larger.)
  for (genvar i = 0; i < CN; i++) begin : g_stage
    always_ff @(posedge clk) begin
      side_t                sd;
      logic                 hyp, ccw, xsub, zsub;
      logic signed [CW+1:0] ystep, xstep;
      logic signed [ZW-1:0] c;
      sd    = cs[i];                             // (a local copy, for Icarus as above)
      hyp   = (sd.mode == M_HROT) || (sd.mode == M_HVEC);
      // Counterclockwise when z >= 0 (rotation modes) or y < 0 (vectoring).
      ccw   = (sd.mode == M_ROT || sd.mode == M_HROT) ? !cz[i][ZW-1] : cy[i][CW+1];
      // Separate statements on purpose: in "lin ? '0 : (cy >>> i)" the
      // unsigned '0 makes the whole expression unsigned, and >>> then shifts
      // in zeros instead of copies of the sign bit (an SV signedness rule).
      xstep = cx[i] >>> i;
      ystep = cy[i] >>> i;
      if (hyp) begin
        xstep = cx[i] >>> hshift(i);
        ystep = cy[i] >>> hshift(i);
      end
      if (sd.mode == M_LIN) ystep = '0;
      case (sd.mode)
        M_ROT:   c = atan_q(i);
        M_VEC:   c = atan_r(i);
        M_HROT:  c = atanh_l(i);
        M_HVEC:  c = atanh_n(i);
        default: c = ZW'(1) << (CZ - i);         // linear: 2^-i
      endcase
      // x moves against y in circular modes and with it in hyperbolic ones.
      // z steps down when turning counterclockwise, up otherwise; zneg
      // swaps that, which is the same as negating the step.
      xsub  = ccw ^ hyp;
      zsub  = ccw ^ sd.zneg;
      cx[i+1] <= xsub ? cx[i] - ystep : cx[i] + ystep;
      cy[i+1] <= ccw ? cy[i] + xstep : cy[i] - xstep;
      cz[i+1] <= zsub ? cz[i] - c : cz[i] + c;
      cs[i+1] <= sd;
    end
  end

  // ----------------------------------------------------- small-angle path
  // sin t = t for t < 2^-SMALL_T quarter turns. t = f' * pi/2 as a float:
  // normalize f' (find its leading 1 among the top 32 bits of sp_win, shift
  // it to the top), multiply the top 32 bits by pi/2, then line the product
  // up as a 26-bit significand for rounding. Five stages, then a delay line
  // so the result arrives together with the CORDIC output.
  logic [63:0] sp1_win;
  logic [5:0]  sp1_lz, sp2_lz, sp3_lz, sp4_lz;
  logic [31:0] sp2_m;
  logic [63:0] sp3_prod, sp4_prod;
  logic [7:0]  sp_exp [CN-4];
  logic [25:0] sp_sig [CN-4];

  always_ff @(posedge clk) begin
    logic [63:0] sh;
    sp1_win  <= sp_win;
    sp1_lz   <= lzc32(sp_win[63:32]);
    sh        = sp1_win << sp1_lz;
    sp2_m    <= sh[63:32];
    sp2_lz   <= sp1_lz;
    sp3_prod <= sp2_m * PIO2;                    // 32 x 32 on DSP48s, two registers
    sp3_lz   <= sp2_lz;
    sp4_prod <= sp3_prod;
    sp4_lz   <= sp3_lz;
    // The product's leading 1 is at bit 63 or 62. Keep 25 bits from there and
    // OR the rest into the sticky bit. Working through the scaling (see
    // small_sin in cordic_model.py), t = 1.xxx * 2^(-SMALL_T - lz) when the
    // leading 1 is at bit 63, one power of two less otherwise.
    if (sp4_prod[63]) begin
      sp_sig[0] <= {sp4_prod[63:39], sp4_prod[38:0] != '0};
      sp_exp[0] <= 8'(127 - SMALL_T) - 8'(sp4_lz);
    end else begin
      sp_sig[0] <= {sp4_prod[62:38], sp4_prod[37:0] != '0};
      sp_exp[0] <= 8'(126 - SMALL_T) - 8'(sp4_lz);
    end
    // Delay line. These registers have no reset, so synthesis can pack them
    // into shift-register LUTs (SRL32) instead of flip-flops.
    for (int k = 1; k < CN - 4; k++) begin
      sp_sig[k] <= sp_sig[k-1];
      sp_exp[k] <= sp_exp[k-1];
    end
  end

  // ------------------------------------------------------- back end
  // Stage CN+7: choose x, y or z and find its leading 1. x and y are widened
  // to z's width (CW == CZ, so the binary points line up). Every value that
  // gets here lies between 2^-14 and 52 (see normalize_cordic in the model),
  // so the leading 1 is within the 24 positions from bit CW+6 (64) down to
  // bit CW-17.
  // The result's exponent is worked out here too, as soon as the leading-zero
  // count is known: bit CW+1 is worth 2^(ebase + 1 - 127). Doing that add in
  // the next stage put it in front of round_pre's subnormal shifter, which
  // was the slowest path in the unit.
  side_t                b1;
  logic signed [ZW-1:0] b1_val;
  logic [4:0]           b1_lz;
  logic signed [EW-1:0] b1_exp;
  logic [7:0]           b1_sexp;
  logic [25:0]          b1_ssig;

  always_ff @(posedge clk) begin
    logic signed [ZW-1:0] val;
    logic [4:0]           lz;
    side_t                last;
    last = cs[CN];                               // (a local copy, for Icarus as above)
    case (last.sel)
      SEL_X:   val = ZW'(cx[CN]);                // sign-extending: x, y are signed
      SEL_Z:   val = cz[CN];
      default: val = ZW'(cy[CN]);
    endcase
    lz       = 5'(lzc24(val[CW+6 -: 24]));
    b1      <= last;
    b1_val  <= val;
    b1_lz   <= lz;
    b1_exp  <= last.ebase + EW'(6) - EW'(lz);    // bit CW+6 is 2^(ebase + 6 - 127)
    b1_sexp <= sp_exp[CN-5];
    b1_ssig <= sp_sig[CN-5];
  end

  // Stage CN+8: shift the leading 1 up to bit CW+6, cut 25 bits plus a sticky
  // bit, and pick the small-angle result instead when that path is in use.
  side_t                b2;
  logic [25:0]          b2_sig;
  logic signed [EW-1:0] b2_exp;

  always_ff @(posedge clk) begin
    logic [CW+30:0] sh;
    sh = {24'b0, b1_val[CW+6:0]} << b1_lz;
    b2 <= b1;
    if (b1.sel == SEL_SMALL) begin
      b2_sig <= b1_ssig;
      b2_exp <= EW'(b1_sexp);
    end else begin
      b2_sig <= {sh[CW+6 -: 25], sh[CW-19:0] != '0};
      b2_exp <= b1_exp;
    end
  end

  // Stage CN+9: the first half of rounding, as in the other units. An atan2
  // y/x or hypot result can be subnormal (or round to zero); round_pre's
  // right shift handles that. It has a stage to itself because doing both
  // variable shifts (normalize, then subnormal) in one clock left only
  // 0.06 ns of slack at 100 MHz.
  side_t      b3;
  round_mid_t b3_mid;

  always_ff @(posedge clk) begin
    b3     <= b2;
    b3_mid <= round_pre(b2.neg, b2_exp, b2_sig);
  end

  // Stage CN+10: second half of rounding and the output register. Every
  // result that reaches here is inexact by the phase 3 rule, so NX is always
  // set, and a result below 2^-126 (tiny) also gets UF, even in the rare case
  // where its rounding happened to be exact.
  always_ff @(posedge clk) begin
    logic [36:0] rp;
    logic        tiny;
    rp   = round_post(b3_mid);
    tiny = (rp[30:0] < 31'h0080_0000);
    out_valid  <= rst ? 1'b0 : vpipe[LAT-2];
    out_tag    <= b3.tag;
    out_result <= b3.done ? b3.res : rp[31:0];
    out_flags  <= b3.done ? b3.fl  : (rp[36:32] | FFLAG_NX | (tiny ? FFLAG_UF : 5'b0));
  end
endmodule
