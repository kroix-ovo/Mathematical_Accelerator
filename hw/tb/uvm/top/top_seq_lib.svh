// Command sequences use array addresses in words, while packet payloads are
// bytes in little-endian order. The codec performs that conversion before the
// UART driver sees the request. I read results back from the scratchpad so a
// passing RUN reply alone cannot hide a missing or misaddressed result write.
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
    for (int fop = 0; fop < accel_pkg::FPU_OP_COUNT; fop++) begin
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

// A run laid out for the scratchpad, the way memory_image does it in Python:
// descriptor, program, constants, tables, then the outputs.
// I lay out SV-owned programs independently of the Python image generator.
class evu_image;
  bit          reduce;
  int unsigned base, n[3];
  bit [31:0]   code[$], pool[$], values[$];
  int unsigned table_at[$], table_dim[$];         // where each table starts in values
  int unsigned r, f;

  function new(int unsigned base = 0, bit reduce = 1);
    this.base = base;
    this.reduce = reduce;
    n = '{1, 1, 1};
  endfunction

  static function bit [31:0] instr(int op, int dst, int a = 0, int b = 0, bit last = 0);
    return (32'(op) << accel_pkg::EVU_OP_SHIFT) | (32'(last) << accel_pkg::EVU_LAST_SHIFT) |
           (32'(dst) << accel_pkg::EVU_DST_SHIFT) | (32'(a) << accel_pkg::EVU_A_SHIFT) |
           (32'(b) << accel_pkg::EVU_B_SHIFT);
  endfunction

  // A source field naming constant k.
  static function int k(int index); return accel_pkg::EVU_SRC_CONST | index; endfunction

  function void add_table(int dim, bit [31:0] v[$]);
    table_at.push_back(values.size());
    table_dim.push_back(dim);
    foreach (v[i]) values.push_back(v[i]);
  endfunction

  function int unsigned points(); return n[0] * n[1] * n[2]; endfunction

  // The whole image as words, starting at base. This also works out r and f.
  function void words(output bit [31:0] w[$]);
    int unsigned code_at = base + accel_pkg::EVU_DESC_WORDS;
    int unsigned constants = code_at + code.size();
    int unsigned tables = constants + pool.size();
    int unsigned size = reduce ? 1 : points();
    r = tables + values.size();
    f = r + size;
    w.delete();
    repeat (accel_pkg::EVU_DESC_WORDS) w.push_back(0);
    w[accel_pkg::EVU_DESC_MODE]      = reduce;
    w[accel_pkg::EVU_DESC_PROG]      = code_at;
    w[accel_pkg::EVU_DESC_PROG_LEN]  = code.size();
    w[accel_pkg::EVU_DESC_CONST]     = constants;
    w[accel_pkg::EVU_DESC_CONST_LEN] = pool.size();
    for (int i = 0; i < 3; i++) w[accel_pkg::EVU_DESC_GRID + i] = n[i];
    w[accel_pkg::EVU_DESC_R] = r;
    w[accel_pkg::EVU_DESC_F] = f;
    foreach (table_at[i])
      w[accel_pkg::EVU_DESC_TABLE + i] = (tables + table_at[i]) | (table_dim[i] << accel_pkg::EVU_DESC_DIM_SHIFT);
    foreach (code[i])   w.push_back(code[i]);
    foreach (pool[i])   w.push_back(pool[i]);
    foreach (values[i]) w.push_back(values[i]);
  endfunction
endclass

// The expression unit as a host uses it: write an image, RUN, read the results
// back so a wrong or missing write shows up as a reply mismatch. These programs
// are written here and share nothing with the Python generators.
// I drive the same write/RUN/read workflow that the Python device driver uses.
class cmd_evu_seq extends cmd_base_seq;
  `uvm_object_utils(cmd_evu_seq)
  localparam int LOAD = accel_pkg::EVU_OP_LOAD;
  int unsigned count = 8;                         // random programs
  bit [31:0]   lcg_seed = 32'h0E7A;
  function new(string name = "cmd_evu_seq");
    super.new(name);
    timeout_clks = 2_000_000;
  endfunction

  // I split the image into legal packet payloads before starting the kernel.
  task write_many(int unsigned addr, bit [31:0] w[$]);
    while (w.size() != 0) begin
      bit [31:0] part[$];
      while (w.size() != 0 && part.size() < 255) part.push_back(w.pop_front());
      write_words(addr, part);
      addr += part.size();
    end
  endtask

  // I send only the descriptor address; all its referenced words are already loaded.
  task run_evu(int unsigned desc);
    byte_q p, rep;
    p.push_back(accel_pkg::K_EVU);
    put32(p, desc);
    send(accel_pkg::OP_RUN, p, rep);
  endtask

  // I read result and flag regions so missing or misplaced writes reach the scoreboard.
  task read_outputs(evu_image m);
    int unsigned n = m.reduce ? 1 : m.points();
    if (n > 255) n = 255;
    read_words(m.r, n);
    read_words(m.f, n);
  endtask

  // I keep the host sequence visible: build image, write it, RUN, then read its outputs.
  task one_run(evu_image m);
    bit [31:0] w[$];
    m.words(w);
    write_many(m.base, w);
    run_evu(m.base);
    read_outputs(m);
  endtask

  function bit [31:0] fp(shortreal x); return $shortrealtobits(x); endfunction

  // x^2 + y over a 4 by 3 grid, added up.
  // I use a small two-axis reduction to check table indexing and an exact final sum.
  function evu_image sum_2d();
    evu_image m = new(0, 1);
    bit [31:0] xs[$], ys[$];
    m.n = '{4, 3, 1};
    xs = '{fp(0.5), fp(1.0), fp(1.5), fp(2.0)};
    ys = '{fp(-1.0), fp(0.25), fp(3.0)};
    m.add_table(0, xs);
    m.add_table(1, ys);
    m.code = '{evu_image::instr(LOAD, 0, 0), evu_image::instr(LOAD, 1, 1),
               evu_image::instr(2, 2, 0, 0), evu_image::instr(0, 3, 2, 1, 1)};
    return m;
  endfunction

  // Three dimensions, one result per point, with a CORDIC op and the divider, and
  // a zero so a point raises flags that must not leak into its neighbours.
  function evu_image map_3d(int unsigned base = 0);
    evu_image m = new(base, 0);
    bit [31:0] a[$], b[$], c[$];
    m.n = '{2, 3, 2};
    a = '{fp(0.0), fp(1.0)};
    b = '{fp(0.5), fp(-0.25), fp(2.0)};
    c = '{fp(1.0), fp(4.0)};
    m.add_table(0, a);
    m.add_table(1, b);
    m.add_table(2, c);
    m.pool = '{fp(3.0)};
    m.code = '{evu_image::instr(LOAD, 0, 0), evu_image::instr(LOAD, 1, 1), evu_image::instr(LOAD, 2, 2),
               evu_image::instr(12, 3, 1), evu_image::instr(3, 4, 3, 0),
               evu_image::instr(1, 5, 4, evu_image::k(0)), evu_image::instr(0, 6, 5, 2, 1)};
    return m;
  endfunction

  // Each kernel straight after the other, sharing the FPU and its slow units.
  task share_the_fpu();
    localparam int unsigned A = 8000, B = 8100, R = 8200, F = 8300;
    evu_image m = map_3d();
    bit [31:0] av[$], bv[$];
    for (int i = 0; i < 6; i++) begin
      av.push_back(fp(0.25 * (i + 1)));
      bv.push_back(fp(0.5 * (i + 1)));
    end
    one_run(m);
    write_words(A, av);
    write_words(B, bv);
    for (int k = 0; k < 4; k++) begin
      int fop = (k == 0) ? 12 : (k == 1) ? 3 : (k == 2) ? 0 : 17;   // SIN, DIV, ADD, LN
      run_fpu(fop, 6, A, B, R, F);
      read_words(R, 6);
      read_words(F, 6);
      run_evu(m.base);                                              // the same descriptor again
      read_outputs(m);
    end
  endtask

  // Every kind of refusal. Nothing may change, so the good run still works after.
  task refused();
    evu_image m = sum_2d();
    bit [31:0] w[$];
    byte_q p, r;
    int unsigned top = 1 << accel_pkg::SCRATCH_AW;
    int unsigned code_at = m.base + accel_pkg::EVU_DESC_WORDS;
    int unsigned offset[$] = '{accel_pkg::EVU_DESC_MODE, accel_pkg::EVU_DESC_PROG_LEN, accel_pkg::EVU_DESC_PROG_LEN,
                               accel_pkg::EVU_DESC_CONST_LEN, accel_pkg::EVU_DESC_GRID, accel_pkg::EVU_DESC_GRID + 2,
                               accel_pkg::EVU_DESC_TABLE + 3, accel_pkg::EVU_DESC_PROG, accel_pkg::EVU_DESC_R,
                               accel_pkg::EVU_DESC_F, accel_pkg::EVU_DESC_TABLE + 4};
    int unsigned value[$]  = '{2, 0, 257, 65, 0, 65536, 3 << 16, top, top, 32'h8000_0000, top - 3};
    one_run(m);
    m.words(w);
    p = '{accel_pkg::K_EVU};            send(accel_pkg::OP_RUN, p, r);  // BAD_LEN
    p = '{accel_pkg::K_EVU, 0, 0};      send(accel_pkg::OP_RUN, p, r);  // BAD_LEN
    run_evu(top - accel_pkg::EVU_DESC_WORDS + 1);                       // BAD_ADDR
    run_evu(32'hFFFF_FFFF);                                             // BAD_ADDR, must not wrap
    send(accel_pkg::OP_STATUS, '{}, r);
    foreach (offset[i]) begin
      bit [31:0] one[$];
      one = '{value[i]};
      write_words(m.base + offset[i], one);
      run_evu(m.base);
      one = '{w[offset[i]]};
      write_words(m.base + offset[i], one);
    end
    begin
      bit [31:0] bad[$], good[$];
      bad  = '{evu_image::instr(18, 2, 0, 0)};                          // no such op
      good = '{m.code[2]};
      write_words(code_at + 2, bad);
      run_evu(m.base);
      bad  = '{evu_image::instr(LOAD, 2, 6)};                           // no such table
      write_words(code_at + 2, bad);
      run_evu(m.base);
      bad  = '{evu_image::instr(0, 2, evu_image::k(0), 0)};             // the pool is empty
      write_words(code_at + 2, bad);
      run_evu(m.base);
      write_words(code_at + 2, good);
    end
    send(accel_pkg::OP_STATUS, '{}, r);
    read_outputs(m);
    run_evu(m.base);
    read_outputs(m);
  endtask

  // A random program: every source is a register already written, or a constant.
  function evu_image random_image(fp_lcg g);
    evu_image m = new($urandom_range(0, 1) ? 0 : 32768, $urandom_range(0, 1));
    fpu_op_e   op;
    bit [31:0] x, y, v[$];
    int        written[$];
    int        length = $urandom_range(2, 30);
    m.n = '{$urandom_range(1, 4), $urandom_range(1, 3), $urandom_range(1, 2)};
    for (int t = 0; t < $urandom_range(1, accel_pkg::EVU_TABLES); t++) begin
      int dim = $urandom_range(0, 2);
      v.delete();
      for (int i = 0; i < m.n[dim]; i++) begin
        g.stimulus(0, op, x, y);
        v.push_back(x);
      end
      m.add_table(dim, v);
    end
    repeat ($urandom_range(0, 8)) begin
      g.stimulus(2, op, x, y);
      m.pool.push_back(y);
    end
    for (int i = 0; i < length; i++) begin
      int dst = $urandom_range(0, accel_pkg::EVU_REGISTERS - 1);
      bit last = (i == length - 1);
      if (i == 0 || $urandom_range(0, 5) == 0) begin
        m.code.push_back(evu_image::instr(LOAD, dst, $urandom_range(0, m.table_at.size() - 1), 0, last));
      end else begin
        int code = $urandom_range(0, accel_pkg::FPU_OP_COUNT - 1);
        int a = written[$urandom_range(0, written.size() - 1)];
        int b = written[$urandom_range(0, written.size() - 1)];
        if (m.pool.size() != 0 && $urandom_range(0, 3) == 0) b = evu_image::k($urandom_range(0, m.pool.size() - 1));
        if (code inside {4, 5, 6, 12, 13, 16, 17}) b = 0;           // unary: b must not name a missing constant
        m.code.push_back(evu_image::instr(code, dst, a, b, last));
      end
      written.push_back(dst);
    end
    return m;
  endfunction

  task body();
    fp_lcg g = new(lcg_seed);
    one_run(sum_2d());
    one_run(map_3d());
    one_run(map_3d(40000));
    share_the_fpu();
    refused();
    repeat (count) one_run(random_image(g));
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
    p.delete(); p.push_back(accel_pkg::K_FPU); p.push_back(accel_pkg::FPU_OP_COUNT);
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

class top_evu_vseq extends top_base_vseq;
  `uvm_object_utils(top_evu_vseq)
  function new(string name = "top_evu_vseq"); super.new(name); endfunction
  task body();
    cmd_evu_seq s = cmd_evu_seq::type_id::create("s");
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
    cmd_evu_seq   evu   = cmd_evu_seq::type_id::create("evu");
    cmd_error_seq err   = cmd_error_seq::type_id::create("err");
    smoke.start(p_sequencer.cmd_sqr);
    mem.start(p_sequencer.cmd_sqr);
    fpu.start(p_sequencer.cmd_sqr);
    evu.start(p_sequencer.cmd_sqr);
    err.start(p_sequencer.cmd_sqr);
    // A COBS block that runs past the end, then an empty frame (ignored).
    inject_garbage('{8'h05, 8'h01, 8'h02, 8'h00});
    inject_garbage('{8'h00});
    smoke.start(p_sequencer.cmd_sqr);                // link still works afterwards
  endtask
endclass
