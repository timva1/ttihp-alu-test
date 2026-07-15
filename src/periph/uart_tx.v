// uart_tx.v — UART transmitter (8N1, LSB-first).
//
// The transmit half of the UART: a pure parallel->serial transmitter. Given a
// byte and a single-cycle `start` pulse it emits start(0) -> d0..d7 (LSB first)
// -> stop(1) on `tx`, holding `busy` high for the whole frame. It owns no
// policy — the UART_DATA register and the "write while tx_busy is ignored,
// poll first" rule live in uart.v, which pulses `start` only when `busy` is low
// (mirrors the uart_rx / uart.v and spi_phy / spi_mem_ctrl splits).
//
// Bit period = (div+1) sysclk cycles (baud = sysclk/(div+1)). No OPT_GOAL:
// transmit is deterministic, with no sampling to harden and no area/speed
// tradeoff to expose.
//
// See docs/microarchitecture/uart_tx.md (refines docs/architecture.md
// Sections 6.1 and 8).

module uart_tx (
    input  wire        clk,
    input  wire        rst_n,     // async active-low
    input  wire [15:0] div,       // UART_DIV; bit period = (div+1) sysclk cycles
    input  wire        start,     // 1-cycle pulse: latch `data`, begin a frame (acted on only when idle)
    input  wire [7:0]  data,      // byte to send; sampled at accepted start; LSB-first on the wire
    output reg         tx,        // serial output, idle high (8N1)
    output reg         busy       // high from accepted start through end of stop bit
);

    localparam IDLE  = 2'd0;
    localparam START = 2'd1;
    localparam DATA  = 2'd2;
    localparam STOP  = 2'd3;

    reg  [1:0]  state;
    reg  [15:0] cnt;        // sysclk down-counter: bit-timing (full bit = div)
    reg  [2:0]  bit_idx;    // data-bit index 0..7
    reg  [7:0]  shift;      // transmit byte register, shifted right one bit/period

    wire        tick = (cnt == 16'd0);   // end of the current bit period

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state   <= IDLE;
            cnt     <= 16'd0;
            bit_idx <= 3'd0;
            shift   <= 8'd0;
            tx      <= 1'b1;             // idle high
            busy    <= 1'b0;
        end else begin
            case (state)
                IDLE: begin
                    tx   <= 1'b1;        // hold idle high
                    busy <= 1'b0;
                    // Accept a start request only when idle (self-gate; uart.v
                    // also gates on ~busy). Latch the byte, drive the start bit.
                    if (start) begin
                        shift   <= data;
                        tx      <= 1'b0;         // start bit
                        busy    <= 1'b1;
                        cnt     <= div;
                        bit_idx <= 3'd0;
                        state   <= START;
                    end
                end

                START: begin
                    if (tick) begin
                        tx      <= shift[0];     // present d0 (LSB first)
                        cnt     <= div;
                        bit_idx <= 3'd0;
                        state   <= DATA;
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                DATA: begin
                    if (tick) begin
                        // Shift right, feeding idle 1s in from the top. tx takes
                        // the next data bit: both nonblocking assignments read
                        // the pre-edge `shift`, so shift[1] here is the next bit
                        // (same idiom as uart_rx's data<={bit_sample,data[7:1]}).
                        shift <= {1'b1, shift[7:1]};
                        cnt   <= div;
                        if (bit_idx == 3'd7) begin
                            tx    <= 1'b1;       // stop bit
                            state <= STOP;
                        end else begin
                            tx      <= shift[1]; // next data bit
                            bit_idx <= bit_idx + 3'd1;
                        end
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                STOP: begin
                    if (tick) begin
                        tx    <= 1'b1;           // remain idle high
                        busy  <= 1'b0;
                        state <= IDLE;
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end
            endcase
        end
    end

endmodule
