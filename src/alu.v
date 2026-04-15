module alu (
    input wire [3:0] alu_op,
    input wire [31:0] alu_input_a,
    input wire [31:0] alu_input_b,
    output reg [31:0] alu_output,
    output reg alu_output_zero
);
    
    // naive implementation (no explicit resource sharing). To test how well logic is optimized
    always @(*) begin
        alu_output = 32'hx;
        case (alu_op)
            4'b0000: alu_output = alu_input_a | alu_input_b; // OR
            4'b0001: alu_output = alu_input_a & alu_input_b; // AND
            4'b0010: alu_output = alu_input_a ^ alu_input_b; // XOR
            4'b0100: alu_output = alu_input_a + alu_input_b; // ADD
            4'b0101: alu_output = alu_input_a - alu_input_b; // SUB
            4'b1000: alu_output = alu_input_a << alu_input_b[4:0]; // SLL
            4'b1010: alu_output = alu_input_a >> alu_input_b[4:0]; // SRL
            4'b1011: alu_output = $signed(alu_input_a) >>> alu_input_b[4:0]; // SRA
            4'b1100: begin // SLTU
                alu_output[31:1] = 31'b0;
                alu_output[0] = alu_input_a < alu_input_b;
            end // SLT
            4'b1101: begin
                alu_output[31:1] = 31'b0;
                alu_output[0] = $signed(alu_input_a) < $signed(alu_input_b);
            end
            default: alu_output = 32'hx;
        endcase
    end

    assign alu_output_zero = ~|alu_output;

endmodule