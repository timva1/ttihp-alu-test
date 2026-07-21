// i2c_master.v — I2C master peripheral (single-primitive command engine).
//
// The whole I2C master in one module (no submodule split): it owns the four
// memory-mapped registers (I2C_CMD / I2C_STATUS / I2C_DATA / I2C_DIV), a
// bit-level SCL/SDA FSM that executes ONE I2C primitive per I2C_CMD write
// (START / WRITE / READ_ACK / READ_NACK / STOP), and open-drain control of the
// two TT bidirectional pins. Protocol variety (device addressing, the R/W bit,
// multi-byte transfers) lives in software, which composes a transaction from a
// sequence of primitives, polling `busy` between them.
//
// Bus side: the same thin single-cycle register-access strobe uart.v defined.
// periph_regs.v (not yet built) does the coarse "which peripheral" decode and
// forwards `access` + addr/we/wdata; i2c_master.v does the fine decode among
// its own four registers.
//
// Open-drain: project.v hardwires uio_out[6]/uio_out[7] to 0; this module only
// toggles the output-enables (scl_oe / sda_oe). drive-low = oe=1, release =
// oe=0 (external pull-up raises the line). SCL is open-drain too and the master
// samples scl_i after releasing SCL, which gives clock stretching for free.
//
// See docs/microarchitecture/i2c_master.md (refines docs/architecture.md
// Sections 6, 6.2, 7 and 8).

module i2c_master #(
    parameter [15:0] DIV_RST = 16'd124  // I2C_DIV_RST: SCL = sysclk/(4*(DIV+1))
                                        // 124 -> 50 MHz/(4*125) = 100 kHz (standard mode)
) (
    input  wire        clk,
    input  wire        rst_n,           // async active-low
    // register-access port (driven by periph_regs / SoC data port)
    input  wire        access,          // 1-cycle strobe: a bus access to an I2C reg this cycle
    input  wire [3:0]  addr,            // req_addr[3:0] -> 0x0 CMD, 0x4 STATUS, 0x8 DATA, 0xC DIV
    input  wire        we,              // 1 = write, 0 = read
    input  wire [31:0] wdata,           // store data: [7:0] byte, [10:8] command on CMD writes
    output reg  [31:0] rdata,           // read data for `addr` (combinational, pre-edge state)
    // open-drain I2C pins (mapped to uio[6]=SCL, uio[7]=SDA in project.v)
    input  wire        scl_i,           // sampled SCL line (enables clock stretching)
    input  wire        sda_i,           // sampled SDA line
    output wire        scl_oe,          // 1 = pull SCL low; 0 = release (external pull-up)
    output wire        sda_oe           // 1 = pull SDA low; 0 = release (external pull-up)
);

    // ---- register offsets within the I2C window (req_addr[3:0]) --------------
    localparam [3:0] ADDR_CMD    = 4'h0;   // W  (0x10)
    localparam [3:0] ADDR_STATUS = 4'h4;   // R  (0x14)
    localparam [3:0] ADDR_DATA   = 4'h8;   // R  (0x18)
    localparam [3:0] ADDR_DIV    = 4'hC;   // RW (0x1C)

    // ---- command codes (I2C_CMD[10:8]) --------------------------------------
    localparam [2:0] CMD_START     = 3'b000;
    localparam [2:0] CMD_WRITE     = 3'b001;
    localparam [2:0] CMD_READ_ACK  = 3'b010;
    localparam [2:0] CMD_READ_NACK = 3'b011;
    localparam [2:0] CMD_STOP      = 3'b100;

    // ---- top FSM states -----------------------------------------------------
    localparam [1:0] S_IDLE  = 2'd0;
    localparam [1:0] S_START = 2'd1;
    localparam [1:0] S_XFER  = 2'd2;   // WRITE / READ_ACK / READ_NACK (9 clocks)
    localparam [1:0] S_STOP  = 2'd3;

    // ---- quarter-period phases within one SCL bit ---------------------------
    localparam [1:0] P_LOW  = 2'd0;    // SCL low,  set SDA
    localparam [1:0] P_RISE = 2'd1;    // release SCL, wait high (clock stretching)
    localparam [1:0] P_HIGH = 2'd2;    // SCL high, sample SDA
    localparam [1:0] P_FALL = 2'd3;    // pull SCL low, end of bit

    // ---- state --------------------------------------------------------------
    reg [15:0] div;                    // I2C_DIV (SCL quarter-period divisor)
    reg [15:0] div_cnt;                // quarter-period down-counter
    reg [1:0]  state;
    reg [1:0]  phase;
    reg [3:0]  bit_cnt;                // 0..8 (8 data bits + 1 ack)
    reg [2:0]  cmd;                    // latched command for the in-flight primitive
    reg [7:0]  shreg;                  // byte shifted out (WRITE) / in (READ)
    reg        busy;                   // I2C_STATUS bit0
    reg        nack;                   // I2C_STATUS bit1 (cleared per command, set by WRITE ack)
    reg [7:0]  rx_data;                // I2C_DATA (last READ byte)
    reg        scl_oe_r;               // registered open-drain enables
    reg        sda_oe_r;
    assign scl_oe = scl_oe_r;
    assign sda_oe = sda_oe_r;

    // ---- timing: one `tick` every (div+1) sysclk cycles ---------------------
    wire tick = (div_cnt == 16'd0);
    // Advance a phase only after a full quarter-period, and in P_RISE only once
    // SCL has actually risen (scl_i) -> clock stretching.
    wire scl_ok  = (phase != P_RISE) | scl_i;
    wire advance = tick & scl_ok;

    // ---- command accept -----------------------------------------------------
    wire        cmd_write   = access & we & (addr == ADDR_CMD);
    wire        cmd_defined = (wdata[10:8] <= CMD_STOP);
    wire        accept_cmd  = cmd_write & ~busy & cmd_defined;   // write-while-busy ignored
    wire        is_read     = (cmd == CMD_READ_ACK) | (cmd == CMD_READ_NACK);

    // ---- SDA drive for the current XFER bit ---------------------------------
    // Constant across the 4 phases of a bit (data changes only while SCL low).
    reg sda_bit_oe;
    always @(*) begin
        if (bit_cnt <= 4'd7) begin
            // data bit: WRITE drives the MSB of shreg, READ releases to sample.
            sda_bit_oe = (cmd == CMD_WRITE) ? ~shreg[7] : 1'b0;
        end else begin
            // ack bit (bit 8): READ_ACK drives ACK (low), READ_NACK/WRITE release.
            sda_bit_oe = (cmd == CMD_READ_ACK) ? 1'b1 : 1'b0;
        end
    end

    // ---- open-drain drive for the current (state, phase) --------------------
    // Registered below; IDLE holds the last value so the bus keeps its state
    // between primitives (SCL held low mid-transaction, released after STOP).
    reg scl_drive, sda_drive;
    always @(*) begin
        case (state)
            // START (incl. repeated start): SCL low -> release (rise) -> pull
            // SDA low while SCL high (the start edge) -> SCL low.
            S_START: begin
                scl_drive = (phase == P_LOW) | (phase == P_FALL);
                sda_drive = (phase == P_HIGH) | (phase == P_FALL);
            end
            // STOP: SDA low, release SCL (rise), then release SDA while SCL high
            // (the stop edge) -> bus idle.
            S_STOP: begin
                scl_drive = (phase == P_LOW);
                sda_drive = (phase == P_LOW) | (phase == P_RISE);
            end
            // XFER: SCL low/rise/high/fall; SDA held at the bit's value.
            S_XFER: begin
                scl_drive = (phase == P_LOW) | (phase == P_FALL);
                sda_drive = sda_bit_oe;
            end
            // IDLE: hold (SCL low mid-transaction, or both released when idle).
            default: begin
                scl_drive = scl_oe_r;
                sda_drive = sda_oe_r;
            end
        endcase
    end

    // ---- read data (combinational, pre-edge state) --------------------------
    always @(*) begin
        case (addr)
            ADDR_STATUS: rdata = {30'b0, nack, busy};
            ADDR_DATA:   rdata = {24'b0, rx_data};
            ADDR_DIV:    rdata = {16'b0, div};
            default:     rdata = 32'b0;   // CMD (write-only) and undefined offsets
        endcase
    end

    // ---- sequential ---------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div      <= DIV_RST;
            div_cnt  <= 16'd0;
            state    <= S_IDLE;
            phase    <= P_LOW;
            bit_cnt  <= 4'd0;
            cmd      <= 3'd0;
            shreg    <= 8'd0;
            busy     <= 1'b0;
            nack     <= 1'b0;
            rx_data  <= 8'd0;
            scl_oe_r <= 1'b0;            // bus released (idle)
            sda_oe_r <= 1'b0;
        end else begin
            // registered open-drain outputs
            scl_oe_r <= scl_drive;
            sda_oe_r <= sda_drive;

            // I2C_DIV write (accepted any time; takes effect at next reload)
            if (access & we & (addr == ADDR_DIV))
                div <= wdata[15:0];

            if (accept_cmd) begin
                // Latch the primitive and enter its FSM branch. nack clears on
                // every accepted command; only a WRITE ack can then raise it.
                cmd     <= wdata[10:8];
                shreg   <= wdata[7:0];
                nack    <= 1'b0;
                busy    <= 1'b1;
                bit_cnt <= 4'd0;
                phase   <= P_LOW;
                div_cnt <= div;
                case (wdata[10:8])
                    CMD_START: state <= S_START;
                    CMD_STOP:  state <= S_STOP;
                    default:   state <= S_XFER;   // WRITE / READ_ACK / READ_NACK
                endcase
            end else if (state != S_IDLE) begin
                if (advance) begin
                    // Sample the line when leaving the HIGH phase.
                    if (phase == P_HIGH) begin
                        if (state == S_XFER && bit_cnt <= 4'd7 && is_read)
                            shreg <= {shreg[6:0], sda_i};       // shift in (MSB first)
                        if (state == S_XFER && bit_cnt == 4'd8 && cmd == CMD_WRITE)
                            nack <= sda_i;                       // slave ACK (1 = not acked)
                    end

                    if (phase == P_FALL) begin
                        // End of a bit: advance bit / finish primitive.
                        phase <= P_LOW;
                        case (state)
                            S_START, S_STOP: begin
                                state <= S_IDLE;
                                busy  <= 1'b0;
                            end
                            default: begin   // S_XFER
                                if (bit_cnt == 4'd8) begin
                                    if (is_read) rx_data <= shreg;   // received byte
                                    state <= S_IDLE;
                                    busy  <= 1'b0;
                                end else begin
                                    bit_cnt <= bit_cnt + 4'd1;
                                    if (cmd == CMD_WRITE)
                                        shreg <= shreg << 1;         // next bit to MSB
                                end
                            end
                        endcase
                    end else begin
                        phase <= phase + 2'd1;
                    end
                    div_cnt <= div;                 // reload for the next quarter-period
                end else if (tick) begin
                    // Quarter elapsed but SCL still held low by a slave (P_RISE
                    // stretch): keep waiting.
                    div_cnt <= div;
                end else begin
                    div_cnt <= div_cnt - 16'd1;
                end
            end
        end
    end

endmodule
