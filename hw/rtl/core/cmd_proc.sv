// This is the command interpreter for one already-validated packet. I read
// its payload from frame_rx, check the opcode and argument sizes, then either
// operate on scratchpad or start a kernel. Finally I write reply bytes into
// the transmit buffer. The Python DeviceModel describes the same commands.
// The state register below is the route through those steps; only a rising
// clock edge changes state or commits a buffer/memory write.
module cmd_proc
  import accel_pkg::*;
#(
    parameter int RX_AW = 11,
    parameter int TX_AW = 10
) (
    input  logic                  clk,
    input  logic                  rst,
    // request
    input  logic                  frame_valid,
    input  logic [7:0]            frame_op,
    input  logic [7:0]            frame_tag,
    input  logic [15:0]           frame_len,
    output logic [RX_AW-1:0]      rb_addr,
    input  logic [7:0]            rb_data,
    // response
    output logic                  tb_we,
    output logic [TX_AW-1:0]      tb_addr,
    output logic [7:0]            tb_wdata,
    output logic                  tx_start,
    output logic [7:0]            tx_op,
    output logic [7:0]            tx_tag,
    output logic [15:0]           tx_len,
    input  logic                  tx_busy,
    // scratchpad
    output logic                  m_en,
    output logic                  m_we,
    output logic [SCRATCH_AW-1:0] m_addr,
    output logic [31:0]           m_wdata,
    input  logic [31:0]           m_rdata,
    // kernel engine
    output logic                  k_start,
    output logic [7:0]            k_kernel,
    output logic [8*21-1:0]       k_args,     // RUN payload bytes 1..21
    input  logic                  k_done,
    input  logic [31:0]           k_cycles,
    input  logic [31:0]           k_aux,
    // stats
    input  logic [15:0]           crc_errors,
    input  logic [15:0]           frame_errors,
    output logic                  busy
);
  localparam logic [32:0] WORDS = 33'(2 ** SCRATCH_AW);
  // Bounds checks use 33-bit sums. A 32-bit sum could wrap an invalid high
  // address back into the legal range before the comparison.

  typedef enum logic [3:0] {
    IDLE, CHECK, LOAD, EXEC, WR, RD_HDR, RD_ISSUE, RD_BYTES, KWAIT, COPY, SEND
  } state_t;
  state_t state;

  logic [7:0]  op, tag;
  logic [15:0] len, idx, cnt, rlen, tptr, nload;
  logic [7:0]  args [32];       // RUN can carry up to 22 payload bytes
  logic [7:0]  rbuf [16];
  logic [1:0]  bsel;
  logic [31:0] addr, word, last_cycles;

  // The wire sends the least significant byte first. These views assemble
  // adjacent payload bytes into the address, count, and kernel arguments.
  wire [31:0] a0 = {args[3], args[2], args[1], args[0]};
  wire [15:0] a4 = {args[5], args[4]};
  wire [31:0] r0 = {args[4], args[3], args[2], args[1]};
  wire [31:0] r1 = {args[8], args[7], args[6], args[5]};
  // FPU kernel arguments: op code, count, then base addresses of a, b, r, f.
  wire [7:0]  fpu_fop   = args[1];
  wire [31:0] fpu_count = {args[5], args[4], args[3], args[2]};
  wire [31:0] fpu_a     = {args[9], args[8], args[7], args[6]};
  wire [31:0] fpu_b     = {args[13], args[12], args[11], args[10]};
  wire [31:0] fpu_r     = {args[17], args[16], args[15], args[14]};
  wire [31:0] fpu_f     = {args[21], args[20], args[19], args[18]};
  // Each of the four arrays must lie inside the scratchpad.
  wire fpu_in_range = ({1'b0, fpu_a} + {1'b0, fpu_count} <= WORDS) &&
                      ({1'b0, fpu_b} + {1'b0, fpu_count} <= WORDS) &&
                      ({1'b0, fpu_r} + {1'b0, fpu_count} <= WORDS) &&
                      ({1'b0, fpu_f} + {1'b0, fpu_count} <= WORDS);
  wire [15:0] nwords = (len - 16'd4) >> 2;

  // Register loop limits before entering a byte-by-byte state. This also
  // keeps length arithmetic out of the memory write timing path.
  logic [15:0] load_last, wr_last;
  assign nload = (op == OP_WRITE_MEM) ? 16'd4 : (len > 32 ? 16'd32 : len);

  assign rb_addr  = (state == LOAD) ? RX_AW'(idx + 16'd4) : RX_AW'(idx);
  // Four incoming bytes make one 32-bit word. wr_pend delays its write one
  // clock, so address and data come from registers at the RAM port.
  logic                  wr_pend;
  logic [SCRATCH_AW-1:0] wr_addr;
  logic [31:0]           wr_data;
  assign m_en     = wr_pend || (state == RD_ISSUE);
  assign m_we     = wr_pend;
  assign m_addr   = wr_pend ? wr_addr : SCRATCH_AW'(addr);
  assign m_wdata  = wr_data;
  assign tb_we    = (state == COPY) || (state == RD_HDR) || (state == RD_BYTES);
  assign tb_addr  = TX_AW'(tptr);
  assign tb_wdata = (state == COPY)   ? rbuf[tptr[3:0]] :
                    (state == RD_HDR) ? ST_OK : m_rdata[8*bsel +: 8];
  assign tx_op    = op | 8'h80;
  assign tx_tag   = tag;
  assign tx_len   = rlen;
  assign busy     = (state != IDLE) || tx_busy;
  assign k_kernel = args[0];
  // Pass payload bytes 1..21 to the kernel engine as one packed vector,
  // byte k of the vector being payload byte k+1.
  always_comb for (int k = 0; k < 21; k++) k_args[8*k +: 8] = args[k + 1];

  task automatic respond_status(input logic [7:0] st);
    // Error replies contain just the status byte; COPY moves it to tx_buf.
    rbuf[0] <= st;
    rlen    <= 16'd1;
    tptr    <= '0;
    state   <= COPY;
  endtask

  always_ff @(posedge clk) begin
    tx_start <= 1'b0;
    k_start  <= 1'b0;
    wr_pend  <= 1'b0;
    if (rst) begin
      state       <= IDLE;
      last_cycles <= '0;
    end else begin
      case (state)
        IDLE: if (frame_valid) begin
          op    <= frame_op;
          tag   <= frame_tag;
          len   <= frame_len;
          wr_last <= frame_len + 16'd3;
          state <= CHECK;
        end

        // Validate byte counts before LOAD interprets any payload arguments.
        // Unknown opcodes still receive a reply because framing already passed.
        CHECK: begin
          idx <= '0;
          load_last <= nload - 1'b1;
          case (op)
            OP_PING, OP_STATUS: if (len == 0) state <= EXEC; else respond_status(ST_BAD_LEN);
            OP_READ_MEM:  if (len == 6) state <= LOAD; else respond_status(ST_BAD_LEN);
            OP_WRITE_MEM: if (len >= 8 && len[1:0] == 2'b00) state <= LOAD; else respond_status(ST_BAD_LEN);
            OP_RUN:       if (len >= 1 && len <= 22) state <= LOAD; else respond_status(ST_BAD_LEN);
            default:      respond_status(ST_BAD_OP);
          endcase
        end

        // LOAD copies only the argument bytes needed for the selected opcode.
        // WRITE_MEM later streams its data directly from the receive buffer.
        LOAD: begin
          args[idx[4:0]] <= rb_data;
          idx <= idx + 1'b1;
          if (idx == load_last) state <= EXEC;
        end

        EXEC: case (op)
          OP_PING: begin
            {rbuf[4], rbuf[3], rbuf[2], rbuf[1]} <= MAGIC;
            rbuf[0]  <= ST_OK;
            rbuf[5]  <= 8'(VERSION_MAJOR);
            rbuf[6]  <= 8'(VERSION_MINOR);
            rbuf[7]  <= 8'(SCRATCH_AW);
            rbuf[8]  <= 8'h00;
            {rbuf[12], rbuf[11], rbuf[10], rbuf[9]} <= 32'(CLK_HZ);
            rlen  <= 16'd13;
            tptr  <= '0;
            state <= COPY;
          end
          OP_STATUS: begin
            rbuf[0] <= ST_OK;
            {rbuf[2], rbuf[1]} <= crc_errors;
            {rbuf[4], rbuf[3]} <= frame_errors;
            {rbuf[8], rbuf[7], rbuf[6], rbuf[5]} <= last_cycles;
            rlen  <= 16'd9;
            tptr  <= '0;
            state <= COPY;
          end
          OP_READ_MEM: begin
            // A reply needs one status byte plus four bytes per requested word.
            // At most 255 words fit the 1024-byte payload limit.
            if (a4 == 0 || a4 > 255) respond_status(ST_BAD_LEN);
            else if ({1'b0, a0} + 33'(a4) > WORDS) respond_status(ST_BAD_ADDR);
            else begin
              addr  <= a0;
              cnt   <= a4;
              tptr  <= '0;
              state <= RD_HDR;
            end
          end
          OP_WRITE_MEM: begin
            if ({1'b0, a0} + 33'(nwords) > WORDS) respond_status(ST_BAD_ADDR);
            else begin
              addr  <= a0;
              idx   <= 16'd8;
              bsel  <= '0;
              state <= WR;
            end
          end
          OP_RUN: begin
            if (args[0] == K_NOP) begin
              if (len == 1) begin k_start <= 1'b1; state <= KWAIT; end
              else respond_status(ST_BAD_LEN);
            end else if (args[0] == K_FILL) begin
              if (len != 13) respond_status(ST_BAD_LEN);
              else if ({1'b0, r0} + {1'b0, r1} > WORDS) respond_status(ST_BAD_ADDR);
              else begin k_start <= 1'b1; state <= KWAIT; end
            end else if (args[0] == K_FPU) begin
              // Checks in the same order as the Python model: length, then
              // op code, then address ranges.
              if (len != 22) respond_status(ST_BAD_LEN);
              else if (fpu_fop > 8'd11) respond_status(ST_BAD_ARG);
              else if (!fpu_in_range) respond_status(ST_BAD_ADDR);
              else begin k_start <= 1'b1; state <= KWAIT; end
            end else respond_status(ST_BAD_KERNEL);
          end
          default: respond_status(ST_BAD_OP);
        endcase

        // bsel selects one of four bytes in a word. The fourth byte completes
        // wr_data; wr_pend commits that word on the following clock.
        WR: begin
          word[8*bsel +: 8] <= rb_data;
          idx  <= idx + 1'b1;
          bsel <= bsel + 1'b1;
          if (bsel == 2'd3) begin
            wr_pend <= 1'b1;
            wr_addr <= SCRATCH_AW'(addr);
            wr_data <= {rb_data, word[23:0]};
            addr    <= addr + 1'b1;
            if (idx == wr_last) respond_status(ST_OK);
          end
        end

        // RAM reads are clocked. RD_ISSUE requests a word, and RD_BYTES
        // spends four clocks writing its little-endian bytes into tx_buf.
        RD_HDR: begin
          tptr  <= 16'd1;
          bsel  <= '0;
          state <= RD_ISSUE;
        end
        RD_ISSUE: state <= RD_BYTES;
        RD_BYTES: begin
          tptr <= tptr + 1'b1;
          bsel <= bsel + 1'b1;
          if (bsel == 2'd3) begin
            addr <= addr + 1'b1;
            cnt  <= cnt - 1'b1;
            if (cnt == 1) begin
              rlen  <= tptr + 1'b1;
              state <= SEND;
            end else state <= RD_ISSUE;
          end
        end

        // The start pulse has already been sent. Wait for the engine's done
        // pulse before using its final cycle register and reporting success.
        KWAIT: if (k_done) begin
          last_cycles <= k_cycles;
          rbuf[0] <= ST_OK;
          {rbuf[4], rbuf[3], rbuf[2], rbuf[1]} <= k_cycles;
          {rbuf[8], rbuf[7], rbuf[6], rbuf[5]} <= k_aux;
          rlen  <= 16'd9;
          tptr  <= '0;
          state <= COPY;
        end

        // tb_we is high in COPY, so each clock commits one prepared rbuf byte.
        // The last byte is stored before the SEND state starts the packet.
        COPY: begin
          tptr <= tptr + 1'b1;
          if (tptr == rlen - 1) state <= SEND;
        end

        // frame_tx can accept a new frame only when its prior one is done.
        SEND: if (!tx_busy) begin
          tx_start <= 1'b1;
          state    <= IDLE;
        end

        default: state <= IDLE;
      endcase
    end
  end
endmodule
