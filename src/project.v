/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_example (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // All output pins must be assigned. If not used, assign to 0.
  // assign uo_out  = ui_in + uio_in;  // Example: ou_out is the sum of ui_in and uio_in
  assign uio_out = 0;
  // assign uio_oe  = 0;

  wire [31:0] alu_input_a, alu_input_b, alu_output;
  wire [3:0] alu_op;
  wire [1:0] alu_output_zero;

  assign alu_input_a = {2{ui_in[7:4], ~ui_in[7:4], ui_in[3:0], ~ui_in[3:0]}};
  assign alu_input_b = {2{~ui_in[3:0], ui_in[7:4], ~ui_in[7:4], ui_in[3:0]}};
  // assign alu_oe = 8'b00000000;
  assign alu_op = uio_in[3:0];
  assign uo_out = alu_output[7:0];

  alu alu_inst (
    .alu_op(alu_op),
    .alu_input_a(alu_input_a),
    .alu_input_b(alu_input_b),
    .alu_output(alu_output),
    .alu_output_zero(alu_output_zero)
  );


  // List all unused inputs to prevent warnings
  wire _unused = &{alu_output[31:8], uio_in[7:4], ena, clk, rst_n, 1'b0};

endmodule
