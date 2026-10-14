// Pieces shared by the FPU block environment and the top-level environment:
// the DPI-C reference model, operand classes, and the seeded LCG generator.
package fp_common_pkg;
  import uvm_pkg::*;
  `include "uvm_macros.svh"

  // ---- reference model (hw/tb/uvm/dpi/fp_ref.c) ---------------------------
  // "import DPI-C" declares a C function that SV code may call. Arguments map
  // to C types: int -> int, int unsigned -> unsigned int, and an output int
  // becomes an int* that the C code writes through.
  import "DPI-C" function int unsigned fp_ref_eval(input int op, input int unsigned a,
                                                   input int unsigned b, output int flags);

  // FPU op codes, mirrored from accel_pkg as an enum so logs print names.
  typedef enum bit [3:0] {
    FADD = 4'h0, FSUB = 4'h1, FMUL = 4'h2, FDIV = 4'h3, FSQRT = 4'h4, FI2F = 4'h5,
    FF2I = 4'h6, FMIN = 4'h7, FMAX = 4'h8, FEQ = 4'h9, FLT = 4'hA, FLE = 4'hB
  } fpu_op_e;

  // Operand classes. Order and values match OperandClass in sw/mathaccel/fpgen.py.
  typedef enum int {
    C_ZERO = 0, C_SUBNORMAL = 1, C_NORMAL = 2, C_NEAR_ONE = 3, C_INF = 4, C_QNAN = 5,
    C_SNAN = 6, C_BOUNDARY = 7, C_LARGE = 8, C_INT_LIKE = 9, C_RAW = 10, C_RELATED = 11
  } fp_class_e;

  // What kind of value a bit pattern actually is (used by coverage). This is
  // a classification of the result of generation, not the class requested.
  typedef enum int {K_ZERO, K_SUBNORMAL, K_NORMAL, K_INF, K_QNAN, K_SNAN} fp_kind_e;

  function automatic fp_kind_e kind_of(bit [31:0] x);
    if (x[30:23] == 8'hFF) return (x[22:0] == 0) ? K_INF : (x[22] ? K_QNAN : K_SNAN);
    if (x[30:23] == 8'h00) return (x[22:0] == 0) ? K_ZERO : K_SUBNORMAL;
    return K_NORMAL;
  endfunction

  function automatic bit is_unary(fpu_op_e op);
    return op inside {FSQRT, FI2F, FF2I};
  endfunction

  // ---- seeded generator ----------------------------------------------------
  // Line-for-line twin of FpLcg in sw/mathaccel/fpgen.py. The same seed gives
  // the same (op, a, b) stream in SV and Python, which is how a failing seed
  // from one environment can be replayed in the other. All arithmetic is on
  // 32-bit unsigned values, which wrap modulo 2^32 exactly like the Python
  // code's "& 0xFFFFFFFF".
  class fp_lcg;
    bit [31:0] state;

    // Class weights out of 256, in the same order as CLASS_WEIGHTS in Python.
    static fp_class_e    W_CLS[11] = '{C_ZERO, C_SUBNORMAL, C_NORMAL, C_NEAR_ONE, C_INF,
                                       C_QNAN, C_SNAN, C_BOUNDARY, C_LARGE, C_INT_LIKE, C_RAW};
    static int unsigned  W_VAL[11] = '{16, 24, 64, 48, 10, 8, 8, 16, 16, 22, 24};

    function new(bit [31:0] seed);
      state = seed;
    endfunction

    function bit [31:0] next();
      state = state * 32'd1664525 + 32'd1013904223;
      return state;
    endfunction

    function fp_class_e pick_class();
      int unsigned r = next() >> 24;
      foreach (W_CLS[i]) begin
        if (r < W_VAL[i]) return W_CLS[i];
        r -= W_VAL[i];
      end
      `uvm_fatal("LCG", "class weights must sum to 256")
      return C_RAW;
    endfunction

    // Build one operand of class `cls`. `other` is the a operand (RELATED only).
    function bit [31:0] operand(fp_class_e cls, bit [31:0] other = 0);
      bit [31:0] r    = next();
      bit [31:0] sign = {r[31], 31'b0};
      bit [31:0] e, low, frac;
      case (cls)
        C_ZERO:      return sign;
        C_SUBNORMAL: return sign | ((next() >> 9) | 1);
        C_NORMAL: begin
          e = 1 + ((r >> 8) & 32'hFF) % 254;
          return sign | (e << 23) | (next() >> 9);
        end
        C_NEAR_ONE: begin
          e = 124 + ((r >> 8) & 32'h7);
          return sign | (e << 23) | (next() >> 9);
        end
        C_INF:  return sign | 32'h7F80_0000;
        C_QNAN: return sign | 32'h7FC0_0000 | (next() >> 10);
        C_SNAN: return sign | 32'h7F80_0000 | ((next() >> 10) | 1);
        C_BOUNDARY: begin
          e    = (r >> 8) & 1;
          low  = next() >> 28;
          frac = ((r >> 9) & 1) ? (32'h7F_FFFF - low) : low;
          return sign | (e << 23) | frac;
        end
        C_LARGE: begin
          e = 250 + ((r >> 8) & 32'h3);
          if ((r >> 10) & 1) e = 254;
          return sign | (e << 23) | (next() >> 9);
        end
        C_INT_LIKE: begin
          e = 127 + ((r >> 8) & 32'h1F);
          return sign | (e << 23) | (next() >> 9);
        end
        C_RAW:     return next();
        C_RELATED: return ((other ^ (next() >> 28)) & 32'h7FFF_FFFF) | sign;
        default:   `uvm_fatal("LCG", "unknown operand class")
      endcase
      return 0;
    endfunction

    // One (op, a, b) triple. force_op < 0 picks the op from the stream.
    function void stimulus(int force_op, output fpu_op_e op, output bit [31:0] a,
                           output bit [31:0] b);
      if (force_op < 0) op = fpu_op_e'((next() >> 24) % 12);
      else              op = fpu_op_e'(force_op);
      a = operand(pick_class());
      // One in eight b operands is RELATED to a (cancellation, near-ties).
      if ((next() >> 29) == 0) b = operand(C_RELATED, a);
      else                     b = operand(pick_class());
    endfunction

    // Compare against values pinned in sw/tests/test_fp32ref.py.
    static function void selftest();
      fp_lcg   g;
      fpu_op_e op;
      bit [31:0] a, b;
      bit [3:0]  exp_op[4] = '{0, 10, 0, 6};
      bit [31:0] exp_a[4]  = '{32'h815A399D, 32'h80000000, 32'h53AE1CEB, 32'h485A870C};
      bit [31:0] exp_b[4]  = '{32'h015A3991, 32'hBEEB20A4, 32'h3E8D6160, 32'h48601DA5};
      g = new(1);
      for (int i = 0; i < 4; i++) begin
        g.stimulus(-1, op, a, b);
        if (op != exp_op[i] || a != exp_a[i] || b != exp_b[i])
          `uvm_fatal("LCG", $sformatf("SV/Python LCG mismatch at %0d: got %0d %08h %08h", i, op, a, b))
      end
    endfunction
  endclass
endpackage
