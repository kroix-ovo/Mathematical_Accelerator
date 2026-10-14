// Tests: each one builds the same environment and runs different sequences.
// Pick one at run time with +UVM_TESTNAME=<name>. Size knobs:
//   +FPU_N=<n>       items per random/LCG sequence (default 2000)
//   +LCG_SEED=<hex>  seed for fpu_lcg_seq (default 1)
//   +FPU_OP=<n>      pin fpu_lcg_test to one op code (default: random ops)
// The simulator seed for randomize() is set with xsim's -sv_seed option.

class fpu_base_test extends uvm_test;
  `uvm_component_utils(fpu_base_test)

  fpu_env      env;
  int unsigned n = 2000;
  bit [31:0]   lcg_seed = 1;
  int          force_op = -1;       // +FPU_OP=<0..11> pins the LCG test's operation

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    if ($value$plusargs("FPU_N=%d", n)) `uvm_info("CFG", $sformatf("FPU_N=%0d", n), UVM_LOW)
    if ($value$plusargs("LCG_SEED=%h", lcg_seed)) `uvm_info("CFG", $sformatf("LCG_SEED=%08h", lcg_seed), UVM_LOW)
    if ($value$plusargs("FPU_OP=%d", force_op)) `uvm_info("CFG", $sformatf("FPU_OP=%0d", force_op), UVM_LOW)
    fp_lcg::selftest();                       // SV and Python generators agree
    env = fpu_env::type_id::create("env", this);
  endfunction

  // Subclasses override this to choose their sequences.
  virtual task run_seqs();
  endtask

  // Objections tell UVM "don't end the test yet". The drain time lets the
  // last results (up to ~30 clocks for divide) come back after the final
  // request before the test ends.
  task run_phase(uvm_phase phase);
    phase.raise_objection(this);
    phase.phase_done.set_drain_time(this, 1us);
    run_seqs();
    phase.drop_objection(this);
  endtask

  // Print a single pass/fail marker that the run script looks for.
  function void report_phase(uvm_phase phase);
    uvm_report_server rs = uvm_report_server::get_server();
    int errs = rs.get_severity_count(UVM_ERROR) + rs.get_severity_count(UVM_FATAL);
    if (errs == 0 && env.sb.n_checked > 0)
      `uvm_info("RESULT", "FPU_UVM_PASS", UVM_NONE)
    else
      `uvm_info("RESULT", $sformatf("FPU_UVM_FAIL (%0d errors, %0d checked)", errs, env.sb.n_checked), UVM_NONE)
  endfunction
endclass

class fpu_smoke_test extends fpu_base_test;
  `uvm_component_utils(fpu_smoke_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_seqs();
    fpu_rand_seq s = fpu_rand_seq::type_id::create("s");
    s.count = 200;
    s.start(env.agent.sqr);
  endtask
endclass

class fpu_corner_test extends fpu_base_test;
  `uvm_component_utils(fpu_corner_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_seqs();
    fpu_corner_seq s = fpu_corner_seq::type_id::create("s");
    s.start(env.agent.sqr);
  endtask
endclass

class fpu_rand_test extends fpu_base_test;
  `uvm_component_utils(fpu_rand_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_seqs();
    fpu_rand_seq s = fpu_rand_seq::type_id::create("s");
    s.count = n;
    s.start(env.agent.sqr);
  endtask
endclass

class fpu_lcg_test extends fpu_base_test;
  `uvm_component_utils(fpu_lcg_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_seqs();
    fpu_lcg_seq s = fpu_lcg_seq::type_id::create("s");
    s.count    = n;
    s.seed     = lcg_seed;
    s.force_op = force_op;
    s.start(env.agent.sqr);
  endtask
endclass

// Runs two sequences at the same time on one sequencer: the sequencer
// interleaves their items, which mixes divide bursts into random traffic.
class fpu_stress_test extends fpu_base_test;
  `uvm_component_utils(fpu_stress_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_seqs();
    fpu_mix_burst_seq mix = fpu_mix_burst_seq::type_id::create("mix");
    fpu_rand_seq      rnd = fpu_rand_seq::type_id::create("rnd");
    fpu_tie_seq       tie = fpu_tie_seq::type_id::create("tie");
    fpu_underflow_seq uf  = fpu_underflow_seq::type_id::create("uf");
    mix.count = n / 8;
    rnd.count = n;
    tie.count = n / 2;
    uf.count  = n / 2;
    fork
      mix.start(env.agent.sqr);
      rnd.start(env.agent.sqr);
    join
    tie.start(env.agent.sqr);
    uf.start(env.agent.sqr);
  endtask
endclass

// Everything, in order. This is what the regression script runs by default.
class fpu_regress_test extends fpu_base_test;
  `uvm_component_utils(fpu_regress_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_seqs();
    fpu_corner_seq    c   = fpu_corner_seq::type_id::create("c");
    fpu_lcg_seq       l   = fpu_lcg_seq::type_id::create("l");
    fpu_rand_seq      r   = fpu_rand_seq::type_id::create("r");
    fpu_tie_seq       t   = fpu_tie_seq::type_id::create("t");
    fpu_underflow_seq u   = fpu_underflow_seq::type_id::create("u");
    fpu_mix_burst_seq mix = fpu_mix_burst_seq::type_id::create("mix");
    l.count = n;  l.seed = lcg_seed;
    r.count = n;  t.count = n / 4;  u.count = n / 4;  mix.count = n / 16;
    c.start(env.agent.sqr);
    l.start(env.agent.sqr);
    fork
      r.start(env.agent.sqr);
      mix.start(env.agent.sqr);
    join
    t.start(env.agent.sqr);
    u.start(env.agent.sqr);
  endtask
endclass
