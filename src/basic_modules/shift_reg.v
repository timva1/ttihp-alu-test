module shift_reg #(
    parameter WIDTH = 8,
    parameter DEPTH = 1,
    parameter EDGE = "posedge"  // "posedge" or "negedge"
) (
    input clk,
    input rst_n,
    input [WIDTH-1:0] data_in,
    output reg [WIDTH-1:0] data_out
);

    reg [WIDTH-1:0] shift_chain [DEPTH-1:0];
    reg [WIDTH-1:0] next_chain [DEPTH-1:0];
    reg [WIDTH-1:0] next_out;

    integer i;

    // Combinational: compute next shift-chain and output values
    always @(*) begin
        next_chain[0] = data_in;
        for (i = 1; i < DEPTH; i = i + 1)
            next_chain[i] = shift_chain[i-1];
        next_out = shift_chain[DEPTH-1];
    end

    // Sequential: only the clock edge differs between branches
    generate
        if (EDGE == "posedge") begin
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    for (i = 0; i < DEPTH; i = i + 1)
                        shift_chain[i] <= {WIDTH{1'b0}};
                    data_out <= {WIDTH{1'b0}};
                end else begin
                    for (i = 0; i < DEPTH; i = i + 1)
                        shift_chain[i] <= next_chain[i];
                    data_out <= next_out;
                end
            end
        end else begin
            always @(negedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    for (i = 0; i < DEPTH; i = i + 1)
                        shift_chain[i] <= {WIDTH{1'b0}};
                    data_out <= {WIDTH{1'b0}};
                end else begin
                    for (i = 0; i < DEPTH; i = i + 1)
                        shift_chain[i] <= next_chain[i];
                    data_out <= next_out;
                end
            end
        end
    endgenerate

endmodule