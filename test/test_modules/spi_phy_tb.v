`default_nettype none
`timescale 1ns / 1ps

/* Testbench for the SPI PHY (byte-granular mode-0 shifter + SCK divider).
   The module is parameterless — the divisor is a runtime input — so a single
   instance suffices; divisor coverage is driven at runtime by the cocotb test.
   `miso` is driven by the Python SPI-mode-0 slave model in test_spi_phy.py. */
module spi_phy_tb ();

  // Dump the signals to a VCD file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("waves/spi_phy_tb.vcd");
    $dumpvars(0, spi_phy_tb);
    #1;
  end

  reg        clk;
  reg        rst_n;
  reg  [7:0] div;
  reg        start;
  reg  [7:0] tx_byte;
  reg        miso;

  wire       busy;
  wire       done;
  wire [7:0] rx_byte;
  wire [7:0] eff_div;
  wire       sck;
  wire       mosi;

  spi_phy dut (
      .clk     (clk),
      .rst_n   (rst_n),
      .div     (div),
      .start   (start),
      .tx_byte (tx_byte),
      .busy    (busy),
      .done    (done),
      .rx_byte (rx_byte),
      .eff_div (eff_div),
      .sck     (sck),
      .mosi    (mosi),
      .miso    (miso)
  );

endmodule
