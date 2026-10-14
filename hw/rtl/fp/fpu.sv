// FPU top level: one request port, one result port, four execution units.
//
// Request handshake: the caller holds req_valid with op/a/b/tag stable; the
// request is taken on a rising clock edge where req_valid and req_ready are
// both 1. Results come back on rsp_valid with the same tag. There is no
// ready signal on the result side: the caller must accept every result.
//
// Results can come back in a different order from the requests, because
// divide/sqrt takes about 33 clocks while the other operations take a fixed
// LAT_PIPE clocks. The tag is how the caller matches results to requests.
//
//   op            unit         timing
//   ADD, SUB      fp_addsub    pipelined, 1 per clock, 6 clocks
//   MUL           fp_mul       pipelined, 1 per clock, 6 clocks
//   I2F ... LE    fp_misc      pipelined, 1 per clock, 6 clocks
//   DIV, SQRT     fp_divsqrt   one at a time, fixed 33 clocks
// plus one output register, so pipelined results appear 7 clocks after
// acceptance.
//
// Result collisions: the three pipelined units have identical latency and
// only one request enters per clock, so at most one of them finishes in any
// clock. fp_divsqrt can finish in the same clock as a pipelined result, so it
// holds its result until a clock where the pipelined units have nothing to
// return. To guarantee that clock arrives, no new requests are accepted while
// a divide/sqrt result is waiting.
module fpu
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,
    input  logic          rst,
    input  logic          req_valid,
    output logic          req_ready,
    input  logic [3:0]    req_op,
    input  logic [31:0]   req_a,
    input  logic [31:0]   req_b,
    input  logic [TW-1:0] req_tag,
    output logic          rsp_valid,
    output logic [31:0]   rsp_result,
    output logic [4:0]    rsp_flags,
    output logic [TW-1:0] rsp_tag
);
  // ---- dispatch -------------------------------------------------------------
  logic is_add, is_mul, is_ds, is_misc, fire;
  assign is_add  = (req_op == FOP_ADD) || (req_op == FOP_SUB);
  assign is_mul  = (req_op == FOP_MUL);
  assign is_ds   = (req_op == FOP_DIV) || (req_op == FOP_SQRT);
  assign is_misc = !(is_add || is_mul || is_ds);   // includes unknown ops

  // Operation selects for the units, as named wires. (Writing the comparison
  // directly inside the port connection made Icarus Verilog produce X.)
  logic op_sub, op_sqrt;
  assign op_sub  = (req_op == FOP_SUB);
  assign op_sqrt = (req_op == FOP_SQRT);

  logic ds_in_ready, ds_out_valid, ds_ack;
  // Not ready while a divide/sqrt result waits for an output slot (see header),
  // or when a DIV/SQRT arrives while that unit is still busy.
  assign req_ready = !ds_out_valid && (!is_ds || ds_in_ready);
  assign fire      = req_valid && req_ready;

  // ---- execution units --------------------------------------------------------
  logic          add_v, mul_v, misc_v, ds_v;
  logic [31:0]   add_r, mul_r, misc_r, ds_r;
  logic [4:0]    add_f, mul_f, misc_f, ds_f;
  logic [TW-1:0] add_t, mul_t, misc_t, ds_t;

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

  // ---- result merge -------------------------------------------------------
  // Pipelined results have priority; divide/sqrt takes any empty clock.
  logic pipe_v;
  assign pipe_v = add_v || mul_v || misc_v;
  assign ds_ack = ds_v && !pipe_v;

  always_ff @(posedge clk) begin
    rsp_valid <= rst ? 1'b0 : (pipe_v || ds_v);
    // At most one of add_v / mul_v / misc_v is 1 in a clock, so a priority
    // chain is just a convenient way to write a multiplexer here.
    if (add_v) begin
      rsp_result <= add_r;  rsp_flags <= add_f;  rsp_tag <= add_t;
    end else if (mul_v) begin
      rsp_result <= mul_r;  rsp_flags <= mul_f;  rsp_tag <= mul_t;
    end else if (misc_v) begin
      rsp_result <= misc_r; rsp_flags <= misc_f; rsp_tag <= misc_t;
    end else begin
      rsp_result <= ds_r;   rsp_flags <= ds_f;   rsp_tag <= ds_t;
    end
  end
endmodule
