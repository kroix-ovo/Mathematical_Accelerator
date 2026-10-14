// Reference model and scoreboard for the whole accelerator.
//
// top_ref_model is an SV version of the device behaviour in docs/protocol.md
// (the Python DeviceModel implements the same document). Given the raw bytes
// of a frame the DUT received, it decides whether the DUT must stay silent
// (link error) or reply, and if so it produces the exact reply wire bytes.
// FPU results come from the DPI-C reference, the same one the block-level
// scoreboard uses.

class top_ref_model extends uvm_object;
  `uvm_object_utils(top_ref_model)

  localparam int unsigned WORDS = 1 << accel_pkg::SCRATCH_AW;
  // Cycle counts of the FPU kernel (same numbers as sw/mathaccel/driver/model.py).
  localparam int unsigned FPU_FETCH = 4, FPU_PIPE_TAIL = 9, DS_PERIOD = 33, DS_TAIL = 6;

  // Scratchpad words. An associative array only stores words that were
  // written; reading a missing key returns the default value 0, like the
  // zero-initialized block RAM.
  bit [31:0]   mem[int unsigned];
  bit [15:0]   crc_errors, frame_errors;
  bit [31:0]   last_cycles;

  function new(string name = "top_ref_model"); super.new(name); endfunction

  function bit [31:0] rd(int unsigned a);
    return mem.exists(a) ? mem[a] : 0;
  endfunction

  static function bit [31:0] le32(byte_q p, int off);
    return {p[off + 3], p[off + 2], p[off + 1], p[off]};
  endfunction
  static function void put32(inout byte_q q, input bit [31:0] v);
    for (int i = 0; i < 4; i++) q.push_back(v[8*i +: 8]);
  endfunction

  function bit in_range(longint unsigned base, longint unsigned count);
    return base + count <= WORDS;
  endfunction

  // Returns 1 and fills `reply` with wire bytes (incl. delimiter) when the DUT
  // must answer, 0 when it must stay silent.
  function bit predict(byte_q wire_frame, output byte_q reply);
    byte_q raw, p, data;
    byte unsigned op, tag, st;
    int unsigned len;
    reply.delete();
    if (wire_frame.size() == 0) return 0;                  // empty frame: ignored
    if (!top_codec::cobs_decode(wire_frame, raw) || raw.size() < 6) begin
      frame_errors++;
      return 0;
    end
    op  = raw[0];
    tag = raw[1];
    len = {raw[3], raw[2]};
    if (len > accel_pkg::MAX_PAYLOAD || raw.size() != len + 6) begin
      frame_errors++;
      return 0;
    end
    begin
      byte_q body;
      for (int i = 0; i < raw.size() - 2; i++) body.push_back(raw[i]);
      if (top_codec::crc16(body) != {raw[raw.size() - 1], raw[raw.size() - 2]}) begin
        crc_errors++;
        return 0;
      end
    end
    for (int i = 0; i < len; i++) p.push_back(raw[4 + i]);
    st = execute(op, p, data);
    data.push_front(st);
    reply = top_codec::cobs_encode(top_codec::frame(op | 8'h80, tag, data));
    reply.push_back(0);
    return 1;
  endfunction

  // Command semantics. Returns the status byte; `data` gets the rest.
  function byte unsigned execute(byte unsigned op, byte_q p, output byte_q data);
    data.delete();
    case (op)
      accel_pkg::OP_PING: begin
        if (p.size() != 0) return accel_pkg::ST_BAD_LEN;
        put32(data, accel_pkg::MAGIC);
        data.push_back(accel_pkg::VERSION_MAJOR);
        data.push_back(accel_pkg::VERSION_MINOR);
        data.push_back(accel_pkg::SCRATCH_AW);
        data.push_back(0);
        put32(data, accel_pkg::CLK_HZ);
        return accel_pkg::ST_OK;
      end
      accel_pkg::OP_STATUS: begin
        if (p.size() != 0) return accel_pkg::ST_BAD_LEN;
        data.push_back(crc_errors[7:0]);  data.push_back(crc_errors[15:8]);
        data.push_back(frame_errors[7:0]); data.push_back(frame_errors[15:8]);
        put32(data, last_cycles);
        return accel_pkg::ST_OK;
      end
      accel_pkg::OP_WRITE_MEM: begin
        int unsigned a, n;
        if (p.size() < 8 || p.size() % 4 != 0) return accel_pkg::ST_BAD_LEN;
        a = le32(p, 0);
        n = (p.size() - 4) / 4;
        if (!in_range(a, n)) return accel_pkg::ST_BAD_ADDR;
        for (int i = 0; i < n; i++) mem[a + i] = le32(p, 4 + 4 * i);
        return accel_pkg::ST_OK;
      end
      accel_pkg::OP_READ_MEM: begin
        int unsigned a, n;
        if (p.size() != 6) return accel_pkg::ST_BAD_LEN;
        a = le32(p, 0);
        n = {p[5], p[4]};
        if (n == 0 || n > 255) return accel_pkg::ST_BAD_LEN;
        if (!in_range(a, n)) return accel_pkg::ST_BAD_ADDR;
        for (int i = 0; i < n; i++) put32(data, rd(a + i));
        return accel_pkg::ST_OK;
      end
      accel_pkg::OP_RUN: return run_kernel(p, data);
      default: return accel_pkg::ST_BAD_OP;
    endcase
  endfunction

  function byte unsigned run_kernel(byte_q p, output byte_q data);
    bit [31:0] cycles, aux;
    data.delete();
    if (p.size() == 0) return accel_pkg::ST_BAD_LEN;
    aux = 0;
    case (p[0])
      accel_pkg::K_NOP: begin
        if (p.size() != 1) return accel_pkg::ST_BAD_LEN;
        cycles = 1;
      end
      accel_pkg::K_FILL: begin
        bit [31:0] base, count, value;
        if (p.size() != 13) return accel_pkg::ST_BAD_LEN;
        base = le32(p, 1); count = le32(p, 5); value = le32(p, 9);
        if (!in_range(base, count)) return accel_pkg::ST_BAD_ADDR;
        for (int i = 0; i < count; i++) mem[base + i] = value;
        cycles = count + 1;
      end
      accel_pkg::K_FPU: begin
        bit [31:0] count, a, b, r, f;
        bit [31:0] av[$], bv[$];
        byte unsigned fop;
        if (p.size() != 22) return accel_pkg::ST_BAD_LEN;
        fop = p[1];
        count = le32(p, 2); a = le32(p, 6); b = le32(p, 10); r = le32(p, 14); f = le32(p, 18);
        if (fop > 11) return accel_pkg::ST_BAD_ARG;
        if (!in_range(a, count) || !in_range(b, count) || !in_range(r, count) || !in_range(f, count))
          return accel_pkg::ST_BAD_ADDR;
        // Read all operands first, then write: matches the hardware for
        // identical or disjoint arrays (see the Python model).
        for (int i = 0; i < count; i++) begin av.push_back(rd(a + i)); bv.push_back(rd(b + i)); end
        for (int i = 0; i < count; i++) begin
          int fl;
          mem[r + i] = fp_ref_eval(fop, av[i], bv[i], fl);
          mem[f + i] = fl;
          aux |= fl;
        end
        if (count == 0)             cycles = 1;
        else if (fop inside {3, 4}) cycles = DS_PERIOD * count + DS_TAIL;
        else                        cycles = FPU_FETCH * count + FPU_PIPE_TAIL;
      end
      default: return accel_pkg::ST_BAD_KERNEL;
    endcase
    last_cycles = cycles;
    put32(data, cycles);
    put32(data, aux);
    return accel_pkg::ST_OK;
  endfunction
endclass

// ---------------------------------------------------------------------------
class top_scoreboard extends uvm_scoreboard;
  `uvm_component_utils(top_scoreboard)

  uvm_analysis_imp_req #(wire_frame_item, top_scoreboard) req_imp;
  uvm_analysis_imp_rsp #(wire_frame_item, top_scoreboard) rsp_imp;

  top_ref_model model;
  byte_q        expected[$];       // queue of predicted replies (one per request)
  int unsigned  n_req, n_rsp, n_silent, n_errors;

  // Which commands and statuses were exercised.
  byte unsigned cov_op, cov_status, cov_kernel;
  covergroup cg_cmd;
    cp_op:     coverpoint cov_op { bins ping = {1}; bins wr = {2}; bins rd = {3}; bins run = {4};
                                   bins status = {5}; bins other = default; }
    cp_status: coverpoint cov_status { bins st[] = {[0:6]}; ignore_bins busy = {4}; }
    x_op_status: cross cp_op, cp_status;
  endgroup

  function new(string name, uvm_component parent);
    super.new(name, parent);
    req_imp = new("req_imp", this);
    rsp_imp = new("rsp_imp", this);
    cg_cmd  = new();
  endfunction

  function void build_phase(uvm_phase phase);
    model = top_ref_model::type_id::create("model");
  endfunction

  function void write_req(wire_frame_item f);
    byte_q reply, raw;
    n_req++;
    if (model.predict(f.bytes, reply)) begin
      expected.push_back(reply);
      void'(top_codec::cobs_decode(reply, raw));
      cov_op     = raw[0] & 8'h7F;
      cov_status = raw[4];
      cg_cmd.sample();
    end else begin
      n_silent++;
    end
  endfunction

  function void write_rsp(wire_frame_item f);
    byte_q exp, got;
    n_rsp++;
    got = f.bytes;
    got.push_back(0);
    if (expected.size() == 0) begin
      n_errors++;
      `uvm_error("TOP_SB", $sformatf("unexpected reply %s", top_codec::hex(got)))
      return;
    end
    exp = expected.pop_front();
    if (got != exp) begin
      n_errors++;
      `uvm_error("TOP_SB", $sformatf("reply mismatch\n  expected %s\n  got      %s",
                                     top_codec::hex(exp, 64), top_codec::hex(got, 64)))
    end
  endfunction

  function void check_phase(uvm_phase phase);
    if (expected.size() != 0)
      `uvm_error("TOP_SB", $sformatf("%0d predicted replies never arrived", expected.size()))
  endfunction

  function void report_phase(uvm_phase phase);
    `uvm_info("SB", $sformatf("%0d requests (%0d silent by design), %0d replies, %0d mismatches, command coverage %0.1f%%",
                              n_req, n_silent, n_rsp, n_errors, cg_cmd.get_coverage()), UVM_NONE)
  endfunction
endclass

// ---------------------------------------------------------------------------
class top_env extends uvm_env;
  `uvm_component_utils(top_env)
  uart_agent     uart;
  cmd_sequencer  cmd_sqr;
  top_vsequencer vsqr;
  top_scoreboard sb;

  function new(string name, uvm_component parent); super.new(name, parent); endfunction

  function void build_phase(uvm_phase phase);
    uart    = uart_agent::type_id::create("uart", this);
    cmd_sqr = cmd_sequencer::type_id::create("cmd_sqr", this);
    vsqr    = top_vsequencer::type_id::create("vsqr", this);
    sb      = top_scoreboard::type_id::create("sb", this);
  endfunction

  function void connect_phase(uvm_phase phase);
    uart.rx_mon.ap.connect(sb.req_imp);
    uart.tx_mon.ap.connect(sb.rsp_imp);
    uart.tx_mon.ap.connect(cmd_sqr.rsp_fifo.analysis_export);
    vsqr.cmd_sqr  = cmd_sqr;
    vsqr.uart_sqr = uart.sqr;
  endfunction

  // The layering sequence runs for the whole test, forwarding commands.
  task run_phase(uvm_phase phase);
    cmd_to_uart_seq layer = cmd_to_uart_seq::type_id::create("layer");
    layer.up = cmd_sqr;
    layer.start(uart.sqr);
  endtask
endclass
