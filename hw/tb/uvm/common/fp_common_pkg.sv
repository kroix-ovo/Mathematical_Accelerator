// The op enum has five bits so values 12..17 reach the phase 3 unit intact.
// The portable LCG still chooses among the original twelve operations when no
// op is forced; dedicated phase 3 sequences add the function-specific cases.
// Keeping that stream stable lets older failing seeds reproduce the same bits.
// Pieces shared by the FPU block environment and the top-level environment:
// the DPI-C reference models, operand classes, and the seeded LCG generator.
// I expose the EVU interpreter through scalar DPI calls, keeping array layout
// out of the SV/C boundary. The top scoreboard loads memory and reads back writes.
package fp_common_pkg;
  import uvm_pkg::*;
  `include "uvm_macros.svh"

  // ---- reference model (hw/tb/uvm/dpi/fp_ref.c) ---------------------------
  // "import DPI-C" declares a C function that SV code may call. Arguments map
  // to C types: int -> int, int unsigned -> unsigned int, and an output int
  // becomes an int* that the C code writes through.
  import "DPI-C" function int unsigned fp_ref_eval(input int op, input int unsigned a,
                                                   input int unsigned b, output int flags);
  // The bit-accurate model of the CORDIC unit (hw/tb/uvm/dpi/cordic_ref.c, a
  // port of sw/mathaccel/cordic_model.py): what the hardware returns for ops
  // 12-17, bit for bit. fp_ref_eval gives the independent, nearly always
  // correctly rounded answer for the same ops, used for the ULP check.
  import "DPI-C" function int unsigned cordic_ref_eval(input int op, input int unsigned a,
                                                       input int unsigned b, output int flags);

  // The expression unit's model (hw/tb/uvm/dpi/evu_ref.c). It keeps its own copy
  // of the scratchpad: clear, poke the words that matter, run, then read back the
  // list of words the run wrote. The status is a status code from accel_pkg.
  import "DPI-C" function void evu_ref_clear();
  import "DPI-C" function void evu_ref_poke(input int unsigned addr, input int unsigned word);
  import "DPI-C" function int evu_ref_run(input int unsigned desc, output int unsigned aux,
                                          output int unsigned low, output int unsigned high);
  import "DPI-C" function int unsigned evu_ref_writes();
  import "DPI-C" function int unsigned evu_ref_write_addr(input int unsigned i);
  import "DPI-C" function int unsigned evu_ref_write_word(input int unsigned i);

  // FPU op codes, mirrored from accel_pkg as an enum so logs print names.
  typedef enum bit [4:0] {
    FADD = 5'h00, FSUB = 5'h01, FMUL = 5'h02, FDIV = 5'h03, FSQRT = 5'h04, FI2F = 5'h05,
    FF2I = 5'h06, FMIN = 5'h07, FMAX = 5'h08, FEQ = 5'h09, FLT = 5'h0A, FLE = 5'h0B,
    FSIN = 5'h0C, FCOS = 5'h0D, FATAN2 = 5'h0E, FHYPOT = 5'h0F, FEXP = 5'h10, FLN = 5'h11
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
    return op inside {FSQRT, FI2F, FF2I, FSIN, FCOS, FEXP, FLN};
  endfunction

  // The phase 3 ops, computed by the CORDIC unit.
  function automatic bit is_cordic(fpu_op_e op);
    return op inside {[FSIN:FLN]};
  endfunction

  // Distance in ULPs between two fp32 results: how many representable floats
  // apart they are. Mapping the bit patterns onto a line of integers (negative
  // numbers mirrored below zero) makes neighbouring floats neighbouring
  // integers, so +0 and -0 count as the same point. Two NaNs are 0 apart; a
  // NaN and a number are "infinitely" far apart.
  function automatic longint ulp_distance(bit [31:0] x, bit [31:0] y);
    longint ix, iy;
    bit xnan = (x[30:23] == 8'hFF) && (x[22:0] != 0);
    bit ynan = (y[30:23] == 8'hFF) && (y[22:0] != 0);
    if (xnan || ynan) return (xnan && ynan) ? 0 : 64'h7FFFFFFF;
    ix = x[31] ? -longint'(x[30:0]) : longint'(x[30:0]);
    iy = y[31] ? -longint'(y[30:0]) : longint'(y[30:0]);
    return (ix > iy) ? ix - iy : iy - ix;
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
