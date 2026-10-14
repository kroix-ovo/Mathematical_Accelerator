// Interface between the UVM testbench classes and the fpu module.
//
// An SV interface is a bundle of signals that can be passed around as one
// object. UVM classes cannot connect to wires directly (classes are software
// objects created at run time), so they reach the signals through a
// "virtual interface" handle that points at this instance.
//
// Clocking blocks pin down *when* the testbench samples and drives signals
// relative to the clock edge, which avoids races between testbench and RTL:
//   - inputs are sampled just before the edge (#1step), i.e. the values the
//     flip-flops in the DUT also see at that edge;
//   - outputs are driven just after the edge, so the DUT sees them on the
//     next edge, the same way a register output would behave.
interface fpu_if #(parameter int TW = 8) (input logic clk);
  logic          rst;
  logic          req_valid, req_ready;
  logic [3:0]    req_op;
  logic [31:0]   req_a, req_b;
  logic [TW-1:0] req_tag;
  logic          rsp_valid;
  logic [31:0]   rsp_result;
  logic [4:0]    rsp_flags;
  logic [TW-1:0] rsp_tag;

  // Free-running clock counter. Monitors stamp each transaction with it so
  // the scoreboard can check latency in clocks.
  longint unsigned cycle = 0;
  always @(posedge clk) cycle <= cycle + 1;

  // Driver's view: what it drives and the one input it waits on.
  clocking drv_cb @(posedge clk);
    default input #1step output #1;
    output req_valid, req_op, req_a, req_b, req_tag;
    input  req_ready;
  endclocking

  // Monitors' view: everything is an input; monitors never drive.
  clocking mon_cb @(posedge clk);
    default input #1step;
    input rst, req_valid, req_ready, req_op, req_a, req_b, req_tag;
    input rsp_valid, rsp_result, rsp_flags, rsp_tag;
  endclocking

  // ---- protocol assertions --------------------------------------------------
  // These are checked by the simulator on every clock, independently of the
  // UVM scoreboard. "|=>" means "then on the next clock".

  // A request that is waiting (valid but not ready) must not change or vanish.
  // This checks the driver as much as the DUT, and documents the handshake.
  a_req_stable: assert property (@(posedge clk) disable iff (rst)
      req_valid && !req_ready |=> req_valid && $stable({req_op, req_a, req_b, req_tag}))
    else $error("request changed while waiting for req_ready");

  // Once out of reset, the DUT's outputs must never be X (unknown).
  a_ready_known: assert property (@(posedge clk) disable iff (rst) !$isunknown(req_ready))
    else $error("req_ready is X");
  a_rsp_known: assert property (@(posedge clk) disable iff (rst)
      rsp_valid |-> !$isunknown({rsp_result, rsp_flags, rsp_tag}))
    else $error("response fields are X while rsp_valid is high");
  a_valid_known: assert property (@(posedge clk) disable iff (rst) !$isunknown(rsp_valid))
    else $error("rsp_valid is X");
endinterface
