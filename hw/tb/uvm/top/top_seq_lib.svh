// Sequences for the top-level environment.
//
// Three kinds:
//   command sequences  run on cmd_sequencer; send protocol commands and wait
//                      for each reply like a real host (one command in flight)
//   raw sequences      run on the UART sequencer; send arbitrary bytes, for
//                      link-error cases the command layer can't express
//   virtual sequences  run on top_vsequencer; start the other two kinds on
//                      their sequencers to build complete scenarios

// ---------------------------------------------------------------------------
class cmd_base_seq extends uvm_sequence #(accel_cmd_item);
  `uvm_object_utils(cmd_base_seq)
  // p_sequencer is a typed handle to the sequencer this sequence runs on, so
  // the sequence can reach its reply FIFO and interface.
  `uvm_declare_p_sequencer(cmd_sequencer)

  static byte unsigned next_tag;
  int unsigned         timeout_clks = 400_000;

  function new(string name = "cmd_base_seq"); super.new(name); endfunction

  // Send one command; if a reply is expected, wait for it and return it decoded
  // (raw frame bytes, without COBS). The scoreboard does the checking; the
  // sequence only needs replies for flow control and to read data back.
  task send(byte unsigned op, byte_q payload, output byte_q reply,
            input corrupt_e corrupt = CORRUPT_NONE);
    accel_cmd_item  c = accel_cmd_item::type_id::create("c");
    wire_frame_item r;
    bit got = 0;
    start_item(c);
    c.op      = op;
    c.tag     = next_tag++;
    c.payload = payload;
    c.corrupt = corrupt;
    finish_item(c);                        // returns when the bytes are on the wire
    reply.delete();
    if (corrupt != CORRUPT_NONE) begin
      // The DUT must stay silent; give it time to (wrongly) answer.
      repeat (60 * 10 * CPB) @(posedge p_sequencer.vif.clk);
      if (p_sequencer.rsp_fifo.try_get(r))
        `uvm_error("SEQ", "reply to a corrupted frame")
      return;
    end
    // fork/join_any races the reply against a timeout; disable fork stops
    // whichever branch is still waiting.
    fork
      begin p_sequencer.rsp_fifo.get(r); got = 1; end
      repeat (timeout_clks) @(posedge p_sequencer.vif.clk);
    join_any
    disable fork;
    if (!got) `uvm_fatal("SEQ", $sformatf("no reply to op %02h", op))
    void'(top_codec::cobs_decode(r.bytes, reply));
  endtask

  // Little helpers for building payloads.
  static function void put32(inout byte_q q, input bit [31:0] v);
    for (int i = 0; i < 4; i++) q.push_back(v[8*i +: 8]);
  endfunction
  static function void put16(inout byte_q q, input bit [15:0] v);
    q.push_back(v[7:0]); q.push_back(v[15:8]);
  endfunction

  task write_words(int unsigned addr, bit [31:0] words[$]);
    byte_q p, r;
    put32(p, addr);
    foreach (words[i]) put32(p, words[i]);
    send(accel_pkg::OP_WRITE_MEM, p, r);
  endtask

  task read_words(int unsigned addr, int unsigned n);
    byte_q p, r;
    put32(p, addr);
    put16(p, n);
    send(accel_pkg::OP_READ_MEM, p, r);
  endtask

  task run_fpu(int fop, int unsigned n, int unsigned a, int unsigned b, int unsigned r, int unsigned f);
    byte_q p, rep;
    p.push_back(accel_pkg::K_FPU);
    p.push_back(fop);
    put32(p, n); put32(p, a); put32(p, b); put32(p, r); put32(p, f);
    send(accel_pkg::OP_RUN, p, rep);
  endtask
endclass

class cmd_smoke_seq extends cmd_base_seq;
  `uvm_object_utils(cmd_smoke_seq)
  function new(string name = "cmd_smoke_seq"); super.new(name); endfunction
  task body();
    byte_q none, r, p;
    send(accel_pkg::OP_PING, none, r);
    send(accel_pkg::OP_STATUS, none, r);
    p.push_back(accel_pkg::K_NOP);
    send(accel_pkg::OP_RUN, p, r);
  endtask
endclass

// Random writes, then reads of the same and neighbouring regions.
class cmd_mem_seq extends cmd_base_seq;
  `uvm_object_utils(cmd_mem_seq)
  int unsigned count = 6;
  function new(string name = "cmd_mem_seq"); super.new(name); endfunction
  task body();
    repeat (count) begin
      int unsigned addr = $urandom_range(0, (1 << accel_pkg::SCRATCH_AW) - 256);
      int unsigned n    = $urandom_range(1, 255);
      bit [31:0]   w[$];
      repeat (n) w.push_back($urandom);
      write_words(addr, w);
      read_words(addr, n);
      read_words(addr > 0 ? addr - 1 : 0, 2);
    end
  endtask
endclass

// FPU kernel for every op, operands from the shared LCG.
class cmd_fpu_seq extends cmd_base_seq;
  `uvm_object_utils(cmd_fpu_seq)
  int unsigned n = 12;
  bit [31:0]   seed = 32'h70F0;
  function new(string name = "cmd_fpu_seq"); super.new(name); endfunction
  task body();
    localparam int unsigned A = 8000, B = 8300, R = 8600, F = 8900;
    fp_lcg     g = new(seed);
    fpu_op_e   op;
    bit [31:0] a, b, av[$], bv[$];
    for (int fop = 0; fop < 12; fop++) begin
      av.delete(); bv.delete();
      repeat (n) begin
        g.stimulus(fop, op, a, b);
        av.push_back(a);
        bv.push_back(b);
      end
      write_words(A, av);
      write_words(B, bv);
      run_fpu(fop, n, A, B, R, F);
      read_words(R, n);
      read_words(F, n);
    end
  endtask
endclass

// Commands with valid framing but bad contents: every error status.
class cmd_error_seq extends cmd_base_seq;
  `uvm_object_utils(cmd_error_seq)
  function new(string name = "cmd_error_seq"); super.new(name); endfunction
  task body();
    byte_q p, r;
    send(8'h33, p, r);                                          // BAD_OP
    p = '{0};                send(accel_pkg::OP_PING, p, r);       // BAD_LEN
    p.delete(); put32(p, 0); put16(p, 0);   send(accel_pkg::OP_READ_MEM, p, r);  // BAD_LEN
    p.delete(); put32(p, (1 << accel_pkg::SCRATCH_AW) - 1); put16(p, 2);
    send(accel_pkg::OP_READ_MEM, p, r);                         // BAD_ADDR
    p = '{8'h7E};            send(accel_pkg::OP_RUN, p, r);       // BAD_KERNEL
    p.delete(); p.push_back(accel_pkg::K_FPU); p.push_back(12);
    repeat (20) p.push_back(0);
    send(accel_pkg::OP_RUN, p, r);                              // BAD_ARG
    p.delete(); p.push_back(accel_pkg::K_FPU); p.push_back(0);
    put32(p, 2); put32(p, 0); put32(p, 0); put32(p, 0); put32(p, (1 << accel_pkg::SCRATCH_AW) - 1);
    send(accel_pkg::OP_RUN, p, r);                              // BAD_ADDR
    // Link errors through the command layer: corrupted CRC, truncated frame.
    p.delete();
    send(accel_pkg::OP_PING, p, r, CORRUPT_CRC);
    send(accel_pkg::OP_PING, p, r, CORRUPT_SHORT);
    send(accel_pkg::OP_STATUS, p, r);                           // counters moved
  endtask
endclass

// ---------------------------------------------------------------------------
// Raw bytes straight onto the UART sequencer. lock() keeps the layering
// sequence from interleaving a command's bytes with these.
class raw_bytes_seq extends uvm_sequence #(uart_byte_item);
  `uvm_object_utils(raw_bytes_seq)
  byte_q bytes;
  function new(string name = "raw_bytes_seq"); super.new(name); endfunction
  task body();
    lock();
    foreach (bytes[i]) begin
      uart_byte_item b = uart_byte_item::type_id::create("b");
      start_item(b);
      b.data = bytes[i];
      finish_item(b);
    end
    unlock();
  endtask
endclass

// ---------------------------------------------------------------------------
class top_base_vseq extends uvm_sequence;
  `uvm_object_utils(top_base_vseq)
  `uvm_declare_p_sequencer(top_vsequencer)
  function new(string name = "top_base_vseq"); super.new(name); endfunction

  // Send garbage that is not a valid COBS frame, then wait for silence.
  task inject_garbage(byte_q bytes);
    raw_bytes_seq s = raw_bytes_seq::type_id::create("garbage");
    s.bytes = bytes;
    s.start(p_sequencer.uart_sqr);
    repeat (60 * 10 * CPB) @(posedge p_sequencer.cmd_sqr.vif.clk);
  endtask
endclass

class top_smoke_vseq extends top_base_vseq;
  `uvm_object_utils(top_smoke_vseq)
  function new(string name = "top_smoke_vseq"); super.new(name); endfunction
  task body();
    cmd_smoke_seq s = cmd_smoke_seq::type_id::create("s");
    s.start(p_sequencer.cmd_sqr);
  endtask
endclass

class top_regress_vseq extends top_base_vseq;
  `uvm_object_utils(top_regress_vseq)
  function new(string name = "top_regress_vseq"); super.new(name); endfunction
  task body();
    cmd_smoke_seq smoke = cmd_smoke_seq::type_id::create("smoke");
    cmd_mem_seq   mem   = cmd_mem_seq::type_id::create("mem");
    cmd_fpu_seq   fpu   = cmd_fpu_seq::type_id::create("fpu");
    cmd_error_seq err   = cmd_error_seq::type_id::create("err");
    smoke.start(p_sequencer.cmd_sqr);
    mem.start(p_sequencer.cmd_sqr);
    fpu.start(p_sequencer.cmd_sqr);
    err.start(p_sequencer.cmd_sqr);
    // A COBS block that runs past the end, then an empty frame (ignored).
    inject_garbage('{8'h05, 8'h01, 8'h02, 8'h00});
    inject_garbage('{8'h00});
    smoke.start(p_sequencer.cmd_sqr);                // link still works afterwards
  endtask
endclass
