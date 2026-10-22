// RMII agent: the frame item, its sequencer, a driver that sends frames
// through the PHY model in rmii_phy_if, and two monitors that rebuild frames
// from the pins (one per direction).

// ---------------------------------------------------------------------------
// One Ethernet frame (destination MAC .. FCS) plus how the PHY presents it.
// The knobs are rand with weighted distributions, so a plain randomize()
// gives mostly normal frames and some unusual ones. dist {a := w} gives value
// a the weight w.
class eth_frame_item extends uvm_sequence_item;
  byte_q   data;
  rand int lead_in;          // 00 dibits with CRS_DV high before the preamble
  rand int pre_dibits;       // 01 preamble dibits before the SFD (28 = 7 bytes)
  rand int toggle_nibbles;   // final nibbles sent while CRS_DV toggles
  rand int gap;              // idle clocks after the frame (>= 48)
  int      rx_er_at = -1;    // dibit index where RX_ER pulses, -1 for none
  bit      rx_er;            // set by the receive monitor when it saw RX_ER

  constraint c_phy {
    lead_in        dist {0 := 4, 1 := 1, 2 := 1, 5 := 1};
    pre_dibits     dist {28 := 5, 20 := 1, 9 := 1};
    toggle_nibbles dist {0 := 3, 1 := 1, 2 := 1, 3 := 1};
    gap            dist {48 := 4, 60 := 1, 200 := 1};
  }

  `uvm_object_utils(eth_frame_item)
  function new(string name = "eth_frame_item");
    super.new(name);
    lead_in = 0; pre_dibits = 28; toggle_nibbles = 0; gap = 48;
  endfunction
  function string convert2string();
    return $sformatf("%0d bytes %s (lead %0d pre %0d toggle %0d rx_er %0d)", data.size(),
                     top_codec::hex(data, 48), lead_in, pre_dibits, toggle_nibbles, rx_er_at);
  endfunction
endclass

typedef uvm_sequencer #(eth_frame_item) rmii_sequencer;

// ---------------------------------------------------------------------------
class rmii_driver extends uvm_driver #(eth_frame_item);
  `uvm_component_utils(rmii_driver)
  virtual rmii_phy_if vif;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction

  function void build_phase(uvm_phase phase);
    if (!uvm_config_db #(virtual rmii_phy_if)::get(this, "", "rmii_vif", vif))
      `uvm_fatal("NO_VIF", "rmii_driver has no rmii_phy_if")
  endfunction

  // All pin-level behaviour lives in the PHY model's send_frame task.
  task run_phase(uvm_phase phase);
    eth_frame_item it;
    forever begin
      seq_item_port.get_next_item(it);
      vif.send_frame(it.data, it.lead_in, it.pre_dibits, it.toggle_nibbles, it.rx_er_at, it.gap);
      seq_item_port.item_done();
    end
  endtask
endclass

// ---------------------------------------------------------------------------
// Rebuilds received frames from RXD/CRS_DV/RX_ER the way a MAC must read
// them: hunt for preamble then SFD; a dibit counts while CRS_DV is high now
// or was one sample ago; two low samples end the frame. I sample on the
// falling edge, half a period away from the PHY's changes.
class rmii_rx_monitor extends uvm_monitor;
  `uvm_component_utils(rmii_rx_monitor)
  virtual rmii_phy_if                 vif;
  uvm_analysis_port #(eth_frame_item) ap;
  function new(string name, uvm_component parent);
    super.new(name, parent);
    ap = new("ap", this);
  endfunction

  function void build_phase(uvm_phase phase);
    if (!uvm_config_db #(virtual rmii_phy_if)::get(this, "", "rmii_vif", vif))
      `uvm_fatal("NO_VIF", "rmii_rx_monitor has no rmii_phy_if")
  endfunction

  // Besides the bytes I record what the PHY did, for coverage: how many 00
  // dibits came before the preamble, how many 01 preamble dibits, and how
  // many nibbles arrived with CRS_DV low (the toggling tail). The one extra
  // low sample after the real end is not a toggle, hence the "- 1".
  task run_phase(uvm_phase phase);
    bit prev = 0, pre = 0, in_frame = 0, er = 0;
    int lead = 0, npre = 0, lows = 0;
    logic [1:0] dib[$];
    forever begin
      bit crs, carrier;
      @(negedge vif.ref_clk);
      crs     = (vif.crs_dv === 1'b1);
      carrier = crs || prev;
      prev    = crs;
      if (!in_frame) begin
        if (!carrier) begin
          pre = 0; lead = 0; npre = 0;
        end else if (vif.rxd == 2'b01) begin
          pre = 1; npre++;
        end else if (vif.rxd == 2'b11 && pre) begin
          in_frame = 1; dib.delete(); er = 0; lows = 0;
        end else if (vif.rxd == 2'b00 && !pre) begin
          lead++;
        end else begin
          pre = 0; npre = 0;
        end
      end else if (!carrier) begin
        eth_frame_item f = eth_frame_item::type_id::create("rx");
        for (int k = 0; k + 3 < dib.size(); k += 4) f.data.push_back({dib[k + 3], dib[k + 2], dib[k + 1], dib[k]});
        f.rx_er          = er;
        f.lead_in        = lead;
        f.pre_dibits     = npre - 3;     // the SFD starts with three more 01 dibits
        f.toggle_nibbles = lows - 1;
        ap.write(f);
        in_frame = 0; pre = 0; lead = 0; npre = 0;
      end else begin
        dib.push_back(vif.rxd);
        if (!crs) lows++;
        if (vif.rx_er === 1'b1) er = 1;
      end
    end
  endtask
endclass

// ---------------------------------------------------------------------------
// Collects frames the DUT transmits and checks the RMII-level rules: 7 x 55,
// D5, whole bytes, a good FCS, at least 64 bytes, and an inter-frame gap of
// at least 48 clocks (96 bit times) with TX_EN low. I sample on the rising
// edge like the PHY does; the DUT changes these pins on the falling edge.
class rmii_tx_monitor extends uvm_monitor;
  `uvm_component_utils(rmii_tx_monitor)
  virtual rmii_phy_if                 vif;
  uvm_analysis_port #(eth_frame_item) ap;
  int unsigned                        n_frames;
  function new(string name, uvm_component parent);
    super.new(name, parent);
    ap = new("ap", this);
  endfunction

  function void build_phase(uvm_phase phase);
    if (!uvm_config_db #(virtual rmii_phy_if)::get(this, "", "rmii_vif", vif))
      `uvm_fatal("NO_VIF", "rmii_tx_monitor has no rmii_phy_if")
  endfunction

  task run_phase(uvm_phase phase);
    logic [1:0] dib[$];
    int unsigned idle = 10_000;
    forever begin
      @(posedge vif.ref_clk);
      if (vif.tx_en === 1'b1) begin
        if (dib.size() == 0 && idle < 48)
          `uvm_error("RMII_TX", $sformatf("inter-frame gap of only %0d clocks", idle))
        dib.push_back(vif.txd);
        idle = 0;
      end else begin
        idle++;
        if (dib.size() != 0) begin
          byte_q raw;
          eth_frame_item f = eth_frame_item::type_id::create("tx");
          for (int k = 0; k + 3 < dib.size(); k += 4) raw.push_back({dib[k + 3], dib[k + 2], dib[k + 1], dib[k]});
          if (dib.size() % 4 != 0)
            `uvm_error("RMII_TX", $sformatf("frame of %0d dibits is not whole bytes", dib.size()))
          for (int i = 0; i < 8; i++)
            if (raw[i] != (i == 7 ? 8'hD5 : 8'h55))
              `uvm_error("RMII_TX", $sformatf("bad preamble %s", top_codec::hex(raw, 8)))
          f.data = eth_codec::slice(raw, 8, raw.size());
          if (!eth_codec::fcs_ok(f.data))
            `uvm_error("RMII_TX", $sformatf("bad FCS on %s", top_codec::hex(f.data, 80)))
          if (f.data.size() < 64)
            `uvm_error("RMII_TX", $sformatf("sent a runt of %0d bytes", f.data.size()))
          n_frames++;
          ap.write(f);
          dib.delete();
        end
      end
    end
  endtask
endclass

// ---------------------------------------------------------------------------
class rmii_agent extends uvm_agent;
  `uvm_component_utils(rmii_agent)
  rmii_sequencer  sqr;
  rmii_driver     drv;
  rmii_rx_monitor rx_mon;
  rmii_tx_monitor tx_mon;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction

  function void build_phase(uvm_phase phase);
    sqr    = rmii_sequencer::type_id::create("sqr", this);
    drv    = rmii_driver::type_id::create("drv", this);
    rx_mon = rmii_rx_monitor::type_id::create("rx_mon", this);
    tx_mon = rmii_tx_monitor::type_id::create("tx_mon", this);
  endfunction

  function void connect_phase(uvm_phase phase);
    drv.seq_item_port.connect(sqr.seq_item_export);
  endfunction
endclass
