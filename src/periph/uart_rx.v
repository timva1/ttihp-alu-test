// uart_rx.v — UART receiver (8N1, LSB-first).
//
// The receive half of the UART: a pure serial->parallel receiver. Given the
// async rx line it detects the start bit, samples 8 data bits at their centers
// (LSB first), checks the stop bit, and hands uart.v one completed byte plus a
// per-byte framing flag via a single-cycle `strobe`. It owns no buffer and no
// status bits — the RX holding buffer, rx_valid, rx_overrun, and the sticky
// rx_frame_err all live in uart.v (mirrors the spi_phy / spi_mem_ctrl split).
//
// Bit period = (div+1) sysclk cycles (baud = sysclk/(div+1)). OPT_GOAL selects
// the mid-bit sampling strategy only: "AREA" = one sample, "SPEED" = 3-sample
// majority vote for noise rejection (never changes the byte on a clean line).
//
// See docs/microarchitecture/uart_rx.md (refines docs/architecture.md
// Sections 6.1 and 8).

module uart_rx #(
    parameter OPT_GOAL = "AREA"     // "AREA" | "SPEED" — sampling strategy only
) (
    input  wire        clk,
    input  wire        rst_n,       // async active-low
    input  wire [15:0] div,         // UART_DIV; bit period = (div+1) sysclk cycles
    input  wire        rx,          // async serial input, idle high (8N1)
    output reg         strobe,      // 1-cycle pulse: data & frame_err valid
    output reg  [7:0]  data,        // received byte, LSB-first → data[0] first
    output reg         frame_err    // stop bit sampled != 1 for this byte
);

    localparam IDLE  = 2'd0;
    localparam START = 2'd1;
    localparam DATA  = 2'd2;
    localparam STOP  = 2'd3;

    // rx is asynchronous to sysclk — the one legitimate CDC point (Section 7's
    // "no CDC" is about internal signals). 2-FF synchronizer; rx_s is the
    // synchronized level used everywhere below, rx_s_d its 1-cycle-delayed copy
    // for falling-edge (start-bit) detection.
    reg  [1:0] rx_sync;
    reg        rx_s_d;
    wire       rx_s = rx_sync[1];

    reg  [1:0]  state;
    reg  [15:0] cnt;        // sysclk down-counter: bit-timing (full bit = div)
    reg  [2:0]  bit_idx;    // data-bit index 0..7

    // Mid-bit sample. "SPEED" votes rx_s at {mid-1, mid, mid+1}; "AREA" takes
    // the single sample at mid. The counter reaching 0 is the mid-bit instant,
    // so we capture the two flanking samples at cnt==1 (just before) and defer
    // the vote — implemented by keeping a small 3-tap shift of recent rx_s.
    reg  [2:0]  vote_sr;    // last 3 synchronized rx samples (SPEED only)
    wire        sample_area  = rx_s;
    wire        sample_speed = (vote_sr[0] & vote_sr[1]) |
                               (vote_sr[0] & vote_sr[2]) |
                               (vote_sr[1] & vote_sr[2]);
    wire        bit_sample   = (OPT_GOAL == "SPEED") ? sample_speed : sample_area;

    wire        tick = (cnt == 16'd0);   // bit-center instant

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_sync   <= 2'b11;          // idle high
            rx_s_d    <= 1'b1;
            state     <= IDLE;
            cnt       <= 16'd0;
            bit_idx   <= 3'd0;
            vote_sr   <= 3'b111;
            strobe    <= 1'b0;
            data      <= 8'd0;
            frame_err <= 1'b0;
        end else begin
            // Synchronizer + edge-detect delay + rolling vote window run always.
            rx_sync <= {rx_sync[0], rx};
            rx_s_d  <= rx_s;
            vote_sr <= {vote_sr[1:0], rx_s};

            strobe <= 1'b0;              // default: strobe is a 1-cycle pulse

            case (state)
                IDLE: begin
                    // Falling edge on the synchronized line = start bit.
                    if (rx_s_d & ~rx_s) begin
                        state <= START;
                        cnt   <= {1'b0, div[15:1]};   // div>>1: center of start
                    end
                end

                START: begin
                    if (tick) begin
                        if (~bit_sample) begin
                            // Still low at mid-start: valid start.
                            state   <= DATA;
                            cnt     <= div;           // full bit → center of d0
                            bit_idx <= 3'd0;
                        end else begin
                            state <= IDLE;            // false start (glitch)
                        end
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                DATA: begin
                    if (tick) begin
                        data    <= {bit_sample, data[7:1]};   // LSB first
                        cnt     <= div;               // full bit → next center
                        if (bit_idx == 3'd7)
                            state <= STOP;
                        bit_idx <= bit_idx + 3'd1;
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                STOP: begin
                    if (tick) begin
                        frame_err <= ~bit_sample;     // stop bit should be high
                        strobe    <= 1'b1;
                        state     <= IDLE;
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end
            endcase
        end
    end

endmodule
