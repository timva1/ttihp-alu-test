# `uart_tx.v` — UART transmitter

Refinement of `docs/architecture.md` §6.1 (the UART peripheral) and §8 (which
splits the UART into `uart.v` = register/buffer glue, `uart_tx.v` = transmitter,
`uart_rx.v` = receiver). This document is the implementation contract for the
transmitter. The architecture specifies the UART as a whole but leaves the
**`uart_tx` ↔ `uart.v` interface** unspecified; that boundary is defined here.

## Role

The transmit half of the UART: a **pure parallel→serial transmitter** for the
fixed 8N1 format. Given a byte and a single-cycle `start` pulse, it emits
`start(0) → d0..d7 (LSB-first) → stop(1)` on `tx` and holds `busy` high for the
whole frame.

It owns **no policy**. The `UART_DATA` register and the §6.1 "write while
`tx_busy` is ignored — poll first" rule live in `uart.v`, which simply pulses
`start` only when `busy` is low. This mirrors the established `uart_rx` split
(the receiver shifts a byte in, `uart.v` owns the RX buffer/status policy) and
the `spi_phy` / `spi_mem_ctrl` lineage (`spi_phy` shifts bits; the controller
owns policy): `uart_tx` shifts a byte out, `uart.v` owns the transmit policy.

## Interface

```verilog
module uart_tx (
    input  wire        clk,
    input  wire        rst_n,     // async active-low
    input  wire [15:0] div,       // UART_DIV; bit period = (div+1) sysclk cycles
    input  wire        start,     // 1-cycle pulse: latch `data`, begin a frame (acted on only when idle)
    input  wire [7:0]  data,      // byte to send; sampled at accepted start; LSB-first on the wire
    output reg         tx,        // serial output, idle high (8N1)
    output reg         busy       // high from accepted start through end of stop bit
);
```

- `div` is a **runtime input**, not a parameter — `UART_DIV` is runtime-writable
  (§3/§6.1). Same choice as `uart_rx` and `spi_phy`'s `div`. Baud =
  sysclk/(`div`+1), so the bit period is `div+1` sysclk cycles.
- **No `OPT_GOAL` parameter** (the one asymmetry with `uart_rx`). Transmit is
  deterministic bit-banging: there is no sampling to make robust and no
  speed/area tradeoff to expose, so an `OPT_GOAL` port would be dead.

### Handshake

| Signal | Direction | Contract |
|---|---|---|
| `start` | in | Single-cycle request. Acted on **only when `state==IDLE`** (`~busy`); ignored otherwise. `uart.v` guarantees it only pulses when `~busy`, but the module self-gates for robustness. |
| `data` | in | The byte to send, LSB-first on the wire → `data[0]` is transmitted first. Latched into `shift` at the accepted `start`; the caller need not hold it afterward. |
| `tx` | out | Registered, idle-high. `0` for the start bit, `d0..d7` LSB-first, `1` for the stop bit, then idle high. |
| `busy` | out | Registered; asserts the cycle after an accepted `start`, deasserts when the stop-bit period completes (return to `IDLE`). Drives the `tx_busy` status bit in `uart.v`. |

`uart.v` maps a `UART_DATA` write to `data` + a one-cycle `start` (pulsed only
when `~busy`), and exposes `busy` as the `tx_busy` status bit. Whether a write
while busy is dropped is `uart.v`'s policy decision, not the transmitter's.

## Timing — 8N1

One frame = start (0) + 8 data (LSB first) + stop (1) = 10 bit periods. Bit
period = `div+1` sysclk cycles.

- On an accepted `start`: latch `data`, drive `tx` low (start bit begins), raise
  `busy`, load `cnt = div`.
- Each bit period: hold the current `tx` level for `div+1` cycles (`cnt` counts
  `div → 0`), then advance to the next bit at `cnt==0`.
- Data bits are shifted out LSB-first, then the stop bit (`tx = 1`), then return
  to idle high and drop `busy`.
- Total frame latency = `10·(div+1)` cycles from the accepted `start` to `busy`
  deassert. `busy` rises 1 cycle after the `start` pulse; `tx` drops to the
  start bit on that same edge.
- Idle/reset: `tx=1`, `busy=0`, `state=IDLE`.

```
        start  d0    d1        ...        d7   stop  idle
tx    ‾‾\____/‾‾‾\__/‾‾‾ ...              /‾‾‾\__/‾‾‾‾‾‾   (LSB first)
       ^ start                                     ^ busy drops
      busy rises
```

## Internal structure & FSM

1. **`shift[7:0]`** — the transmit byte register (the §6.1 "shift register").
   Loaded from `data` at the accepted `start`, shifted right one bit per bit
   period (idle 1s shifted in from the top).
2. **`cnt[15:0]`** — sysclk down-counter for bit timing. Full bit = load `div`,
   count to 0 (= `div+1` cycles). Same timing basis as `uart_rx`.
3. **`bit_idx[2:0]`** — data-bit index 0..7.
4. **FSM**: `IDLE → START → DATA → STOP → IDLE` — the same shape as `uart_rx`,
   run in the transmit direction.
   - `IDLE`: `tx=1`, `busy=0`. On `start` → `shift=data`, `tx<=0` (start bit),
     `busy<=1`, `cnt<=div`, `bit_idx<=0`, → `START`.
   - `START`: at `cnt==0` → `tx<=shift[0]` (present d0), reload `cnt=div`,
     `bit_idx=0`, → `DATA`.
   - `DATA`: at `cnt==0` shift right; if `bit_idx==7` → `tx<=1` (stop bit),
     → `STOP`; else `tx<=shift[1]` (next data bit), `bit_idx++`; reload
     `cnt=div`.
   - `STOP`: at `cnt==0` → `tx<=1`, `busy<=0`, → `IDLE`.
   - non-`IDLE` otherwise: `cnt<=cnt-1`.

`tx` is a **registered output** (glitch-free on the pin). The
`tx<=shift[1]`-uses-the-old-`shift` step is the same nonblocking-shift idiom
`uart_rx` uses (`data<={bit_sample, data[7:1]}`): both nonblocking assignments
read the pre-edge `shift`, so `tx` takes the next bit while `shift` advances.

## Key decisions (and rejected alternatives)

- **Pure transmitter; holding register + drop-policy in `uart.v`.** Keeps the
  transmitter a parallel→serial shifter with no policy, matching the
  `uart_rx`/`uart.v` and `spi_phy`/`spi_mem_ctrl` splits. *Rejected:*
  double-buffering a TX holding register ahead of the shifter — §6.1 specifies
  the simpler "write-while-busy dropped, poll first" model, so a second buffer
  would add area and soften the documented `tx_busy` status semantics.
- **`div` as a runtime input, not a parameter.** Preserves §3/§6.1's
  runtime-writable `UART_DIV`. Same choice as `uart_rx` / `spi_phy`.
- **No `OPT_GOAL` parameter.** Transmit is deterministic; there is no sampling
  to harden and no cycle/area tradeoff to expose. *Rejected:* carrying a
  vestigial `OPT_GOAL` purely for literal port-symmetry with `uart_rx`.
- **Self-gate `start` on `IDLE`.** Even though `uart.v` already gates on
  `~busy`, ignoring `start` outside `IDLE` keeps the module correct in isolation
  for its unit bench.

## Verification note

Unit-tested (§9.1) against a Python UART *receiver* model sampling `tx`:
loopback of known bytes at several divisors (LSB-first, 8N1), `busy` timing,
start/data/stop framing, back-to-back frames, `start` ignored while busy, and a
directed check of the idle-high line at reset. Full plan in
`docs/verification/uart_tx.md`.
