// spi_phy.v — SPI shift register + SCK divider (mode 0, MSB first).
//
// Lowest layer of the SPI stack: a byte-granular, full-duplex SPI-mode-0
// shifter with an integrated SCK divider. Given one byte it clocks 8 bits out
// on MOSI (transitions on falling SCK) while sampling 8 bits in from MISO
// (rising SCK), then returns the received byte. It owns no command, address,
// chip-select, or burst state — those live in spi_mem_ctrl.v. CS_n is driven
// by the controller, which times its deselect from the exported eff_div.
//
// See docs/microarchitecture/spi_phy.md (refines docs/architecture.md
// Sections 5.2 and 8).

module spi_phy (
    input  wire       clk,
    input  wire       rst_n,    // async active-low

    // Control side (to/from spi_mem_ctrl)
    input  wire [7:0] div,      // SCK divisor from SPI_DIV; 0 clamped to 1
    input  wire       start,    // 1-cycle pulse: begin an 8-bit transfer (idle only)
    input  wire [7:0] tx_byte,  // MOSI data, latched at start
    output wire       busy,     // high while a transfer is in progress
    output reg        done,     // 1-cycle pulse at completion; rx_byte valid
    output reg  [7:0] rx_byte,  // MISO data captured this transfer
    output wire [7:0] eff_div,  // effective divisor (post 0->1 clamp)

    // SPI pins (mode 0: CPOL=0, CPHA=0)
    output reg        sck,
    output reg        mosi,
    input  wire       miso
);

    localparam IDLE  = 1'b0;
    localparam SHIFT = 1'b1;

    reg        state;
    reg  [7:0] div_cnt;    // half-period down-counter (sysclk cycles)
    reg  [7:0] eff_div_r;  // divisor latched for the running transfer
    reg  [4:0] half_cnt;   // half-period (SCK edge) counter: 16 per byte
    reg  [7:0] tx_shift;   // MSB drives MOSI, shifts left on falling edges
    reg  [7:0] rx_shift;   // captures MISO (MSB first) on rising edges

    // Divisor of 0 is reserved and treated as 1 (Section 5.2) — no setting can
    // stop the bus. Exported combinationally so the controller can reuse it for
    // deselect timing without re-implementing the clamp.
    assign eff_div = (div == 8'd0) ? 8'd1 : div;
    assign busy    = (state == SHIFT);

    // A half-period boundary (SCK edge) lands when the divider counter expires.
    wire tick = (div_cnt == 8'd0);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= IDLE;
            div_cnt   <= 8'd0;
            eff_div_r <= 8'd1;
            half_cnt  <= 5'd0;
            tx_shift  <= 8'd0;
            rx_shift  <= 8'd0;
            sck       <= 1'b0;
            mosi      <= 1'b0;
            done      <= 1'b0;
            rx_byte   <= 8'd0;
        end else begin
            done <= 1'b0;  // default: done is a single-cycle pulse

            case (state)
                IDLE: begin
                    // Idle outputs low (Section 5.2).
                    sck  <= 1'b0;
                    mosi <= 1'b0;
                    if (start) begin
                        state     <= SHIFT;
                        eff_div_r <= eff_div;            // latch clamped divisor
                        div_cnt   <= eff_div - 8'd1;     // first half-period
                        half_cnt  <= 5'd0;
                        tx_shift  <= tx_byte;
                        mosi      <= tx_byte[7];         // present bit7 with SCK
                                                        //   low (mode-0 setup)
                        rx_shift  <= 8'd0;
                    end
                end

                SHIFT: begin
                    if (tick) begin
                        div_cnt  <= eff_div_r - 8'd1;    // reload half-period
                        half_cnt <= half_cnt + 5'd1;
                        sck      <= ~sck;                // toggle at each boundary

                        if (~sck) begin
                            // Rising edge: sample MISO, MSB first.
                            rx_shift <= {rx_shift[6:0], miso};
                        end else begin
                            // Falling edge: shift next MOSI bit out.
                            tx_shift <= {tx_shift[6:0], 1'b0};
                            mosi     <= tx_shift[6];     // new MSB after the shift
                        end

                        // 16th (final) half-period: SCK returns low, byte done.
                        // The 8th sample landed on the previous (15th) tick, so
                        // rx_shift already holds the full received byte.
                        if (half_cnt == 5'd15) begin
                            state   <= IDLE;
                            sck     <= 1'b0;
                            mosi    <= 1'b0;
                            done    <= 1'b1;
                            rx_byte <= rx_shift;
                        end
                    end else begin
                        div_cnt <= div_cnt - 8'd1;
                    end
                end
            endcase
        end
    end

endmodule
