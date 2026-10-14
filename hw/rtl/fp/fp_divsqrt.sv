// Iterative fp32 divide and square root, one result bit per clock.
//
// Unlike the pipelined units this one handles a single operation at a time:
// in_ready is low while it works. Every operation takes the same number of
// clocks, even special cases like x/0 whose answer is known immediately.
// Fixed timing makes the FPU kernel's cycle count depend only on how many
// elements it processes, which keeps the host-side model simple.
//
// Division uses "restoring" long division in binary, the same procedure as
// pencil-and-paper division: at each step, if the divisor fits into the
// current remainder, write a 1 in the quotient and subtract it, else write 0.
// Square root uses the matching digit-by-digit method: bring down two bits of
// the radicand per step and try to extend the root by a 1.
//
// Timeline after the request is accepted (state register values):
//   PREP     normalize subnormal significands, compute the exponent
//   ITER     27 clocks, one quotient/root bit each
//   PACK     build the 26-bit significand with its sticky bit
//   ROUND1   first half of rounding
//   ROUND2   second half of rounding
//   OUT      hold the result until the FPU top accepts it (out_ack)
module fp_divsqrt
  import accel_pkg::*;
  import fp_pkg::*;
#(
    parameter int TW = 8
) (
    input  logic          clk,
    input  logic          rst,
    input  logic          in_valid,
    output logic          in_ready,
    input  logic          in_sqrt,   // 1: sqrt(a), 0: a / b
    input  logic [31:0]   in_a,
    input  logic [31:0]   in_b,
    input  logic [TW-1:0] in_tag,
    output logic          out_valid,
    input  logic          out_ack,
    output logic [31:0]   out_result,
    output logic [4:0]    out_flags,
    output logic [TW-1:0] out_tag
);
  localparam int ITERS = 27;

  typedef enum logic [2:0] {IDLE, PREP, ITER, PACK, ROUND1, ROUND2, OUT} state_t;
  state_t state;

  logic                 sqrt_op, sign, special;
  logic [TW-1:0]        tag;
  logic [31:0]          sp_res;          // result when special = 1
  logic [4:0]           sp_fl;
  logic [23:0]          ma, mb;          // significands as received
  logic [5:0]           lza, lzb;        // their leading zero counts
  logic signed [EW-1:0] ea, eb, exp;
  logic [4:0]           cnt;
  // Division registers
  logic [25:0]          rem_d;           // partial remainder
  logic [23:0]          div;             // normalized divisor
  logic [26:0]          quo;             // quotient bits, MSB first
  // Square root registers
  logic [51:0]          rad;             // radicand, consumed two bits a step
  logic [28:0]          rem_s;           // partial remainder
  logic [25:0]          root;            // root bits so far
  round_mid_t           mid;             // between the two rounding clocks
  logic [25:0]          sig_q;           // significand built in PACK
  logic signed [EW-1:0] exp_q;

  assign in_ready  = (state == IDLE);
  assign out_valid = (state == OUT);

  always_ff @(posedge clk) begin
    if (rst) begin
      state <= IDLE;
    end else begin
      case (state)
        // ------------------------------------------------------------------
        // Capture the request, classify it, and count leading zeros.
        IDLE: if (in_valid) begin
          sqrt_op <= in_sqrt;
          tag     <= in_tag;
          ma      <= mant24(in_a);
          mb      <= mant24(in_b);
          lza     <= lzc24(mant24(in_a));
          lzb     <= lzc24(mant24(in_b));
          ea      <= exp_eff(in_a);
          eb      <= exp_eff(in_b);
          sign    <= in_sqrt ? in_a[31] : (in_a[31] ^ in_b[31]);
          special <= 1'b1;
          sp_fl   <= '0;
          if (in_sqrt) begin
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
          end else begin
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
          end
          state <= PREP;
        end

        // ------------------------------------------------------------------
        // Normalize and load the iteration registers.
        PREP: begin
          logic [23:0]          na, nb;
          logic signed [EW-1:0] ea_n, eb_n, e_unb;
          na   = ma << lza;                 // leading 1 now at bit 23
          nb   = mb << lzb;
          ea_n = ea - EW'(lza);             // value unchanged: shift up, exp down
          eb_n = eb - EW'(lzb);
          cnt  <= '0;
          if (!sqrt_op) begin
            // a/b = (na/nb) * 2^(ea-eb). The quotient na/nb is in (0.5, 2);
            // the first bit computed is its 2^0 place.
            exp   <= ea_n - eb_n + EW'(127);
            rem_d <= {2'b00, na};
            div   <= nb;
            quo   <= '0;
          end else begin
            // value = na * 2^(E-23) with E = ea_n - 127. I want an even power
            // of two outside the root, so I write the value as m'' * 2^Q with
            // m'' = na<<1 (Q = E-24) when E is even, or na<<2 (Q = E-25) when
            // E is odd. Then sqrt = sqrt(m'' * 2^26) * 2^((Q-26)/2), and the
            // integer root of m''*2^26 has exactly 26 bits with its leading 1
            // at bit 25. The biased result exponent works out to
            // 127 + floor(E/2), which is an arithmetic shift right of E.
            e_unb = ea_n - EW'(127);
            exp  <= EW'(127) + (e_unb >>> 1);
            rad  <= e_unb[0] ? {na, 2'b00, 26'b0} : {1'b0, na, 1'b0, 26'b0};
            rem_s <= '0;
            root  <= '0;
          end
          state <= ITER;
        end

        // ------------------------------------------------------------------
        // One bit per clock. Division runs all 27 steps; square root needs
        // 26 and idles on the last one so both take the same time.
        ITER: begin
          if (!sqrt_op) begin
            if (rem_d >= {2'b00, div}) begin
              rem_d <= (rem_d - {2'b00, div}) << 1;
              quo   <= {quo[25:0], 1'b1};
            end else begin
              rem_d <= rem_d << 1;
              quo   <= {quo[25:0], 1'b0};
            end
          end else if (cnt < 5'd26) begin
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
        // Pack the bits into round_pack's format: 25 bits from the leading 1
        // down, then a sticky bit that is 1 if the remainder is nonzero (the
        // exact answer has more 1 bits further down).
        // PACK, ROUND1 and ROUND2 are separate clocks for timing: as one or
        // two clocks this logic missed 100 MHz or met it with under 0.1 ns.
        PACK: begin
          if (!sqrt_op) begin
            if (quo[26]) begin                    // quotient in [1, 2)
              sig_q <= {quo[26:2], quo[1] | quo[0] | (rem_d != '0)};
              exp_q <= exp;
            end else begin                        // quotient in (0.5, 1)
              sig_q <= {quo[25:1], quo[0] | (rem_d != '0)};
              exp_q <= exp - 1'b1;
            end
          end else begin
            sig_q <= {root[25:1], root[0] | (rem_s != '0)};
            exp_q <= exp;
          end
          state <= ROUND1;
        end

        // First half of rounding (subnormal shift, tininess).
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

        OUT: if (out_ack) state <= IDLE;

        default: state <= IDLE;
      endcase
    end
  end
endmodule
