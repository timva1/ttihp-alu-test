# `uart.v` — UART register/buffer glue

Refinement of `docs/architecture.md` §6.1 (the UART peripheral), §6 (the
peripheral register map, offsets `0x00`/`0x04`/`0x08`), and §8 (which splits the
UART into `uart.v` = register/buffer glue, `uart_tx.v` = transmitter,
`uart_rx.v` = receiver). This document is the implementation contract for
`uart.v`. It also **defines the `uart.v` ↔ bus (`periph_regs.v`) boundary**,
which the architecture leaves unspecified — the same way `uart_tx.md` defined
the `uart_tx ↔ uart.v` boundary.

## Role

The register/buffer glue of the UART. It owns all *policy* that §6.1 assigns to
the UART as a whole and instantiates the two pure datapath halves:

- the three memory-mapped registers `UART_DATA` / `UART_STATUS` / `UART_DIV`;
- the single-byte RX holding buffer and the `rx_valid` / `rx_overrun` /
  `rx_frame_err` status bits;
- the "write while `tx_busy` is ignored — poll first" transmit rule.

This is the third instance of the established split (`spi_phy` /
`spi_mem_ctrl`, and `uart_tx` + `uart_rx` / `uart.v`): **datapath lives in the
halves, policy lives here.** `uart_tx` shifts a byte out, `uart_rx` shifts a
byte in; `uart.v` owns the buffers, status semantics, and bus interface.

## Interface

```verilog
module uart #(
    parameter OPT_GOAL = "AREA",        // forwarded to uart_rx (sampling strategy only)
    parameter [15:0] DIV_RST = 16'd433  // UART_DIV_RST (§3): reset baud divisor
) (
    input  wire        clk,
    input  wire        rst_n,           // async active-low
    // register-access port (driven by periph_regs / SoC data port)
    input  wire        access,          // 1-cycle strobe: a bus access to a UART reg occurs this cycle
    input  wire [3:0]  addr,            // req_addr[3:0] → 0x0 DATA, 0x4 STATUS, 0x8 DIV
    input  wire        we,              // 1 = write, 0 = read
    input  wire [31:0] wdata,           // store data, low byte = payload
    output reg  [31:0] rdata,           // read data for `addr` (combinational in current state + addr)
    // serial pins (mapped to uo_out[4] / ui_in[3] in project.v)
    output wire        tx,
    input  wire        rx,
    // debug (drives dbg_rx_valid on uo_out[6], §7)
    output wire        rx_valid_o
);
```

### The `uart.v` ↔ bus boundary (defined here)

`periph_regs.v` (§8, "register decode") is not built yet, so building `uart.v`
first pins down this interface. **`periph_regs.v` does the coarse decode (which
peripheral) and forwards a thin single-cycle register-access strobe; `uart.v`
does the fine decode among its own three registers and applies all read
side-effects.**

| Signal | Dir | Contract |
|---|---|---|
| `access` | in | Single-cycle "do it now" strobe = the accepted-request cycle for a UART register. `req_ready` is always high at the `periph_regs` layer (registers never stall), so there is one `access` pulse per bus access. |
| `addr` | in | `req_addr[3:0]`. Only `0x0`/`0x4`/`0x8` are defined; other offsets in the window read 0 and ignore writes (§6). |
| `we` | in | 1 = write, 0 = read. |
| `wdata` | in | Store data; low byte carries the payload (§6: "the low byte carries the payload unless noted"). |
| `rdata` | out | Combinational read data for `addr` from the **pre-edge** state. On a read, `periph_regs` registers `rdata` into `rsp_rdata` and forms the one-cycle `rsp_valid`; read side-effects fire at the next edge. |

**Why a strobe, not a plain combinational read port:** read side-effects (pop
RX, clear the sticky status bits) must fire *exactly once* per bus read. A
purely combinational read port cannot express "this read happened"; the
`access` strobe can. This mirrors why `uart_tx`/`uart_rx` take single-cycle
`start`/`strobe` pulses.

**Rejected alternative:** hand `uart.v` the raw bus (`req_valid`,
`req_addr[7:0]`, `req_size`, …) and let it decode its own `0x00–0x0F` window.
Rejected because §8 assigns address routing to `periph_regs.v`; duplicating the
region decode in every peripheral bloats each block and scatters the map.

## Registers & internal state

| State | Reset | Purpose |
|---|---|---|
| `div[15:0]` | `DIV_RST` | `UART_DIV`. Feeds `uart_tx.div` and `uart_rx.div`. A write to `0x8` updates it — **no clamp** (§6.1 clamps only SPI's divisor, not the UART's). |
| `rx_buf[7:0]` | `0` | RX holding buffer. |
| `rx_valid` | `0` | Set when `uart_rx` delivers a byte and the buffer was empty; cleared by a `UART_DATA` read that pops. Exposed as `rx_valid_o`. |
| `rx_overrun` | `0` | Sticky. Set when a byte arrives while the buffer is (still) full → new byte dropped. Cleared on a `UART_STATUS` read. |
| `rx_frame_err` | `0` | Sticky. Set from `uart_rx.frame_err` on an accepted byte. Cleared on a `UART_STATUS` read. |

`tx_busy` is not a stored register here — it is `uart_tx.busy` wired straight to
the status bit.

TX has **no holding register** (§6.1 / `uart_tx.md`): a `UART_DATA` write drives
`uart_tx.data = wdata[7:0]` and pulses `uart_tx.start` **only when `~tx_busy`**.
`wdata` is stable during the `access` cycle — exactly when `uart_tx` latches its
`data` at the accepted `start` — so no intermediate register is needed.

## Behavior (fine decode on `addr`)

Read data is combinational from the current (pre-edge) state; side-effects are
registered at the `access` edge.

**Reads (`we == 0`):**

- `0x0` **DATA**: `rdata = rx_valid ? {24'b0, rx_buf} : 32'b0`. If `rx_valid`,
  pop (`rx_valid <= 0`). Reading while `rx_valid == 0` returns 0 with no
  side-effect (§6.1).
- `0x4` **STATUS**: `rdata = {28'b0, rx_frame_err, rx_overrun, rx_valid,
  tx_busy}` (bit0 `tx_busy`, bit1 `rx_valid`, bit2 `rx_overrun`, bit3
  `rx_frame_err`). Clears `rx_overrun` and `rx_frame_err` (subject to the
  set-wins rule below).
- `0x8` **DIV**: `rdata = {16'b0, div}`.
- other offsets: `rdata = 0`.

**Writes (`we == 1`):**

- `0x0` **DATA**: if `~tx_busy`, start a TX frame with `wdata[7:0]`; else the
  write is dropped (poll-first policy).
- `0x8` **DIV**: `div <= wdata[15:0]`.
- `0x4` STATUS and all other offsets: ignored (read-only / undefined — §6).

## RX collision rules (agreed corner cases)

Both resolve as **"a new RX event wins over the clear-on-read"**, so a byte or
an error is never silently lost.

**(a) RX byte completes the same cycle a `UART_DATA` read pops it.**
`uart_rx.strobe` fires while `access & ~we & addr==0x0` also fires. The overrun
decision is gated on **"buffer full *after* this cycle's pop,"** not on the
stale `rx_valid`:

- read consumes the *old* `rx_buf`;
- the new byte loads into `rx_buf`, `rx_valid` stays 1;
- **no overrun** — this is correct depth-1-FIFO behavior and avoids spurious
  overruns on a busy poll loop.

**(b) `rx_frame_err` (or `rx_overrun`) set vs. clear-on-read collision.** A byte
with `frame_err` (resp. an overrun event) occurs the same cycle STATUS is read.
**Set wins:** the sticky bit ends the cycle at 1; the clear is overridden.

Note on visibility: because `rdata` reflects the **pre-edge** state, this STATUS
read does **not** report the just-arriving error — it returns the snapshot as of
before that byte, and the error surfaces on the **next** STATUS read. This is
coherent: the byte's `rx_valid` also latches at this same edge, so its
`rx_valid` and its `rx_frame_err` first become visible together, as a set, on
subsequent reads — never a frame-error flag for a byte software cannot yet read.

## Timing

- No FSM in `uart.v`; the two sub-FSMs live in `uart_tx` / `uart_rx`. All
  register effects here are single-cycle synchronous at the `access` edge.
- `div` changes take effect at the next bit period each half begins; changing
  the divisor mid-frame is undefined (same caveat as `uart_tx` / `uart_rx`).
- `tx` and `rx` are the module's serial pins; `rx` is synchronized inside
  `uart_rx` (the one legitimate CDC point), so `uart.v` treats it as a plain
  wire to the receiver.

## Key decisions (and rejected alternatives)

- **Strobe-based register-access port; coarse decode in `periph_regs.v`.**
  Keeps `uart.v` owning only its three registers and their side-effects.
  *Rejected:* raw-bus interface with a per-peripheral region decode (see above).
- **No TX holding register.** §6.1's "write-while-busy dropped, poll first"
  model; a second buffer would add area and soften the `tx_busy` semantics
  (same call as `uart_tx.md`).
- **`div` runtime-writable, no clamp.** §3/§6.1 make `UART_DIV` runtime; only
  SPI clamps its divisor. A `div` of 0 gives a 1-cycle bit period — legal,
  software's responsibility.
- **`OPT_GOAL` forwarded to `uart_rx` only.** It selects the receiver's mid-bit
  sampling strategy; `uart_tx` and the glue have no area/speed tradeoff.
- **"New RX event wins" for both collision cases (4a, 4b).** Never silently
  drop a byte or an error; the alternative (clear-wins, or combinationally
  surfacing a not-yet-readable error) is either lossy or incoherent.

## Verification note

Unit-tested (§9.1) by driving the register-access port and the `rx` pin and
observing `tx`, `rdata`, and the status bits: TX→RX loopback of known bytes at
several divisors, `tx_busy` gating (write-while-busy dropped), `rx_valid`
pop-on-read, `rx_overrun` (byte arriving while buffer full) and its
clear-on-read, `rx_frame_err` from a bad stop bit and its clear-on-read,
`UART_DIV` read/write round-trip, and directed checks of the two collision
rules (4a no-spurious-overrun, 4b set-wins). Full plan in
`docs/verification/uart.md`.
