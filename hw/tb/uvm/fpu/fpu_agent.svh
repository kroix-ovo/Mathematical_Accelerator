// FPU agent: sequencer, driver and the two monitors.
//
// Data flow:
//   sequence --(items)--> sequencer --(items)--> driver --(pins)--> DUT
//   DUT --(pins)--> req monitor --(analysis port)--> scoreboard, coverage
//   DUT --(pins)--> rsp monitor --(analysis port)--> scoreboard, coverage
// The monitors only watch the pins. They report what really happened, so
// the scoreboard never trusts the driver's own idea of what it sent.

// The sequencer arbitrates between sequences and feeds the driver. The base
// class already does everything needed; the typedef just names it.
typedef uvm_sequencer #(fpu_req_item) fpu_sequencer;

// ---------------------------------------------------------------------------
class fpu_driver extends uvm_driver #(fpu_req_item);
  `uvm_component_utils(fpu_driver)

  virtual fpu_if vif;
  bit [7:0]      next_tag;        // tags are assigned here, in order

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  // build_phase runs before simulation time starts. The virtual interface
  // handle is passed down from the top module through uvm_config_db, a
  // global key-value store that UVM components can read by path and name.
  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db #(virtual fpu_if)::get(this, "", "vif", vif))
      `uvm_fatal("NO_VIF", "fpu_driver has no virtual interface")
  endfunction

  // run_phase is a task, so it can wait on clocks. It runs for the whole test.
  task run_phase(uvm_phase phase);
    fpu_req_item item;
    vif.drv_cb.req_valid <= 1'b0;
    @(negedge vif.rst);                         // wait for reset release
    forever begin
      // Back-to-back requests: if a new item is already waiting, drive it on
      // the very next clock. If not, drop req_valid first so the DUT doesn't
      // see the previous request a second time, then block until one arrives.
      seq_item_port.try_next_item(item);
      if (item == null) begin
        vif.drv_cb.req_valid <= 1'b0;
        seq_item_port.get_next_item(item);
        @(vif.drv_cb);                           // re-align to a clock edge
      end
      if (item.gap != 0) begin
        vif.drv_cb.req_valid <= 1'b0;
        repeat (item.gap) @(vif.drv_cb);
      end
      item.tag = next_tag++;
      vif.drv_cb.req_valid <= 1'b1;
      vif.drv_cb.req_op    <= item.op;
      vif.drv_cb.req_a     <= item.a;
      vif.drv_cb.req_b     <= item.b;
      vif.drv_cb.req_tag   <= item.tag;
      // The handshake completes on the first edge where ready is sampled high.
      do @(vif.drv_cb); while (vif.drv_cb.req_ready !== 1'b1);
      seq_item_port.item_done();
    end
  endtask
endclass

// ---------------------------------------------------------------------------
// Watches the request side. A request "happened" on an edge where valid and
// ready were both high; that is the only moment the DUT took the operands.
class fpu_req_monitor extends uvm_monitor;
  `uvm_component_utils(fpu_req_monitor)

  virtual fpu_if                     vif;
  uvm_analysis_port #(fpu_req_item)  ap;    // broadcasts to any subscriber

  function new(string name, uvm_component parent);
    super.new(name, parent);
    ap = new("ap", this);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db #(virtual fpu_if)::get(this, "", "vif", vif))
      `uvm_fatal("NO_VIF", "fpu_req_monitor has no virtual interface")
  endfunction

  task run_phase(uvm_phase phase);
    forever begin
      @(vif.mon_cb);
      if (!vif.mon_cb.rst && vif.mon_cb.req_valid && vif.mon_cb.req_ready) begin
        fpu_req_item t = fpu_req_item::type_id::create("observed_req");
        t.op    = fpu_op_e'(vif.mon_cb.req_op);
        t.a     = vif.mon_cb.req_a;
        t.b     = vif.mon_cb.req_b;
        t.tag   = vif.mon_cb.req_tag;
        t.cycle = vif.cycle;
        ap.write(t);
      end
    end
  endtask
endclass

// ---------------------------------------------------------------------------
class fpu_rsp_monitor extends uvm_monitor;
  `uvm_component_utils(fpu_rsp_monitor)

  virtual fpu_if                    vif;
  uvm_analysis_port #(fpu_rsp_item) ap;

  function new(string name, uvm_component parent);
    super.new(name, parent);
    ap = new("ap", this);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db #(virtual fpu_if)::get(this, "", "vif", vif))
      `uvm_fatal("NO_VIF", "fpu_rsp_monitor has no virtual interface")
  endfunction

  task run_phase(uvm_phase phase);
    forever begin
      @(vif.mon_cb);
      if (!vif.mon_cb.rst && vif.mon_cb.rsp_valid) begin
        fpu_rsp_item t = fpu_rsp_item::type_id::create("observed_rsp");
        t.result = vif.mon_cb.rsp_result;
        t.flags  = vif.mon_cb.rsp_flags;
        t.tag    = vif.mon_cb.rsp_tag;
        t.cycle  = vif.cycle;
        ap.write(t);
      end
    end
  endtask
endclass

// ---------------------------------------------------------------------------
// An agent groups the components for one interface. is_active (a UVM
// built-in setting) decides whether it drives stimulus or only watches.
class fpu_agent extends uvm_agent;
  `uvm_component_utils(fpu_agent)

  fpu_sequencer   sqr;
  fpu_driver      drv;
  fpu_req_monitor req_mon;
  fpu_rsp_monitor rsp_mon;

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    req_mon = fpu_req_monitor::type_id::create("req_mon", this);
    rsp_mon = fpu_rsp_monitor::type_id::create("rsp_mon", this);
    if (get_is_active() == UVM_ACTIVE) begin
      sqr = fpu_sequencer::type_id::create("sqr", this);
      drv = fpu_driver::type_id::create("drv", this);
    end
  endfunction

  // connect_phase wires components together after they all exist.
  function void connect_phase(uvm_phase phase);
    if (get_is_active() == UVM_ACTIVE)
      drv.seq_item_port.connect(sqr.seq_item_export);
  endfunction
endclass
