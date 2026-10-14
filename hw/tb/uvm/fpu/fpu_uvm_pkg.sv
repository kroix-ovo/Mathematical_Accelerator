// UVM environment for the fpu block. The package pulls the class files
// together in dependency order; `include pastes each file's text here.
package fpu_uvm_pkg;
  import uvm_pkg::*;
  import fp_common_pkg::*;
  `include "uvm_macros.svh"

  `include "fpu_items.svh"
  `include "fpu_agent.svh"
  `include "fpu_scoreboard.svh"
  `include "fpu_seq_lib.svh"
  `include "fpu_tests.svh"
endpackage
