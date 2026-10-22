// This is the board-independent top level used by the simulators. I treat a
// command as a trip through four stages: receive bytes, decode and check a
// packet, perform a command, then build and transmit a reply. Each stage
// passes bytes or control pulses to the next; the clock moves all registers.
// nexys_top adds physical pins, clocking, reset handling, and LEDs.
//
// Requests can arrive on two transports: the UART, and UDP datagrams through
// the Ethernet block (eth_stack). Each transport has its own COBS decoder and
// frame_rx; a small arbiter hands one checked frame at a time to the single
// cmd_proc and remembers where it came from. The reply goes through the one
// frame_tx and COBS encoder, then back out on the transport its request used.
module accel_core
  import accel_pkg::*;
#(
    parameter int CLKS_PER_BIT = CLK_HZ / BAUD
) (
    input  logic       clk,
    input  logic       rst,
    input  logic       uart_rx_i,
    output logic       uart_tx_o,
    // RMII to the Ethernet PHY. clk_rmii is the 50 MHz reference clock, made
    // by the same MMCM as clk with aligned edges (see nexys_top).
    input  logic       clk_rmii,
    input  logic [1:0] rmii_rxd,
    input  logic       rmii_crs_dv,
    input  logic       rmii_rx_er,
    output logic [1:0] rmii_txd,
    output logic       rmii_tx_en,
    output logic       busy_o,
    output logic       err_o,
    // Ethernet status for LEDs: frame pulses and "has ever happened" flags.
    output logic       eth_rx_o,
    output logic       eth_tx_o,
    output logic       eth_err_o,
    output logic       eth_drop_o,
    // Debug view of the Ethernet path (see nexys_top's LED list).
    output logic [6:0] eth_dbg_o
);
  localparam int RX_AW = 11;
  localparam int TX_AW = 10;

  // One UART byte arrives as a one-clock valid pulse. COBS turns the serial
  // byte stream into packet bytes; frame_rx checks its length and CRC before
  // cmd_proc is allowed to see it.
  logic       rx_valid;
  logic [7:0] rx_byte;
  uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_uart_rx (
      .clk, .rst, .rx(uart_rx_i), .valid(rx_valid), .data(rx_byte));

  logic       dec_valid, dec_end, dec_err;
  logic [7:0] dec_byte;
  cobs_decoder u_dec (
      .clk, .rst, .in_valid(rx_valid), .in_data(rx_byte),
      .out_valid(dec_valid), .out_data(dec_byte), .frame_end(dec_end), .frame_err(dec_err));

  logic             u_valid, cmd_busy;
  logic [7:0]       u_op, u_tag, u_rb_data;
  logic [15:0]      u_len, u_crc_errors, u_frame_errors;
  logic [RX_AW-1:0] rb_addr;
  frame_rx #(.BUF_AW(RX_AW)) u_frx (
      .clk, .rst, .in_valid(dec_valid), .in_data(dec_byte), .in_end(dec_end), .in_err(dec_err),
      .hold(cmd_busy), .frame_valid(u_valid), .frame_op(u_op), .frame_tag(u_tag), .frame_len(u_len),
      .rd_addr(rb_addr), .rd_data(u_rb_data), .crc_errors(u_crc_errors), .frame_errors(u_frame_errors));

  // The Ethernet transport hands over the payload bytes of UDP command
  // datagrams. From here on they take the same path as UART bytes, through
  // their own decoder and frame_rx. core_idle tells eth_stack when it may
  // send the next frame: the command processor is idle and no reply is
  // still on its way out of the encoder.
  logic        e_byte_valid, e_dec_valid, e_dec_end, e_dec_err, core_idle;
  logic [7:0]  e_byte, e_dec_byte, enc_byte;
  logic        eth_in_valid, eth_in_ready;
  logic [15:0] eth_rx_errors, eth_rx_drops;
  logic        eth_rx_frame, eth_tx_frame;
  logic [6:0]  eth_dbg;
  eth_stack u_eth (
      .clk, .clk_rmii, .rst,
      .rmii_rxd, .rmii_crs_dv, .rmii_rx_er, .rmii_txd, .rmii_tx_en,
      .out_valid(e_byte_valid), .out_data(e_byte), .core_idle,
      .in_valid(eth_in_valid), .in_ready(eth_in_ready), .in_data(enc_byte),
      .rx_errors(eth_rx_errors), .rx_drops(eth_rx_drops), .rx_frame(eth_rx_frame), .tx_frame(eth_tx_frame),
      .dbg({eth_dbg[6:4], eth_dbg[2:0]}));

  cobs_decoder u_edec (
      .clk, .rst, .in_valid(e_byte_valid), .in_data(e_byte),
      .out_valid(e_dec_valid), .out_data(e_dec_byte), .frame_end(e_dec_end), .frame_err(e_dec_err));

  logic        e_valid;
  logic [7:0]  e_op, e_tag, e_rb_data;
  logic [15:0] e_len, e_crc_errors, e_frame_errors;
  frame_rx #(.BUF_AW(RX_AW)) u_efrx (
      .clk, .rst, .in_valid(e_dec_valid), .in_data(e_dec_byte), .in_end(e_dec_end), .in_err(e_dec_err),
      .hold(cmd_busy), .frame_valid(e_valid), .frame_op(e_op), .frame_tag(e_tag), .frame_len(e_len),
      .rd_addr(rb_addr), .rd_data(e_rb_data), .crc_errors(e_crc_errors), .frame_errors(e_frame_errors));

  // Arbiter. cmd_proc takes a frame only on a clock where it is idle and
  // frame_valid is high, and copies op/tag/len right then, so the mux for
  // those fields uses the valid signals of that same clock. If both
  // transports finish a frame on the same clock, the UDP one wins and the
  // UART one is lost; the host retries after its timeout, exactly as for a
  // frame that arrives while cmd_proc is busy. src remembers the winner for
  // the payload reads that follow and for routing the reply.
  localparam logic SRC_UART = 1'b0, SRC_ETH = 1'b1;
  logic        src;
  logic        f_valid;
  logic [7:0]  f_op, f_tag, rb_data;
  logic [15:0] f_len, crc_errors, frame_errors;
  assign f_valid = (u_valid || e_valid) && !cmd_busy;
  assign f_op    = e_valid ? e_op  : u_op;
  assign f_tag   = e_valid ? e_tag : u_tag;
  assign f_len   = e_valid ? e_len : u_len;
  assign rb_data = (src == SRC_ETH) ? e_rb_data : u_rb_data;
  always_ff @(posedge clk)
    if (rst)          src <= SRC_UART;
    else if (f_valid) src <= e_valid ? SRC_ETH : SRC_UART;

  // STATUS reports link errors over both transports together (protocol v0.2
  // has one pair of counters). The sums wrap at 16 bits, like each counter.
  assign crc_errors   = u_crc_errors + e_crc_errors;
  assign frame_errors = u_frame_errors + e_frame_errors;

  // cmd_proc writes reply payload bytes here. frame_tx later reads them in
  // order and adds the header and CRC. The buffer is separate from scratchpad.
  logic             tb_we;
  logic [TX_AW-1:0] tb_waddr, tb_raddr;
  logic [7:0]       tb_wdata, tb_rdata;
  logic [7:0]       tx_buf [2**TX_AW];
  always_ff @(posedge clk) if (tb_we) tx_buf[tb_waddr] <= tb_wdata;
  assign tb_rdata = tx_buf[tb_raddr];

  // The command processor and kernel engine share one scratchpad port. A
  // running kernel owns it; command memory operations wait for its completion.
  logic                  c_en, c_we, k_en, k_we;
  logic [SCRATCH_AW-1:0] c_addr, k_addr;
  logic [31:0]           c_wdata, k_wdata, m_rdata;
  logic                  k_start, k_busy, k_done;
  logic [7:0]            k_kernel;
  logic [8*21-1:0]       k_args;
  logic [31:0]           k_cycles, k_aux;
  logic                  kb_we;
  logic [SCRATCH_AW-1:0] kb_addr;
  logic [31:0]           kb_wdata;

  scratch_mem #(.AW(SCRATCH_AW)) u_mem (
      .clk,
      .en   (k_busy ? k_en    : c_en),
      .we   (k_busy ? k_we    : c_we),
      .addr (k_busy ? k_addr  : c_addr),
      .wdata(k_busy ? k_wdata : c_wdata),
      .rdata(m_rdata),
      // Port B belongs to the kernel engine (FPU result write-back).
      .b_we(kb_we), .b_addr(kb_addr), .b_wdata(kb_wdata));

  kernel_engine u_kern (
      .clk, .rst, .start(k_start), .kernel(k_kernel), .args(k_args),
      .busy(k_busy), .done(k_done), .cycles(k_cycles), .aux(k_aux),
      .m_en(k_en), .m_we(k_we), .m_addr(k_addr), .m_wdata(k_wdata), .m_rdata,
      .b_we(kb_we), .b_addr(kb_addr), .b_wdata(kb_wdata));

  // This state machine interprets validated opcodes and prepares replies.
  logic        tx_start, tx_busy;
  logic [7:0]  tx_op, tx_tag;
  logic [15:0] tx_len;
  cmd_proc #(.RX_AW(RX_AW), .TX_AW(TX_AW)) u_cmd (
      .clk, .rst,
      .frame_valid(f_valid), .frame_op(f_op), .frame_tag(f_tag), .frame_len(f_len),
      .rb_addr, .rb_data,
      .tb_we, .tb_addr(tb_waddr), .tb_wdata,
      .tx_start, .tx_op, .tx_tag, .tx_len, .tx_busy,
      .m_en(c_en), .m_we(c_we), .m_addr(c_addr), .m_wdata(c_wdata), .m_rdata,
      .k_start, .k_kernel, .k_args, .k_done, .k_cycles, .k_aux,
      .crc_errors, .frame_errors, .busy(cmd_busy));

  // Reply bytes travel through framing, a one-entry register, COBS encoding,
  // then the UART transmitter or the Ethernet block. ready/valid means a byte
  // moves only when both sides agree on the same rising clock edge.
  logic       raw_valid, raw_ready, raw_last, enc_valid, enc_ready;
  logic [7:0] raw_byte;
  frame_tx #(.BUF_AW(TX_AW)) u_ftx (
      .clk, .rst, .start(tx_start), .op(tx_op), .tag(tx_tag), .len(tx_len), .busy(tx_busy),
      .rd_addr(tb_raddr), .rd_data(tb_rdata),
      .out_valid(raw_valid), .out_ready(raw_ready), .out_data(raw_byte), .out_last(raw_last));

  // Register slice: cuts frame_tx's output mux off the encoder's write path.
  logic       rs_valid, rs_ready, rs_last;
  logic [7:0] rs_byte;
  stream_reg #(.W(9)) u_rs (
      .clk, .rst, .in_valid(raw_valid), .in_ready(raw_ready), .in_data({raw_last, raw_byte}),
      .out_valid(rs_valid), .out_ready(rs_ready), .out_data({rs_last, rs_byte}));

  cobs_encoder u_enc (
      .clk, .rst, .in_valid(rs_valid), .in_ready(rs_ready), .in_data(rs_byte), .in_last(rs_last),
      .out_valid(enc_valid), .out_ready(enc_ready), .out_data(enc_byte));

  // Reply routing. Each reply's source is pushed when frame_tx starts it and
  // popped when the encoder has sent that reply's 0x00 delimiter (COBS output
  // contains no other zero). At most two replies can be in flight, one
  // leaving the encoder and the next waiting in frame_tx, so four entries are
  // more than enough. The FIFO head selects the transport for encoded bytes.
  logic [1:0] q_wr, q_rd;
  logic [2:0] q_cnt;
  logic       q_src [4];
  wire        dst      = q_src[q_rd];
  wire        enc_pop  = enc_valid && enc_ready && (enc_byte == 8'h00);
  always_ff @(posedge clk) begin
    if (rst) begin
      q_wr  <= '0;
      q_rd  <= '0;
      q_cnt <= '0;
    end else begin
      if (tx_start) begin
        q_src[q_wr] <= src;
        q_wr        <= q_wr + 1'b1;
      end
      if (enc_pop) q_rd <= q_rd + 1'b1;
      q_cnt <= q_cnt + (tx_start ? 3'd1 : 3'd0) - (enc_pop ? 3'd1 : 3'd0);
    end
  end
  // tx_start is included because of one clock in the hand-off: cmd_proc is
  // back in IDLE and frame_tx has not raised busy yet, while the reply has not
  // been pushed into this FIFO either. Without it, eth_stack could start the
  // next frame in that clock and find cmd_proc busy a moment later.
  assign core_idle = !cmd_busy && !tx_start && (q_cnt == 0);

  logic uart_ready;
  assign eth_in_valid = enc_valid && (dst == SRC_ETH);
  assign enc_ready    = (dst == SRC_ETH) ? eth_in_ready : uart_ready;

  uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_uart_tx (
      .clk, .rst, .valid(enc_valid && (dst == SRC_UART)), .ready(uart_ready), .data(enc_byte),
      .tx(uart_tx_o));

  // Status. The Ethernet error flags stay set once something went wrong, so
  // a short event is still visible on an LED afterwards.
  assign busy_o     = cmd_busy;
  assign err_o      = (crc_errors != 0) || (frame_errors != 0);
  assign eth_rx_o   = eth_rx_frame;
  assign eth_tx_o   = eth_tx_frame;
  assign eth_err_o  = (eth_rx_errors != 0);
  assign eth_drop_o = (eth_rx_drops != 0);
  // Bit 3: a reply is between frame_tx and the end of the encoder.
  assign eth_dbg[3] = (q_cnt != 0);
  assign eth_dbg_o  = eth_dbg;
endmodule
