// Conversions, min/max and compares, pipelined with latency LAT = 6.
//
//   I2F       signed 32-bit integer -> fp32 (rounds when |int| > 2^24)
//   F2I       fp32 -> signed 32-bit integer, round to nearest even,
//             saturating to INT_MAX / INT_MIN with the invalid flag
//   MIN, MAX  IEEE minimumNumber / maximumNumber (RISC-V fmin/fmax): a NaN
//             operand loses to a number, -0 counts as smaller than +0
//   EQ        quiet equality: invalid only for signaling NaN inputs
//   LT, LE    signaling compares: invalid for any NaN input
// Compare results are the integers 0 or 1 in the 32-bit result.
// I route table LOADs through the same tagged write-back as arithmetic.
// I preserve the bits here, including sNaNs, because loading is not an add.
//   MOVE      op code EVU_OP_LOAD: a unchanged, no flags, even for a signaling
//             NaN. Only the expression unit sends it, so that a table value
//             reaches its register through the same write path as every other
//             result. cmd_proc never lets a host send it.
//
// The real work takes four stages (I2F rounding uses two); stages 5-6 are
// plain delay registers so this unit has the same latency as fp_addsub and
// fp_mul.
// I compare the encoded fields rather than using Verilog real arithmetic:
// these are synthesizable bit comparisons, shifts and multiplexers. The five
// op bits select a path; each path still carries its tag through six stages.
// Even a comparison that is already decided in stage 1 waits in registers
// so the FPU can merge it with add and multiply without result collisions.
module fp_misc
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,                   // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic          rst,                   // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic          in_valid,              // Input valid: a high level presents a new operation for acceptance on the rising clock edge.
    input  logic [4:0]    in_op,                 // Input operation code: selects conversion, minimum/maximum or comparison; unsupported codes return invalid NaN.
    input  logic [31:0]   in_a,                  // Input operand A: floating-point bits, or a signed 32-bit integer for integer-to-float conversion.
    input  logic [31:0]   in_b,                  // Input operand B: floating-point comparison/minimum/maximum operand; ignored for conversions.
    input  logic [TW-1:0] in_tag,                // Input request tag: identifier carried beside the operands so the caller can match the result.
    output logic          out_valid,             // Output valid: result, exception flags and tag describe a completed operation on this clock.
    output logic [31:0]   out_result,            // Output result: floating-point bits, signed integer bits for float-to-integer, or integer 0/1 for comparisons.
    output logic [4:0]    out_flags,             // Output exception flags: bits 4..0 mean invalid, divide by zero, overflow, underflow and inexact.
    output logic [TW-1:0] out_tag                // Output request tag: unchanged identifier of the operation that produced this result.
);
  // ---------------------------------------------------------------- stage 1
  // Everything here only looks at bit fields or does one shift/compare.
  logic                 s1_v, s1_done, s1_neg, s1_zero;
  logic [4:0]           s1_op;
  logic [TW-1:0]        s1_tag;
  logic [31:0]          s1_res, s1_abs;   // s1_abs: |integer| for I2F
  logic [5:0]           s1_lz;
  logic [4:0]           s1_fl;
  logic [23:0]          s1_int;           // F2I integer part before rounding
  logic                 s1_g, s1_st;      // F2I guard and sticky bits

  // Signed-magnitude ordering helpers (valid when neither input is NaN).
  logic both_zero, a_lt_b, a_eq_b, a_tot_lt;
  always_comb begin
    both_zero = is_zero(in_a) && is_zero(in_b);
    a_eq_b    = (in_a == in_b) || both_zero;          // -0 == +0
    // Different signs: the negative one is smaller (unless both are zero).
    // Same sign: compare the magnitude bits; for negatives the order flips.
    if (in_a[31] != in_b[31]) a_lt_b = in_a[31] && !both_zero;
    else if (in_a[31])         a_lt_b = in_a[30:0] > in_b[30:0];
    else                       a_lt_b = in_a[30:0] < in_b[30:0];
    // Total-order version used by MIN/MAX: identical except -0 < +0.
    if (in_a[31] != in_b[31]) a_tot_lt = in_a[31];
    else                       a_tot_lt = a_lt_b;
  end

  always_ff @(posedge clk) begin
    logic        nan_any, snan_any;
    logic [7:0]  e;
    logic [55:0] wide;
    logic [7:0]  sh;

    s1_v   <= rst ? 1'b0 : in_valid;
    s1_tag <= in_tag;
    s1_op  <= in_op;
    s1_fl  <= '0;
    s1_res <= '0;
    s1_done <= 1'b1;                  // 1: result already final after stage 1
    nan_any  = is_nan(in_a) || is_nan(in_b);
    snan_any = is_snan(in_a) || is_snan(in_b);

    // I2F prep: absolute value and its leading zeros. -(-2^31) wraps to
    // 0x80000000, which is the right unsigned magnitude.
    s1_neg  <= in_a[31];
    s1_abs  <= in_a[31] ? (~in_a + 1'b1) : in_a;
    s1_lz   <= lzc32(in_a[31] ? (~in_a + 1'b1) : in_a);
    s1_zero <= (in_a == '0);

    // F2I prep: line the significand up so the binary point sits between
    // bit 32 and bit 31 of `wide`. The value is mant24 * 2^(e-150).
    e  = in_a[30:23];
    sh = 8'd150 - e;
    wide = {mant24(in_a), 32'b0} >> sh;      // only used when 1 <= sh <= 24
    s1_int <= '0; s1_g <= 1'b0; s1_st <= 1'b0;

    case (in_op)
      FOP_I2F: s1_done <= 1'b0;              // finished in stages 2-3

      FOP_F2I: begin
        if (is_nan(in_a)) begin
          s1_res <= 32'h7FFF_FFFF;  s1_fl <= FFLAG_NV;
        end else if (in_a == 32'hCF00_0000) begin
          s1_res <= 32'h8000_0000;           // exactly -2^31 fits
        end else if (e >= 8'd158) begin       // |x| >= 2^31, includes inf
          s1_res <= in_a[31] ? 32'h8000_0000 : 32'h7FFF_FFFF;
          s1_fl  <= FFLAG_NV;
        end else if (e >= 8'd150) begin       // integer already, shift left
          s1_res <= in_a[31] ? -(32'(mant24(in_a)) << (e - 8'd150))
                             :  (32'(mant24(in_a)) << (e - 8'd150));
        end else if (sh > 8'd24) begin        // |x| < 0.5 rounds to 0
          s1_res <= '0;
          s1_fl  <= (in_a[30:0] != '0) ? FFLAG_NX : '0;
        end else begin                        // fraction bits to round away
          s1_done <= 1'b0;
          s1_int  <= wide[55:32];
          s1_g    <= wide[31];
          s1_st   <= wide[30:0] != '0;
        end
      end

      FOP_MIN, FOP_MAX: begin
        s1_fl <= snan_any ? FFLAG_NV : '0;
        if (is_nan(in_a) && is_nan(in_b)) s1_res <= CANON_NAN;
        else if (is_nan(in_a))            s1_res <= in_b;
        else if (is_nan(in_b))            s1_res <= in_a;
        else if (in_op == FOP_MIN)        s1_res <= (a_tot_lt || in_a == in_b) ? in_a : in_b;
        else                              s1_res <= a_tot_lt ? in_b : in_a;
      end

      FOP_EQ: begin
        s1_res <= {31'b0, !nan_any && a_eq_b};
        s1_fl  <= snan_any ? FFLAG_NV : '0;
      end
      FOP_LT: begin
        s1_res <= {31'b0, !nan_any && a_lt_b};
        s1_fl  <= nan_any ? FFLAG_NV : '0;
      end
      FOP_LE: begin
        s1_res <= {31'b0, !nan_any && (a_lt_b || a_eq_b)};
        s1_fl  <= nan_any ? FFLAG_NV : '0;
      end

      5'(EVU_OP_LOAD): s1_res <= in_a;         // MOVE: s1_fl stays 0

      default: begin                           // not an op this unit knows
        s1_res <= CANON_NAN;  s1_fl <= FFLAG_NV;
      end
    endcase
  end

  // ---------------------------------------------------------------- stage 2
  // I2F: shift the integer so its leading 1 is at bit 31; that bit becomes
  // the hidden 1. The exponent is 127 + (31 - lz).
  // F2I: round the integer part to nearest even and apply the sign.
  logic                 s2_v, s2_done, s2_neg, s2_zero;
  logic [TW-1:0]        s2_tag;
  logic [4:0]           s2_op;
  logic [31:0]          s2_res;
  logic [4:0]           s2_fl;
  logic [25:0]          s2_sig;
  logic signed [EW-1:0] s2_exp;

  always_ff @(posedge clk) begin
    logic [31:0] norm, mag;
    logic        inc;
    s2_v <= rst ? 1'b0 : s1_v;
    s2_tag <= s1_tag;  s2_op <= s1_op;  s2_neg <= s1_neg;  s2_zero <= s1_zero;
    s2_done <= s1_done;  s2_res <= s1_res;  s2_fl <= s1_fl;
    norm    = s1_abs << s1_lz;
    s2_sig <= {norm[31:7], norm[6:0] != '0};
    s2_exp <= EW'(127 + 31) - EW'(s1_lz);
    if (!s1_done && s1_op == FOP_F2I) begin
      inc = s1_g & (s1_st | s1_int[0]);
      mag = 32'(s1_int) + inc;
      s2_res  <= s1_neg ? -mag : mag;
      s2_fl   <= (s1_g | s1_st) ? FFLAG_NX : '0;
      s2_done <= 1'b1;
    end
  end

  // ---------------------------------------------------------------- stage 3
  // I2F rounding, first half. Other ops already have their result.
  logic          s3_v, s3_bypass;
  logic [TW-1:0] s3_tag;
  round_mid_t    s3_mid;
  logic [31:0]   s3_res;
  logic [4:0]    s3_fl;

  always_ff @(posedge clk) begin
    s3_v      <= rst ? 1'b0 : s2_v;
    s3_tag    <= s2_tag;
    s3_mid    <= round_pre(s2_neg, s2_exp, s2_sig);
    s3_bypass <= s2_done || s2_zero;
    if (s2_done) begin
      s3_res <= s2_res;  s3_fl <= s2_fl;
    end else begin
      s3_res <= '0;      s3_fl <= '0;            // integer 0 -> +0.0
    end
  end

  // ---------------------------------------------------------------- stage 4
  // I2F rounding, second half.
  logic          s4_v;
  logic [TW-1:0] s4_tag;
  logic [31:0]   s4_res;
  logic [4:0]    s4_fl;

  always_ff @(posedge clk) begin
    logic [36:0] rp;
    rp = round_post(s3_mid);
    s4_v   <= rst ? 1'b0 : s3_v;
    s4_tag <= s3_tag;
    s4_res <= s3_bypass ? s3_res : rp[31:0];
    s4_fl  <= s3_bypass ? s3_fl  : rp[36:32];
  end

  // ---------------------------------------------------------- stages 5-6
  // Delay line: two more registers to match the other units' latency.
  logic          d_v   [2];
  logic [TW-1:0] d_tag [2];
  logic [31:0]   d_res [2];
  logic [4:0]    d_fl  [2];

  always_ff @(posedge clk) begin
    d_v[0] <= rst ? 1'b0 : s4_v;  d_tag[0] <= s4_tag;  d_res[0] <= s4_res;  d_fl[0] <= s4_fl;
    d_v[1] <= rst ? 1'b0 : d_v[0];
    d_tag[1] <= d_tag[0];  d_res[1] <= d_res[0];  d_fl[1] <= d_fl[0];
  end

  assign out_valid  = d_v[1];
  assign out_tag    = d_tag[1];
  assign out_result = d_res[1];
  assign out_flags  = d_fl[1];
endmodule
