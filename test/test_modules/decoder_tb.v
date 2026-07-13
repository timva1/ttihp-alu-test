`default_nettype none
`timescale 1ns / 1ps

/* Testbench for the combinational RV32E decoder.
   One shared `instr` input feeds three parameter configurations so both
   parameter axes (RV32E, ENABLE_SUBWORD) are exercised in a single run:
     - dflt  : RV32E=1, ENABLE_SUBWORD=1  (baseline / tape-out build)
     - rv32i : RV32E=0                    (x16-x31 legal)
     - nosub : ENABLE_SUBWORD=0           (subword loads/stores illegal)
   The cocotb test drives `instr` and checks each instance against a Python
   golden model. */
module decoder_tb ();

  // Dump the signals to a VCD file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("waves/decoder_tb.vcd");
    $dumpvars(0, decoder_tb);
    #1;
  end

  reg [31:0] instr;

  // Per-instance output bundles. Suffix _d/_i/_n = dflt/rv32i/nosub.
  wire [4:0]  rs1_addr_d, rs2_addr_d, rd_addr_d;
  wire [31:0] imm_d;
  wire [3:0]  alu_op_d;
  wire        alu_a_sel_d, alu_b_sel_d;
  wire [1:0]  result_sel_d;
  wire        rd_wen_d, mem_read_d, mem_write_d;
  wire [1:0]  mem_size_d;
  wire        mem_unsigned_d, is_branch_d;
  wire [2:0]  branch_cond_d;
  wire        is_jal_d, is_jalr_d, is_ecall_d, is_ebreak_d, illegal_d;

  wire [4:0]  rs1_addr_i, rs2_addr_i, rd_addr_i;
  wire [31:0] imm_i;
  wire [3:0]  alu_op_i;
  wire        alu_a_sel_i, alu_b_sel_i;
  wire [1:0]  result_sel_i;
  wire        rd_wen_i, mem_read_i, mem_write_i;
  wire [1:0]  mem_size_i;
  wire        mem_unsigned_i, is_branch_i;
  wire [2:0]  branch_cond_i;
  wire        is_jal_i, is_jalr_i, is_ecall_i, is_ebreak_i, illegal_i;

  wire [4:0]  rs1_addr_n, rs2_addr_n, rd_addr_n;
  wire [31:0] imm_n;
  wire [3:0]  alu_op_n;
  wire        alu_a_sel_n, alu_b_sel_n;
  wire [1:0]  result_sel_n;
  wire        rd_wen_n, mem_read_n, mem_write_n;
  wire [1:0]  mem_size_n;
  wire        mem_unsigned_n, is_branch_n;
  wire [2:0]  branch_cond_n;
  wire        is_jal_n, is_jalr_n, is_ecall_n, is_ebreak_n, illegal_n;

  decoder #(.RV32E(1), .ENABLE_SUBWORD(1)) dflt (
      .instr(instr),
      .rs1_addr(rs1_addr_d), .rs2_addr(rs2_addr_d), .rd_addr(rd_addr_d),
      .imm(imm_d), .alu_op(alu_op_d),
      .alu_a_sel(alu_a_sel_d), .alu_b_sel(alu_b_sel_d),
      .result_sel(result_sel_d), .rd_wen(rd_wen_d),
      .mem_read(mem_read_d), .mem_write(mem_write_d),
      .mem_size(mem_size_d), .mem_unsigned(mem_unsigned_d),
      .is_branch(is_branch_d), .branch_cond(branch_cond_d),
      .is_jal(is_jal_d), .is_jalr(is_jalr_d),
      .is_ecall(is_ecall_d), .is_ebreak(is_ebreak_d), .illegal(illegal_d)
  );

  decoder #(.RV32E(0), .ENABLE_SUBWORD(1)) rv32i (
      .instr(instr),
      .rs1_addr(rs1_addr_i), .rs2_addr(rs2_addr_i), .rd_addr(rd_addr_i),
      .imm(imm_i), .alu_op(alu_op_i),
      .alu_a_sel(alu_a_sel_i), .alu_b_sel(alu_b_sel_i),
      .result_sel(result_sel_i), .rd_wen(rd_wen_i),
      .mem_read(mem_read_i), .mem_write(mem_write_i),
      .mem_size(mem_size_i), .mem_unsigned(mem_unsigned_i),
      .is_branch(is_branch_i), .branch_cond(branch_cond_i),
      .is_jal(is_jal_i), .is_jalr(is_jalr_i),
      .is_ecall(is_ecall_i), .is_ebreak(is_ebreak_i), .illegal(illegal_i)
  );

  decoder #(.RV32E(1), .ENABLE_SUBWORD(0)) nosub (
      .instr(instr),
      .rs1_addr(rs1_addr_n), .rs2_addr(rs2_addr_n), .rd_addr(rd_addr_n),
      .imm(imm_n), .alu_op(alu_op_n),
      .alu_a_sel(alu_a_sel_n), .alu_b_sel(alu_b_sel_n),
      .result_sel(result_sel_n), .rd_wen(rd_wen_n),
      .mem_read(mem_read_n), .mem_write(mem_write_n),
      .mem_size(mem_size_n), .mem_unsigned(mem_unsigned_n),
      .is_branch(is_branch_n), .branch_cond(branch_cond_n),
      .is_jal(is_jal_n), .is_jalr(is_jalr_n),
      .is_ecall(is_ecall_n), .is_ebreak(is_ebreak_n), .illegal(illegal_n)
  );

endmodule
