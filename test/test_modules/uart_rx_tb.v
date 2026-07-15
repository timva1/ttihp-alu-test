`default_nettype none
`timescale 1ns / 1ps

/* Testbench for the UART receiver (8N1, LSB-first serial->parallel).
   Two instances share one stimulus (clk/rst_n/div/rx) so the SPEED-mode
   majority-vote can be contrasted against AREA on identical input:
     - area  : OPT_GOAL="AREA"   (single mid-bit sample)
     - speed : OPT_GOAL="SPEED"  (3-sample majority vote)
   `rx` is driven by the Python UART transmitter model in
   test/common/uart_model.py; the cocotb test checks each instance's
   strobe/data/frame_err against the byte the model sent. */
module uart_rx_tb ();

  // Dump the signals to a VCD file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("waves/uart_rx_tb.vcd");
    $dumpvars(0, uart_rx_tb);
    #1;
  end

  reg         clk;
  reg         rst_n;
  reg  [15:0] div;
  reg         rx;

  // Per-instance output bundles. Suffix _a/_s = area/speed.
  wire        strobe_a, frame_err_a;
  wire [7:0]  data_a;
  wire        strobe_s, frame_err_s;
  wire [7:0]  data_s;

  uart_rx #(.OPT_GOAL("AREA")) area (
      .clk       (clk),
      .rst_n     (rst_n),
      .div       (div),
      .rx        (rx),
      .strobe    (strobe_a),
      .data      (data_a),
      .frame_err (frame_err_a)
  );

  uart_rx #(.OPT_GOAL("SPEED")) speed (
      .clk       (clk),
      .rst_n     (rst_n),
      .div       (div),
      .rx        (rx),
      .strobe    (strobe_s),
      .data      (data_s),
      .frame_err (frame_err_s)
  );

endmodule
