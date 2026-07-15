`default_nettype none
`timescale 1ns / 1ps

/* Testbench for the UART transmitter (8N1, LSB-first parallel->serial).
   A single uart_tx instance (no OPT_GOAL, so no AREA/SPEED variants): the
   cocotb test drives `start`/`data`/`div` and samples the serial `tx` output
   with the Python UART receiver model in test/common/uart_model.py, checking
   the byte read back and `busy` timing. */
module uart_tx_tb ();

  // Dump the signals to a VCD file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("waves/uart_tx_tb.vcd");
    $dumpvars(0, uart_tx_tb);
    #1;
  end

  reg         clk;
  reg         rst_n;
  reg  [15:0] div;
  reg         start;
  reg  [7:0]  data;

  wire        tx;
  wire        busy;

  uart_tx dut (
      .clk   (clk),
      .rst_n (rst_n),
      .div   (div),
      .start (start),
      .data  (data),
      .tx    (tx),
      .busy  (busy)
  );

endmodule
