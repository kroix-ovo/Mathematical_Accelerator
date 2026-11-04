// AUTO-GENERATED from sw/mathaccel/cordic_model.py by tools/gen_cordic.py -- do not edit.
//
// Widths and constant tables for fp_cordic.sv. The numbers come from the
// bit-accurate Python model, which the testbench compares the RTL against,
// so both always use the same constants. See cordic_model.py for how each
// one is derived and tools/measure_ulp.py for how the widths were chosen.
//
// sin/cos work with angles in quarter turns (units of pi/2): the
// Payne-Hanek reduction produces them in those units, so the CORDIC never
// multiplies an angle by pi/2. atan2 accumulates its angle in radians, so
// it has its own arctangent table, and the result needs no scaling either.
// exp works in units of ln2 for the same reason as sin/cos, and ln in
// natural units, each with its own hyperbolic arctangent table.
//
// An integer table entry T with F fraction bits represents T / 2^F.
// I generate these bit patterns once so the hardware and datapath models
// share the same quantization. The separate mpmath reference checks
// mathematical accuracy; sharing tables alone cannot establish it.
package cordic_pkg;

  // Datapath widths.
  localparam int PH_L    = 96;    // bits of 2/pi in the Payne-Hanek window
  localparam int PH_F    = 94;    // fraction bits of the reduced angle f
  localparam int CZ      = 44;    // fraction bits of the CORDIC angle z
  localparam int CW      = 44;    // fraction bits of the CORDIC x and y
  localparam int CN      = 42;    // CORDIC iterations, one pipeline stage each
  localparam int TINY_E  = 115;   // biased exponents below this take the tiny-input path
  localparam int SMALL_T = 13;    // f' < 2^-SMALL_T takes the sin t = t path
  localparam int ATAN_D  = 14;    // atan2 exponent gap from which y/x replaces the angle
  localparam int LN_T    = 12;    // ln of x within 2^-LN_T of 1 takes the d/(2+d) path
  localparam int EXP_TINY_E = 102;  // exp: biased exponents below this give 1
  localparam int EXP_BIG_E  = 134;  // exp: biased exponents from this overflow/underflow
  // z holds up to |ln x|/2 < 64: CZ fraction bits, 6 integer bits, sign.
  localparam int ZW = CZ + 7;

  // Start value of x: the product of 1/sqrt(1 + 2^-2i) over all stages, with
  // CW fraction bits. Each stage stretches the vector by sqrt(1 + 2^-2i);
  // starting this much shorter makes it end on the unit circle.
  localparam logic [CW+1:0] CORDIC_K = 46'h09B74EDA8436;

  // pi/2 with 31 fraction bits, for the small-angle path.
  localparam logic [31:0] PIO2 = 32'hC90FDAA2;

  // The same K with 32 fraction bits, to pre-scale the atan2/hypot inputs.
  localparam logic [31:0] KV = 32'h9B74EDA8;

  // atan2 base angles pi/2 and pi in radians, CZ fraction bits, z's width.
  localparam logic [ZW-1:0] PIO2_Z = 51'h01921FB54442D;
  localparam logic [ZW-1:0] PI_Z   = 51'h03243F6A8885A;

  // exp start value G/2, CW fraction bits: G = 1 / prod sqrt(1 - 2^-2s) undoes
  // the hyperbolic stages' length change.
  localparam logic [CW+1:0] HYP_G2 = 46'h09A8F4390077;

  // ln2/2 with CZ+8 fraction bits, for ln's base angle |e| * ln2/2.
  localparam logic [50:0] LN2H = 51'h58B90BFBE8E7C;

  // sqrt(2) as a 24-bit significand, rounded up: m >= SQRT2_M means m >= sqrt(2).
  localparam logic [23:0] SQRT2_M = 24'hB504F4;

  // Per-stage constants. Each stage calls these with its constant index i,
  // so they fold to constants; no table exists in the hardware.
  //   atan_q   atan(2^-i) in quarter turns (sin/cos)
  //   atan_r   atan(2^-i) in radians (atan2, hypot)
  //   hshift   the shift stage i uses in hyperbolic modes (4 and 13 repeat)
  //   atanh_l  atanh(2^-hshift(i)) in units of ln2 (exp)
  //   atanh_n  atanh(2^-hshift(i)) in natural units (ln)
  // All have CZ fraction bits and are rounded to nearest.
  function automatic logic [ZW-1:0] atan_q(input int i);
    case (i)
       0: return 51'h0080000000000;
       1: return 51'h004B90147677D;
       2: return 51'h0027ECE16D7B9;
       3: return 51'h0014444750777;
       4: return 51'h000A2C350C396;
       5: return 51'h0005175F85641;
       6: return 51'h00028BD87970A;
       7: return 51'h000145F154475;
       8: return 51'h0000A2F94D1B4;
       9: return 51'h0000517CBAECC;
      10: return 51'h000028BE60024;
      11: return 51'h0000145F3052A;
      12: return 51'h00000A2F98338;
      13: return 51'h00000517CC1B0;
      14: return 51'h0000028BE60DB;
      15: return 51'h00000145F306E;
      16: return 51'h000000A2F9837;
      17: return 51'h000000517CC1B;
      18: return 51'h00000028BE60E;
      19: return 51'h000000145F307;
      20: return 51'h0000000A2F983;
      21: return 51'h0000000517CC2;
      22: return 51'h000000028BE61;
      23: return 51'h0000000145F30;
      24: return 51'h00000000A2F98;
      25: return 51'h00000000517CC;
      26: return 51'h0000000028BE6;
      27: return 51'h00000000145F3;
      28: return 51'h000000000A2FA;
      29: return 51'h000000000517D;
      30: return 51'h00000000028BE;
      31: return 51'h000000000145F;
      32: return 51'h0000000000A30;
      33: return 51'h0000000000518;
      34: return 51'h000000000028C;
      35: return 51'h0000000000146;
      36: return 51'h00000000000A3;
      37: return 51'h0000000000051;
      38: return 51'h0000000000029;
      39: return 51'h0000000000014;
      40: return 51'h000000000000A;
      41: return 51'h0000000000005;
      default: return '0;
    endcase
  endfunction

  function automatic logic [ZW-1:0] atan_r(input int i);
    case (i)
       0: return 51'h00C90FDAA2217;
       1: return 51'h0076B19C1586F;
       2: return 51'h003EB6EBF2590;
       3: return 51'h001FD5BA9AAC3;
       4: return 51'h000FFAADDB968;
       5: return 51'h0007FF556EEA6;
       6: return 51'h0003FFEAAB777;
       7: return 51'h0001FFFD555BC;
       8: return 51'h0000FFFFAAAAE;
       9: return 51'h00007FFFF5555;
      10: return 51'h00003FFFFEAAB;
      11: return 51'h00001FFFFFD55;
      12: return 51'h00000FFFFFFAB;
      13: return 51'h000007FFFFFF5;
      14: return 51'h000003FFFFFFF;
      15: return 51'h0000020000000;
      16: return 51'h0000010000000;
      17: return 51'h0000008000000;
      18: return 51'h0000004000000;
      19: return 51'h0000002000000;
      20: return 51'h0000001000000;
      21: return 51'h0000000800000;
      22: return 51'h0000000400000;
      23: return 51'h0000000200000;
      24: return 51'h0000000100000;
      25: return 51'h0000000080000;
      26: return 51'h0000000040000;
      27: return 51'h0000000020000;
      28: return 51'h0000000010000;
      29: return 51'h0000000008000;
      30: return 51'h0000000004000;
      31: return 51'h0000000002000;
      32: return 51'h0000000001000;
      33: return 51'h0000000000800;
      34: return 51'h0000000000400;
      35: return 51'h0000000000200;
      36: return 51'h0000000000100;
      37: return 51'h0000000000080;
      38: return 51'h0000000000040;
      39: return 51'h0000000000020;
      40: return 51'h0000000000010;
      41: return 51'h0000000000008;
      default: return '0;
    endcase
  endfunction

  function automatic logic [ZW-1:0] atanh_l(input int i);
    case (i)
       0: return 51'h00CAE00D1CFDF;
       1: return 51'h005E54E37A9C5;
       2: return 51'h002E68B244BB9;
       3: return 51'h00171CFDC8CC6;
       4: return 51'h00171CFDC8CC6;
       5: return 51'h000B8B9A0FDA1;
       6: return 51'h0005C570A17C4;
       7: return 51'h0002E2ACC58FE;
       8: return 51'h00017154F16F3;
       9: return 51'h0000B8AA4A8CE;
      10: return 51'h00005C551F812;
      11: return 51'h00002E2A8F07E;
      12: return 51'h00001715476CE;
      13: return 51'h00000B8AA3B39;
      14: return 51'h00000B8AA3B39;
      15: return 51'h000005C551D97;
      16: return 51'h000002E2A8ECB;
      17: return 51'h0000017154765;
      18: return 51'h000000B8AA3B3;
      19: return 51'h0000005C551D9;
      20: return 51'h0000002E2A8ED;
      21: return 51'h0000001715476;
      22: return 51'h0000000B8AA3B;
      23: return 51'h00000005C551E;
      24: return 51'h00000002E2A8F;
      25: return 51'h0000000171547;
      26: return 51'h00000000B8AA4;
      27: return 51'h000000005C552;
      28: return 51'h000000002E2A9;
      29: return 51'h0000000017154;
      30: return 51'h000000000B8AA;
      31: return 51'h0000000005C55;
      32: return 51'h0000000002E2B;
      33: return 51'h0000000001715;
      34: return 51'h0000000000B8B;
      35: return 51'h00000000005C5;
      36: return 51'h00000000002E3;
      37: return 51'h0000000000171;
      38: return 51'h00000000000B9;
      39: return 51'h000000000005C;
      40: return 51'h000000000002E;
      41: return 51'h0000000000017;
      default: return '0;
    endcase
  endfunction

  function automatic logic [ZW-1:0] atanh_n(input int i);
    case (i)
       0: return 51'h008C9F53D5682;
       1: return 51'h004162BBEA045;
       2: return 51'h00202B12393D6;
       3: return 51'h001005588AD37;
       4: return 51'h001005588AD37;
       5: return 51'h000800AAC448D;
       6: return 51'h0004001556223;
       7: return 51'h00020002AAB11;
       8: return 51'h0001000055559;
       9: return 51'h000080000AAAB;
      10: return 51'h0000400001555;
      11: return 51'h00002000002AB;
      12: return 51'h0000100000055;
      13: return 51'h000008000000B;
      14: return 51'h000008000000B;
      15: return 51'h0000040000001;
      16: return 51'h0000020000000;
      17: return 51'h0000010000000;
      18: return 51'h0000008000000;
      19: return 51'h0000004000000;
      20: return 51'h0000002000000;
      21: return 51'h0000001000000;
      22: return 51'h0000000800000;
      23: return 51'h0000000400000;
      24: return 51'h0000000200000;
      25: return 51'h0000000100000;
      26: return 51'h0000000080000;
      27: return 51'h0000000040000;
      28: return 51'h0000000020000;
      29: return 51'h0000000010000;
      30: return 51'h0000000008000;
      31: return 51'h0000000004000;
      32: return 51'h0000000002000;
      33: return 51'h0000000001000;
      34: return 51'h0000000000800;
      35: return 51'h0000000000400;
      36: return 51'h0000000000200;
      37: return 51'h0000000000100;
      38: return 51'h0000000000080;
      39: return 51'h0000000000040;
      40: return 51'h0000000000020;
      41: return 51'h0000000000010;
      default: return '0;
    endcase
  endfunction

  function automatic int hshift(input int i);
    case (i)
       0: return 1;
       1: return 2;
       2: return 3;
       3: return 4;
       4: return 4;
       5: return 5;
       6: return 6;
       7: return 7;
       8: return 8;
       9: return 9;
      10: return 10;
      11: return 11;
      12: return 12;
      13: return 13;
      14: return 13;
      15: return 14;
      16: return 15;
      17: return 16;
      18: return 17;
      19: return 18;
      20: return 19;
      21: return 20;
      22: return 21;
      23: return 22;
      24: return 23;
      25: return 24;
      26: return 25;
      27: return 26;
      28: return 27;
      29: return 28;
      30: return 29;
      31: return 30;
      32: return 31;
      33: return 32;
      34: return 33;
      35: return 34;
      36: return 35;
      37: return 36;
      38: return 37;
      39: return 38;
      40: return 39;
      41: return 40;
      default: return '0;
    endcase
  endfunction

  // Payne-Hanek window ROM. For an input with biased exponent e, the entry
  // holds PH_L bits of 2/pi starting at fraction bit b_(e-151): the bits
  // before it only add whole turns, and the bits after it are too small to
  // matter. Windows for small exponents start before b_1 and so begin with
  // zeros. Only exponents TINY_E .. 254 reach the reduction. Synthesis
  // turns this case statement into a ROM of LUTs addressed by e.
  function automatic logic [PH_L-1:0] ph_window(input logic [7:0] e);
    case (e)
      8'd115: return 96'h000000000517CC1B727220A9;
      8'd116: return 96'h000000000A2F9836E4E44152;
      8'd117: return 96'h00000000145F306DC9C882A5;
      8'd118: return 96'h0000000028BE60DB9391054A;
      8'd119: return 96'h00000000517CC1B727220A94;
      8'd120: return 96'h00000000A2F9836E4E441529;
      8'd121: return 96'h0000000145F306DC9C882A53;
      8'd122: return 96'h000000028BE60DB9391054A7;
      8'd123: return 96'h0000000517CC1B727220A94F;
      8'd124: return 96'h0000000A2F9836E4E441529F;
      8'd125: return 96'h000000145F306DC9C882A53F;
      8'd126: return 96'h00000028BE60DB9391054A7F;
      8'd127: return 96'h000000517CC1B727220A94FE;
      8'd128: return 96'h000000A2F9836E4E441529FC;
      8'd129: return 96'h00000145F306DC9C882A53F8;
      8'd130: return 96'h0000028BE60DB9391054A7F0;
      8'd131: return 96'h00000517CC1B727220A94FE1;
      8'd132: return 96'h00000A2F9836E4E441529FC2;
      8'd133: return 96'h0000145F306DC9C882A53F84;
      8'd134: return 96'h000028BE60DB9391054A7F09;
      8'd135: return 96'h0000517CC1B727220A94FE13;
      8'd136: return 96'h0000A2F9836E4E441529FC27;
      8'd137: return 96'h000145F306DC9C882A53F84E;
      8'd138: return 96'h00028BE60DB9391054A7F09D;
      8'd139: return 96'h000517CC1B727220A94FE13A;
      8'd140: return 96'h000A2F9836E4E441529FC275;
      8'd141: return 96'h00145F306DC9C882A53F84EA;
      8'd142: return 96'h0028BE60DB9391054A7F09D5;
      8'd143: return 96'h00517CC1B727220A94FE13AB;
      8'd144: return 96'h00A2F9836E4E441529FC2757;
      8'd145: return 96'h0145F306DC9C882A53F84EAF;
      8'd146: return 96'h028BE60DB9391054A7F09D5F;
      8'd147: return 96'h0517CC1B727220A94FE13ABE;
      8'd148: return 96'h0A2F9836E4E441529FC2757D;
      8'd149: return 96'h145F306DC9C882A53F84EAFA;
      8'd150: return 96'h28BE60DB9391054A7F09D5F4;
      8'd151: return 96'h517CC1B727220A94FE13ABE8;
      8'd152: return 96'hA2F9836E4E441529FC2757D1;
      8'd153: return 96'h45F306DC9C882A53F84EAFA3;
      8'd154: return 96'h8BE60DB9391054A7F09D5F47;
      8'd155: return 96'h17CC1B727220A94FE13ABE8F;
      8'd156: return 96'h2F9836E4E441529FC2757D1F;
      8'd157: return 96'h5F306DC9C882A53F84EAFA3E;
      8'd158: return 96'hBE60DB9391054A7F09D5F47D;
      8'd159: return 96'h7CC1B727220A94FE13ABE8FA;
      8'd160: return 96'hF9836E4E441529FC2757D1F5;
      8'd161: return 96'hF306DC9C882A53F84EAFA3EA;
      8'd162: return 96'hE60DB9391054A7F09D5F47D4;
      8'd163: return 96'hCC1B727220A94FE13ABE8FA9;
      8'd164: return 96'h9836E4E441529FC2757D1F53;
      8'd165: return 96'h306DC9C882A53F84EAFA3EA6;
      8'd166: return 96'h60DB9391054A7F09D5F47D4D;
      8'd167: return 96'hC1B727220A94FE13ABE8FA9A;
      8'd168: return 96'h836E4E441529FC2757D1F534;
      8'd169: return 96'h06DC9C882A53F84EAFA3EA69;
      8'd170: return 96'h0DB9391054A7F09D5F47D4D3;
      8'd171: return 96'h1B727220A94FE13ABE8FA9A6;
      8'd172: return 96'h36E4E441529FC2757D1F534D;
      8'd173: return 96'h6DC9C882A53F84EAFA3EA69B;
      8'd174: return 96'hDB9391054A7F09D5F47D4D37;
      8'd175: return 96'hB727220A94FE13ABE8FA9A6E;
      8'd176: return 96'h6E4E441529FC2757D1F534DD;
      8'd177: return 96'hDC9C882A53F84EAFA3EA69BB;
      8'd178: return 96'hB9391054A7F09D5F47D4D377;
      8'd179: return 96'h727220A94FE13ABE8FA9A6EE;
      8'd180: return 96'hE4E441529FC2757D1F534DDC;
      8'd181: return 96'hC9C882A53F84EAFA3EA69BB8;
      8'd182: return 96'h9391054A7F09D5F47D4D3770;
      8'd183: return 96'h27220A94FE13ABE8FA9A6EE0;
      8'd184: return 96'h4E441529FC2757D1F534DDC0;
      8'd185: return 96'h9C882A53F84EAFA3EA69BB81;
      8'd186: return 96'h391054A7F09D5F47D4D37703;
      8'd187: return 96'h7220A94FE13ABE8FA9A6EE06;
      8'd188: return 96'hE441529FC2757D1F534DDC0D;
      8'd189: return 96'hC882A53F84EAFA3EA69BB81B;
      8'd190: return 96'h91054A7F09D5F47D4D377036;
      8'd191: return 96'h220A94FE13ABE8FA9A6EE06D;
      8'd192: return 96'h441529FC2757D1F534DDC0DB;
      8'd193: return 96'h882A53F84EAFA3EA69BB81B6;
      8'd194: return 96'h1054A7F09D5F47D4D377036D;
      8'd195: return 96'h20A94FE13ABE8FA9A6EE06DB;
      8'd196: return 96'h41529FC2757D1F534DDC0DB6;
      8'd197: return 96'h82A53F84EAFA3EA69BB81B6C;
      8'd198: return 96'h054A7F09D5F47D4D377036D8;
      8'd199: return 96'h0A94FE13ABE8FA9A6EE06DB1;
      8'd200: return 96'h1529FC2757D1F534DDC0DB62;
      8'd201: return 96'h2A53F84EAFA3EA69BB81B6C5;
      8'd202: return 96'h54A7F09D5F47D4D377036D8A;
      8'd203: return 96'hA94FE13ABE8FA9A6EE06DB14;
      8'd204: return 96'h529FC2757D1F534DDC0DB629;
      8'd205: return 96'hA53F84EAFA3EA69BB81B6C52;
      8'd206: return 96'h4A7F09D5F47D4D377036D8A5;
      8'd207: return 96'h94FE13ABE8FA9A6EE06DB14A;
      8'd208: return 96'h29FC2757D1F534DDC0DB6295;
      8'd209: return 96'h53F84EAFA3EA69BB81B6C52B;
      8'd210: return 96'hA7F09D5F47D4D377036D8A56;
      8'd211: return 96'h4FE13ABE8FA9A6EE06DB14AC;
      8'd212: return 96'h9FC2757D1F534DDC0DB62959;
      8'd213: return 96'h3F84EAFA3EA69BB81B6C52B3;
      8'd214: return 96'h7F09D5F47D4D377036D8A566;
      8'd215: return 96'hFE13ABE8FA9A6EE06DB14ACC;
      8'd216: return 96'hFC2757D1F534DDC0DB629599;
      8'd217: return 96'hF84EAFA3EA69BB81B6C52B32;
      8'd218: return 96'hF09D5F47D4D377036D8A5664;
      8'd219: return 96'hE13ABE8FA9A6EE06DB14ACC9;
      8'd220: return 96'hC2757D1F534DDC0DB6295993;
      8'd221: return 96'h84EAFA3EA69BB81B6C52B327;
      8'd222: return 96'h09D5F47D4D377036D8A5664F;
      8'd223: return 96'h13ABE8FA9A6EE06DB14ACC9E;
      8'd224: return 96'h2757D1F534DDC0DB6295993C;
      8'd225: return 96'h4EAFA3EA69BB81B6C52B3278;
      8'd226: return 96'h9D5F47D4D377036D8A5664F1;
      8'd227: return 96'h3ABE8FA9A6EE06DB14ACC9E2;
      8'd228: return 96'h757D1F534DDC0DB6295993C4;
      8'd229: return 96'hEAFA3EA69BB81B6C52B32788;
      8'd230: return 96'hD5F47D4D377036D8A5664F10;
      8'd231: return 96'hABE8FA9A6EE06DB14ACC9E21;
      8'd232: return 96'h57D1F534DDC0DB6295993C43;
      8'd233: return 96'hAFA3EA69BB81B6C52B327887;
      8'd234: return 96'h5F47D4D377036D8A5664F10E;
      8'd235: return 96'hBE8FA9A6EE06DB14ACC9E21C;
      8'd236: return 96'h7D1F534DDC0DB6295993C439;
      8'd237: return 96'hFA3EA69BB81B6C52B3278872;
      8'd238: return 96'hF47D4D377036D8A5664F10E4;
      8'd239: return 96'hE8FA9A6EE06DB14ACC9E21C8;
      8'd240: return 96'hD1F534DDC0DB6295993C4390;
      8'd241: return 96'hA3EA69BB81B6C52B32788720;
      8'd242: return 96'h47D4D377036D8A5664F10E41;
      8'd243: return 96'h8FA9A6EE06DB14ACC9E21C82;
      8'd244: return 96'h1F534DDC0DB6295993C43904;
      8'd245: return 96'h3EA69BB81B6C52B327887208;
      8'd246: return 96'h7D4D377036D8A5664F10E410;
      8'd247: return 96'hFA9A6EE06DB14ACC9E21C820;
      8'd248: return 96'hF534DDC0DB6295993C439041;
      8'd249: return 96'hEA69BB81B6C52B3278872083;
      8'd250: return 96'hD4D377036D8A5664F10E4107;
      8'd251: return 96'hA9A6EE06DB14ACC9E21C820F;
      8'd252: return 96'h534DDC0DB6295993C439041F;
      8'd253: return 96'hA69BB81B6C52B3278872083F;
      8'd254: return 96'h4D377036D8A5664F10E4107F;
      default: return '0;
    endcase
  endfunction

  // exp's window ROM, used by the same multiplier. For biased exponent e the
  // entry is 2^(e-64)/ln2, rounded, so that significand times entry is
  // |x|/ln2 with 86 fraction bits (and stays below 2^94, no wrap).
  function automatic logic [PH_L-1:0] exp_window(input logic [7:0] e);
    case (e)
      8'd102: return 96'h000000000000005C551D94AE;
      8'd103: return 96'h00000000000000B8AA3B295C;
      8'd104: return 96'h0000000000000171547652B8;
      8'd105: return 96'h00000000000002E2A8ECA570;
      8'd106: return 96'h00000000000005C551D94AE1;
      8'd107: return 96'h0000000000000B8AA3B295C1;
      8'd108: return 96'h000000000000171547652B83;
      8'd109: return 96'h0000000000002E2A8ECA5706;
      8'd110: return 96'h0000000000005C551D94AE0C;
      8'd111: return 96'h000000000000B8AA3B295C18;
      8'd112: return 96'h00000000000171547652B830;
      8'd113: return 96'h000000000002E2A8ECA57060;
      8'd114: return 96'h000000000005C551D94AE0C0;
      8'd115: return 96'h00000000000B8AA3B295C17F;
      8'd116: return 96'h0000000000171547652B82FE;
      8'd117: return 96'h00000000002E2A8ECA5705FC;
      8'd118: return 96'h00000000005C551D94AE0BF8;
      8'd119: return 96'h0000000000B8AA3B295C17F1;
      8'd120: return 96'h000000000171547652B82FE1;
      8'd121: return 96'h0000000002E2A8ECA5705FC3;
      8'd122: return 96'h0000000005C551D94AE0BF86;
      8'd123: return 96'h000000000B8AA3B295C17F0C;
      8'd124: return 96'h00000000171547652B82FE17;
      8'd125: return 96'h000000002E2A8ECA5705FC2F;
      8'd126: return 96'h000000005C551D94AE0BF85E;
      8'd127: return 96'h00000000B8AA3B295C17F0BC;
      8'd128: return 96'h0000000171547652B82FE177;
      8'd129: return 96'h00000002E2A8ECA5705FC2EF;
      8'd130: return 96'h00000005C551D94AE0BF85DE;
      8'd131: return 96'h0000000B8AA3B295C17F0BBC;
      8'd132: return 96'h000000171547652B82FE1778;
      8'd133: return 96'h0000002E2A8ECA5705FC2EF0;
      default: return '0;
    endcase
  endfunction

endpackage
