# `spi_phy.v` — verification plan

Verification contract for the SPI PHY (`docs/microarchitecture/spi_phy.md`,
refining `docs/architecture.md` §5.2 / §9.1). This document is kept in sync with
`test/test_spi_phy.py` + `test/test_modules/spi_phy_tb.v`.

## Method

- **Tool: cocotb** (Icarus). A single module under test — not an integration of
  >2 independently-developed submodules — so cocotb, not UVM (§9 convention).
- **Reference: a Python SPI mode-0 slave model.** SPI is a full-duplex identity
  shift (MSB first), so the slave model *is* the golden reference; there is no
  separate golden function. The slave:
  - presents its first MISO bit the moment `start` is asserted (while SCK is
    still low), so bit 7 is valid before the first rising edge — this is what
    makes the `div=1` case correct, where the first rising edge is only one
    sysclk after the accept;
  - advances MISO on each SCK **falling** edge (mode 0: slave drives on falling,
    master samples on rising);
  - captures MOSI on each SCK **rising** edge — the same edge a real 23-/25-series
    chip samples — and returns the assembled byte for comparison against
    `tx_byte`.
- Core helper `do_transfer(dut, tx, resp, div)` frames one byte over the
  `start`/`busy`/`done` handshake and returns `(mosi_seen_by_slave, rx_byte)`.

## Scaffolding

- `test/test_modules/spi_phy_tb.v` — one `spi_phy` instance. The module is
  parameterless (the divisor is a runtime input), so unlike the decoder/rf
  benches there is no multi-instance parameter sweep; divisor coverage is driven
  at runtime. Control/`clk`/`rst_n` are `reg`; `sck`/`mosi`/`busy`/`done`/
  `rx_byte`/`eff_div` are `wire`; `miso` is a `reg` driven by the slave model.
  Dumps `waves/spi_phy_tb.vcd`.
- `test/test_spi_phy.py` — the tests below.

## Scenarios

| # | Test | What it asserts | Why |
|---|---|---|---|
| 1 | MOSI correctness | slave-captured 8 MOSI bits == `tx_byte` (MSB first) | transmit path + bit order |
| 2 | MISO → `rx_byte` | `rx_byte` == byte the slave shifted back | receive path + bit order |
| 3 | Full-duplex | distinct `tx`/`resp` in one transfer; both correct at once | TX and RX shifters independent |
| 4 | Handshake shape | `busy` high across transfer; `done` pulses **exactly one cycle**; `rx_byte` valid and `busy` low in the `done` cycle | controller-facing contract |
| 5 | Latency | cycles(start→done) == **16·eff_div**, swept div ∈ {1,2,4,8} | SCK divider timing = §5.2 budget |
| 6 | Divisor clamp | `div=0` ⇒ `eff_div==1` and latency == 16 (same as `div=1`) | §5.2 "0 reserved, treated as 1" |
| 7 | `eff_div` export | `eff_div == (div==0?1:div)` over a sweep | controller reuses it for deselect timing |
| 8 | Idle levels | after reset **and** between transfers: `sck=0, mosi=0, busy=0, done=0` | §5.2 idle/reset spec |
| 9 | Back-to-back | `start` re-pulsed right after `done`; each byte's data independent | models the controller streaming a frame (per-byte, gaps allowed) |
| 10 | `start` ignored while busy | mid-transfer `start` with a different `tx` neither restarts nor corrupts the in-flight byte or its latency | handshake contract ("accepted only when idle") |
| 11 | SCK shape | exactly 8 rising edges per byte; SCK starts and ends low | mode-0 waveform |
| 12 | Randomized fuzz | ~200 random `(tx, resp, div ∈ {0,1,2,4})`; both directions + latency each time | broad data × divisor coverage |

## Coverage rationale

Directed tests (1–11) pin every handshake, timing, mode-0, and idle corner
exactly; the fuzz test (12) sweeps data against the divisor set for volume
confidence. Divisor coverage spans the clamp (`0`), the max rate (`1`), the SRAM
floor (`2`), and the reset value (`4`), i.e. every value called out in §5.2/§6.4.

## Repo cocotb rules

- Every test's setup restarts the `Clock` (no module-level `_clock_started`
  guard — `start_soon` tasks are cancelled at end of each test).
- Every coroutine ends with an unconditional trailing
  `await ClockCycles(dut.clk, 1)`, placed after any `try/except`, to avoid the
  known Icarus/cocotb FST-teardown segfault.

## Exit criterion

Per the build-module workflow: the bench must **run cleanly** (no simulator or
teardown error). Passing is the goal of the separate verify-module workflow;
this plan's completion is a bench that executes and reports pass/fail.
