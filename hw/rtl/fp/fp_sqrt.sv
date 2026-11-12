// Iterative fp32 square root, one root bit per clock.
//
// This is the square-root half of the old fp_divsqrt unit. I split it from
// division so a square root can run while a divide is in progress: this module
// has its own state machine, in_ready, tag and held result. The arithmetic is
// unchanged from fp_divsqrt, so every result bit, every flag and the clock
// count are the same as before.
//
// Square root uses the digit-by-digit method, the binary cousin of the old
// pencil-and-paper square root: each step brings down two more bits of the
// radicand and tries to extend the root by a 1.
//
// Timeline after the request is accepted (state register values):
//   PREP     normalize a subnormal significand, compute the exponent
//   ITER     27 clocks: 26 root bits, then one idle clock
//   PACK     build the 26-bit significand with its sticky bit
//   ROUND1   first half of rounding
//   ROUND2   second half of rounding
//   OUT      hold the result until the FPU top accepts it (out_ack)
// The root only needs 26 steps. I keep the idle 27th clock from fp_divsqrt so
// a square root still takes exactly as long as before, 33 clocks from one
// accepted request to the next, and every timing model stays valid. Speeding
// up square root is a separate task; this split is only meant to separate.
module fp_sqrt
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,                   // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    input  logic          rst,                   // Reset: active high, sampled on the clock edge to clear control state and pending validity.
    input  logic          in_valid,              // Input valid: a square root is accepted only when input ready is also high on the rising edge.
    output logic          in_ready,              // Input ready: high while idle, allowing a new square-root request to be captured.
    input  logic [31:0]   in_a,                  // Input operand A: floating-point radicand.
    input  logic [TW-1:0] in_tag,                // Input request tag: identifier carried beside the operand so the caller can match the result.
    output logic          out_valid,             // Output valid: completed result, flags and tag remain available until output acknowledgement.
    input  logic          out_ack,               // Output acknowledgement: high accepts the waiting result on the rising edge and frees the unit.
    output logic [31:0]   out_result,            // Output result: 32-bit encoded root; meaningful only while output valid is high.
    output logic [4:0]    out_flags,             // Output exception flags: bits 4..0 mean invalid, divide by zero, overflow, underflow and inexact.
    output logic [TW-1:0] out_tag                // Output request tag: unchanged identifier of the operation that produced this result.
);
  localparam int ITERS = 27;                    // clocks in ITER; the root needs 26 of them

  typedef enum logic [2:0] {IDLE, PREP, ITER, PACK, ROUND1, ROUND2, OUT} state_t;
  state_t state;

  logic                 sign, special;
  logic [TW-1:0]        tag;
  logic [31:0]          sp_res;          // result when special = 1
  logic [4:0]           sp_fl;           // flags when special = 1
  logic [23:0]          ma;              // significand as received
  logic [5:0]           lza;             // its leading zero count
  logic signed [EW-1:0] ea, exp;         // effective exponent, then the root's biased exponent
  logic [4:0]           cnt;             // which ITER clock this is
  logic [51:0]          rad;             // radicand, consumed two bits a step
  logic [28:0]          rem_s;           // partial remainder
  logic [25:0]          root;            // root bits so far
  round_mid_t           mid;             // between the two rounding clocks
  logic [25:0]          sig_q;           // significand built in PACK
  logic signed [EW-1:0] exp_q;           // exponent that goes with sig_q

  // Ready and valid come straight from the state register.
  assign in_ready  = (state == IDLE);
  assign out_valid = (state == OUT);

  always_ff @(posedge clk) begin
    if (rst) begin
      state <= IDLE;
    end else begin
      case (state)
        // ------------------------------------------------------------------
        // I capture the request and decide the special cases now; the unit
        // still runs its whole timeline so every square root takes as long.
        IDLE: if (in_valid) begin
          tag     <= in_tag;
          ma      <= mant24(in_a);
          lza     <= lzc24(mant24(in_a));
          ea      <= exp_eff(in_a);
          sign    <= in_a[31];
          special <= 1'b1;
          sp_fl   <= '0;
          if (is_nan(in_a)) begin
            sp_res <= CANON_NAN;  sp_fl <= is_snan(in_a) ? FFLAG_NV : '0;
          end else if (is_zero(in_a)) begin
            sp_res <= in_a;                           // sqrt(+-0) = +-0
          end else if (in_a[31]) begin
            sp_res <= CANON_NAN;  sp_fl <= FFLAG_NV;  // negative input
          end else if (is_inf(in_a)) begin
            sp_res <= POS_INF;
          end else begin
            special <= 1'b0;
          end
          state <= PREP;
        end

        // ------------------------------------------------------------------
        // I normalize the significand and load the iteration registers.
        PREP: begin
          logic [23:0]          na;
          logic signed [EW-1:0] ea_n, e_unb;
          na   = ma << lza;                 // leading 1 now at bit 23
          ea_n = ea - EW'(lza);             // value unchanged: shift up, exp down
          cnt  <= '0;
          // value = na * 2^(E-23) with E = ea_n - 127. I want an even power
          // of two outside the root, so I write the value as m'' * 2^Q with
          // m'' = na<<1 (Q = E-24) when E is even, or na<<2 (Q = E-25) when
          // E is odd. Then sqrt = sqrt(m'' * 2^26) * 2^((Q-26)/2), and the
          // integer root of m''*2^26 has exactly 26 bits with its leading 1
          // at bit 25. The biased result exponent works out to
          // 127 + floor(E/2), which is an arithmetic shift right of E.
          e_unb = ea_n - EW'(127);
          exp   <= EW'(127) + (e_unb >>> 1);
          rad   <= e_unb[0] ? {na, 2'b00, 26'b0} : {1'b0, na, 1'b0, 26'b0};
          rem_s <= '0;
          root  <= '0;
          state <= ITER;
        end

        // ------------------------------------------------------------------
        // One root bit per clock for 26 clocks, then one idle clock.
        ITER: begin
          if (cnt < 5'd26) begin
            logic [28:0] r2, trial;
            r2    = {rem_s[26:0], rad[51:50]};      // bring down two bits
            trial = {1'b0, root, 2'b01};            // (root * 4) + 1
            rad  <= rad << 2;
            if (r2 >= trial) begin
              rem_s <= r2 - trial;
              root  <= {root[24:0], 1'b1};
            end else begin
              rem_s <= r2;
              root  <= {root[24:0], 1'b0};
            end
          end
          cnt <= cnt + 1'b1;
          if (cnt == 5'(ITERS - 1)) state <= PACK;
        end

        // ------------------------------------------------------------------
        // 25 root bits from the leading 1 down, then the sticky bit: 1 when
        // the remainder is nonzero, meaning the exact root continues.
        PACK: begin
          sig_q <= {root[25:1], root[0] | (rem_s != '0)};
          exp_q <= exp;
          state <= ROUND1;
        end

        // First half of rounding (subnormal shift, tininess). A square root of
        // an fp32 number can never be subnormal, but I keep the shared
        // rounding path rather than special-case it.
        ROUND1: begin
          mid   <= round_pre(sign, exp_q, sig_q);
          state <= ROUND2;
        end

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
