`default_nettype none
`timescale 1ns / 1ps

/* This testbench just instantiates the module and makes some convenient wires
   that can be driven / tested by the cocotb test.py.
*/
module alu_tb ();

  // Dump the signals to a VCD file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("waves/alu_tb.vcd");
    $dumpvars(0, alu_tb);
    #1;
  end

  reg [3:0] alu_op;
  reg [31:0] alu_input_a;
  reg [31:0] alu_input_b;
  wire [31:0] alu_output;
  wire alu_output_zero;

  alu alu_inst (
      .alu_op(alu_op),
      .alu_input_a(alu_input_a),
      .alu_input_b(alu_input_b),
      .alu_output(alu_output),
      .alu_output_zero(alu_output_zero)
  );

endmodule
