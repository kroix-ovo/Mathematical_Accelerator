// Sequence items: the transactions that flow through the FPU testbench.
//
// A UVM sequence item is a plain data object. Sequences create them, the
// sequencer hands them to the driver, and monitors create new ones from what
// they observe on the wires. Nothing in an item knows about clocks or pins.

// ---------------------------------------------------------------------------
// One FPU request. Two ways to fill in the operands:
//   1. randomize(): the solver picks op, operand classes and a seed within
//      the constraints below; post_randomize() then builds the bit patterns.
//   2. set directly by a sequence (directed tests, LCG mode).
class fpu_req_item extends uvm_sequence_item;
  // "rand" fields are chosen by randomize(); constraints limit the choices.
  rand fpu_op_e     op;
  rand fp_class_e   cls_a, cls_b;
  rand bit [31:0]   bits_seed;     // seeds the operand builder
  rand int unsigned gap;           // idle clocks the driver waits first

  // Filled in by post_randomize, by a sequence, or by the driver (tag).
  bit [31:0]       a, b;
  bit [7:0]        tag;
  longint unsigned cycle;          // clock at which the request was accepted

  // dist gives relative weights: ":=" applies the weight to each value,
  // ":/" spreads it over the whole range. These match fpgen.py's weights
  // closely, with RELATED only allowed for b.
  constraint c_classes {
    cls_a dist {C_ZERO := 16, C_SUBNORMAL := 24, C_NORMAL := 64, C_NEAR_ONE := 48,
                C_INF := 10, C_QNAN := 8, C_SNAN := 8, C_BOUNDARY := 16, C_LARGE := 16,
                C_INT_LIKE := 22, C_RAW := 24};
    cls_b dist {C_ZERO := 16, C_SUBNORMAL := 24, C_NORMAL := 64, C_NEAR_ONE := 48,
                C_INF := 10, C_QNAN := 8, C_SNAN := 8, C_BOUNDARY := 16, C_LARGE := 16,
                C_INT_LIKE := 22, C_RAW := 24, C_RELATED := 32};
  }
  // Mostly back-to-back requests, sometimes short or long pauses.
  constraint c_gap { gap dist {0 := 80, [1:3] :/ 15, [4:20] :/ 5}; }

  `uvm_object_utils_begin(fpu_req_item)
    `uvm_field_enum(fpu_op_e, op, UVM_DEFAULT)
    `uvm_field_int(a, UVM_DEFAULT | UVM_HEX)
    `uvm_field_int(b, UVM_DEFAULT | UVM_HEX)
    `uvm_field_int(tag, UVM_DEFAULT | UVM_HEX)
  `uvm_object_utils_end

  function new(string name = "fpu_req_item");
    super.new(name);
  endfunction

  // Called automatically after a successful randomize(). The operand bits
  // come from the same builder the LCG sequences use, so both modes produce
  // the same kinds of values.
  function void post_randomize();
    fp_lcg g = new(bits_seed);
    a = g.operand(cls_a);
    b = g.operand(cls_b, a);
  endfunction

  function string convert2string();
    return $sformatf("%s a=%08h b=%08h tag=%02h", op.name(), a, b, tag);
  endfunction
endclass

// ---------------------------------------------------------------------------
// One FPU result, built by the response monitor.
class fpu_rsp_item extends uvm_sequence_item;
  bit [31:0]       result;
  bit [4:0]        flags;
  bit [7:0]        tag;
  longint unsigned cycle;

  `uvm_object_utils_begin(fpu_rsp_item)
    `uvm_field_int(result, UVM_DEFAULT | UVM_HEX)
    `uvm_field_int(flags, UVM_DEFAULT | UVM_BIN)
    `uvm_field_int(tag, UVM_DEFAULT | UVM_HEX)
  `uvm_object_utils_end

  function new(string name = "fpu_rsp_item");
    super.new(name);
  endfunction

  function string convert2string();
    return $sformatf("result=%08h flags=%05b tag=%02h", result, flags, tag);
  endfunction
endclass
