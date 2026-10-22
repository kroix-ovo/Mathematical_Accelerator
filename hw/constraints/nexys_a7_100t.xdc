## Nexys A7-100T (xc7a100tcsg324-1). Pin names follow the Digilent master XDC.

## Clock
set_property -dict { PACKAGE_PIN E3  IOSTANDARD LVCMOS33 } [get_ports { CLK100MHZ }]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports { CLK100MHZ }]

## Reset button (active low)
set_property -dict { PACKAGE_PIN C12 IOSTANDARD LVCMOS33 } [get_ports { CPU_RESETN }]

## USB-UART (FT2232HQ)
set_property -dict { PACKAGE_PIN C4  IOSTANDARD LVCMOS33 } [get_ports { UART_TXD_IN }]
set_property -dict { PACKAGE_PIN D4  IOSTANDARD LVCMOS33 } [get_ports { UART_RXD_OUT }]

## LEDs
set_property -dict { PACKAGE_PIN H17 IOSTANDARD LVCMOS33 } [get_ports { LED[0] }]
set_property -dict { PACKAGE_PIN K15 IOSTANDARD LVCMOS33 } [get_ports { LED[1] }]
set_property -dict { PACKAGE_PIN J13 IOSTANDARD LVCMOS33 } [get_ports { LED[2] }]
set_property -dict { PACKAGE_PIN N14 IOSTANDARD LVCMOS33 } [get_ports { LED[3] }]
set_property -dict { PACKAGE_PIN R18 IOSTANDARD LVCMOS33 } [get_ports { LED[4] }]
set_property -dict { PACKAGE_PIN V17 IOSTANDARD LVCMOS33 } [get_ports { LED[5] }]
set_property -dict { PACKAGE_PIN U17 IOSTANDARD LVCMOS33 } [get_ports { LED[6] }]
set_property -dict { PACKAGE_PIN U16 IOSTANDARD LVCMOS33 } [get_ports { LED[7] }]
set_property -dict { PACKAGE_PIN V16 IOSTANDARD LVCMOS33 } [get_ports { LED[8] }]
set_property -dict { PACKAGE_PIN T15 IOSTANDARD LVCMOS33 } [get_ports { LED[9] }]
set_property -dict { PACKAGE_PIN U14 IOSTANDARD LVCMOS33 } [get_ports { LED[10] }]
set_property -dict { PACKAGE_PIN T16 IOSTANDARD LVCMOS33 } [get_ports { LED[11] }]
set_property -dict { PACKAGE_PIN V15 IOSTANDARD LVCMOS33 } [get_ports { LED[12] }]
set_property -dict { PACKAGE_PIN V14 IOSTANDARD LVCMOS33 } [get_ports { LED[13] }]
set_property -dict { PACKAGE_PIN V12 IOSTANDARD LVCMOS33 } [get_ports { LED[14] }]
set_property -dict { PACKAGE_PIN V11 IOSTANDARD LVCMOS33 } [get_ports { LED[15] }]

## SMSC LAN8720A Ethernet PHY (RMII). Pins from Digilent's Nexys-A7-100T-Master.xdc.
## ETH_MDIO and ETH_INTN are not used by this design and are left unconnected.
set_property -dict { PACKAGE_PIN C9  IOSTANDARD LVCMOS33 } [get_ports { ETH_MDC }]
set_property -dict { PACKAGE_PIN B3  IOSTANDARD LVCMOS33 } [get_ports { ETH_RSTN }]
set_property -dict { PACKAGE_PIN D9  IOSTANDARD LVCMOS33 } [get_ports { ETH_CRSDV }]
set_property -dict { PACKAGE_PIN C10 IOSTANDARD LVCMOS33 } [get_ports { ETH_RXERR }]
set_property -dict { PACKAGE_PIN C11 IOSTANDARD LVCMOS33 } [get_ports { ETH_RXD[0] }]
set_property -dict { PACKAGE_PIN D10 IOSTANDARD LVCMOS33 } [get_ports { ETH_RXD[1] }]
set_property -dict { PACKAGE_PIN B9  IOSTANDARD LVCMOS33 } [get_ports { ETH_TXEN }]
set_property -dict { PACKAGE_PIN A10 IOSTANDARD LVCMOS33 } [get_ports { ETH_TXD[0] }]
set_property -dict { PACKAGE_PIN A8  IOSTANDARD LVCMOS33 } [get_ports { ETH_TXD[1] }]
set_property -dict { PACKAGE_PIN D5  IOSTANDARD LVCMOS33 } [get_ports { ETH_REFCLK }]
## Fast edges for the 50 MHz clock and the data the PHY samples with it.
set_property SLEW FAST [get_ports { ETH_REFCLK ETH_TXD[*] ETH_TXEN }]

## RMII timing. ETH_REFCLK is a copy of the MMCM's clk_ref made by the ODDR,
## so it is declared as a clock generated at that pin. Vivado then knows when
## the PHY sees each edge, including the delay through the output pad.
create_generated_clock -name eth_refclk -source [get_pins u_refclk_oddr/C] -multiply_by 1 [get_ports ETH_REFCLK]
set clk_rmii [get_clocks -of_objects [get_pins u_mmcm/CLKOUT1]]

## Receive: the LAN8720A drives RXD, CRS_DV and RXER at most 14 ns after a
## rising REFCLK at its pin and holds them at least 3 ns (datasheet rev 1.4,
## Table 5.10, REF_CLK In mode). I add 1 ns to the maximum for the board
## traces (clock out, data back) and nothing to the minimum.
## REFCLK rises 2 ns before my clk_rmii edges, so Vivado would pair each
## launch with the clk_rmii edge 2 ns later. The data actually lands on the
## edge after that; -setup 2 moves the setup check there. The hold check
## moves with it, which is right here: the next value must not arrive before
## that same edge. (The usual "-hold 1" partner would relax hold by a whole
## period and hide a real problem.)
set_input_delay -clock eth_refclk -max 15.0 [get_ports { ETH_RXD[*] ETH_CRSDV ETH_RXERR }]
set_input_delay -clock eth_refclk -min 3.0  [get_ports { ETH_RXD[*] ETH_CRSDV ETH_RXERR }]
set_multicycle_path -setup 2 -from [get_clocks eth_refclk] -to $clk_rmii

## Transmit: the PHY needs TXD and TXEN stable 4 ns before and 1.5 ns after
## its REFCLK edge (same table). 0.5 ns is added to the setup side for the
## traces. The outputs leave on the falling edge of clk_rmii (rmii_tx.sv).
set_output_delay -clock eth_refclk -max 4.5  [get_ports { ETH_TXD[*] ETH_TXEN }]
set_output_delay -clock eth_refclk -min -1.5 [get_ports { ETH_TXD[*] ETH_TXEN }]

## Asynchronous inputs
set_false_path -from [get_ports { CPU_RESETN UART_TXD_IN }]
set_false_path -to   [get_ports { UART_RXD_OUT LED[*] ETH_RSTN ETH_MDC }]

## Configuration
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property CFGBVS VCCO [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
