module register_file #(
    parameter WRITE_EDGE = "posedge",  // "posedge" or "negedge"
    parameter USE_E_EXT = 0             // 0 for RV32I (32 registers), 1 for RV32E (16 registers)
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [4:0]  rs1_addr,
    input  wire [4:0]  rs2_addr,
    input  wire [4:0]  rd_addr,
    input  wire [31:0] rd_data,
    input  wire        rd_wen,
    output wire [31:0] rs1_data,
    output wire [31:0] rs2_data
);

    // Determine number of registers based on E extension
    localparam NUM_REGS = USE_E_EXT ? 16 : 32;
    localparam ADDR_WIDTH = USE_E_EXT ? 4 : 5;

    // Register file memory
    reg [31:0] regs [0:NUM_REGS-1];

    // Initialize all registers to zero
    integer i;
    initial begin
        for (i = 0; i < NUM_REGS; i = i + 1) begin
            regs[i] = 32'b0;
        end
    end


    generate
        if (WRITE_EDGE == "negedge") begin
            always @(negedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    for (i = 0; i < NUM_REGS; i = i + 1) begin
                        regs[i] <= 32'b0;
                    end
                end else if (rd_wen && rd_addr[ADDR_WIDTH-1:0] != {ADDR_WIDTH{1'b0}}) begin
                    regs[rd_addr[ADDR_WIDTH-1:0]] <= rd_data;
                end
            end

            assign rs1_data = regs[rs1_addr[ADDR_WIDTH-1:0]];
            assign rs2_data = regs[rs2_addr[ADDR_WIDTH-1:0]];
        end else begin  // Default to posedge
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    for (i = 0; i < NUM_REGS; i = i + 1) begin
                        regs[i] <= 32'b0;
                    end
                end else if (rd_wen && rd_addr[ADDR_WIDTH-1:0] != {ADDR_WIDTH{1'b0}}) begin
                    regs[rd_addr[ADDR_WIDTH-1:0]] <= rd_data;
                end
            end

            assign rs1_data = rd_wen && (rs1_addr[ADDR_WIDTH-1:0] == rd_addr[ADDR_WIDTH-1:0]) ? rd_data : regs[rs1_addr[ADDR_WIDTH-1:0]];
            assign rs2_data = rd_wen && (rs2_addr[ADDR_WIDTH-1:0] == rd_addr[ADDR_WIDTH-1:0]) ? rd_data : regs[rs2_addr[ADDR_WIDTH-1:0]];
        end
    endgenerate

endmodule
