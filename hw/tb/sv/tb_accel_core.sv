// Vector-driven testbench for accel_core (Vivado xsim).
// Replays request frames over the UART pin and compares the response bytes
// with the Python reference model's output (hw/tb/vectors.py).
module tb_accel_core;
  localparam int CPB = 8;  // fast UART for simulation
  string vec_file = "vectors.txt";

  logic clk = 0, rst = 1, rx = 1, tx;
  always #5 clk = ~clk;

  accel_core #(.CLKS_PER_BIT(CPB)) dut (
      .clk, .rst, .uart_rx_i(rx), .uart_tx_o(tx), .busy_o(), .err_o());

  // ---- UART monitor: collect bytes sent by the DUT ----
  byte unsigned rxq[$];
  initial begin
    forever begin
      byte unsigned b;
      @(negedge tx);
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

    if ($value$plusargs("VEC=%s", vec_file)) ;
    fd = $fopen(vec_file, "r");
    if (fd == 0) $fatal(1, "cannot open %s", vec_file);

    repeat (10) @(posedge clk);
    rst = 0;
    repeat (10) @(posedge clk);

    errors = 0;
    vec = 0;
    while (!$feof(fd)) begin
      code = $fscanf(fd, "%s %d", kind, n);
      if (code != 2) break;
      exp.delete();
      for (int i = 0; i < n; i++) begin
        code = $fscanf(fd, "%h", v);
        exp.push_back(v[7:0]);
      end
      if (kind == "S") begin
        vec++;
        rxq.delete();
        foreach (exp[i]) send_byte(exp[i]);
      end else if (kind == "E") begin
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
          foreach (exp[i])
            if (rxq[i] != exp[i]) begin
              $error("vector %0d byte %0d: expected %02x got %02x", vec, i, exp[i], rxq[i]);
              for (int j = 0; j < 24 && j < n; j++) $display("  [%0d] exp %02x got %02x", j, exp[j], rxq[j]);
              errors++;
              break;
            end
        end
      end else begin  // "N": no response expected
        repeat (40 * CPB * 10) @(posedge clk);
        if (rxq.size() != 0) begin
          $error("vector %0d: expected no response, got %0d bytes", vec, rxq.size());
          errors++;
        end
      end
    end
    $fclose(fd);
    if (errors == 0) $display("PASS: %0d vectors", vec);
    else $display("FAIL: %0d errors over %0d vectors", errors, vec);
    $finish;
  end
endmodule
