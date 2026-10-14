// Pipelined fp32 add / subtract. Accepts one operation per clock and returns
// its result exactly LAT = 6 clocks later. There is no stall: the pipeline
// always moves forward, so the caller must accept every result.
//
// Floating-point addition works like adding numbers in scientific notation by
// hand: line up the exponents, add the significands, then renormalize.
//   stage 1  unpack, handle special values, order operands so |x| >= |y|
//   stage 2  shift the smaller significand right to line up the exponents
//   stage 3  add or subtract the significands, count leading zeros
//   stage 4  shift the sum so its leading 1 is back in the top position
//   stage 5  first half of rounding (fp_pkg::round_pre)
//   stage 6  second half of rounding (fp_pkg::round_post), output register
// Each "stage" ends at a register (an always_ff assignment), so the logic
// between two registers only has to settle within one 10 ns clock period.
module fp_addsub
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8          // tag width: an ID carried alongside the data
) (
    input  logic          clk,
    input  logic          rst,
    input  logic          in_valid,
    input  logic          in_sub,   // 1: compute a - b
    input  logic [31:0]   in_a,
    input  logic [31:0]   in_b,
    input  logic [TW-1:0] in_tag,
    output logic          out_valid,
    output logic [31:0]   out_result,
    output logic [4:0]    out_flags,
    output logic [TW-1:0] out_tag
);
  // ---------------------------------------------------------------- stage 1
  // Subtraction is addition with b's sign flipped. "Effective subtraction"
  // means the two (sign-adjusted) operands have different signs, so their
  // magnitudes are subtracted.
  logic [31:0] b_adj;
  logic        a_big;
  assign b_adj = {in_b[31] ^ in_sub, in_b[30:0]};
  // Compare magnitudes by comparing bits 30:0. For non-negative IEEE values
  // the bit patterns sort the same way the numbers do.
  assign a_big = in_a[30:0] >= b_adj[30:0];

  logic                 s1_v, s1_special, s1_eff_sub, s1_sx;
  logic [TW-1:0]        s1_tag;
  logic signed [EW-1:0] s1_ex;
  logic [7:0]           s1_d;           // exponent difference, saturated
  logic [23:0]          s1_mx, s1_my;
  logic [31:0]          s1_res;         // result for special cases
  logic [4:0]           s1_fl;

  always_ff @(posedge clk) begin
    logic [31:0] x, y;
    logic [EW-1:0] diff;
    s1_v   <= rst ? 1'b0 : in_valid;   // valid bits need reset; data does not
    s1_tag <= in_tag;
    // x is the operand with the larger magnitude, y the smaller.
    x = a_big ? in_a : b_adj;
    y = a_big ? b_adj : in_a;
    s1_sx      <= x[31];
    s1_eff_sub <= x[31] ^ y[31];
    s1_ex      <= exp_eff(x);
    s1_mx      <= mant24(x);
    s1_my      <= mant24(y);
    diff        = exp_eff(x) - exp_eff(y);       // >= 0 because |x| >= |y|
    s1_d       <= (diff > 31) ? 8'd31 : diff[7:0];

    // Special values decide the result without any arithmetic.
    s1_special <= 1'b1;
    s1_fl      <= '0;
    if (is_nan(in_a) || is_nan(in_b)) begin
      s1_res <= CANON_NAN;
      s1_fl  <= (is_snan(in_a) || is_snan(in_b)) ? FFLAG_NV : '0;
    end else if (is_inf(in_a) && is_inf(b_adj) && (in_a[31] != b_adj[31])) begin
      s1_res <= CANON_NAN;                        // +inf + -inf is undefined
      s1_fl  <= FFLAG_NV;
    end else if (is_inf(in_a)) begin
      s1_res <= in_a;
    end else if (is_inf(b_adj)) begin
      s1_res <= b_adj;
    end else begin
      s1_special <= 1'b0;
      s1_res     <= '0;
    end
  end

  // ---------------------------------------------------------------- stage 2
  // Align: shift y right by the exponent difference. I keep 3 extra low bits
  // (guard, round, sticky) so no rounding information is lost. y has 24 bits;
  // with 3 extra bits the aligned value is 27 bits wide.
  logic                 s2_v, s2_special, s2_eff_sub, s2_sx;
  logic [TW-1:0]        s2_tag;
  logic signed [EW-1:0] s2_ex;
  logic [26:0]          s2_x, s2_y;
  logic [31:0]          s2_res;
  logic [4:0]           s2_fl;

  always_ff @(posedge clk) begin
    // y followed by 32 zero bits. The shift is at most 31, so even y's lowest
    // bit stays inside `wide` and can reach the sticky OR below. (With fewer
    // zero bits a tiny y was shifted out entirely and the inexact flag lost.)
    logic [55:0] wide;
    logic [26:0] shifted;
    logic        sticky;
    s2_v <= rst ? 1'b0 : s1_v;  s2_tag <= s1_tag;  s2_special <= s1_special;
    s2_res <= s1_res;  s2_fl <= s1_fl;
    s2_eff_sub <= s1_eff_sub;  s2_sx <= s1_sx;  s2_ex <= s1_ex;
    s2_x <= {s1_mx, 3'b000};
    wide    = {s1_my, 32'b0} >> s1_d;
    shifted = wide[55:29];             // 24 bits of y plus guard, round, sticky slots
    sticky  = wide[28:0] != '0;        // anything shifted below the 3 extra bits
    s2_y <= {shifted[26:1], shifted[0] | sticky};
  end

  // ---------------------------------------------------------------- stage 3
  // Add or subtract. Because |x| >= |y| the difference is never negative.
  // The sum has one more bit (bit 27) for a carry out of addition.
  logic                 s3_v, s3_special, s3_eff_sub, s3_sx;
  logic [TW-1:0]        s3_tag;
  logic signed [EW-1:0] s3_ex;
  logic [27:0]          s3_sum;
  logic [5:0]           s3_lz;
  logic [31:0]          s3_res;
  logic [4:0]           s3_fl;

  always_ff @(posedge clk) begin
    logic [27:0] sum;
    s3_v <= rst ? 1'b0 : s2_v;  s3_tag <= s2_tag;  s3_special <= s2_special;
    s3_res <= s2_res;  s3_fl <= s2_fl;
    s3_eff_sub <= s2_eff_sub;  s3_sx <= s2_sx;  s3_ex <= s2_ex;
    sum     = s2_eff_sub ? ({1'b0, s2_x} - {1'b0, s2_y}) : ({1'b0, s2_x} + {1'b0, s2_y});
    s3_sum <= sum;
    s3_lz  <= lzc28(sum);              // used by stage 4 to renormalize
  end

  // ---------------------------------------------------------------- stage 4
  // Renormalize so the leading 1 sits at bit 26 (where x's hidden bit was).
  //  - carry out (bit 27 set): shift right one place, exponent + 1
  //  - cancellation (leading zeros): shift left, exponent - (zeros - 1)
  // Left shifts bring in zeros, which is exact: when operands are close
  // enough for massive cancellation, alignment shifted y by at most one
  // place, so no bits were lost into the sticky bit.
  // The result is squeezed into round_pack's 26-bit format.
  logic                 s4_v, s4_special, s4_zero, s4_sign;
  logic [TW-1:0]        s4_tag;
  logic signed [EW-1:0] s4_exp;
  logic [25:0]          s4_sig;
  logic [31:0]          s4_res;
  logic [4:0]           s4_fl;

  always_ff @(posedge clk) begin
    logic [27:0] norm;
    s4_v <= rst ? 1'b0 : s3_v;  s4_tag <= s3_tag;  s4_special <= s3_special;
    s4_res <= s3_res;  s4_fl <= s3_fl;
    s4_sign <= s3_sx;
    // Exact zero: x - x. In round-to-nearest the sum of opposite-signed equal
    // values is +0; adding two zeros of the same sign keeps that sign.
    s4_zero <= (s3_sum == '0);
    if (s3_sum == '0) s4_sign <= s3_eff_sub ? 1'b0 : s3_sx;
    if (s3_sum[27]) begin
      norm    = s3_sum >> 1;
      s4_exp <= s3_ex + 1'b1;
      // The bit shifted out must not be forgotten: fold it into the sticky.
      norm[0] = norm[0] | s3_sum[0];
    end else begin
      norm    = s3_sum << (s3_lz - 1'b1);
      s4_exp <= s3_ex - EW'(s3_lz) + 1'b1;
    end
    // norm[26] is the leading 1; [25:3] fraction; [2] guard; [1:0] -> sticky
    s4_sig <= {norm[26:2], norm[1] | norm[0]};
  end

  // ---------------------------------------------------------------- stage 5
  // First half of rounding (see fp_pkg): subnormal shift and tininess.
  // Special-value and exact-zero results bypass rounding.
  logic          s5_v, s5_bypass;
  logic [TW-1:0] s5_tag;
  round_mid_t    s5_mid;
  logic [31:0]   s5_res;
  logic [4:0]    s5_fl;

  always_ff @(posedge clk) begin
    s5_v      <= rst ? 1'b0 : s4_v;
    s5_tag    <= s4_tag;
    s5_mid    <= round_pre(s4_sign, s4_exp, s4_sig);
    s5_bypass <= s4_special || s4_zero;
    if (s4_special) begin
      s5_res <= s4_res;  s5_fl <= s4_fl;
    end else begin
      s5_res <= {s4_sign, 31'b0};  s5_fl <= '0;   // exact zero
    end
  end

  // ---------------------------------------------------------------- stage 6
  // Second half of rounding: increment, carry, overflow, pack. This is also
  // the output register; every pipelined unit has the same latency (6) so
  // their results can never collide on the shared result bus.
  always_ff @(posedge clk) begin
    logic [36:0] rp;
    rp = round_post(s5_mid);
    out_valid  <= rst ? 1'b0 : s5_v;
    out_tag    <= s5_tag;
    out_result <= s5_bypass ? s5_res : rp[31:0];
    out_flags  <= s5_bypass ? s5_fl  : rp[36:32];
  end

endmodule
