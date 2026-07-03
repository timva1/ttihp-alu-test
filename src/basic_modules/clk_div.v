// clk_out toggles every `divisor` cycles of clk, so its period is
// 2*divisor clk cycles (i.e. f_out = f_clk / (2*divisor)).
module clk_div #(
    parameter WIDTH = 8
) (
    input  wire             clk,
    input  wire             rst_n,
    input  wire             clear,
    input  wire             enable,
    input  wire [WIDTH-1:0] divisor,
    output reg              clk_out,
    output reg  [WIDTH-1:0] counter,
    output wire             clk_out_edge
);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            counter <= 0;
            clk_out <= 0;
        end else if (clear) begin
            counter <= 0;
            clk_out <= 0;
        end
        else if (counter == (divisor - 1) && enable) begin
            counter <= 0;
            clk_out <= ~clk_out;
        end else begin
            counter <= counter + 1;
        end
    end

    assign clk_out_edge = (counter == (divisor - 1)) && enable;

endmodule
