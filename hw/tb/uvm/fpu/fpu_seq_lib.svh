// Sequence library for the FPU agent.
//
// A sequence is a task (body) that generates items and passes each one to
// the sequencer with start_item / finish_item:
//   start_item(item)   waits until the sequencer grants this sequence a turn
//   finish_item(item)  hands the item to the driver and waits for item_done
// Several sequences can run on the same sequencer at once; the sequencer
// interleaves their items.

class fpu_base_seq extends uvm_sequence #(fpu_req_item);
  `uvm_object_utils(fpu_base_seq)
  int unsigned count = 100;

  function new(string name = "fpu_base_seq");
    super.new(name);
  endfunction

  // Send one fully specified request (no randomization).
  task send(fpu_op_e op, bit [31:0] a, bit [31:0] b, int unsigned gap = 0);
    fpu_req_item it = fpu_req_item::type_id::create("it");
    start_item(it);
    it.op  = op;
    it.a   = a;
    it.b   = b;
    it.gap = gap;
    finish_item(it);
  endtask
endclass

// ---------------------------------------------------------------------------
// Constrained-random requests. force_op >= 0 pins the operation.
class fpu_rand_seq extends fpu_base_seq;
  `uvm_object_utils(fpu_rand_seq)
  int force_op = -1;

  function new(string name = "fpu_rand_seq");
    super.new(name);
  endfunction

  task body();
    fpu_op_e fop = fpu_op_e'(force_op);
    repeat (count) begin
      fpu_req_item it = fpu_req_item::type_id::create("it");
      bit ok;
      start_item(it);
      // "with" adds constraints for this one call. The solver must satisfy
      // both the class constraints and these. local:: means "the variable in
      // this sequence", not a field of the item being randomized.
      // (Written as two calls: xelab 2023.2 crashes on an inline constraint
      // that combines a sequence variable test with an enum cast.)
      if (force_op < 0) ok = it.randomize();
      else              ok = it.randomize() with { op == local::fop; };
      if (!ok) `uvm_fatal("RAND", "randomize() failed")
      finish_item(it);
    end
  endtask
endclass

// ---------------------------------------------------------------------------
// Deterministic stream from the shared LCG. The same seed produces the same
// requests in pyuvm (hw/tb/pyuvm), so a failure can be replayed in either.
class fpu_lcg_seq extends fpu_base_seq;
  `uvm_object_utils(fpu_lcg_seq)
  bit [31:0] seed = 1;
  int        force_op = -1;

  function new(string name = "fpu_lcg_seq");
    super.new(name);
  endfunction

  task body();
    fp_lcg     g = new(seed);
    fpu_op_e   op;
    bit [31:0] a, b;
    repeat (count) begin
      g.stimulus(force_op, op, a, b);
      send(op, a, b);
    end
  endtask
endclass

// ---------------------------------------------------------------------------
// Every op applied to every pair from a table of special values: signed
// zeros, smallest/largest subnormals and normals, ones, infinities, quiet
// and signaling NaNs, and powers of two near the integer conversion limits.
// Same table as SPECIALS in sw/tests/test_fp32ref.py.
class fpu_corner_seq extends fpu_base_seq;
  `uvm_object_utils(fpu_corner_seq)

  static bit [31:0] SPECIALS[28] = '{
      32'h00000000, 32'h80000000, 32'h00000001, 32'h80000001, 32'h007FFFFF, 32'h00800000,
      32'h00800001, 32'h3F800000, 32'hBF800000, 32'h3F800001, 32'h3FFFFFFF, 32'h40000000,
      32'h7F7FFFFF, 32'hFF7FFFFF, 32'h7F800000, 32'hFF800000, 32'h7FC00000, 32'hFFC00001,
      32'h7F800001, 32'hFFA00000, 32'h4F000000, 32'hCF000000, 32'h4EFFFFFF, 32'h3F000000,
      32'h3EFFFFFF, 32'h34000000, 32'h00400000, 32'h7F000000};

  function new(string name = "fpu_corner_seq");
    super.new(name);
  endfunction

  task body();
    for (int op = 0; op < 12; op++)
      foreach (SPECIALS[i])
        foreach (SPECIALS[j])
          send(fpu_op_e'(op), SPECIALS[i], SPECIALS[j]);
  endtask
endclass

// ---------------------------------------------------------------------------
// Rounding ties for ADD/SUB: b is exactly half, one quarter, or three
// quarters of a's last-place unit, so the exact sum lands on or next to a
// rounding boundary. These are the cases where round-to-nearest-even makes
// a visible choice, and random operands almost never hit them.
class fpu_tie_seq extends fpu_base_seq;
  `uvm_object_utils(fpu_tie_seq)

  function new(string name = "fpu_tie_seq");
    super.new(name);
  endfunction

  task body();
    repeat (count) begin
      bit [7:0]  e;
      bit [22:0] f;
      bit [31:0] a, b;
      int        off;
      e   = $urandom_range(30, 220);
      f   = $urandom;
      off = $urandom_range(23, 26);     // 2^-23 .. 2^-26 of a: one, half, quarter ulp...
      a   = {1'b0, e, f};
      b   = {1'($urandom_range(0, 1)), 8'(e - off), 23'(($urandom_range(0, 1)) ? 23'h400000 : 23'h0)};
      send($urandom_range(0, 1) ? FADD : FSUB, a, b);
    end
  endtask
endclass

// ---------------------------------------------------------------------------
// Divide/sqrt interleaved with pipelined ops, all back to back. This makes
// divide results finish while the pipeline is busy, exercising the result
// merge, the out-of-order tags, and the req_ready stall paths.
class fpu_mix_burst_seq extends fpu_base_seq;
  `uvm_object_utils(fpu_mix_burst_seq)

  function new(string name = "fpu_mix_burst_seq");
    super.new(name);
  endfunction

  task body();
    fp_lcg     g = new(32'h5EED_0001);
    fpu_op_e   op;
    bit [31:0] a, b;
    repeat (count) begin
      // A divide or sqrt, then a random number (0..12) of pipelined ops.
      g.stimulus(($urandom_range(0, 1)) ? int'(FDIV) : int'(FSQRT), op, a, b);
      send(op, a, b);
      repeat ($urandom_range(0, 12)) begin
        g.stimulus(int'(fpu_op_e'($urandom_range(0, 2))), op, a, b);  // ADD, SUB or MUL
        send(op, a, b);
      end
    end
  endtask
endclass

// ---------------------------------------------------------------------------
// Underflow focus: operands near 2^-126 for MUL, DIV and ADD/SUB.
class fpu_underflow_seq extends fpu_base_seq;
  `uvm_object_utils(fpu_underflow_seq)

  function new(string name = "fpu_underflow_seq");
    super.new(name);
  endfunction

  task body();
    repeat (count) begin
      fpu_req_item it = fpu_req_item::type_id::create("it");
      start_item(it);
      if (!it.randomize() with {
            op inside {FADD, FSUB, FMUL, FDIV};
            cls_a inside {C_SUBNORMAL, C_BOUNDARY, C_NEAR_ONE};
            cls_b inside {C_SUBNORMAL, C_BOUNDARY, C_NEAR_ONE, C_RELATED}; })
        `uvm_fatal("RAND", "randomize() failed")
      finish_item(it);
    end
  endtask
endclass
