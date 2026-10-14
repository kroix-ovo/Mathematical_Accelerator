// Items, the UART agent, and the command layer above it.
//
// Two levels of transaction:
//   accel_cmd_item  one protocol command (op, tag, payload), what a test
//                   author thinks in
//   uart_byte_item  one byte on the serial wire, what the driver sends
// A layering sequence converts the first into the second, so command-level
// sequences never deal with COBS, CRC or bit timing.

localparam int CPB = 8;    // clocks per UART bit; must match tb_top_uvm's DUT parameter

// ---------------------------------------------------------------------------
class uart_byte_item extends uvm_sequence_item;
  rand byte unsigned data;
  `uvm_object_utils_begin(uart_byte_item)
    `uvm_field_int(data, UVM_DEFAULT | UVM_HEX)
  `uvm_object_utils_end
  function new(string name = "uart_byte_item"); super.new(name); endfunction
endclass

// Ways a command can be deliberately broken on its way to the wire.
typedef enum {CORRUPT_NONE, CORRUPT_CRC, CORRUPT_SHORT} corrupt_e;

class accel_cmd_item extends uvm_sequence_item;
  byte unsigned op, tag;
  byte_q        payload;
  corrupt_e     corrupt = CORRUPT_NONE;
  `uvm_object_utils(accel_cmd_item)
  function new(string name = "accel_cmd_item"); super.new(name); endfunction
  function string convert2string();
    return $sformatf("op=%02h tag=%02h len=%0d %s %s", op, tag, payload.size(),
                     top_codec::hex(payload), corrupt.name());
  endfunction
endclass

// A complete frame as seen on one wire: the bytes between two 0x00
// delimiters, still COBS encoded. Monitors publish these.
class wire_frame_item extends uvm_sequence_item;
  byte_q           bytes;
  longint unsigned cycle;
  `uvm_object_utils(wire_frame_item)
  function new(string name = "wire_frame_item"); super.new(name); endfunction
endclass

// ---------------------------------------------------------------------------
// UART agent
typedef uvm_sequencer #(uart_byte_item) uart_sequencer;

class uart_driver extends uvm_driver #(uart_byte_item);
  `uvm_component_utils(uart_driver)
  virtual top_if vif;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction

  function void build_phase(uvm_phase phase);
    if (!uvm_config_db #(virtual top_if)::get(this, "", "vif", vif))
      `uvm_fatal("NO_VIF", "uart_driver has no virtual interface")
  endfunction

  // 8N1 framing: start bit (0), eight data bits LSB first, stop bit (1).
  // Each bit is held for CPB clocks.
  task run_phase(uvm_phase phase);
    uart_byte_item it;
    vif.uart_rx <= 1'b1;
    @(negedge vif.rst);                 // the DUT ignores the line during reset
    repeat (2 * CPB) @(posedge vif.clk);
    forever begin
      seq_item_port.get_next_item(it);
      vif.uart_rx <= 1'b0;
      repeat (CPB) @(posedge vif.clk);
      for (int i = 0; i < 8; i++) begin
        vif.uart_rx <= it.data[i];
        repeat (CPB) @(posedge vif.clk);
      end
      vif.uart_rx <= 1'b1;
      repeat (CPB) @(posedge vif.clk);
      seq_item_port.item_done();
    end
  endtask
endclass

// Decodes bytes from one UART wire and groups them into frames at each 0x00.
// `watch_tx` picks the wire: 0 = host->DUT (uart_rx), 1 = DUT->host (uart_tx).
class uart_frame_monitor extends uvm_monitor;
  `uvm_component_utils(uart_frame_monitor)
  virtual top_if                       vif;
  bit                                  watch_tx;
  uvm_analysis_port #(wire_frame_item) ap;
  int unsigned                         n_bytes;

  function new(string name, uvm_component parent);
    super.new(name, parent);
    ap = new("ap", this);
  endfunction

  function void build_phase(uvm_phase phase);
    if (!uvm_config_db #(virtual top_if)::get(this, "", "vif", vif))
      `uvm_fatal("NO_VIF", "uart_frame_monitor has no virtual interface")
    void'(uvm_config_db #(bit)::get(this, "", "watch_tx", watch_tx));
  endfunction

  function logic line();
    return watch_tx ? vif.uart_tx : vif.uart_rx;
  endfunction

  task run_phase(uvm_phase phase);
    byte_q cur;
    byte unsigned b;
    @(negedge vif.rst);
    forever begin
      // Wait for the falling edge of a start bit, then sample each bit in
      // its middle: half a bit to the middle of the start bit, then one full
      // bit period per data bit.
      do @(posedge vif.clk); while (line() !== 1'b0);
      repeat (CPB / 2) @(posedge vif.clk);
      for (int i = 0; i < 8; i++) begin
        repeat (CPB) @(posedge vif.clk);
        b[i] = line();
      end
      repeat (CPB) @(posedge vif.clk);
      if (line() !== 1'b1) `uvm_error("UART", $sformatf("bad stop bit on %s", watch_tx ? "tx" : "rx"))
      n_bytes++;
      if (b == 0) begin
        wire_frame_item f = wire_frame_item::type_id::create("frame");
        f.bytes = cur;
        f.cycle = vif.cycle;
        ap.write(f);
        cur.delete();
      end else begin
        cur.push_back(b);
      end
    end
  endtask
endclass

class uart_agent extends uvm_agent;
  `uvm_component_utils(uart_agent)
  uart_sequencer     sqr;
  uart_driver        drv;
  uart_frame_monitor rx_mon, tx_mon;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction

  function void build_phase(uvm_phase phase);
    sqr = uart_sequencer::type_id::create("sqr", this);
    drv = uart_driver::type_id::create("drv", this);
    uvm_config_db #(bit)::set(this, "rx_mon", "watch_tx", 0);
    uvm_config_db #(bit)::set(this, "tx_mon", "watch_tx", 1);
    rx_mon = uart_frame_monitor::type_id::create("rx_mon", this);
    tx_mon = uart_frame_monitor::type_id::create("tx_mon", this);
  endfunction

  function void connect_phase(uvm_phase phase);
    drv.seq_item_port.connect(sqr.seq_item_export);
  endfunction
endclass

// ---------------------------------------------------------------------------
// Command layer.
//
// The command sequencer has no driver of its own. Instead, a layering
// sequence running on the UART sequencer pulls command items from it (the
// same get_next_item / item_done calls a driver would make) and turns each
// into bytes. The sequencer also holds what command sequences need to act
// like a real host: a FIFO of decoded replies and the interface handle (to
// wait a timeout when no reply is expected).
class cmd_sequencer extends uvm_sequencer #(accel_cmd_item);
  `uvm_component_utils(cmd_sequencer)
  uvm_tlm_analysis_fifo #(wire_frame_item) rsp_fifo;
  virtual top_if                           vif;

  function new(string name, uvm_component parent);
    super.new(name, parent);
    rsp_fifo = new("rsp_fifo", this);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db #(virtual top_if)::get(this, "", "vif", vif))
      `uvm_fatal("NO_VIF", "cmd_sequencer has no virtual interface")
  endfunction
endclass

class cmd_to_uart_seq extends uvm_sequence #(uart_byte_item);
  `uvm_object_utils(cmd_to_uart_seq)
  cmd_sequencer up;          // upper-layer sequencer to pull commands from

  function new(string name = "cmd_to_uart_seq"); super.new(name); endfunction

  task body();
    accel_cmd_item cmd;
    byte_q raw, wire_bytes;
    forever begin
      up.get_next_item(cmd);
      raw = top_codec::frame(cmd.op, cmd.tag, cmd.payload);
      case (cmd.corrupt)
        CORRUPT_CRC:   raw[raw.size() - 1] ^= 8'h5A;     // flip bits in the CRC
        CORRUPT_SHORT: while (raw.size() > 3) void'(raw.pop_back());  // too short to be a frame
        default: ;
      endcase
      wire_bytes = top_codec::cobs_encode(raw);
      wire_bytes.push_back(0);
      // lock() gives this sequence the byte sequencer exclusively until
      // unlock(), so a raw-byte sequence started in parallel can't insert
      // bytes into the middle of this frame.
      lock();
      foreach (wire_bytes[i]) begin
        uart_byte_item b = uart_byte_item::type_id::create("b");
        start_item(b);
        b.data = wire_bytes[i];
        finish_item(b);
      end
      unlock();
      up.item_done();
    end
  endtask
endclass

// The virtual sequencer holds handles to the real sequencers so virtual
// sequences can start command sequences and raw byte sequences together.
class top_vsequencer extends uvm_sequencer;
  `uvm_component_utils(top_vsequencer)
  cmd_sequencer  cmd_sqr;
  uart_sequencer uart_sqr;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
