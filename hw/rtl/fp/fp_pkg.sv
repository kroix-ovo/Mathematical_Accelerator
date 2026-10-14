// Shared fp32 helpers for the FPU units.
//
// IEEE-754 single precision packs a number into 32 bits:
//   bit 31      sign (1 = negative)
//   bits 30:23  biased exponent e (stored value = real exponent + 127)
//   bits 22:0   fraction f
// e = 1..254 is a normal number:    (-1)^s * 1.f * 2^(e-127)
// e = 0      is zero or subnormal:  (-1)^s * 0.f * 2^-126   (no hidden 1)
// e = 255    is infinity (f = 0) or NaN (f != 0).
// A NaN whose top fraction bit (bit 22) is clear is "signaling" (sNaN); using
// one as an input raises the invalid flag. Bit 22 set means "quiet" (qNaN).
//
// Every arithmetic unit produces an exact or nearly exact intermediate result
// and hands it to the rounding functions at the bottom of this file
// (round_pre, then round_post one clock later). They are the only place that
// rounds, so there is one piece of rounding logic to get right, and it
// mirrors round_fraction() in the Python model (sw/mathaccel/fp32ref.py).
package fp_pkg;
  import accel_pkg::*;

  localparam logic [31:0] CANON_NAN = 32'h7FC0_0000;
  localparam logic [31:0] POS_INF   = 32'h7F80_0000;

  // Signed exponent width used inside the units. Exponents can go far below
  // zero (products of two subnormals) or above 254 (overflow) before rounding,
  // so 12 bits (-2048..2047) leaves plenty of headroom.
  localparam int EW = 12;

  // ---- classification -----------------------------------------------------
  // Each helper looks only at bit fields, so it becomes a few gates.
  function automatic logic is_zero(input logic [31:0] x);
    return x[30:0] == '0;
  endfunction
  function automatic logic is_sub(input logic [31:0] x);     // subnormal
    return x[30:23] == 8'h00 && x[22:0] != '0;
  endfunction
  function automatic logic is_inf(input logic [31:0] x);
    return x[30:23] == 8'hFF && x[22:0] == '0;
  endfunction
  function automatic logic is_nan(input logic [31:0] x);
    return x[30:23] == 8'hFF && x[22:0] != '0;
  endfunction
  function automatic logic is_snan(input logic [31:0] x);
    return is_nan(x) && !x[22];
  endfunction

  // Significand with the hidden bit made explicit (24 bits). A subnormal has
  // no hidden 1, so bit 23 is just "exponent field is nonzero".
  function automatic logic [23:0] mant24(input logic [31:0] x);
    return {x[30:23] != 8'h00, x[22:0]};
  endfunction

  // Exponent to use in arithmetic. A subnormal behaves as if its exponent
  // were 1 (it shares 2^-126 with the smallest normal numbers).
  function automatic logic signed [EW-1:0] exp_eff(input logic [31:0] x);
    return (x[30:23] == 8'h00) ? EW'(1) : EW'({1'b0, x[30:23]});
  endfunction

  // ---- leading zero count -------------------------------------------------
  // Counts zeros above the first 1. Written as a loop over bit positions; the
  // synthesis tool turns it into a priority encoder (no loop exists in the
  // hardware). Returns N when the input is all zeros.
  function automatic logic [5:0] lzc24(input logic [23:0] x);
    logic [5:0] n;
    n = 6'd24;
    for (int i = 0; i < 24; i++) if (x[i]) n = 6'(23 - i);
    return n;
  endfunction
  function automatic logic [5:0] lzc28(input logic [27:0] x);
    logic [5:0] n;
    n = 6'd28;
    for (int i = 0; i < 28; i++) if (x[i]) n = 6'(27 - i);
    return n;
  endfunction
  function automatic logic [5:0] lzc32(input logic [31:0] x);
    logic [5:0] n;
    n = 6'd32;
    for (int i = 0; i < 32; i++) if (x[i]) n = 6'(31 - i);
    return n;
  endfunction

  // ---- shift right keeping a sticky bit ------------------------------------
  // Shifting a value right throws away low bits. Rounding only needs to know
  // whether anything nonzero was thrown away, so those bits are ORed into
  // bit 0 (the "sticky" bit). A shift of 26 or more leaves only the sticky.
  function automatic logic [25:0] shr_sticky26(input logic [25:0] x, input logic [EW-1:0] sh);
    logic [25:0] out;
    logic        lost;
    if (sh >= 26) begin
      out  = '0;
      lost = (x != '0);
    end else begin
      out  = x >> sh;
      // Bits that fall off the bottom are the low `sh` bits of x.
      lost = (x & ((26'd1 << sh) - 1'b1)) != '0;
    end
    return {out[25:1], out[0] | lost};
  endfunction

  // ---- round and pack -----------------------------------------------------
  // Input format (what every unit hands over):
  //   sign
  //   exp   signed, biased like the IEEE field. The value is
  //         1.f * 2^(exp-127) when sig[25] is the leading 1.
  //   sig   26 bits: [25] leading 1, [24:2] 23 fraction bits, [1] guard bit
  //         (first bit below the last kept bit), [0] sticky (OR of all bits
  //         below the guard bit, in the exact result).
  // Output: {flags[4:0], result[31:0]} with flags in RISC-V order
  //   NV DZ OF UF NX. Rounding only ever sets OF, UF and NX.
  //
  // Round to nearest, ties to even: round up when the discarded part is more
  // than half an LSB (guard=1 and sticky=1) or exactly half and the kept LSB
  // is odd (guard=1, sticky=0, lsb=1). That is inc = guard & (sticky | lsb).
  //
  // The work is split into two functions so a unit can put a register
  // between them; done in one clock it was too slow for 100 MHz.
  //   round_pre   decides normal vs subnormal, does the subnormal right
  //               shift, and works out tininess
  //   round_post  adds the rounding increment, handles the carry and
  //               overflow, and packs the fields
  // The value passed between the halves is one packed vector. Its fields:
  //   [40]     sign
  //   [39]     sub   subnormal path taken
  //   [38]     tiny  tiny after rounding (subnormal path only)
  //   [37:26]  e     exponent, two's complement (normal path)
  //   [25:0]   s     significand, already shifted if subnormal
  typedef logic [40:0] round_mid_t;

  function automatic round_mid_t round_pre(input logic sign,
                                           input logic signed [EW-1:0] exp,
                                           input logic [25:0] sig);
    logic        sub, tiny;
    logic [25:0] s;
    sub  = (exp < 1);
    tiny = 1'b0;
    if (!sub) begin
      s = sig;
    end else begin
      // The stored exponent can't go below the subnormal exponent, so shift
      // the significand right by (1 - exp) places to line it up with 2^-126.
      s = shr_sticky26(sig, EW'(1) - exp);
      // Tininess is judged after rounding: the result is tiny unless the
      // value, rounded to 24 bits with an unlimited exponent, reaches 2^-126.
      // That only happens when exp == 0 and all 24 kept bits are 1 and they
      // round up.
      tiny = !((exp == 0) && (sig[25:2] == '1) && sig[1] && (sig[0] | sig[2]));
    end
    return {sign, sub, tiny, exp, s};
  endfunction

  function automatic logic [36:0] round_post(input round_mid_t m);
    logic                 sign, sub, tiny;
    logic [25:0]          s;
    logic [24:0]          q;        // 24-bit significand + carry-out bit
    logic                 inc, inexact;
    logic signed [EW-1:0] e;
    {sign, sub, tiny, e, s} = m;    // unpack the fields listed above
    inc     = s[1] & (s[0] | s[2]);
    inexact = s[1] | s[0];
    q       = {1'b0, s[25:2]} + inc;
    if (sub) begin
      // If rounding carried into bit 23, the result is the smallest normal
      // number; that bit lands exactly in the exponent field's LSB.
      return {(inexact ? FFLAG_NX : 5'b0) | ((inexact && tiny) ? FFLAG_UF : 5'b0),
              sign, 7'b0, q[23], q[22:0]};
    end
    if (q[24]) begin
      // 1.111..1 rounded up to 10.000..0: renormalize by one place.
      // The new significand is exactly 1.0, so the fraction is all zero.
      q = q >> 1;
      e = e + 1'b1;
    end
    if (e >= 255)
      // Beyond the largest finite number: RNE always overflows to infinity.
      return {FFLAG_OF | FFLAG_NX, sign, POS_INF[30:0]};
    return {inexact ? FFLAG_NX : 5'b0, sign, e[7:0], q[22:0]};
  endfunction

  // Both halves in one call, for code that is not timing critical.
  function automatic logic [36:0] round_pack(input logic sign,
                                             input logic signed [EW-1:0] exp,
                                             input logic [25:0] sig);
    return round_post(round_pre(sign, exp, sig));
  endfunction

endpackage
