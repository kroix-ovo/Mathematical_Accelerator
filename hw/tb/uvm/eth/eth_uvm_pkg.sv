// UVM environment for the Ethernet transport, driven through the RMII pins
// of accel_core (and its UART pins, for the mixed-transport checks). The
// command layer, UART agent and SV device model come from top_uvm_pkg.
package eth_uvm_pkg;
  import uvm_pkg::*;
  import fp_common_pkg::*;
  import top_uvm_pkg::*;
  `include "uvm_macros.svh"

  // Four analysis inputs on one scoreboard need four write method names.
  `uvm_analysis_imp_decl(_erx)
  `uvm_analysis_imp_decl(_etx)
  `uvm_analysis_imp_decl(_urx)
  `uvm_analysis_imp_decl(_utx)

  `include "eth_codec.svh"
  `include "eth_agent.svh"
  `include "eth_scoreboard.svh"
  `include "eth_seq_lib.svh"
  `include "eth_tests.svh"
endpackage
