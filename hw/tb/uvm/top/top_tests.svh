// Top-level tests. Select with +UVM_TESTNAME.
class top_base_test extends uvm_test;
  `uvm_component_utils(top_base_test)
  top_env env;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction

  function void build_phase(uvm_phase phase);
    fp_lcg::selftest();
    env = top_env::type_id::create("env", this);
  endfunction

  virtual task run_vseq(); endtask

  task run_phase(uvm_phase phase);
    phase.raise_objection(this);
    phase.phase_done.set_drain_time(this, 20us);   // let the last reply finish
    run_vseq();
    phase.drop_objection(this);
  endtask

  function void report_phase(uvm_phase phase);
    uvm_report_server rs = uvm_report_server::get_server();
    int errs = rs.get_severity_count(UVM_ERROR) + rs.get_severity_count(UVM_FATAL);
    if (errs == 0 && env.sb.n_rsp > 0) `uvm_info("RESULT", "TOP_UVM_PASS", UVM_NONE)
    else `uvm_info("RESULT", $sformatf("TOP_UVM_FAIL (%0d errors)", errs), UVM_NONE)
  endfunction
endclass

class top_smoke_test extends top_base_test;
  `uvm_component_utils(top_smoke_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_vseq();
    top_smoke_vseq v = top_smoke_vseq::type_id::create("v");
    v.start(env.vsqr);
  endtask
endclass

class top_regress_test extends top_base_test;
  `uvm_component_utils(top_regress_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_vseq();
    top_regress_vseq v = top_regress_vseq::type_id::create("v");
    v.start(env.vsqr);
  endtask
endclass
