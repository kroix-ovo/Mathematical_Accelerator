// FPU top level: one request port, one result port, five execution units.
//
// Request handshake: the caller holds req_valid with op/a/b/tag stable; the
// request is taken on a rising clock edge where req_valid and req_ready are
// both 1. Results come back on rsp_valid with the same tag. There is no
// ready signal on the result side: the caller must accept every result.
//
// Results can come back in a different order from the requests, because the
// units have different latencies. The tag is how the caller matches results
// to requests.
//
//   op               unit         timing
//   ADD, SUB         fp_addsub    pipelined, 1 per clock, 6 clocks
//   MUL              fp_mul       pipelined, 1 per clock, 6 clocks
//   I2F ... LE       fp_misc      pipelined, 1 per clock, 6 clocks
//   DIV, SQRT        fp_divsqrt   one at a time, fixed 33 clocks
//   SIN ... LN       fp_cordic    pipelined, 1 per clock, 52 clocks, then
//                                 a result FIFO
// plus one output register, so pipelined results appear 7 clocks after
// acceptance, and CORDIC results 54 clocks after acceptance when nothing else
// is using the result port (one more clock for the FIFO), and never later than
// 54 + AGE_MAX + 9 = 95 clocks (see the age rule below).
// Op codes 18-30 go to fp_misc, which returns NaN with the invalid flag, and op
// code 31 is the expression unit's internal move (cmd_proc never sends any of
// these; it rejects them with BAD_ARG).
//
// Result collisions: the three 6-clock units have identical latency and
// only one request enters per clock, so at most one of them finishes in any
// clock. These results always win the result port, since those units can't
// wait. The two slower units have to find a free clock:
//   - fp_divsqrt holds its result until a clock where the 6-clock units have
//     nothing to return. To guarantee that clock arrives, no new requests are
//     accepted while a divide/sqrt result is waiting.
//   - fp_cordic results go into a FIFO (FD = 64 entries), which hands one
//     result to the port in each clock that neither of the above uses.
//     fp_cordic itself can't stall either, so every CORDIC request reserves a
//     FIFO slot when it is accepted (the counter c_cnt below counts results in
//     flight plus results stored), and CORDIC requests wait while all FD
//     slots are reserved. 64 is more than the 53 results that can be in
//     flight, so a stream of only CORDIC ops never waits for slots.
//   - Age rule: when the oldest result in the FIFO has waited AGE_MAX clocks,
//     no new requests are accepted until it has left. Without this, a steady
//     mix of 6-clock ops and CORDIC ops (one request every clock) leaves the
//     FIFO's backlog in place indefinitely, because it only shrinks in clocks
//     the 6-clock units leave free; the testbench saw a result wait 263
//     clocks, longer than it takes an 8-bit tag to come round again. With the
//     rule, once the requests stop, the 6-clock units are empty within 7
//     clocks, at most one divide/sqrt result goes first, and then the FIFO
//     gets the port. So no result waits in the FIFO more than AGE_MAX + 9
//     clocks.
//   - A full FIFO also stops all new requests. With the age rule and FD = 64
//     this can't happen; it is the backstop for smaller FIFOs.
// One FPU kernel run uses a single op, so inside the kernel engine none of
// these collisions happen; they matter for the FPU testbenches, which mix ops.
// I use "fire" for an accepted request, not merely an offered one. Every
// unit's in_valid comes from fire, so a stalled request cannot execute twice.
// A tag is an identifier carried beside the arithmetic; it has no effect on
// the number. Keep it with the result because mixed operations can finish
// out of order. The FIFO stores result, flags, tag and arrival time together.
// c_cnt is a promise of storage for unfinished CORDIC work; f_cnt counts
// actual stored results. Checking only f_cnt would admit work whose result
// arrives later to find that every FIFO entry has already been used.
// For Phase 4 I also accept the internal LOAD move through fp_misc.
// I keep its latency and tag path aligned with the short arithmetic ops.
module fpu
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8,
    // CORDIC result FIFO depth, a power of 2. With one request per clock the
    // FIFO holds at most about 60 results, so the default never fills; the
    // testbench also builds a 16-entry version to exercise the stall rules.
    parameter int FD = 64,
    // Age rule threshold in clocks (see the header). Must stay well below 256,
    // the range of the 8-bit arrival time stamps.
    parameter int AGE_MAX = 32
) (
    input  logic          clk,                   // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic          rst,                   // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic          req_valid,             // Request valid: caller holds operation, operands and tag steady until request ready accepts them.
    output logic          req_ready,             // Request ready: high permits acceptance on the rising edge; low asks the caller to retain the request.
    input  logic [4:0]    req_op,                // Request operation code: five-bit selector for arithmetic, conversion, comparison or a phase 3 function.
    input  logic [31:0]   req_a,                 // Request operand A: 32-bit encoded first operand; interpreted as a signed integer only for integer-to-float.
    input  logic [31:0]   req_b,                 // Request operand B: 32-bit encoded second operand; ignored by unary operations.
    input  logic [TW-1:0] req_tag,               // Request tag: caller-provided identifier returned with the result, including when operations finish out of order.
    output logic          rsp_valid,             // Response valid: result, flags and tag are available now; the caller must accept them without a ready signal.
    output logic [31:0]   rsp_result,            // Response result: floating-point answer, converted signed integer, or integer 0/1 for a comparison.
    output logic [4:0]    rsp_flags,             // Response exception flags: bits 4..0 mean invalid, divide by zero, overflow, underflow and inexact.
    output logic [TW-1:0] rsp_tag                // Response tag: request identifier used to associate this answer with the original operands.
);
  localparam int FA = $clog2(FD);      // FIFO pointer width

  // ---- dispatch -------------------------------------------------------------
  logic is_add, is_mul, is_ds, is_cord, is_misc, fire;
  assign is_add  = (req_op == FOP_ADD) || (req_op == FOP_SUB);
  assign is_mul  = (req_op == FOP_MUL);
  assign is_ds   = (req_op == FOP_DIV) || (req_op == FOP_SQRT);
  assign is_cord = (req_op >= FOP_SIN) && (req_op <= FOP_LN);
  assign is_misc = !(is_add || is_mul || is_ds || is_cord);   // includes unknown ops

  // Operation selects for the units, as named wires. (Writing the comparison
  // directly inside the port connection made Icarus Verilog produce X.)
  logic op_sub, op_sqrt;
  assign op_sub  = (req_op == FOP_SUB);
  assign op_sqrt = (req_op == FOP_SQRT);

  logic          ds_in_ready, ds_out_valid, ds_ack;
  logic [FA:0]   c_cnt;                // CORDIC results in flight or in the FIFO, 0..FD
  logic [FA:0]   f_cnt;                // results stored in the FIFO, 0..FD
  logic          f_full, c_room, f_old;
  assign f_full = (f_cnt == (FA+1)'(FD));
  assign c_room = (c_cnt != (FA+1)'(FD));
  // Not ready while a divide/sqrt result waits for an output slot, while the
  // CORDIC FIFO is full or its oldest result is too old (see header), when a
  // DIV/SQRT arrives while that unit is still busy, or when a CORDIC op
  // arrives with no free FIFO slot.
  assign req_ready = !ds_out_valid && !f_full && !f_old && (!is_ds || ds_in_ready) &&
                     (!is_cord || c_room);
  assign fire      = req_valid && req_ready;

  // ---- execution units --------------------------------------------------------
  logic          add_v, mul_v, misc_v, ds_v, cord_v;
  logic [31:0]   add_r, mul_r, misc_r, ds_r, cord_r;
  logic [4:0]    add_f, mul_f, misc_f, ds_f, cord_f;
  logic [TW-1:0] add_t, mul_t, misc_t, ds_t, cord_t;

  fp_addsub #(.TW(TW)) u_add (
      .clk, .rst, .in_valid(fire && is_add), .in_sub(op_sub),
      .in_a(req_a), .in_b(req_b), .in_tag(req_tag),
      .out_valid(add_v), .out_result(add_r), .out_flags(add_f), .out_tag(add_t));

  fp_mul #(.TW(TW)) u_mul (
      .clk, .rst, .in_valid(fire && is_mul), .in_a(req_a), .in_b(req_b), .in_tag(req_tag),
      .out_valid(mul_v), .out_result(mul_r), .out_flags(mul_f), .out_tag(mul_t));

  fp_misc #(.TW(TW)) u_misc (
      .clk, .rst, .in_valid(fire && is_misc), .in_op(req_op),
      .in_a(req_a), .in_b(req_b), .in_tag(req_tag),
      .out_valid(misc_v), .out_result(misc_r), .out_flags(misc_f), .out_tag(misc_t));

  fp_divsqrt #(.TW(TW)) u_ds (
      .clk, .rst, .in_valid(fire && is_ds), .in_ready(ds_in_ready), .in_sqrt(op_sqrt),
      .in_a(req_a), .in_b(req_b), .in_tag(req_tag),
      .out_valid(ds_out_valid), .out_ack(ds_ack),
      .out_result(ds_r), .out_flags(ds_f), .out_tag(ds_t));
  assign ds_v = ds_out_valid;

  fp_cordic #(.TW(TW)) u_cord (
      .clk, .rst, .in_valid(fire && is_cord), .in_op(req_op),
      .in_a(req_a), .in_b(req_b), .in_tag(req_tag),
      .out_valid(cord_v), .out_result(cord_r), .out_flags(cord_f), .out_tag(cord_t));

  // ---- CORDIC result FIFO -------------------------------------------------
  // A plain circular buffer. The memory has no reset and is read without a
  // clock (Vivado builds it from LUT RAM); only the pointers and counters are
  // reset. Writes never find it full, because of the slot reservation.
  // Each entry also stores the time it arrived (the low 8 bits of a clock
  // counter), so the age of the oldest result is now - arrival, modulo 256.
  logic [32+5+TW+8-1:0] f_mem [FD];
  logic [FA-1:0]        f_wp, f_rp;
  logic                 f_v, f_pop;
  logic [31:0]          f_r;
  logic [4:0]           f_f;
  logic [TW-1:0]        f_t;
  logic [7:0]           now, f_ts, f_age;
  assign f_v   = (f_cnt != '0);
  // Braces unpack one wide memory word into named buses. An empty FIFO
  // may expose old bits here; f_v prevents those bits becoming a result.
  assign {f_r, f_f, f_t, f_ts} = f_mem[f_rp];
  assign f_age = now - f_ts;

  always_ff @(posedge clk) begin
    if (cord_v) f_mem[f_wp] <= {cord_r, cord_f, cord_t, now};
    if (rst) begin
      f_wp  <= '0;
      f_rp  <= '0;
      f_cnt <= '0;
      c_cnt <= '0;
      now   <= '0;
      f_old <= 1'b0;
    end else begin
      now <= now + 1'b1;
      // Registered, so the age compare stays off the req_ready path; it is
      // computed one clock early, so f_old is 1 from the clock the oldest
      // result turns AGE_MAX. When that result leaves, f_old can stay 1 for
      // one more clock than needed, which only costs that one clock.
      f_old <= f_v && (f_age >= 8'(AGE_MAX - 1));
      if (cord_v) f_wp <= f_wp + 1'b1;
      if (f_pop)  f_rp <= f_rp + 1'b1;
      // A reservation is made when a CORDIC request is accepted and given
      // back when its result leaves the FIFO.
      f_cnt <= f_cnt + (FA+1)'(cord_v) - (FA+1)'(f_pop);
      c_cnt <= c_cnt + (FA+1)'(fire && is_cord) - (FA+1)'(f_pop);
    end
  end

  // ---- result merge -------------------------------------------------------
  // 6-clock results have priority, then divide/sqrt, then the CORDIC FIFO.
  logic pipe_v;
  assign pipe_v = add_v || mul_v || misc_v;
  assign ds_ack = ds_v && !pipe_v;
  assign f_pop  = f_v && !pipe_v && !ds_v;

  always_ff @(posedge clk) begin
    rsp_valid <= rst ? 1'b0 : (pipe_v || ds_v || f_v);
    // At most one of add_v / mul_v / misc_v is 1 in a clock, so a priority
    // chain is just a convenient way to write a multiplexer here.
    if (add_v) begin
      rsp_result <= add_r;  rsp_flags <= add_f;  rsp_tag <= add_t;
    end else if (mul_v) begin
      rsp_result <= mul_r;  rsp_flags <= mul_f;  rsp_tag <= mul_t;
    end else if (misc_v) begin
      rsp_result <= misc_r; rsp_flags <= misc_f; rsp_tag <= misc_t;
    end else if (ds_v) begin
      rsp_result <= ds_r;   rsp_flags <= ds_f;   rsp_tag <= ds_t;
    end else begin
      rsp_result <= f_r;    rsp_flags <= f_f;    rsp_tag <= f_t;
    end
  end
endmodule
