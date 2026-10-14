// Pipelined fp32 multiply, one operation per clock, latency LAT = 6.
//
// Multiplying (1.f1 * 2^e1) by (1.f2 * 2^e2) gives (1.f1 * 1.f2) * 2^(e1+e2):
// multiply the significands, add the exponents. The significand product of
// two 24-bit numbers is 48 bits and lies in [1, 4), so at most a one-place
// renormalization is needed afterwards.
//
// Subnormal inputs have leading zeros in their significand. I shift them up
// first ("normalize") and lower their exponent to match, so the multiplier
// always sees two significands with a leading 1. That keeps the product in
// [1, 4) and lets round_pack handle a possibly subnormal result.
//   stage 1  unpack, special values, count leading zeros of each significand
//   stage 2  normalize subnormal significands, add exponents
//   stage 3  24 x 24 multiply (maps onto the FPGA's DSP48 multiplier blocks)
//   stage 4  second multiplier register (lets the DSP blocks run at speed)
//   stage 5  renormalize the 48-bit product, first half of rounding
//   stage 6  second half of rounding, output register
module fp_mul
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,
    input  logic          rst,
    input  logic          in_valid,
    input  logic [31:0]   in_a,
    input  logic [31:0]   in_b,
    input  logic [TW-1:0] in_tag,
    output logic          out_valid,
    output logic [31:0]   out_result,
    output logic [4:0]    out_flags,
    output logic [TW-1:0] out_tag
);
  // ---------------------------------------------------------------- stage 1
  logic                 s1_v, s1_special, s1_sign;
  logic [TW-1:0]        s1_tag;
  logic [23:0]          s1_ma, s1_mb;
  logic [5:0]           s1_lza, s1_lzb;
  logic signed [EW-1:0] s1_ea, s1_eb;
  logic [31:0]          s1_res;
  logic [4:0]           s1_fl;

  always_ff @(posedge clk) begin
    s1_v    <= rst ? 1'b0 : in_valid;
    s1_tag  <= in_tag;
    s1_sign <= in_a[31] ^ in_b[31];      // sign of a product: XOR of the signs
    s1_ma   <= mant24(in_a);
    s1_mb   <= mant24(in_b);
    s1_lza  <= lzc24(mant24(in_a));      // 0 for normal numbers
    s1_lzb  <= lzc24(mant24(in_b));
    s1_ea   <= exp_eff(in_a);
    s1_eb   <= exp_eff(in_b);

    s1_special <= 1'b1;
    s1_fl      <= '0;
    if (is_nan(in_a) || is_nan(in_b)) begin
      s1_res <= CANON_NAN;
      s1_fl  <= (is_snan(in_a) || is_snan(in_b)) ? FFLAG_NV : '0;
    end else if ((is_inf(in_a) && is_zero(in_b)) || (is_zero(in_a) && is_inf(in_b))) begin
      s1_res <= CANON_NAN;               // inf * 0 is undefined
      s1_fl  <= FFLAG_NV;
    end else if (is_inf(in_a) || is_inf(in_b)) begin
      s1_res <= {in_a[31] ^ in_b[31], POS_INF[30:0]};
    end else if (is_zero(in_a) || is_zero(in_b)) begin
      s1_res <= {in_a[31] ^ in_b[31], 31'b0};   // exact signed zero, no flags
    end else begin
      s1_special <= 1'b0;
      s1_res     <= '0;
    end
  end

  // ---------------------------------------------------------------- stage 2
  // value = (ma / 2^23) * 2^(ea-127). Shifting ma left by lz and subtracting
  // lz from ea keeps the value the same. The product exponent is then
  // (ea - 127) + (eb - 127), which as a biased exponent is ea + eb - 127.
  logic                 s2_v, s2_special, s2_sign;
  logic [TW-1:0]        s2_tag;
  logic [23:0]          s2_ma, s2_mb;
  logic signed [EW-1:0] s2_exp;
  logic [31:0]          s2_res;
  logic [4:0]           s2_fl;

  always_ff @(posedge clk) begin
    s2_v <= rst ? 1'b0 : s1_v;
    s2_tag <= s1_tag;  s2_special <= s1_special;  s2_sign <= s1_sign;
    s2_res <= s1_res;  s2_fl <= s1_fl;
    s2_ma  <= s1_ma << s1_lza;
    s2_mb  <= s1_mb << s1_lzb;
    s2_exp <= s1_ea - EW'(s1_lza) + s1_eb - EW'(s1_lzb) - EW'(127);
  end

  // ------------------------------------------------------- stages 3 and 4
  // The multiplication itself. Vivado maps a * b followed by two registers
  // onto DSP48 slices and uses the registers inside the DSP, which is how the
  // multiplier meets timing at 100 MHz. A 24 x 24 product needs two DSP48s.
  logic                 s3_v, s3_special, s3_sign, s4_v, s4_special, s4_sign;
  logic [TW-1:0]        s3_tag, s4_tag;
  logic signed [EW-1:0] s3_exp, s4_exp, s4_exp_inc;
  logic [47:0]          s3_prod, s4_prod;
  logic [31:0]          s3_res, s4_res;
  logic [4:0]           s3_fl, s4_fl;

  always_ff @(posedge clk) begin
    s3_v <= rst ? 1'b0 : s2_v;
    s3_tag <= s2_tag;  s3_special <= s2_special;  s3_sign <= s2_sign;
    s3_res <= s2_res;  s3_fl <= s2_fl;  s3_exp <= s2_exp;
    s3_prod <= s2_ma * s2_mb;
    s4_v <= rst ? 1'b0 : s3_v;
    s4_tag <= s3_tag;  s4_special <= s3_special;  s4_sign <= s3_sign;
    s4_res <= s3_res;  s4_fl <= s3_fl;  s4_exp <= s3_exp;
    // exp + 1 is needed in stage 5 when the product is >= 2. Computing it
    // here keeps one adder out of stage 5, which was the slowest path in
    // the design (it met 100 MHz by only 0.013 ns before this change).
    s4_exp_inc <= s3_exp + 1'b1;
    s4_prod <= s3_prod;
  end

  // ---------------------------------------------------------------- stage 5
  // Both significands were in [2^23, 2^24), so the product is in [2^46, 2^48):
  // its leading 1 is at bit 47 (product >= 2.0) or bit 46 (product < 2.0).
  // Keep 25 bits starting at the leading 1 (24 significand + guard) and OR
  // everything below into the sticky bit, then do the first half of
  // rounding (subnormal shift) in the same stage.
  logic          s5_v, s5_special;
  logic [TW-1:0] s5_tag;
  round_mid_t    s5_mid;
  logic [31:0]   s5_res;
  logic [4:0]    s5_fl;

  always_ff @(posedge clk) begin
    logic [25:0]          sig;
    logic signed [EW-1:0] exp;
    s5_v <= rst ? 1'b0 : s4_v;
    s5_tag <= s4_tag;  s5_special <= s4_special;
    s5_res <= s4_res;  s5_fl <= s4_fl;
    if (s4_prod[47]) begin
      sig = {s4_prod[47:23], s4_prod[22:0] != '0};
      exp = s4_exp_inc;                  // product was >= 2: one more power of 2
    end else begin
      sig = {s4_prod[46:22], s4_prod[21:0] != '0};
      exp = s4_exp;
    end
    s5_mid <= round_pre(s4_sign, exp, sig);
  end

  // ---------------------------------------------------------------- stage 6
  always_ff @(posedge clk) begin
    logic [36:0] rp;
    rp = round_post(s5_mid);
    out_valid  <= rst ? 1'b0 : s5_v;
    out_tag    <= s5_tag;
    out_result <= s5_special ? s5_res : rp[31:0];
    out_flags  <= s5_special ? s5_fl  : rp[36:32];
  end
endmodule
