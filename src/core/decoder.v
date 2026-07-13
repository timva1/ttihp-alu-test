// decoder.v — combinational RV32E instruction decode.
//
// Pure combinational: instruction word in, control bundle out. No PC input,
// no state, no clock. One decoder serves both the MULTICYCLE and PIPELINED
// cores; the FSM/pipeline only sequences these outputs differently.
//
// See docs/microarchitecture/decoder.md (refines docs/architecture.md Section 4.1).

module decoder #(
    parameter RV32E          = 1,  // 1: x16-x31 references halt as illegal
    parameter ENABLE_SUBWORD = 1   // 0: LB/LH/LBU/LHU/SB/SH decode illegal
) (
    input  wire [31:0] instr,

    // Register fields (raw ISA fields; rf masks to 4 bits under USE_E_EXT)
    output reg  [4:0]  rs1_addr,
    output reg  [4:0]  rs2_addr,
    output reg  [4:0]  rd_addr,

    // Immediate (sign-extended, format selected by opcode)
    output reg  [31:0] imm,

    // Primary ALU pass
    output reg  [3:0]  alu_op,      // {mod, funct3} encoding (Section 4.1)
    output reg         alu_a_sel,   // A_RS1 / A_PC
    output reg         alu_b_sel,   // B_RS2 / B_IMM

    // Writeback
    output reg  [1:0]  result_sel,  // RES_ALU / RES_MEM / RES_PC4 / RES_IMM
    output reg         rd_wen,

    // Memory access
    output reg         mem_read,
    output reg         mem_write,
    output reg  [1:0]  mem_size,    // funct3[1:0]: 00=B 01=H 10=W
    output reg         mem_unsigned,// funct3[2]

    // Control transfer
    output reg         is_branch,
    output reg  [2:0]  branch_cond, // funct3
    output reg         is_jal,
    output reg         is_jalr,

    // Environment (halt causes, kept distinct from illegal)
    output reg         is_ecall,
    output reg         is_ebreak,

    output reg         illegal
);

    // ---- Opcodes (instr[6:0]) ----
    localparam [6:0] OP_LUI      = 7'b0110111;
    localparam [6:0] OP_AUIPC    = 7'b0010111;
    localparam [6:0] OP_JAL      = 7'b1101111;
    localparam [6:0] OP_JALR     = 7'b1100111;
    localparam [6:0] OP_BRANCH   = 7'b1100011;
    localparam [6:0] OP_LOAD     = 7'b0000011;
    localparam [6:0] OP_STORE    = 7'b0100011;
    localparam [6:0] OP_OP_IMM   = 7'b0010011;
    localparam [6:0] OP_OP       = 7'b0110011;
    localparam [6:0] OP_MISC_MEM = 7'b0001111;  // FENCE / FENCE.I
    localparam [6:0] OP_SYSTEM   = 7'b1110011;  // ECALL / EBREAK / CSR

    // ---- Output encodings ----
    localparam A_RS1 = 1'b0, A_PC  = 1'b1;
    localparam B_RS2 = 1'b0, B_IMM = 1'b1;
    localparam [1:0] RES_ALU = 2'd0, RES_MEM = 2'd1, RES_PC4 = 2'd2, RES_IMM = 2'd3;

    // ALU op {mod, funct3}
    localparam [3:0] ALU_ADD  = 4'b0000;
    localparam [3:0] ALU_SUB  = 4'b1000;
    localparam [3:0] ALU_SLT  = 4'b0010;
    localparam [3:0] ALU_SLTU = 4'b0011;

    // ---- Field extracts ----
    wire [6:0] opcode = instr[6:0];
    wire [2:0] funct3 = instr[14:12];
    wire [6:0] funct7 = instr[31:25];

    // ---- Immediate formats ----
    wire [31:0] imm_i = {{20{instr[31]}}, instr[31:20]};
    wire [31:0] imm_s = {{20{instr[31]}}, instr[31:25], instr[11:7]};
    wire [31:0] imm_b = {{20{instr[31]}}, instr[7], instr[30:25], instr[11:8], 1'b0};
    wire [31:0] imm_u = {instr[31:12], 12'b0};
    wire [31:0] imm_j = {{12{instr[31]}}, instr[19:12], instr[20], instr[30:21], 1'b0};

    // Which register fields the current instruction actually reads (drives the
    // RV32E x16-x31 range check; rd use is tracked by rd_wen).
    reg uses_rs1;
    reg uses_rs2;

    always @(*) begin
        // ---- Defaults (a legal NOP-shaped bundle) ----
        rs1_addr     = instr[19:15];
        rs2_addr     = instr[24:20];
        rd_addr      = instr[11:7];
        imm          = imm_i;
        alu_op       = ALU_ADD;
        alu_a_sel    = A_RS1;
        alu_b_sel    = B_IMM;
        result_sel   = RES_ALU;
        rd_wen       = 1'b0;
        mem_read     = 1'b0;
        mem_write    = 1'b0;
        mem_size     = funct3[1:0];
        mem_unsigned = funct3[2];
        is_branch    = 1'b0;
        branch_cond  = funct3;
        is_jal       = 1'b0;
        is_jalr      = 1'b0;
        is_ecall     = 1'b0;
        is_ebreak    = 1'b0;
        illegal      = 1'b0;
        uses_rs1     = 1'b0;
        uses_rs2     = 1'b0;

        case (opcode)
            // ---- LUI: rd = imm ----
            OP_LUI: begin
                imm        = imm_u;
                result_sel = RES_IMM;
                rd_wen     = 1'b1;
            end

            // ---- AUIPC: rd = PC + imm ----
            OP_AUIPC: begin
                imm        = imm_u;
                alu_a_sel  = A_PC;
                alu_b_sel  = B_IMM;
                alu_op     = ALU_ADD;
                result_sel = RES_ALU;
                rd_wen     = 1'b1;
            end

            // ---- JAL: target = PC + imm (ALU), rd = PC + 4 ----
            OP_JAL: begin
                imm        = imm_j;
                alu_a_sel  = A_PC;
                alu_b_sel  = B_IMM;
                alu_op     = ALU_ADD;
                result_sel = RES_PC4;
                rd_wen     = 1'b1;
                is_jal     = 1'b1;
            end

            // ---- JALR: target = rs1 + imm (bit0 cleared by control), rd = PC + 4 ----
            OP_JALR: begin
                imm        = imm_i;
                alu_a_sel  = A_RS1;
                alu_b_sel  = B_IMM;
                alu_op     = ALU_ADD;
                result_sel = RES_PC4;
                rd_wen     = 1'b1;
                is_jalr    = 1'b1;
                uses_rs1   = 1'b1;
                if (funct3 != 3'b000)
                    illegal = 1'b1;
            end

            // ---- BRANCH: primary pass is the comparison (rs1 ? rs2) ----
            OP_BRANCH: begin
                imm         = imm_b;
                alu_a_sel   = A_RS1;
                alu_b_sel   = B_RS2;
                is_branch   = 1'b1;
                branch_cond = funct3;
                uses_rs1    = 1'b1;
                uses_rs2    = 1'b1;
                case (funct3)
                    3'b000, 3'b001: alu_op = ALU_SUB;   // BEQ / BNE  (zero flag)
                    3'b100, 3'b101: alu_op = ALU_SLT;   // BLT / BGE  (signed)
                    3'b110, 3'b111: alu_op = ALU_SLTU;  // BLTU / BGEU (unsigned)
                    default:        illegal = 1'b1;     // 010 / 011 reserved
                endcase
            end

            // ---- LOAD: rd = mem[rs1 + imm] ----
            OP_LOAD: begin
                imm        = imm_i;
                alu_a_sel  = A_RS1;
                alu_b_sel  = B_IMM;
                alu_op     = ALU_ADD;
                result_sel = RES_MEM;
                rd_wen     = 1'b1;
                mem_read   = 1'b1;
                uses_rs1   = 1'b1;
                // Legal funct3: 000 LB, 001 LH, 010 LW, 100 LBU, 101 LHU
                if (funct3 == 3'b011 || funct3 == 3'b110 || funct3 == 3'b111)
                    illegal = 1'b1;
                if (!ENABLE_SUBWORD && funct3[1:0] != 2'b10)  // non-word => subword
                    illegal = 1'b1;
            end

            // ---- STORE: mem[rs1 + imm] = rs2 ----
            OP_STORE: begin
                imm        = imm_s;
                alu_a_sel  = A_RS1;
                alu_b_sel  = B_IMM;
                alu_op     = ALU_ADD;
                mem_write  = 1'b1;
                uses_rs1   = 1'b1;
                uses_rs2   = 1'b1;
                // Legal funct3: 000 SB, 001 SH, 010 SW
                if (funct3[2] || funct3 == 3'b011)
                    illegal = 1'b1;
                if (!ENABLE_SUBWORD && funct3[1:0] != 2'b10)
                    illegal = 1'b1;
            end

            // ---- OP-IMM: rd = rs1 op imm ----
            OP_OP_IMM: begin
                imm        = imm_i;
                alu_a_sel  = A_RS1;
                alu_b_sel  = B_IMM;
                result_sel = RES_ALU;
                rd_wen     = 1'b1;
                uses_rs1   = 1'b1;
                if (funct3 == 3'b001 || funct3 == 3'b101) begin
                    // Shift-immediate: mod = instr[30] (SRAI); validate funct7/shamt[5]
                    alu_op = {instr[30], funct3};
                    if (funct3 == 3'b001) begin
                        if (funct7 != 7'b0000000) illegal = 1'b1;              // SLLI
                    end else begin
                        if (funct7 != 7'b0000000 && funct7 != 7'b0100000)      // SRLI / SRAI
                            illegal = 1'b1;
                    end
                end else begin
                    alu_op = {1'b0, funct3};  // ADDI/SLTI/SLTIU/XORI/ORI/ANDI
                end
            end

            // ---- OP: rd = rs1 op rs2 ----
            OP_OP: begin
                alu_a_sel  = A_RS1;
                alu_b_sel  = B_RS2;
                result_sel = RES_ALU;
                rd_wen     = 1'b1;
                uses_rs1   = 1'b1;
                uses_rs2   = 1'b1;
                alu_op     = {instr[30], funct3};  // mod = funct7[5]
                // Legal funct7: 0000000 (all), or 0100000 for SUB/SRA only.
                // Catches M-ext (0000001) and stray funct7 as illegal.
                if (funct7 == 7'b0000000)
                    ;                                              // legal
                else if (funct7 == 7'b0100000 &&
                         (funct3 == 3'b000 || funct3 == 3'b101))
                    ;                                              // SUB / SRA
                else
                    illegal = 1'b1;
            end

            // ---- MISC-MEM: FENCE (NOP); FENCE.I illegal (no Zifencei in v1) ----
            OP_MISC_MEM: begin
                if (funct3 != 3'b000)
                    illegal = 1'b1;
                // funct3 == 000 (FENCE): falls through as a bubble.
            end

            // ---- SYSTEM: ECALL / EBREAK only (no Zicsr in v1) ----
            OP_SYSTEM: begin
                if (funct3 == 3'b000 && rs1_addr == 5'b0 && rd_addr == 5'b0) begin
                    case (instr[31:20])
                        12'h000: is_ecall  = 1'b1;
                        12'h001: is_ebreak = 1'b1;
                        default: illegal   = 1'b1;
                    endcase
                end else begin
                    illegal = 1'b1;  // CSR ops and malformed ECALL/EBREAK
                end
            end

            // ---- Unknown opcode (incl. instr[1:0] != 11) ----
            default: illegal = 1'b1;
        endcase

        // ---- RV32E: references to x16-x31 (bit 4 set) on a used field halt ----
        if (RV32E &&
            ((uses_rs1 && rs1_addr[4]) ||
             (uses_rs2 && rs2_addr[4]) ||
             (rd_wen   && rd_addr[4])))
            illegal = 1'b1;
    end

endmodule
