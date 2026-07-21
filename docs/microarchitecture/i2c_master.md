# `i2c_master.v` — I2C master peripheral

Refinement of `docs/architecture.md` §6.2 (the I2C master), §6 (the peripheral
register map, offsets `0x10`/`0x14`/`0x18`/`0x1C`), §7 (pinout: `uio[6]` = SCL,
`uio[7]` = SDA, open-drain), and §8 (which lists `i2c_master.v` as a single
new file). This document is the implementation contract for `i2c_master.v`.

Like `uart.md`, it also **defines the `i2c_master.v` ↔ bus (`periph_regs.v`)
boundary** — the same single-cycle register-access strobe `uart.v` established —
which the architecture leaves unspecified.

## Role

The whole I2C master in one module (no submodule split — see *Key decisions*).
It owns:

- the four memory-mapped registers `I2C_CMD` / `I2C_STATUS` / `I2C_DATA` /
  `I2C_DIV`;
- a bit-level SCL/SDA FSM that executes **one I2C primitive per `I2C_CMD`
  write** (START, WRITE, READ_ACK, READ_NACK, STOP);
- open-drain line control on the two TT bidirectional pins.

Per §6.2, protocol variety (device addressing, the R/W bit, multi-byte
transfers) lives in **software**: the hardware executes primitives, the driver
composes transactions. See *Software composition* below for the canonical
register-read sequence.

## Interface

```verilog
module i2c_master #(
    parameter [15:0] DIV_RST = 16'd124  // I2C_DIV_RST: SCL = sysclk/(4·(DIV+1))
                                        // 124 → 50 MHz/(4·125) = 100 kHz (standard mode)
) (
    input  wire        clk,
    input  wire        rst_n,        // async active-low
    // register-access port (driven by periph_regs / SoC data port)
    input  wire        access,       // 1-cycle strobe: a bus access to an I2C reg this cycle
    input  wire [3:0]  addr,         // req_addr[3:0] → 0x0 CMD, 0x4 STATUS, 0x8 DATA, 0xC DIV
    input  wire        we,           // 1 = write, 0 = read
    input  wire [31:0] wdata,        // store data, low byte = payload
    output reg  [31:0] rdata,        // read data for `addr` (combinational, pre-edge state)
    // open-drain I2C pins (mapped to uio[6]=SCL, uio[7]=SDA in project.v)
    input  wire        scl_i,        // sampled SCL line (enables clock stretching)
    input  wire        sda_i,        // sampled SDA line
    output wire        scl_oe,       // 1 = pull SCL low; 0 = release (external pull-up)
    output wire        sda_oe        // 1 = pull SDA low; 0 = release (external pull-up)
);
```

> **§3 spec gap:** the architecture's parameter table (§3) lists `UART_DIV_RST`
> and `SPI_CLK_DIV_RST` but no `I2C_DIV_RST`. This module adds `DIV_RST`
> (default `16'd124`, ~100 kHz at 50 MHz sysclk); §3 should be backfilled with
> the matching `I2C_DIV_RST` entry for consistency.

### Open-drain convention (§6.2)

Both lines are open-drain. `project.v` **hardwires `uio_out[6]` and
`uio_out[7]` to 0**; this module only ever toggles the output-enables:

- **drive low** = `*_oe = 1` (→ `uio_oe` = 1, `uio_out` = 0, line pulled to 0);
- **release** = `*_oe = 0` (→ `uio_oe` = 0, external pull-up raises the line).

SCL is open-drain too, and the master **samples `scl_i` after releasing SCL**;
if a slave is holding SCL low the master waits — this is **clock stretching**,
supported for free (see *Phase engine*). A non-stretching slave simply reads
back high immediately and the wait is zero cycles.

### The `i2c_master.v` ↔ bus boundary (defined here)

Identical contract to `uart.md`: `periph_regs.v` does the coarse decode (which
peripheral) and forwards a thin single-cycle register-access strobe;
`i2c_master.v` does the fine decode among its own four registers.

| Signal | Dir | Contract |
|---|---|---|
| `access` | in | Single-cycle "do it now" strobe = the accepted-request cycle for an I2C register. Registers never stall (`req_ready` always high at the `periph_regs` layer), so one `access` pulse per bus access. |
| `addr` | in | `req_addr[3:0]`. Only `0x0`/`0x4`/`0x8`/`0xC` are defined; other offsets in the window read 0 and ignore writes (§6). The coarse decode selects the I2C window (offsets `0x10`–`0x1C`); `addr[3:0]` is the within-window offset, so `0x10`→`0x0`, …, `0x1C`→`0xC`. |
| `we` | in | 1 = write, 0 = read. |
| `wdata` | in | Store data; `[7:0]` = data byte, `[10:8]` = command on `I2C_CMD` writes. |
| `rdata` | out | Combinational read data for `addr` from the **pre-edge** state. `periph_regs` registers it into `rsp_rdata` and forms `rsp_valid`. |

## Registers & internal state

| State | Reset | Purpose |
|---|---|---|
| `div[15:0]` | `DIV_RST` | `I2C_DIV`. SCL quarter-period divisor. A write to `0xC` updates it — **no clamp** (§6.2 specifies none; unlike SPI). A written 0 gives a quarter-period of one sysclk (fastest); software's responsibility. |
| `nack` | `0` | `I2C_STATUS` bit1. **Cleared at every accepted command**; **set by a WRITE** whose ack phase samples `sda_i == 1` (slave did not ACK). START/READ/STOP clear it and never set it. "Sticky until next command" = holds through STATUS polls until the next command accept. |
| `rx_data[7:0]` | `0` | `I2C_DATA`. Loaded from the shift register when a READ_ACK/READ_NACK completes. Plain register read (no side-effect). |
| `busy` | `0` | `I2C_STATUS` bit0. Set when a command is accepted; cleared when the FSM returns to `IDLE`. Gates command acceptance (write-while-busy ignored). |
| `cmd[2:0]` | `0` | Latched command for the in-flight primitive. |
| `shreg[7:0]` | `0` | Byte being shifted out (WRITE) or in (READ). |
| `bit_cnt[3:0]` | `0` | Bit index within a byte transfer, `0..8` (8 data bits + 1 ack). |
| `phase[1:0]` | `0` | Quarter-period phase within one SCL bit (LOW/RISE/HIGH/FALL). |
| `div_cnt[15:0]` | `0` | Down-counter from `div`; emits `tick` every `div+1` sysclk cycles. |
| `state` | `IDLE` | Top FSM state (below). |
| `scl_oe_r`, `sda_oe_r` | `0`, `0` | Registered open-drain enables; drive `scl_oe`/`sda_oe`. Reset released (bus idle). |

## Register map (fine decode on `addr`)

Read data is combinational from the current (pre-edge) state; side-effects are
registered at the `access` edge.

**Reads (`we == 0`):**

- `0x4` **STATUS**: `rdata = {30'b0, nack, busy}`. No side-effect.
- `0x8` **DATA**: `rdata = {24'b0, rx_data}`. No side-effect.
- `0xC` **DIV**: `rdata = {16'b0, div}`.
- `0x0` CMD (write-only) and other offsets: `rdata = 0`.

**Writes (`we == 1`):**

- `0x0` **CMD**: if `~busy` **and** `wdata[10:8]` is a defined command
  (`000`–`100`), latch `cmd <= wdata[10:8]`, `shreg <= wdata[7:0]`, clear
  `nack`, set `busy`, and enter the command's FSM branch. If `busy`, the write
  is **dropped** (poll-first policy, §6.2). Undefined commands (`101`–`111`)
  are ignored (no `busy`, no bus activity) — matching §6's "writes to undefined
  offsets are ignored."
- `0xC` **DIV**: `div <= wdata[15:0]`. A `div` write is accepted regardless of
  `busy` but only takes effect at the next `div_cnt` reload; software should
  set the divisor while idle.
- `0x4` STATUS, `0x8` DATA, all other offsets: ignored (read-only / undefined).

## Command set (`I2C_CMD[10:8]`, §6.2)

| Code | Command | Action |
|---|---|---|
| `000` | START | (Repeated) start condition |
| `001` | WRITE | Shift out `shreg` (MSB first), sample slave ACK → `nack` |
| `010` | READ_ACK | Shift in one byte → `I2C_DATA`, master drives ACK (SDA low) |
| `011` | READ_NACK | Shift in one byte → `I2C_DATA`, master drives NACK (SDA high) |
| `100` | STOP | Stop condition |

## Timing — quarter-period phase engine

`div_cnt` reloads from `div` and emits a one-cycle `tick` every `div+1` sysclk
cycles. Each SCL **bit** spans **4 ticks** → SCL period = `4·(div+1)` sysclk
(§6.2 exactly: "four divider ticks per SCL period: low, rise, high, fall").
SCL and SDA are generated as divided *enables* in the single `clk` domain — no
derived clocks, no CDC (§7 clocking).

The four phases of a bit:

| `phase` | SCL | Action |
|---|---|---|
| 0 **LOW** | pulled low (`scl_oe=1`) | drive SDA to this bit's value (data changes only while SCL is low) |
| 1 **RISE** | released (`scl_oe=0`) | **clock stretching:** advance to HIGH only on `tick & scl_i` — wait while a slave holds SCL low |
| 2 **HIGH** | high | **sample `sda_i`** (read data bit, or slave ACK) at this phase |
| 3 **FALL** | pulled low (`scl_oe=1`) | end of bit → next bit / next state |

START and STOP reuse the same 4-phase clock but manipulate **SDA** during the
HIGH phase (while SCL is high) instead of during LOW — that mid-high SDA
transition is exactly what makes a start/stop condition.

## Top FSM

`IDLE → { START | XFER | STOP } → IDLE`. `XFER` serves WRITE, READ_ACK, and
READ_NACK (they differ only in what SDA does on bits 0–7 and on the ack bit).

- **IDLE**: lines released, `busy = 0`. On an accepted `I2C_CMD` write, set
  `busy` and branch by `cmd`: START→`START`, WRITE/READ*→`XFER`, STOP→`STOP`.

- **START** (also repeated-start): release SDA (LOW), release SCL and wait for
  high honoring stretching (RISE), pull SDA low while SCL is high (HIGH), then
  pull SCL low (FALL). **Ends: SCL low, SDA low** — ready for `XFER` to drive
  bit 0. A repeated start (issued mid-transaction, SCL already low from the
  prior primitive) works identically; a fresh start from bus-idle (SCL already
  high) sees the SCL release as a no-op.

- **XFER** (9 clocks over `shreg` + `bit_cnt 0..8`):
  - **bits 0–7:** WRITE drives `sda_oe = ~shreg[7]` in LOW, then shifts
    `shreg <<= 1`; READ releases SDA and samples `sda_i` into `shreg[0]`
    (shifting) in HIGH.
  - **bit 8 (ack):** WRITE releases SDA and samples `sda_i` in HIGH →
    `nack <= sda_i`; READ_ACK drives SDA low (ACK), READ_NACK releases SDA
    (NACK, high via pull-up).
  - On completion a READ copies `shreg → rx_data`. **Ends: SCL low, SDA
    released** — ready for a repeated START or STOP.

- **STOP**: pull SDA low (LOW), release SCL and wait for high honoring
  stretching (RISE), then release SDA while SCL is high (HIGH) — the rising SDA
  is the stop condition. **Ends: both lines released (bus idle).**

### Inter-primitive line-state contract

Because software composes transactions from separate primitives, the line
state each primitive **leaves** must be the state the next one **expects**:

| Primitive | Leaves |
|---|---|
| START | SCL low, SDA low |
| WRITE / READ_ACK / READ_NACK | SCL low, SDA released |
| STOP | both released (bus idle) |
| (reset / IDLE) | both released (bus idle) |

This is what lets `START` generate a genuine **repeated** start from the end of
a WRITE (SCL low, then the START sequence releases and re-drives), with no STOP
in between — the case the register-read pattern below depends on.

## Software composition (worked example: read a device register)

The canonical "read N bytes from register `REG` of 7-bit device `DEV`" flow.
Every step is: write `I2C_CMD = (cmd<<8)|data`, then poll `I2C_STATUS.busy==0`
before the next. Commands: START=0, WRITE=1, READ_ACK=2, READ_NACK=3, STOP=4.

```
  cmd(START, 0)                       // (repeated) start
  cmd(WRITE, (DEV<<1)|0)              // device address + W bit
      → poll busy; check STATUS.nack  // device present & ACKed?
  cmd(WRITE, REG)                     // register pointer byte
      → poll busy; check STATUS.nack
  cmd(START, 0)                       // REPEATED start (no STOP between)
  cmd(WRITE, (DEV<<1)|1)              // device address + R bit
      → poll busy; check STATUS.nack
  cmd(READ_ACK, 0);  b0 = I2C_DATA    // byte 0, master ACKs → more coming
  cmd(READ_ACK, 0);  b1 = I2C_DATA    // byte 1
  cmd(READ_ACK, 0);  b2 = I2C_DATA    // byte 2
  cmd(READ_NACK, 0); b3 = I2C_DATA    // byte 3 (last), master NACKs
  cmd(STOP, 0)
  word = assemble(b0,b1,b2,b3)        // MSB/LSB order per the chip's datasheet
```

Notes this makes explicit:

- **Repeated-START is a first-class primitive** — "write reg pointer, then
  read" needs a START without an intervening STOP; the inter-primitive contract
  above delivers it.
- **READ_ACK vs READ_NACK is the driver's call** — only software knows it's on
  the last byte (ACK all but the last, NACK the last). The hardware has no byte
  count (§6.2 "protocol variety in software where it's free").
- **`I2C_DATA` holds only the most recent byte** — read it out *between* READ
  primitives (the busy-poll loop is the natural place). No RX FIFO.
- **`nack` after each WRITE** is the address/register error surface (missing or
  wrong device, or a chip that NAKs the pointer). Reads never set `nack` — the
  master drives those ack bits.

## Key decisions (+ rejected alternatives)

1. **One primitive per `I2C_CMD` write; software composes transactions**
   (§6.2). Keeps the hardware a small FSM and puts addressing / R-W-bit /
   byte-count logic in a driver, where it costs no silicon.
   *Rejected:* a fixed-function "read N bytes from register R" engine — larger,
   less flexible, and it would have to embed protocol policy (below).

2. **`nack` cleared at every accepted command, set only by WRITE.** Chosen with
   the user over "only WRITE updates nack": a fresh command starts from a clean
   `nack`, and only the WRITE ack phase can raise it, so `STATUS.nack` after a
   WRITE unambiguously reports *that* WRITE's ack. Reset value 0.

3. **Clock stretching for free** by sampling `scl_i` in the RISE phase (§6.2).
   No config bit — a non-stretching slave reads back high immediately, so the
   wait is zero cycles and correctness is unchanged.

4. **No submodule split.** Unlike UART (`uart_tx`/`uart_rx`/`uart.v`) and SPI
   (`spi_phy`/`spi_mem_ctrl`), the bit FSM and register file are small and
   tightly coupled and there is no separately-reusable "phy" worth a boundary.
   §8 lists `i2c_master.v` as a single file; this plan honors that.

5. **No command / read-data FIFO in v1 — and not a parameter.** *Rejected*
   after analysis:
   - **Marginal benefit at baseline.** At 100 kHz, one byte+ack ≈ 90 µs
     (~4500 sysclk @ 50 MHz); the software gap a FIFO removes is a few poll
     iterations (single-digit µs) and — because I2C lets the master hold SCL
     low between bits indefinitely — completely benign. A FIFO would only pay
     under fast-mode-plus (400 kHz–1 MHz) *and* large streaming reads.
   - **The real cost is semantics, not flops.** A command FIFO would drag
     protocol policy back into hardware: a NACK on the address WRITE must abort
     the queued READ/STOP primitives, so the FSM would need a "stop-on-NACK +
     flush" policy — exactly what decision #1 keeps in software. It also splits
     `busy` into `busy`/`full` and changes the write-gate rule.
   - **Parameterizing isn't free here.** A knob becomes a §9.3 config-matrix
     axis and a `SYS_ID` fingerprint bit (§6), and unlike the architecture's
     other parameters it is **not** architecturally-invariant (it changes
     observable timing *and* NACK-abort behavior) — a genuine second variant to
     verify.
   - **Forward-compatible, so deferrable.** The register map need not change to
     add this later (a `full`-vs-`busy` status split plus a FIFO slots in
     without touching offsets or the primitive encoding) — the same
     forward-compatibility argument §6.1 makes for adding UART FIFOs later. If a
     real fast-mode + streaming workload later shows the inter-primitive gap
     hurting, that is the moment to design the NACK-flush policy deliberately.

## Pin mapping (in `project.v`, for reference — not this module)

| Pin | Direction | Source |
|---|---|---|
| `uio[6]` (SCL) | bidir | `uio_out[6] = 0`, `uio_oe[6] = scl_oe`; `scl_i = uio_in[6]` |
| `uio[7]` (SDA) | bidir | `uio_out[7] = 0`, `uio_oe[7] = sda_oe`; `sda_i = uio_in[7]` |

Both need external pull-ups (§7 pinout).
