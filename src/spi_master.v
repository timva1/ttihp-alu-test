/* simulation model for SPI master controller
 * 
 */

module spi_master #(
    parameter NUM_SLAVES = 1,
    parameter ACTIVE_LOW_CS = 1,

    localparam NUM_SLAVES_WIDTH = $clog2(NUM_SLAVES),
    localparam wire [1:0] TRANSFER = 2'b00,
    localparam wire [1:0] CHANGE_CLK = 2'b01
) (

    // CPU-side
    input wire clk,
    input wire rst_n,

    input wire [1:0] cmd,
    input wire [7:0] data_in,
    input wire data_in_valid,

    input wire [NUM_SLAVES_WIDTH-1:0] slave_select,
    input wire start_transfer,

    input wire data_out_ack,
    output reg [7:0] data_out,
    output reg data_out_valid,

    // SPI-side
    output reg spi_clk,
    output reg [NUM_SLAVES-1:0] spi_cs_n,
    output reg spi_mosi,
    input wire spi_miso
    
);
    // --- architecture selection messages --- //
`define AREA_OPT
`ifdef AREA_OPT
    // area-optimized: use 8-bit shift register to queue write data for spi transfer
    initial begin
        if ($test$plusargs("ARCH_INFO")) begin
            $display("ARCH INFO: Using area-optimized 8-bit shift register for SPI transfer (spi_master.v)");
        end
    end

    localparam SPI_SHIFT_REG_WIDTH = 8;

`else
    // speed-optimized: use 64-bit shift register
    initial begin
        if ($test$plusargs("ARCH_INFO")) begin
            $display("ARCH INFO: Using speed-optimized 64-bit shift register for SPI transfer (spi_master.v)");
        end
    end

    localparam SPI_SHIFT_REG_WIDTH = 64;

`endif

    reg [7:0] clk_divisor_reg;
    wire clk_div_clear;
    wire spi_clk_edge;
    wire spi_clk_enable;

    localparam wire [1:0] IDLE = 2'b00;
    localparam wire [1:0] TRANSFER = 2'b01;
    reg [1:0] spi_master_state, next_spi_master_state;
    wire transfer_done;

`ifdef AREA_OPT
    reg [3:0] shift_count, next_shift_count; // number of bits in shift register
    reg [7:0] output_shift_reg, next_output_shift_reg;

    reg [3:0] input_shift_count, next_input_shift_count; // number of bits in shift register
    reg [7:0] input_shift_reg, next_input_shift_reg;
`else
    reg [6:0] shift_count, next_shift_count; // number of bits in shift register
    reg [63:0] output_shift_reg, next_output_shift_reg;

    reg [6:0] input_shift_count, next_input_shift_count; // number of bits in shift register
    reg [63:0] input_shift_reg, next_input_shift_reg;
`endif

    // --- SPI clock generation --- //

    wire [7:0] _unused_counter;

    clk_div #(
        .WIDTH(8)
    ) clk_div_inst (
        .clk(clk),
        .rst_n(rst_n),
        .enable(spi_clk_enable),
        .clear(clk_div_clear),
        .divisor(clk_divisor_reg),
        .counter(_unused_counter),
        .clk_out(spi_clk),
        .clk_out_edge(spi_clk_edge)
    );

    // clock divider register
    always @ (posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            clk_divisor_reg <= 8'hFF; // slow clock
        end else begin
            if (cmd == CHANGE_CLK && data_in_valid) begin
                clk_divisor_reg <= data_in;
            end
        end
    end
    // clear clock divider count when changing clock divisor
    assign clk_div_clear = spi_master_state == IDLE;
    assign spi_clk_enable = spi_master_state == TRANSFER;

    // --- SPI master state machine --- //

    always @ (posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            spi_master_state <= IDLE;
        end else begin
            spi_master_state <= next_spi_master_state;
        end
    end

    always @ (*) begin
        next_spi_master_state = spi_master_state;
        if (spi_master_state == IDLE) begin
            if (start_transfer) begin
                next_spi_master_state = TRANSFER;
            end
        end else if (spi_master_state == TRANSFER) begin
            if (transfer_done) begin
                next_spi_master_state = IDLE;
            end
        end
    end

`ifdef AREA_OPT
    // using 8-bit shift reg
    assign transfer_done = (output_shift_count == 4'd0) && (spi_master_state == TRANSFER);
`else
    assign transfer_done = (output_shift_count == 7'd0) && (spi_master_state == TRANSFER);
`endif

    // --- SPI-side write logic --- //
    always @ (posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            shift_reg <= 'b0;
            output_shift_count <= 'd0;
        end else begin
            shift_reg <= next_shift_reg;
            output_shift_count <= next_output_shift_count;
        end
    end

    always @ (*) begin
        next_shift_reg = shift_reg;
        next_output_shift_count = output_shift_count;
        if (spi_master_state == IDLE) begin
            if (start_transfer) begin
                next_shift_reg[(SPI_SHIFT_REG_WIDTH - output_shift_count + (output_shift_count % 8)) -:8] = data_in;
                next_output_shift_count = output_shift_count + 'd8; // 8 bits to transfer
            end
        end else if (spi_master_state == TRANSFER) begin
            if (!spi_clk && spi_clk_edge) begin // data transition on falling edge of spi_clk
                for (int i = 0; i < SPI_SHIFT_REG_WIDTH; i++) begin
                    next_shift_reg[i + 1] = shift_reg[i]; // shift left
                end
                next_shift_reg[0] = 1'b0; // fill with 0
                next_output_shift_count = output_shift_count - 1;
            end
        end
    end

    // --- SPI-side read logic --- //
    always @ (posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            input_shift_reg <= 'b0;
            input_shift_count <= 'd0;
        end else begin
            input_shift_reg <= next_input_shift_reg;
            input_shift_count <= next_input_shift_count;
        end
    end

    always @ (*) begin
        next_input_shift_reg = input_shift_reg;
        next_input_shift_count = input_shift_count;
        if (spi_master_state == TRANSFER) begin
            if (spi_clk && spi_clk_edge) begin // data sampled on rising edge of spi_clk
                for (int i = 0; i < SPI_SHIFT_REG_WIDTH; i++) begin
                    next_input_shift_reg[i] = input_shift_reg[i + 1]; // shift right
                end
                next_input_shift_reg[SPI_SHIFT_REG_WIDTH - 1] = spi_miso; // read from MISO
                next_input_shift_count = input_shift_count + 1;
            end
        end else if (spi_master_state == IDLE) begin
            if (data_out_ack) begin
                next_input_shift_count = input_shift_count - 8;
            end
        end
    end

    assign data_out = input_shift_reg[input_shift_count -:8]; // MSB first
    assign data_out_valid = (spi_master_state == IDLE) && (input_shift_count > 0);

    // --- SPI output logic --- //
    integer s;
    generate for (s = 0; s < NUM_SLAVES; s++) begin : gen_spi_cs_n
        assign spi_cs_n[s] = (slave_select == s) ? (ACTIVE_LOW_CS ? 1'b0 : 1'b1) : (ACTIVE_LOW_CS ? 1'b1 : 1'b0);
    end endgenerate
    assign spi_mosi = shift_reg[SPI_SHIFT_REG_WIDTH - 1]; // MSB first



endmodule