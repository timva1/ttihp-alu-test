# `spi_phy.v` — SPI shift register + SCK divider

Refinement of `docs/architecture.md` §5.2 (the "PHY" bullet) and §8 (which
splits the SPI block into `spi_phy.v` = "shift register + SCK divider" and
`spi_mem_ctrl.v` = "command layer + burst logic"). This document is the
implementation contract for the module. The architecture specifies the SPI
block as a whole but leaves the **`spi_phy` ↔ `spi_mem_ctrl` interface**
unspecified; that boundary is defined here.

## Role

The lowest layer of the SPI stack: a **byte-granular, full-duplex,
SPI-mode-0 shifter with an integrated SCK divider**. Given one byte it clocks
8 bits out on MOSI while simultaneously clocking 8 bits in from MISO, then
returns the received byte. It knows nothing about commands, addresses,
chip-selects, or bursts — all framing, CS_n management, burst continuation,
and address mapping live in `spi_mem_ctrl.v`.

This isolation is deliberate: §9's QSPI note requires that a future 1→4 lane
upgrade touch "only `spi_phy.v` and the pin map." Keeping the PHY free of any
command/burst state is what makes that true.

## Interface

```
module spi_phy (
    input  wire       clk,
    input  wire       rst_n,      // async active-low
    // control side (to/from spi_mem_ctrl)
    input  wire [7:0] div,        // SCK divisor from SPI_DIV; 0 clamped to 1
    input  wire       start,      // 1-cycle pulse: begin an 8-bit transfer
    input  wire [7:0] tx_byte,    // MOSI data, latched at start
    output wire       busy,       // high while a transfer is in progress
    output reg        done,       // 1-cycle pulse at completion; rx_byte valid
    output reg  [7:0] rx_byte,    // MISO data captured this transfer
    output wire [7:0] eff_div,    // effective divisor (post 0→1 clamp), for the
                                  //   controller's deselect timing (no re-clamp)
    // SPI pins (mode 0)
    output reg        sck,
    output reg        mosi,
    input  wire       miso
);
```

Note what is **absent**: `cs_n`. CS0_n/CS1_n are owned by `spi_mem_ctrl`
(pure command-layer/burst state per §8). The PHY is wired only to
MOSI / SCK / MISO. The controller times its ≥1-SCK-period deselect (§5.2) by
counting `2·eff_div` sysclk cycles using this module's exported `eff_div`.

### Handshake

| Signal | Direction | Contract |
|---|---|---|
| `start` | in | Accepted **only when idle** (`!busy`). A pulse while `busy` is ignored (no queueing). |
| `tx_byte` | in | Sampled the cycle `start` is accepted; may change afterward. |
| `div` | in | Sampled (and clamped) the cycle `start` is accepted; stable-per-frame is the controller's job, but latching here makes the PHY independently testable. |
| `busy` | out | Rises the cycle after an accepted `start`, falls when the transfer completes. |
| `done` | out | Single-cycle pulse in the completion cycle; `rx_byte` is valid that cycle and **held** until the next transfer completes. |
| `rx_byte` | out | The 8 bits sampled from MISO, MSB first (first bit sampled = `rx_byte[7]`). |

## Timing — SPI mode 0 (CPOL=0, CPHA=0, MSB first)

Per §5.2: MOSI transitions on the **falling** SCK edge, MISO is sampled on the
**rising** SCK edge; SCK idles low.

- `eff_div = (div == 0) ? 1 : div` — a divisor of 0 is reserved and treated as
  1 (§5.2); no setting can stop the bus. SCK period = `2·eff_div` sysclk
  cycles, half-period = `eff_div`. `eff_div = 1` ⇒ SCK = sysclk/2 (max).
- On accepted `start`: latch `tx_byte`→TX shifter and `eff_div`; present
  `mosi = tx_byte[7]` with SCK still **low** (mode-0 first-bit setup, which has
  no leading falling edge); assert `busy`.
- Then 8 SCK periods. At each **rising** edge: sample `miso` into the RX
  shifter. At each **falling** edge: shift the TX shifter so the next bit
  appears on `mosi`. The last (8th) rising edge samples bit 0; SCK then returns
  low, `done` pulses, `busy` drops.
- Total latency = **16·eff_div** sysclk cycles (8 bits × 2·eff_div), matching
  the §5.2 budget (8 SPI clocks per byte).
- Idle / reset outputs: `sck = 0`, `mosi = 0`, `busy = 0`, `done = 0`.

```
             bit7  bit6  ...        bit0
start __/‾\_________________________________
mosi    ⟨b7 ⟩⟨ b6 ⟩⟨ b5 ⟩ ...      ⟨ b0 ⟩____   (changes on falling edges)
sck  ______/‾‾\__/‾‾\__/ ... ‾\__/‾‾\_______
              ^     ^                 ^
           sample sample           sample bit0  → done
```

## Internal structure ("shift register + SCK divider")

Exactly the two elements the architecture names, plus a minimal FSM:

1. **SCK divider**: an `eff_div`-comparison half-period counter
   (`div_cnt`, 8-bit) that produces a half-period tick every `eff_div` sysclk
   cycles; each tick toggles `sck`.
2. **Bit/edge counter** (`bit_cnt`, 4-bit): counts the 16 half-periods
   (8 rising + 8 falling) of a byte; terminal count ends the transfer.
3. **TX shift register** (8-bit): MSB drives `mosi`; shifts left on falling
   edges.
4. **RX shift register** (8-bit): captures `miso` on rising edges (shift-in at
   LSB, or index by bit — MSB-first); exposed as `rx_byte`.
5. **FSM**: `IDLE` → (on accepted `start`) `SHIFT` → (on terminal `bit_cnt`)
   back to `IDLE` with `done` pulsed. `eff_div` and TX data are latched on the
   `IDLE→SHIFT` transition.

Rising vs. falling edges are detected from the divider tick plus the current
`sck` level (a tick while `sck==0` is the upcoming rising edge, etc.), so no
second clock or derived clock exists — SCK is a divided **enable**, consistent
with §7's "one clock domain, no CDC."

## Key decisions (and rejected alternatives)

- **Byte-granular, gaps allowed (not gapless streaming).** The PHY does exactly
  one byte per `start` and returns SCK to idle-low between bytes. The controller
  streams a frame by pulsing `start` repeatedly while holding CS low; if it is
  not ready with the next byte, SCK simply pauses low with CS low, which both
  25-series NOR flash and 23-series serial SRAM tolerate indefinitely (fully
  static SPI). Gaplessness is therefore a *performance* property the controller
  may pursue later, never a *correctness* requirement of the PHY.
  *Rejected:* a PHY that guarantees back-to-back gapless bytes via a
  `tx_ready`/holding-register handshake — more PHY state and edge-case timing
  for zero functional benefit, and addable later without changing this
  interface.
- **CS_n lives in `spi_mem_ctrl`, not here.** Which chip to select and the
  ≥1-SCK deselect delay are burst/command state per §8; the controller counts
  `2·eff_div` sysclk cycles (using exported `eff_div`) to time the deselect.
  This keeps the PHY purely pin-shift and QSPI-ready.
  *Rejected:* driving CS_n and the deselect delay inside the PHY — concentrates
  pin timing but drags burst/framing state down into the PHY against §8.
- **`div`/`eff_div` latched per transfer inside the PHY.** Makes the module
  independently testable (drive `div`, pulse `start`) and matches §6.4's "writes
  take effect at the next CS assertion" as long as the controller only changes
  `div` between frames.

## Verification note

Unit-tested (§9.1) against a Python SPI-mode-0 slave model driving MISO:
checks the 8 MOSI bits equal `tx_byte` (MSB first), `rx_byte` equals the bits
the model shifted back, `done`/`busy` handshake shape, exact `16·eff_div`
latency across several divisors, the `div=0 → eff_div=1` clamp, and idle output
levels. Full plan in `docs/verification/spi_phy.md`.
