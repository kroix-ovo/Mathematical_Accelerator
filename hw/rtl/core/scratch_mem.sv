// This is 64 Ki 32-bit words at the configured address width: 256 KiB.
//
// It has two ports, A and B, that work at the same time (a "true dual-port"
// RAM; the FPGA's block RAMs have two ports built in, so this costs no extra
// memory). Port A reads and writes. Port B only writes; the FPU kernel uses
// it to store results while port A is fetching the next operands.
// Reads appear in rdata after the rising edge, which callers must account for.
// If both ports write the same address in the same clock, the stored value
// is undefined; no user of this memory does that.
// The array declaration describes storage, and the clocked reads describe
// block RAM's registered outputs. Setting an address does not immediately
// produce its word: the controller must allow the read clock edge first.
// I give the storage no reset logic, so the controller can restart a command
// without clearing the data someone has already loaded.
module scratch_mem #(
    parameter int AW = 16
) (
    input  logic          clk,                   // Clock: rising edge updates this module's registers; 100 MHz in the board design.
    // port A: read / write
    input  logic          en,                    // Memory port A enable: high allows a registered read and, if write enable is high, a write on the rising edge.
    input  logic          we,                    // Memory port A write enable: high writes the addressed word when port A enable is also high.
    input  logic [AW-1:0] addr,                  // Memory port A address: word index used for the enabled read and optional write.
    input  logic [31:0]   wdata,                 // Memory port A write data: 32-bit value stored at the selected word index on an enabled write.
    output logic [31:0]   rdata,                 // Memory port A read data: registered word from the last enabled read; a same-edge write returns the old word.
    // port B: write only
    input  logic          b_we,                  // Memory port B write enable: high independently stores one word on the rising clock edge.
    input  logic [AW-1:0] b_addr,                // Memory port B address: word index for the second port's independent write.
    input  logic [31:0]   b_wdata                // Memory port B write data: 32-bit value stored when port B write enable is high.
);
  logic [31:0] mem [2**AW];

  // Vivado can infer initialized FPGA block RAM from this loop. The Python
  // model also starts at zero, so simulation and a configured board agree.
  initial for (int i = 0; i < 2 ** AW; i++) mem[i] = '0;

  // Each port is its own clocked block, the pattern Vivado recognizes as a
  // dual-port block RAM. These use plain "always" rather than always_ff:
  // always_ff forbids two blocks writing the same variable, but two ports
  // writing one memory array is exactly what a dual-port RAM is.
  always @(posedge clk) begin
    // Nonblocking assignments use the old memory value on a write clock.
    if (en) begin
      if (we) mem[addr] <= wdata;
      rdata <= mem[addr];
    end
  end

  always @(posedge clk) begin
    if (b_we) mem[b_addr] <= b_wdata;
  end
endmodule
