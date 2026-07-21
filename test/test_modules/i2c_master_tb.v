`default_nettype none
`timescale 1ns / 1ps

/* Testbench for the I2C master (single-primitive command engine).
   One i2c_master instance. The two open-drain lines are modeled as a wired-AND
   with pull-ups: the master drives via scl_oe/sda_oe, the Python I2CSlave model
   pulls via slave_scl_low/slave_sda_low, and a released line reads high.
   See test/test_i2c_master.py and docs/verification/i2c_master.md. */
module i2c_master_tb ();

  // Dump the signals to a VCD file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("waves/i2c_master_tb.vcd");
    $dumpvars(0, i2c_master_tb);
    #1;
  end

  reg         clk;
  reg         rst_n;
  // register-access port (driven like periph_regs)
  reg         access;
  reg  [3:0]  addr;
  reg         we;
  reg  [31:0] wdata;
  wire [31:0] rdata;

  // master open-drain enables
  wire        scl_oe;
  wire        sda_oe;

  // slave-side open-drain pulls, driven by the Python I2CSlave model
  reg         slave_scl_low;
  reg         slave_sda_low;

  // open-drain wired-AND with pull-ups: a line is high unless someone pulls low
  wire        scl_i = ~(scl_oe | slave_scl_low);
  wire        sda_i = ~(sda_oe | slave_sda_low);

  i2c_master dut (
      .clk    (clk),
      .rst_n  (rst_n),
      .access (access),
      .addr   (addr),
      .we     (we),
      .wdata  (wdata),
      .rdata  (rdata),
      .scl_i  (scl_i),
      .sda_i  (sda_i),
      .scl_oe (scl_oe),
      .sda_oe (sda_oe)
  );

endmodule
