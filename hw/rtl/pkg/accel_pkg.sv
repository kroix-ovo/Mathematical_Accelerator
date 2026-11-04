// AUTO-GENERATED from spec/accel.toml by tools/gen_spec.py -- do not edit.
// Shared device settings and one-byte command IDs. I generate this
// package with the Python constants so both ends use the same protocol.
// localparam fixes each value at compile time; this package adds no registers.
package accel_pkg;

  // Identity, version, clock/baud settings, and buffer limits.
  // SCRATCH_AW counts word-address bits; each scratchpad word is 32 bits.
  localparam logic [31:0] MAGIC         = 32'h4D414343;
  localparam int          VERSION_MAJOR = 0;
  localparam int          VERSION_MINOR = 3;
  localparam int          CLK_HZ        = 100000000;
  localparam int          BAUD          = 2000000;
  localparam int          SCRATCH_AW    = 16;
  localparam int          MAX_PAYLOAD   = 1024;
  // The FPU kernel accepts op codes 0 .. FPU_OP_COUNT-1 (BAD_ARG otherwise).
  localparam int          FPU_OP_COUNT  = 18;

  // Ethernet/UDP transport: board MAC and IPv4 address, UDP command port.
  // The first byte on the wire is the most significant byte here.
  localparam logic [47:0] ETH_MAC       = 48'h024D41434301;
  localparam logic [31:0] ETH_IP        = 32'hC0A80A02;
  localparam logic [15:0] ETH_UDP_PORT  = 16'd50000;

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

  // FPU operation codes, five bits wide (codes 12-17 are phase 3).
  localparam logic [4:0] FOP_ADD = 5'h00;
  localparam logic [4:0] FOP_SUB = 5'h01;
  localparam logic [4:0] FOP_MUL = 5'h02;
  localparam logic [4:0] FOP_DIV = 5'h03;
  localparam logic [4:0] FOP_SQRT = 5'h04;
  localparam logic [4:0] FOP_I2F = 5'h05;
  localparam logic [4:0] FOP_F2I = 5'h06;
  localparam logic [4:0] FOP_MIN = 5'h07;
  localparam logic [4:0] FOP_MAX = 5'h08;
  localparam logic [4:0] FOP_EQ = 5'h09;
  localparam logic [4:0] FOP_LT = 5'h0A;
  localparam logic [4:0] FOP_LE = 5'h0B;
  localparam logic [4:0] FOP_SIN = 5'h0C;
  localparam logic [4:0] FOP_COS = 5'h0D;
  localparam logic [4:0] FOP_ATAN2 = 5'h0E;
  localparam logic [4:0] FOP_HYPOT = 5'h0F;
  localparam logic [4:0] FOP_EXP = 5'h10;
  localparam logic [4:0] FOP_LN = 5'h11;

  // IEEE exception flag masks, five bits wide (RISC-V fflags order).
  localparam logic [4:0] FFLAG_NX = 5'h01;
  localparam logic [4:0] FFLAG_UF = 5'h02;
  localparam logic [4:0] FFLAG_OF = 5'h04;
  localparam logic [4:0] FFLAG_DZ = 5'h08;
  localparam logic [4:0] FFLAG_NV = 5'h10;

endpackage
