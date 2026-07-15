# `uart_rx.v` — UART receiver

Refinement of `docs/architecture.md` §6.1 (the UART peripheral) and §8 (which
splits the UART into `uart.v` = register/buffer glue, `uart_tx.v` = transmitter,
`uart_rx.v` = receiver). This document is the implementation contract for the
receiver. The architecture specifies the UART as a whole but leaves the
**`uart_rx` ↔ `uart.v` interface** unspecified; that boundary is defined here.

## Role

The receive half of the UART: a **pure serial→parallel receiver** for the fixed
8N1 format. Given the async `rx` line it detects the start bit, samples 8 data
bits (LSB-first) at their centers, checks the stop bit, and hands `uart.v` one
completed byte plus a per-byte framing flag via a single-cycle `strobe`.

It owns **no buffer and no status bits**. The single-byte RX holding buffer,
`rx_valid`, `rx_overrun` (a byte completing while `rx_valid` is still set,
§6.1), and the sticky `rx_frame_err` all live in `uart.v`. This mirrors the
established `spi_phy` / `spi_mem_ctrl` split (`spi_phy` shifts bits; the
controller owns framing/buffer state): `uart_rx` shifts a byte in, `uart.v`
owns the buffering *policy*.

## Interface

```verilog
module uart_rx #(
    parameter OPT_GOAL = "AREA"     // "AREA" | "SPEED" — sampling strategy only
) (
    input  wire        clk,
    input  wire        rst_n,       // async active-low
    input  wire [15:0] div,         // UART_DIV; bit period = (div+1) sysclk cycles
    input  wire        rx,          // async serial input, idle high (8N1)
    output reg         strobe,      // 1-cycle pulse: data & frame_err valid
    output reg  [7:0]  data,        // received byte, LSB-first on the wire → data[0] first
    output reg         frame_err    // stop bit sampled != 1 for this byte
);
```

- `div` is a **runtime input**, not a parameter — `UART_DIV` is runtime-writable
  (§3/§6.1). Same choice as `spi_phy`'s `div`. Baud = sysclk/(`div`+1), so the
  bit period is `div+1` sysclk cycles.
- `OPT_GOAL` is the only parameter and changes **sampling only**, never the byte
  result on a clean line — consistent with §3's "`OPT_GOAL` never changes
  architecturally-visible results, only cycle counts / robustness."

### Handshake

| Signal | Direction | Contract |
|---|---|---|
| `rx` | in | Async, idle high. Internally passed through a 2-FF synchronizer before use. |
| `strobe` | out | Single-cycle pulse in the cycle the stop bit is sampled. Fires **even on frame error**. |
| `data` | out | The 8 data bits, LSB-first on the wire → first bit received is `data[0]`. Valid in the `strobe` cycle; held until the next byte completes. |
| `frame_err` | out | High in the `strobe` cycle iff the stop bit sampled ≠ 1 (line still low = break/mis-framing). Valid with `strobe`. |

`uart.v` consumes `strobe` to load its RX buffer and to update `rx_valid` /
`rx_overrun` / sticky `rx_frame_err`. Whether a frame-errored byte is kept is
`uart.v`'s policy decision, not the receiver's.

## Timing — 8N1

One frame = start (0) + 8 data (LSB first) + stop (1) = 10 bit periods. Bit
period = `div+1` sysclk cycles.

- **Start detect**: falling edge on the synchronized `rx` (idle-high → low).
- **Start centering**: after the edge, wait `div>>1` (≈ half a bit) so the next
  sample lands at the *middle* of the start bit; resample there. Still low →
  valid start; high → false start (glitch), abandon back to idle.
- **Data**: from mid-start, reload the full bit period `div` before each sample,
  so successive samples land at the centers of data bits 0..7. Shift each into
  `data` LSB-first.
- **Stop**: one more full bit period → sample at mid-stop. `frame_err = ~sample`.
  Pulse `strobe`, return to idle.
- Total frame latency ≈ `10·(div+1)` cycles from start-bit edge to `strobe`.
- Idle/reset: `strobe=0`, `frame_err=0`, `data` held.

```
        start  d0    d1        ...        d7   stop
rx    ‾‾\____/‾‾‾\__/‾‾‾ ...              /‾‾‾\__/‾‾‾‾   (LSB first)
         ^     ^     ^                     ^     ^
        mid   mid   mid                   mid   mid
       start  d0    d1        ...          d7  stop → strobe
```

## Internal structure & FSM

1. **2-FF synchronizer** on `rx` (`rx_sync[1:0]`). `rx` is asynchronous to
   sysclk — the one legitimate CDC point in this design; §7's "one clock domain,
   no CDC" is about *internal* signals, and an external async pin must be
   synchronized. Start detection uses the synchronized level.
2. **`cnt[15:0]`** — sysclk down-counter for bit timing. Full bit = load `div`,
   count to 0 (= `div+1` cycles). Start-centering = load `div>>1`.
3. **`bit_idx[2:0]`** — data-bit index 0..7.
4. **FSM**: `IDLE → START → DATA → STOP → IDLE`.
   - `IDLE`: on `rx` falling edge → `START`, load `cnt = div>>1`.
   - `START`: at `cnt==0` sample; low → `DATA`, load `cnt = div`, `bit_idx = 0`;
     high → `IDLE` (false start).
   - `DATA`: at `cnt==0` sample bit into `data[bit_idx]`, reload `cnt = div`;
     after `bit_idx == 7` → `STOP`, reload `cnt = div`.
   - `STOP`: at `cnt==0` sample; `frame_err = ~sample`; pulse `strobe`; → `IDLE`.

### Sampling: `OPT_GOAL`

The "sample" at each bit center depends on `OPT_GOAL`:

- **`"AREA"`**: single sample of the synchronized `rx` at the center count.
- **`"SPEED"`**: 3-sample **majority vote** of the synchronized `rx` over the
  three consecutive sysclk samples ending at the bit-center instant (a rolling
  3-tap window; the vote is taken the cycle `cnt==0`) — `bit =
  majority(s0,s1,s2)`. Costs three sample registers + a voter, no separate 16×
  clock. The ±1-sysclk asymmetry of the window vs. true center is negligible
  against the `div+1`-cycle (≈434) bit period.

**Why not literal 16× oversampling** (the §6.1 wording): the standard 16×-baud
sample clock exists in textbook UARTs to (a) center the sample point to ~1/16 of
a bit and (b) allow majority voting. In *this* design the sample clock is the
50 MHz sysclk and the oversampling ratio is `div+1` sysclk cycles per bit
(**434× at 115200 baud**), so centering resolution is already far finer than
16× — benefit (a) is saturated and a literal 16×-tick generator would *worsen*
it while needing `(div+1)%16` handling. The only remaining payoff of
"SPEED oversampling" here is (b) noise rejection, which the 3-sample majority
vote delivers cheaply. On a clean line SPEED and AREA produce identical bytes,
honoring §3.

## Key decisions (and rejected alternatives)

- **Pure receiver; buffer/status in `uart.v`.** Keeps the receiver a
  serial→parallel shifter with no policy, matching the `spi_phy`/`spi_mem_ctrl`
  split. *Rejected:* holding the RX buffer and driving
  `rx_valid`/`rx_overrun`/sticky `rx_frame_err` inside `uart_rx` — concentrates
  §6.1 status policy in the receiver and blurs the boundary.
- **SPEED = majority-vote-of-3, not literal 16×.** Captures the sole benefit of
  oversampling that survives the high sysclk ratio (noise rejection) at minimal
  area, with no divisibility constraint on `div`. *Rejected:* a 16×-baud tick
  generator (gains nothing here, can worsen centering, needs fractional
  division); *rejected:* ignoring `OPT_GOAL` entirely (drops the asked-for
  robustness).
- **`div` as a runtime input, not a parameter.** Preserves §3/§6.1's
  runtime-writable `UART_DIV`. Same choice as `spi_phy`.
- **`strobe` fires on frame error too, carrying the byte.** Lets `uart.v` decide
  keep-vs-drop policy and set the sticky flag; the receiver stays policy-free.

## Verification note

Unit-tested (§9.1) against a Python UART transmitter model driving `rx`:
loopback of known bytes at several divisors (LSB-first, 8N1), mid-bit alignment,
start-bit false-trigger rejection, frame-error detection on a bad stop bit,
`strobe`/`data`/`frame_err` timing, and — under `OPT_GOAL="SPEED"` — recovery
from a single mid-bit glitch that AREA would mis-sample. Full plan in
`docs/verification/uart_rx.md`.
