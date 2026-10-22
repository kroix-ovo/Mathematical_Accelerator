// Ethernet tests. Select with +UVM_TESTNAME (run_uvm.py eth --test ...).
class eth_base_test extends uvm_test;
  `uvm_component_utils(eth_base_test)
  eth_env env;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction

  function void build_phase(uvm_phase phase);
    fp_lcg::selftest();
    env = eth_env::type_id::create("env", this);
  endfunction

  virtual task run_vseq(); endtask

  task run_phase(uvm_phase phase);
    phase.raise_objection(this);
    phase.phase_done.set_drain_time(this, 300us);   // let the last frames finish
    run_vseq();
    phase.drop_objection(this);
  endtask

  function void report_phase(uvm_phase phase);
    uvm_report_server rs = uvm_report_server::get_server();
    int errs = rs.get_severity_count(UVM_ERROR) + rs.get_severity_count(UVM_FATAL);
    if (errs == 0 && env.sb.n_etx > 0) `uvm_info("RESULT", "ETH_UVM_PASS", UVM_NONE)
    else `uvm_info("RESULT", $sformatf("ETH_UVM_FAIL (%0d errors)", errs), UVM_NONE)
  endfunction
endclass

class eth_smoke_test extends eth_base_test;
  `uvm_component_utils(eth_smoke_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_vseq();
    eth_smoke_vseq v = eth_smoke_vseq::type_id::create("v");
    v.start(env.vsqr);
  endtask
endclass

class eth_regress_test extends eth_base_test;
  `uvm_component_utils(eth_regress_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  virtual task run_vseq();
    eth_regress_vseq v = eth_regress_vseq::type_id::create("v");
    v.start(env.vsqr);
  endtask
endclass
