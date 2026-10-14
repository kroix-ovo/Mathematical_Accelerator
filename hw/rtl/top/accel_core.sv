// This is the board-independent top level used by the simulators. I treat a
// command as a trip through four stages: receive UART bits, decode and check
// a packet, perform a command, then build and transmit a reply. Each stage
// passes bytes or control pulses to the next; the clock moves all registers.
// nexys_top adds physical pins, reset handling, and LEDs around this module.
module accel_core
  import accel_pkg::*;
#(
    parameter int CLKS_PER_BIT = CLK_HZ / BAUD
) (
    input  logic clk,
    input  logic rst,
    input  logic uart_rx_i,
    output logic uart_tx_o,
    output logic busy_o,
    output logic err_o
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

  logic             f_valid, cmd_busy;
  logic [7:0]       f_op, f_tag, rb_data;
  logic [15:0]      f_len, crc_errors, frame_errors;
  logic [RX_AW-1:0] rb_addr;
  frame_rx #(.BUF_AW(RX_AW)) u_frx (
      .clk, .rst, .in_valid(dec_valid), .in_data(dec_byte), .in_end(dec_end), .in_err(dec_err),
      .hold(cmd_busy), .frame_valid(f_valid), .frame_op(f_op), .frame_tag(f_tag), .frame_len(f_len),
      .rd_addr(rb_addr), .rd_data(rb_data), .crc_errors, .frame_errors);

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
  // then the UART transmitter. ready/valid means a byte moves only when both
  // sides agree on the same rising clock edge.
  logic       raw_valid, raw_ready, raw_last, enc_valid, enc_ready;
  logic [7:0] raw_byte, enc_byte;
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

  uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_uart_tx (
      .clk, .rst, .valid(enc_valid), .ready(enc_ready), .data(enc_byte), .tx(uart_tx_o));

  assign busy_o = cmd_busy;
  assign err_o  = (crc_errors != 0) || (frame_errors != 0);
endmodule
