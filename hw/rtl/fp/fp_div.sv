// Iterative fp32 divide, one quotient bit per clock.
//
// This is the division half of the old fp_divsqrt unit. I split the two
// operations so a divide and a square root can be worked on at the same time:
// each module now has its own state machine, its own in_ready, its own tag and
// its own held result. The arithmetic is unchanged from fp_divsqrt, so every
// result bit, every flag and the clock count are the same as before.
//
// The unit handles a single operation at a time: in_ready is low while it
// works. Every operation takes the same number of clocks, even special cases
// like x/0 whose answer is known immediately. Fixed timing makes the FPU
// kernel's cycle count depend only on how many elements it processes, which
// keeps the host-side model simple.
//
// Division uses "restoring" long division in binary, the same procedure as
// pencil-and-paper division: at each step, if the divisor fits into the
// current remainder, I write a 1 in the quotient and subtract it, else I write
// a 0 and keep the remainder.
//
// Timeline after the request is accepted (state register values):
//   PREP     normalize subnormal significands, compute the exponent
//   ITER     27 clocks, one quotient bit each
//   PACK     build the 26-bit significand with its sticky bit
//   ROUND1   first half of rounding
//   ROUND2   second half of rounding
//   OUT      hold the result until the FPU top accepts it (out_ack)
// From an accepted request to the next possible one is 33 clocks when the
// result is accepted on its first OUT clock: capture, PREP, 27 ITER, PACK,
// ROUND1, ROUND2, OUT. When out_valid rises, the answer stays available until
// out_ack, so the FPU can wait for a free clock on its shared result port.
module fp_div
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,                   // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic          rst,                   // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic          in_valid,              // Input valid: a divide is accepted only when input ready is also high on the rising edge.
    output logic          in_ready,              // Input ready: high while idle, allowing a new divide request to be captured.
    input  logic [31:0]   in_a,                  // Input operand A: floating-point dividend.
    input  logic [31:0]   in_b,                  // Input operand B: floating-point divisor.
    input  logic [TW-1:0] in_tag,                // Input request tag: identifier carried beside the operands so the caller can match the result.
    output logic          out_valid,             // Output valid: completed result, flags and tag remain available until output acknowledgement.
    input  logic          out_ack,               // Output acknowledgement: high accepts the waiting result on the rising edge and frees the unit.
    output logic [31:0]   out_result,            // Output result: 32-bit encoded quotient; meaningful only while output valid is high.
    output logic [4:0]    out_flags,             // Output exception flags: bits 4..0 mean invalid, divide by zero, overflow, underflow and inexact.
    output logic [TW-1:0] out_tag                // Output request tag: unchanged identifier of the operation that produced this result.
);
  localparam int ITERS = 27;                    // quotient bits: 24 significand bits, guard, round, and one for a quotient below 1

  typedef enum logic [2:0] {IDLE, PREP, ITER, PACK, ROUND1, ROUND2, OUT} state_t;
  state_t state;

  logic                 sign, special;
  logic [TW-1:0]        tag;
  logic [31:0]          sp_res;          // result when special = 1
  logic [4:0]           sp_fl;           // flags when special = 1
  logic [23:0]          ma, mb;          // significands as received
  logic [5:0]           lza, lzb;        // their leading zero counts
  logic signed [EW-1:0] ea, eb, exp;     // effective exponents, then the quotient's biased exponent
  logic [4:0]           cnt;             // which quotient bit ITER is finding now
  logic [25:0]          rem_d;           // partial remainder
  logic [23:0]          div;             // normalized divisor
  logic [26:0]          quo;             // quotient bits, MSB first
  round_mid_t           mid;             // between the two rounding clocks
  logic [25:0]          sig_q;           // significand built in PACK
  logic signed [EW-1:0] exp_q;           // exponent that goes with sig_q

  // Ready and valid come straight from the state, so neither depends on the
  // request inputs in the same clock (no combinational path through here).
  assign in_ready  = (state == IDLE);
  assign out_valid = (state == OUT);

  always_ff @(posedge clk) begin
    if (rst) begin
      // Only the state needs a reset: every data register is written before
      // anything reads it.
      state <= IDLE;
    end else begin
      case (state)
        // ------------------------------------------------------------------
        // I capture the request, classify it, and count leading zeros. The
        // special cases are decided here, but the unit still runs the full
        // timeline so that every divide takes the same number of clocks.
        IDLE: if (in_valid) begin
          tag     <= in_tag;
          ma      <= mant24(in_a);
          mb      <= mant24(in_b);
          lza     <= lzc24(mant24(in_a));
          lzb     <= lzc24(mant24(in_b));
          ea      <= exp_eff(in_a);
          eb      <= exp_eff(in_b);
          sign    <= in_a[31] ^ in_b[31];
          special <= 1'b1;
          sp_fl   <= '0;
          if (is_nan(in_a) || is_nan(in_b)) begin
            sp_res <= CANON_NAN;
            sp_fl  <= (is_snan(in_a) || is_snan(in_b)) ? FFLAG_NV : '0;
          end else if ((is_inf(in_a) && is_inf(in_b)) || (is_zero(in_a) && is_zero(in_b))) begin
            sp_res <= CANON_NAN;  sp_fl <= FFLAG_NV;  // inf/inf, 0/0
          end else if (is_inf(in_a)) begin
            sp_res <= {in_a[31] ^ in_b[31], POS_INF[30:0]};
          end else if (is_inf(in_b) || is_zero(in_a)) begin
            sp_res <= {in_a[31] ^ in_b[31], 31'b0};   // x/inf, 0/x
          end else if (is_zero(in_b)) begin
            sp_res <= {in_a[31] ^ in_b[31], POS_INF[30:0]};
            sp_fl  <= FFLAG_DZ;                       // nonzero / 0
          end else begin
            special <= 1'b0;
          end
          state <= PREP;
        end

        // ------------------------------------------------------------------
        // I normalize both significands and load the iteration registers.
        PREP: begin
          logic [23:0]          na, nb;
          logic signed [EW-1:0] ea_n, eb_n;
          na   = ma << lza;                 // leading 1 now at bit 23
          nb   = mb << lzb;
          ea_n = ea - EW'(lza);             // value unchanged: shift up, exp down
          eb_n = eb - EW'(lzb);
          cnt  <= '0;
          // a/b = (na/nb) * 2^(ea-eb). The quotient na/nb is in (0.5, 2);
          // the first bit computed is its 2^0 place.
          exp   <= ea_n - eb_n + EW'(127);
          rem_d <= {2'b00, na};
          div   <= nb;
          quo   <= '0;
          state <= ITER;
        end

        // ------------------------------------------------------------------
        // One quotient bit per clock. The remainder stays below twice the
        // divisor, so one compare decides the bit.
        ITER: begin
          if (rem_d >= {2'b00, div}) begin
            rem_d <= (rem_d - {2'b00, div}) << 1;
            quo   <= {quo[25:0], 1'b1};
          end else begin
            rem_d <= rem_d << 1;
            quo   <= {quo[25:0], 1'b0};
          end
          cnt <= cnt + 1'b1;
          if (cnt == 5'(ITERS - 1)) state <= PACK;
        end

        // ------------------------------------------------------------------
        // I pack the bits into round_pack's format: 25 bits from the leading 1
        // down, then a sticky bit that is 1 if the remainder is nonzero (the
        // exact answer has more 1 bits further down).
        // PACK, ROUND1 and ROUND2 are separate clocks for timing: as one or
        // two clocks this logic missed 100 MHz or met it with under 0.1 ns.
        PACK: begin
          if (quo[26]) begin                    // quotient in [1, 2)
            sig_q <= {quo[26:2], quo[1] | quo[0] | (rem_d != '0)};
            exp_q <= exp;
          end else begin                        // quotient in (0.5, 1)
            sig_q <= {quo[25:1], quo[0] | (rem_d != '0)};
            exp_q <= exp - 1'b1;
          end
          state <= ROUND1;
        end

        // First half of rounding (subnormal shift, tininess).
        ROUND1: begin
          mid   <= round_pre(sign, exp_q, sig_q);
          state <= ROUND2;
        end

        // Second half of rounding, then I choose between the rounded value and
        // the special-case answer decided at capture.
        ROUND2: begin
          logic [36:0] rp;
          rp = round_post(mid);
          out_result <= special ? sp_res : rp[31:0];
          out_flags  <= special ? sp_fl  : rp[36:32];
          out_tag    <= tag;
          state      <= OUT;
        end

        // I hold the answer here until the FPU's result port takes it.
        OUT: if (out_ack) state <= IDLE;

        default: state <= IDLE;
      endcase
    end
  end
endmodule
