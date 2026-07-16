// uart.v — UART register/buffer glue (8N1).
//
// The policy half of the UART: it owns the three memory-mapped registers
// (UART_DATA / UART_STATUS / UART_DIV), the single-byte RX holding buffer, and
// the rx_valid / rx_overrun / rx_frame_err status bits, and instantiates the
// two pure datapath halves uart_tx (parallel->serial) and uart_rx
// (serial->parallel). Datapath lives in the halves, policy lives here — the
// third instance of the spi_phy/spi_mem_ctrl and uart_tx+uart_rx/uart.v split.
//
// Bus side: a thin single-cycle register-access strobe. periph_regs.v (not yet
// built) does the coarse "which peripheral" decode and forwards `access` +
// addr/we/wdata; uart.v does the fine decode among its own three registers and
// applies all read side effects (pop RX, clear sticky status). A strobe (not a
// plain combinational read port) is required because those side effects must
// fire exactly once per bus read.
//
// See docs/microarchitecture/uart.md (refines docs/architecture.md
// Sections 6, 6.1 and 8).

module uart #(
    parameter OPT_GOAL = "AREA",        // forwarded to uart_rx (sampling strategy only)
    parameter [15:0] DIV_RST = 16'd433  // UART_DIV_RST: reset baud divisor
) (
    input  wire        clk,
    input  wire        rst_n,           // async active-low
    // register-access port (driven by periph_regs / SoC data port)
    input  wire        access,          // 1-cycle strobe: a bus access to a UART reg this cycle
    input  wire [3:0]  addr,            // req_addr[3:0] → 0x0 DATA, 0x4 STATUS, 0x8 DIV
    input  wire        we,              // 1 = write, 0 = read
    input  wire [31:0] wdata,           // store data, low byte = payload
    output reg  [31:0] rdata,           // read data for `addr` (combinational in pre-edge state)
    // serial pins (mapped to uo_out[4] / ui_in[3] in project.v)
    output wire        tx,
    input  wire        rx,
    // debug (drives dbg_rx_valid on uo_out[6])
    output wire        rx_valid_o
);

    // Register offsets within the UART window (req_addr[3:0]).
    localparam ADDR_DATA   = 4'h0;
    localparam ADDR_STATUS = 4'h4;
    localparam ADDR_DIV    = 4'h8;

    // ---- state owned by the glue --------------------------------------------
    reg  [15:0] div;           // UART_DIV, feeds both halves
    reg  [7:0]  rx_buf;        // RX holding buffer
    reg         rx_valid;      // buffer occupied
    reg         rx_overrun;    // sticky: byte arrived while buffer full
    reg         rx_frame_err;  // sticky: bad stop bit on an accepted byte

    // ---- datapath halves ----------------------------------------------------
    wire        tx_busy;
    wire        rx_strobe;     // 1-cycle: rx_data / rx_frame_err_w valid
    wire [7:0]  rx_data;
    wire        rx_frame_err_w;

    // A UART_DATA write while ~tx_busy launches a frame; uart_tx latches `data`
    // at the accepted `start`, and `wdata` is stable during the access cycle.
    wire tx_start = access & we & (addr == ADDR_DATA) & ~tx_busy;

    uart_tx u_tx (
        .clk   (clk),
        .rst_n (rst_n),
        .div   (div),
        .start (tx_start),
        .data  (wdata[7:0]),
        .tx    (tx),
        .busy  (tx_busy)
    );

    uart_rx #(.OPT_GOAL(OPT_GOAL)) u_rx (
        .clk       (clk),
        .rst_n     (rst_n),
        .div       (div),
        .rx        (rx),
        .strobe    (rx_strobe),
        .data      (rx_data),
        .frame_err (rx_frame_err_w)
    );

    assign rx_valid_o = rx_valid;

    // ---- access decode ------------------------------------------------------
    wire acc_read  = access & ~we;
    wire acc_write = access &  we;
    wire rd_data   = acc_read  & (addr == ADDR_DATA);    // pops the RX buffer
    wire rd_status = acc_read  & (addr == ADDR_STATUS);  // clears sticky bits
    wire wr_div    = acc_write & (addr == ADDR_DIV);

    // Buffer-empty *after* this cycle's pop: a UART_DATA read draining the
    // buffer this cycle frees it for an arriving byte (corner case 4a — no
    // spurious overrun on a busy poll loop).
    wire buf_free_after_pop = ~rx_valid | rd_data;

    // Classify an arriving byte: accepted into the buffer, or dropped (overrun).
    wire rx_accept = rx_strobe &  buf_free_after_pop;
    wire rx_drop   = rx_strobe & ~buf_free_after_pop;

    // Sticky-bit next values, "set wins over clear-on-read" (corner case 4b):
    //   next = set_event ? 1 : (clear_event ? 0 : current)
    // overrun sets on a dropped byte; frame_err sets only on an *accepted* bad
    // byte; both clear on a STATUS read.
    wire rx_overrun_n   = rx_drop                     ? 1'b1
                        : rd_status                     ? 1'b0 : rx_overrun;
    wire rx_frame_err_n = (rx_accept & rx_frame_err_w) ? 1'b1
                        : rd_status                    ? 1'b0 : rx_frame_err;
    // valid: any arriving byte leaves the buffer occupied; otherwise a
    // UART_DATA read pops it empty.
    wire rx_valid_n     = rx_strobe ? 1'b1 : (rd_data ? 1'b0 : rx_valid);

    // ---- read data (combinational, pre-edge state) --------------------------
    always @(*) begin
        case (addr)
            ADDR_DATA:   rdata = rx_valid ? {24'b0, rx_buf} : 32'b0;
            ADDR_STATUS: rdata = {28'b0, rx_frame_err, rx_overrun, rx_valid, tx_busy};
            ADDR_DIV:    rdata = {16'b0, div};
            default:     rdata = 32'b0;
        endcase
    end

    // ---- registered side effects --------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div          <= DIV_RST;
            rx_buf       <= 8'd0;
            rx_valid     <= 1'b0;
            rx_overrun   <= 1'b0;
            rx_frame_err <= 1'b0;
        end else begin
            // UART_DIV write.
            if (wr_div)
                div <= wdata[15:0];

            // RX buffer: an accepted byte lands; otherwise the buffer holds.
            if (rx_accept)
                rx_buf <= rx_data;

            // RX status (next values computed above with set-wins priority).
            rx_valid     <= rx_valid_n;
            rx_overrun   <= rx_overrun_n;
            rx_frame_err <= rx_frame_err_n;
        end
    end

endmodule
