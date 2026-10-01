// AUTO-GENERATED from spec/accel.toml by tools/gen_spec.py -- do not edit.
package accel_pkg;

  localparam logic [31:0] MAGIC         = 32'h4D414343;
  localparam int          VERSION_MAJOR = 0;
  localparam int          VERSION_MINOR = 1;
  localparam int          CLK_HZ        = 100000000;
  localparam int          BAUD          = 2000000;
  localparam int          SCRATCH_AW    = 16;
  localparam int          MAX_PAYLOAD   = 1024;

  localparam logic [7:0] OP_PING = 8'h01;
  localparam logic [7:0] OP_WRITE_MEM = 8'h02;
  localparam logic [7:0] OP_READ_MEM = 8'h03;
  localparam logic [7:0] OP_RUN = 8'h04;
  localparam logic [7:0] OP_STATUS = 8'h05;

  localparam logic [7:0] ST_OK = 8'h00;
  localparam logic [7:0] ST_BAD_OP = 8'h01;
  localparam logic [7:0] ST_BAD_LEN = 8'h02;
  localparam logic [7:0] ST_BAD_ADDR = 8'h03;
  localparam logic [7:0] ST_BUSY = 8'h04;
  localparam logic [7:0] ST_BAD_KERNEL = 8'h05;

  localparam logic [7:0] K_NOP = 8'h00;
  localparam logic [7:0] K_FILL = 8'h01;

endpackage
