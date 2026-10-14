// AUTO-GENERATED from spec/accel.toml by tools/gen_spec.py -- do not edit.
// Shared device settings and one-byte command IDs. I generate this
// package with the Python constants so both ends use the same protocol.
// localparam fixes each value at compile time; this package adds no registers.
package accel_pkg;

  // Identity, version, clock/baud settings, and buffer limits.
  // SCRATCH_AW counts word-address bits; each scratchpad word is 32 bits.
  localparam logic [31:0] MAGIC         = 32'h4D414343;
  localparam int          VERSION_MAJOR = 0;
  localparam int          VERSION_MINOR = 2;
  localparam int          CLK_HZ        = 100000000;
  localparam int          BAUD          = 2000000;
  localparam int          SCRATCH_AW    = 16;
  localparam int          MAX_PAYLOAD   = 1024;

  // Request opcodes; a reply sets bit 7 of the same opcode.
  localparam logic [7:0] OP_PING = 8'h01;
  localparam logic [7:0] OP_WRITE_MEM = 8'h02;
  localparam logic [7:0] OP_READ_MEM = 8'h03;
  localparam logic [7:0] OP_RUN = 8'h04;
  localparam logic [7:0] OP_STATUS = 8'h05;

  // First byte of every reply payload; zero means success.
  localparam logic [7:0] ST_OK = 8'h00;
  localparam logic [7:0] ST_BAD_OP = 8'h01;
  localparam logic [7:0] ST_BAD_LEN = 8'h02;
  localparam logic [7:0] ST_BAD_ADDR = 8'h03;
  localparam logic [7:0] ST_BUSY = 8'h04;
  localparam logic [7:0] ST_BAD_KERNEL = 8'h05;
  localparam logic [7:0] ST_BAD_ARG = 8'h06;

  // Kernel IDs carried in the first byte of a RUN request payload.
  localparam logic [7:0] K_NOP = 8'h00;
  localparam logic [7:0] K_FILL = 8'h01;
  localparam logic [7:0] K_FPU = 8'h02;

  // FPU operation codes, four bits wide (fpu.sv op input).
  localparam logic [3:0] FOP_ADD = 4'h0;
  localparam logic [3:0] FOP_SUB = 4'h1;
  localparam logic [3:0] FOP_MUL = 4'h2;
  localparam logic [3:0] FOP_DIV = 4'h3;
  localparam logic [3:0] FOP_SQRT = 4'h4;
  localparam logic [3:0] FOP_I2F = 4'h5;
  localparam logic [3:0] FOP_F2I = 4'h6;
  localparam logic [3:0] FOP_MIN = 4'h7;
  localparam logic [3:0] FOP_MAX = 4'h8;
  localparam logic [3:0] FOP_EQ = 4'h9;
  localparam logic [3:0] FOP_LT = 4'hA;
  localparam logic [3:0] FOP_LE = 4'hB;

  // IEEE exception flag masks, five bits wide (RISC-V fflags order).
  localparam logic [4:0] FFLAG_NX = 5'h01;
  localparam logic [4:0] FFLAG_UF = 5'h02;
  localparam logic [4:0] FFLAG_OF = 5'h04;
  localparam logic [4:0] FFLAG_DZ = 5'h08;
  localparam logic [4:0] FFLAG_NV = 5'h10;

endpackage
