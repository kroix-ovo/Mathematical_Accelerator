// UVM environment for the whole accelerator, driven through its UART pins.
package top_uvm_pkg;
  import uvm_pkg::*;
  import fp_common_pkg::*;
  `include "uvm_macros.svh"

  // Two analysis inputs on one scoreboard need distinct write method names.
  `uvm_analysis_imp_decl(_req)
  `uvm_analysis_imp_decl(_rsp)

  `include "top_codec.svh"
  `include "top_agents.svh"
  `include "top_scoreboard.svh"
  `include "top_seq_lib.svh"
  `include "top_tests.svh"
endpackage
