// This is 64 Ki 32-bit words at the configured address width: 256 KiB.
//
// It has two ports, A and B, that work at the same time (a "true dual-port"
// RAM; the FPGA's block RAMs have two ports built in, so this costs no extra
// memory). Port A reads and writes. Port B only writes; the FPU kernel uses
// it to store results while port A is fetching the next operands.
// Reads appear in rdata after the rising edge, which callers must account for.
// If both ports write the same address in the same clock, the stored value
// is undefined; no user of this memory does that.
module scratch_mem #(
    parameter int AW = 16
) (
    input  logic          clk,
    // port A: read / write
    input  logic          en,
    input  logic          we,
    input  logic [AW-1:0] addr,
    input  logic [31:0]   wdata,
    output logic [31:0]   rdata,
    // port B: write only
    input  logic          b_we,
    input  logic [AW-1:0] b_addr,
    input  logic [31:0]   b_wdata
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
