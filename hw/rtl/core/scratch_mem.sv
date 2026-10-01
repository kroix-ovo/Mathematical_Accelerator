// This is 64 Ki 32-bit words at the configured address width: 256 KiB.
// One port means a clock can address one word for reading or writing. Reads
// appear in rdata after the rising edge, which callers must account for.
module scratch_mem #(
    parameter int AW = 16
) (
    input  logic          clk,
    input  logic          en,
    input  logic          we,
    input  logic [AW-1:0] addr,
    input  logic [31:0]   wdata,
    output logic [31:0]   rdata
);
  logic [31:0] mem [2**AW];

  // Vivado can infer initialized FPGA block RAM from this loop. The Python
  // model also starts at zero, so simulation and a configured board agree.
  initial for (int i = 0; i < 2 ** AW; i++) mem[i] = '0;

  always_ff @(posedge clk) begin
    // Nonblocking assignments use the old memory value on a write clock.
    if (en) begin
      if (we) mem[addr] <= wdata;
      rdata <= mem[addr];
    end
  end
endmodule
