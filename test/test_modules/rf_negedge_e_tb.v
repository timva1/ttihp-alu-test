`default_nettype none
`timescale 1ns / 1ps

module rf_negedge_e_tb ();

  initial begin
    $dumpfile("waves/rf_negedge_e_tb.vcd");
    $dumpvars(0, rf_negedge_e_tb);
    #1;
  end

  reg         clk;
  reg         rst_n;
  reg  [4:0]  rs1_addr;
  reg  [4:0]  rs2_addr;
  reg  [4:0]  rd_addr;
  reg  [31:0] rd_data;
  reg         rd_wen;
  wire [31:0] rs1_data;
  wire [31:0] rs2_data;

  register_file #(
      .WRITE_EDGE ("negedge"),
      .USE_E_EXT  (1)
  ) rf_inst (
      .clk      (clk),
      .rst_n    (rst_n),
      .rs1_addr (rs1_addr),
      .rs2_addr (rs2_addr),
      .rd_addr  (rd_addr),
      .rd_data  (rd_data),
      .rd_wen   (rd_wen),
      .rs1_data (rs1_data),
      .rs2_data (rs2_data)
  );

endmodule
