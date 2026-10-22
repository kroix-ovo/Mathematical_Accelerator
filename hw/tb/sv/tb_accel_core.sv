// This is the xsim version of the UART integration test. I drive requests
// into accel_core's serial input and collect serial reply bytes independently.
// The Python model writes the expected bytes into vectors.txt. Matching the
// full reply tests header fields, COBS, CRC, payload data, and kernel clocks.
// Testbench tasks, queues, and delays describe simulation, not FPGA hardware.
module tb_accel_core;
  localparam int CPB = 8;  // fast UART for simulation
  string vec_file = "vectors.txt";

  logic clk = 0, rst = 1, rx = 1, tx;
  always #5 clk = ~clk;  // Toggle every 5 ns: one complete clock is 10 ns.
  // The Ethernet side is idle in this test. Its 50 MHz clock toggles every
  // 10 ns from time 0, so its rising edges coincide with every other clk edge.
  logic clk_rmii = 0;
  always #10 clk_rmii = ~clk_rmii;

  accel_core #(.CLKS_PER_BIT(CPB)) dut (
      .clk, .rst, .uart_rx_i(rx), .uart_tx_o(tx),
      .clk_rmii, .rmii_rxd(2'b00), .rmii_crs_dv(1'b0), .rmii_rx_er(1'b0), .rmii_txd(), .rmii_tx_en(),
      .busy_o(), .err_o(), .eth_rx_o(), .eth_tx_o(), .eth_err_o(), .eth_drop_o());

  // This initial block runs alongside the stimulus block. rxq is a growing
  // queue of reply bytes; it does not impose a packet interpretation on them.
  byte unsigned rxq[$];
  initial begin
    forever begin
      byte unsigned b;
      @(negedge tx);
      // Center in the start bit, then sample each data bit one bit time later.
      repeat (CPB / 2) @(posedge clk);
      for (int i = 0; i < 8; i++) begin
        repeat (CPB) @(posedge clk);
        b[i] = tx;
      end
      repeat (CPB) @(posedge clk);
      if (tx !== 1'b1) $error("stop bit error");
      rxq.push_back(b);
    end
  end

  task automatic send_byte(input byte unsigned b);
    // An automatic task has its own local arguments for each call. Drive the
    // low start bit, eight least-significant-first data bits, then high stop.
    rx = 0;
    repeat (CPB) @(posedge clk);
    for (int i = 0; i < 8; i++) begin
      rx = b[i];
      repeat (CPB) @(posedge clk);
    end
    rx = 1;
    repeat (CPB) @(posedge clk);
  endtask

  initial begin
    int fd, n, code, errors, vec;
    string kind;
    byte unsigned exp[$];
    int unsigned v;

    // +VEC=<path> can override the vector file; otherwise use the runner's
    // vectors.txt in build/xsim. Missing input must fail immediately.
    if ($value$plusargs("VEC=%s", vec_file)) ;
    fd = $fopen(vec_file, "r");
    if (fd == 0) $fatal(1, "cannot open %s", vec_file);

    repeat (10) @(posedge clk);
    rst = 0;
    repeat (10) @(posedge clk);

    errors = 0;
    vec = 0;
    while (!$feof(fd)) begin
      // The file has S(send), E(expect), and N(no reply) records. n is the
      // number of hexadecimal bytes after the record's label and count.
      code = $fscanf(fd, "%s %d", kind, n);
      if (code != 2) break;
      exp.delete();
      for (int i = 0; i < n; i++) begin
        code = $fscanf(fd, "%h", v);
        exp.push_back(v[7:0]);
      end
      if (kind == "S") begin
        // Keep DUT state across vectors, but discard the previous reply queue.
        vec++;
        rxq.delete();
        foreach (exp[i]) send_byte(exp[i]);
      end else if (kind == "E") begin
        // Race reply completion against a bounded timeout. join_any resumes
        // after either finishes; disable cancels the still-waiting branch.
        fork : wait_resp
          wait (rxq.size() >= n);
          begin
            repeat (200000) @(posedge clk);
            $error("vector %0d: timeout, got %0d of %0d bytes", vec, rxq.size(), n);
            errors++;
          end
        join_any
        disable wait_resp;
        repeat (4 * CPB * 10) @(posedge clk);  // catch any extra bytes
        if (rxq.size() != n) begin
          $error("vector %0d: expected %0d bytes, got %0d", vec, n, rxq.size());
          errors++;
        end else begin
          // Full wire-byte equality catches errors even if decoded payloads
          // happen to agree. Print a small prefix to help locate a mismatch.
          foreach (exp[i])
            if (rxq[i] != exp[i]) begin
              $error("vector %0d byte %0d: expected %02x got %02x", vec, i, exp[i], rxq[i]);
              for (int j = 0; j < 24 && j < n; j++) $display("  [%0d] exp %02x got %02x", j, exp[j], rxq[j]);
              errors++;
              break;
            end
        end
      end else begin  // "N": no response expected
        // Bad link frames are dropped without a reply. Give a late response
        // enough UART time to appear before declaring the expected silence.
        repeat (40 * CPB * 10) @(posedge clk);
        if (rxq.size() != 0) begin
          $error("vector %0d: expected no response, got %0d bytes", vec, rxq.size());
          errors++;
        end
      end
    end
    $fclose(fd);
    // The Python xsim runner uses these final markers to set its exit status.
    if (errors == 0) $display("PASS: %0d vectors", vec);
    else $display("FAIL: %0d errors over %0d vectors", errors, vec);
    $finish;
  end
endmodule
