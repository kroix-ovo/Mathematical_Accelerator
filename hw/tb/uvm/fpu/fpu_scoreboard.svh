// Scoreboard: predicts every result and checks every response.
//
// When a request is observed, the scoreboard asks the C reference model what
// the answer should be and files the prediction under the request's tag.
// When a response is observed, it looks up the tag and compares bit-for-bit:
// the 32-bit result pattern and the 5 flag bits must both match.
//
// It also checks timing that the RTL promises:
//   - pipelined ops return exactly LAT_PIPE clocks after acceptance
//   - divide/sqrt return no sooner than LAT_DS_MIN clocks
//   - no response without a request, no request left without a response

// uvm_analysis_imp_decl creates uniquely named "write" callbacks, so one
// component can receive two different streams (write_req and write_rsp).
`uvm_analysis_imp_decl(_req)
`uvm_analysis_imp_decl(_rsp)

class fpu_scoreboard extends uvm_scoreboard;
  `uvm_component_utils(fpu_scoreboard)

  // Clock counts measured from the request edge to the response edge as the
  // monitors see them (the response monitor samples one clock after the DUT
  // registers its output, which the constants include).
  localparam int LAT_PIPE   = 7;
  localparam int LAT_DS_MIN = 30;

  uvm_analysis_imp_req #(fpu_req_item, fpu_scoreboard) req_imp;
  uvm_analysis_imp_rsp #(fpu_rsp_item, fpu_scoreboard) rsp_imp;

  // Outstanding predictions, indexed by tag (an associative array works like
  // a dictionary: only the keys that were written exist).
  fpu_req_item pending[bit [7:0]];
  bit [31:0]   exp_result[bit [7:0]];
  bit [4:0]    exp_flags[bit [7:0]];

  int unsigned n_checked, n_errors;
  int unsigned per_op[fpu_op_e];
  int unsigned max_report = 20;     // stop printing details after this many

  function new(string name, uvm_component parent);
    super.new(name, parent);
    req_imp = new("req_imp", this);
    rsp_imp = new("rsp_imp", this);
  endfunction

  function void write_req(fpu_req_item t);
    int fl;
    if (pending.exists(t.tag))
      `uvm_error("SB_TAG", $sformatf("tag %02h reused while still outstanding", t.tag))
    pending[t.tag]    = t;
    exp_result[t.tag] = fp_ref_eval(int'(t.op), t.a, t.b, fl);
    exp_flags[t.tag]  = fl[4:0];
  endfunction

  function void write_rsp(fpu_rsp_item r);
    fpu_req_item q;
    longint unsigned lat;
    if (!pending.exists(r.tag)) begin
      `uvm_error("SB_ORPHAN", $sformatf("response with unknown tag: %s", r.convert2string()))
      return;
    end
    q   = pending[r.tag];
    lat = r.cycle - q.cycle;
    n_checked++;
    per_op[q.op]++;

    if (r.result !== exp_result[r.tag] || r.flags !== exp_flags[r.tag]) begin
      n_errors++;
      if (n_errors <= max_report)
        `uvm_error("SB_MISMATCH", $sformatf(
            "%s\n  expected result=%08h flags=%05b\n  got      result=%08h flags=%05b",
            q.convert2string(), exp_result[r.tag], exp_flags[r.tag], r.result, r.flags))
    end

    if (q.op inside {FDIV, FSQRT}) begin
      if (lat < LAT_DS_MIN)
        `uvm_error("SB_LAT", $sformatf("%s returned after %0d clocks (< %0d)", q.op.name(), lat, LAT_DS_MIN))
    end else if (lat != LAT_PIPE) begin
      `uvm_error("SB_LAT", $sformatf("%s returned after %0d clocks (expected %0d)", q.op.name(), lat, LAT_PIPE))
    end

    pending.delete(r.tag);
    exp_result.delete(r.tag);
    exp_flags.delete(r.tag);
  endfunction

  // check_phase runs after the test's stimulus is finished.
  function void check_phase(uvm_phase phase);
    if (pending.num() != 0)
      `uvm_error("SB_LEFTOVER", $sformatf("%0d requests never got a response", pending.num()))
  endfunction

  function void report_phase(uvm_phase phase);
    string s = $sformatf("checked %0d results, %0d mismatches\n", n_checked, n_errors);
    foreach (per_op[op]) s = {s, $sformatf("  %-6s %0d\n", op.name(), per_op[op])};
    `uvm_info("SB", s, UVM_NONE)
  endfunction
endclass

// ---------------------------------------------------------------------------
// Functional coverage: which combinations of op and operand kind were
// actually exercised, and which flags were raised. A covergroup counts how
// often each "bin" (value or range) was hit; crosses count combinations.
class fpu_coverage extends uvm_component;
  `uvm_component_utils(fpu_coverage)

  uvm_analysis_imp_req #(fpu_req_item, fpu_coverage) req_imp;
  uvm_analysis_imp_rsp #(fpu_rsp_item, fpu_coverage) rsp_imp;

  fpu_op_e  op;
  fp_kind_e ka, kb;
  bit [4:0] flags;
  fpu_op_e  op_of_tag[bit [7:0]];

  covergroup cg_req;
    cp_op: coverpoint op;
    cp_ka: coverpoint ka;
    cp_kb: coverpoint kb;
    // Every op against every kind of first operand, and for two-operand ops,
    // every pairing of operand kinds.
    x_op_ka: cross cp_op, cp_ka;
    x_op_ka_kb: cross cp_op, cp_ka, cp_kb {
      ignore_bins unary = binsof(cp_op) intersect {FSQRT, FI2F, FF2I};
    }
  endgroup

  covergroup cg_rsp;
    cp_op: coverpoint op;
    cp_nx: coverpoint flags[0];
    cp_uf: coverpoint flags[1];
    cp_of: coverpoint flags[2];
    cp_dz: coverpoint flags[3];
    cp_nv: coverpoint flags[4];
    x_op_nv: cross cp_op, cp_nv;
    // MIN, MAX and the compares never round, so "inexact" can't happen for
    // them; ignore_bins removes impossible combinations from the score.
    x_op_nx: cross cp_op, cp_nx {
      ignore_bins exact_ops = binsof(cp_op) intersect {FMIN, FMAX, FEQ, FLT, FLE} &&
                              binsof(cp_nx) intersect {1};
    }
  endgroup

  function new(string name, uvm_component parent);
    super.new(name, parent);
    req_imp = new("req_imp", this);
    rsp_imp = new("rsp_imp", this);
    cg_req  = new();
    cg_rsp  = new();
  endfunction

  function void write_req(fpu_req_item t);
    op = t.op;
    ka = kind_of(t.a);
    kb = kind_of(t.b);
    op_of_tag[t.tag] = t.op;
    cg_req.sample();
  endfunction

  function void write_rsp(fpu_rsp_item r);
    if (!op_of_tag.exists(r.tag)) return;
    op    = op_of_tag[r.tag];
    flags = r.flags;
    cg_rsp.sample();
    op_of_tag.delete(r.tag);
  endfunction

  function void report_phase(uvm_phase phase);
    `uvm_info("COV", $sformatf("request coverage %0.1f%%, response coverage %0.1f%%",
                               cg_req.get_coverage(), cg_rsp.get_coverage()), UVM_NONE)
  endfunction
endclass

// ---------------------------------------------------------------------------
class fpu_env extends uvm_env;
  `uvm_component_utils(fpu_env)

  fpu_agent      agent;
  fpu_scoreboard sb;
  fpu_coverage   cov;

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    agent = fpu_agent::type_id::create("agent", this);
    sb    = fpu_scoreboard::type_id::create("sb", this);
    cov   = fpu_coverage::type_id::create("cov", this);
  endfunction

  function void connect_phase(uvm_phase phase);
    agent.req_mon.ap.connect(sb.req_imp);
    agent.rsp_mon.ap.connect(sb.rsp_imp);
    agent.req_mon.ap.connect(cov.req_imp);
    agent.rsp_mon.ap.connect(cov.rsp_imp);
  endfunction
endclass
