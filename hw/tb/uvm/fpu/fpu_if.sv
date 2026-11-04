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
interface fpu_if #(parameter int TW = 8) (input logic clk); // Clock input: rising edge times floating-point requests, responses and testbench sampling.
  logic          rst;         // Reset: testbench drives active high to clear the floating-point unit's control and valid state.
  logic          req_valid;   // Request valid: driver holds an offered operation until request ready accepts it on the rising edge.
  logic          req_ready;   // Request ready: unit permits capture of the operation, operands and tag when request valid is also high.
  logic [4:0]    req_op;      // Request operation code: five-bit selector for the eighteen implemented floating-point operations.
  logic [31:0]   req_a;       // Request operand A: encoded first operand; a signed integer only for integer-to-float conversion.
  logic [31:0]   req_b;       // Request operand B: encoded second operand, ignored by unary operations.
  logic [TW-1:0] req_tag;     // Request tag: identifier assigned by the driver and retained for matching the eventual response.
  logic          rsp_valid;  // Response valid: unit presents a completed result, flags and tag; the testbench must accept it immediately.
  logic [31:0]   rsp_result; // Response result: floating-point bits, converted integer bits, or integer zero/one for a comparison.
  logic [4:0]    rsp_flags;  // Response exception flags: bits 4..0 mean invalid, divide by zero, overflow, underflow and inexact.
  logic [TW-1:0] rsp_tag;    // Response tag: identifier associating the observed answer with the accepted request.

  // Free-running clock counter. Monitors stamp each transaction with it so
  // the scoreboard can check latency in clocks.
  longint unsigned cycle = 0; // Clock cycle count: free-running timestamp used to measure request-to-response latency.
  always @(posedge clk) cycle <= cycle + 1;

  // Driver's view: what it drives and the one input it waits on.
  clocking drv_cb @(posedge clk);
    default input #1step output #1;
    output req_valid; // Request valid: driver presents a request through this clock-synchronized view.
    output req_op;    // Request operation code: driver selects the arithmetic, conversion, comparison or phase 3 function.
    output req_a;     // Request operand A: driver supplies the first operand's raw 32-bit encoding.
    output req_b;     // Request operand B: driver supplies the second operand's raw 32-bit encoding.
    output req_tag;   // Request tag: driver supplies the identifier that must return with the result.
    input  req_ready; // Request ready: driver samples the unit's acceptance permission before advancing to another item.
  endclocking

  // Monitors' view: everything is an input; monitors never drive.
  clocking mon_cb @(posedge clk);
    default input #1step;
    input rst;        // Reset: passive monitor ignores protocol observations while reset is asserted.
    input req_valid;  // Request valid: monitor observes whether the caller is offering an operation.
    input req_ready;  // Request ready: monitor records acceptance only when request valid is also sampled high.
    input req_op;     // Request operation code: monitor records the selected function for the accepted request.
    input req_a;      // Request operand A: monitor records the accepted first operand without changing its bits.
    input req_b;      // Request operand B: monitor records the accepted second operand without changing its bits.
    input req_tag;    // Request tag: monitor indexes the outstanding prediction under this identifier.
    input rsp_valid;  // Response valid: monitor records a completed response when sampled high.
    input rsp_result; // Response result: monitor sends the observed answer to the scoreboard for checking.
    input rsp_flags;  // Response exception flags: monitor records invalid, divide-by-zero, overflow, underflow and inexact status.
    input rsp_tag;    // Response tag: monitor uses this identifier to find the corresponding request prediction.
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
