# `uart.v` — verification plan

Verification contract for the UART register/buffer glue (`src/periph/uart.v`,
microarchitecture in `docs/microarchitecture/uart.md`, refining
`docs/architecture.md` §6/§6.1/§8). Implemented by
`test/test_modules/uart_tb.v` + `test/test_uart.py`, run via
`make -B test_uart`.

## Approach — cocotb, not UVM

`uart.v` integrates **two** already-unit-verified halves (`uart_tx`,
`uart_rx`). The skill's UVM threshold is >2 independently-developed submodules,
so this is a cocotb integration bench. It targets the **glue's policy** — the
three registers, the RX buffer, the status bits, the decode, and the two agreed
collision rules — and does *not* re-derive the halves' bit-timing (covered by
`test_uart_tx` / `test_uart_rx`). Small divisors (3–15) keep frames short.

## Harness

- Single `uart` DUT, defaults `OPT_GOAL="AREA"`, `DIV_RST=16'd433`.
- Reuse `test/common/uart_model.py`:
  - `UartRxModel(dut.tx, dut.clk)` — golden sampler on the TX pin.
  - `UartTxModel(dut.rx, dut.clk)` — drives the RX pin; `stop=0` injects a
    framing error, `glitch=` injects noise.
- A register-access helper in the test drives the `access`/`addr`/`we`/`wdata`
  port edge-aligned (same idiom as the TX bench's `pulse_start`) and samples
  `rdata`: `bus_write(off, val)` and `bus_read(off)` (a `bus_read` pulses
  `access` with `we=0`, captures `rdata` at the sampling edge, so read
  side-effects fire exactly once).
- Register offsets: `DATA=0x0`, `STATUS=0x4`, `DIV=0x8`. Status bit layout
  `{rx_frame_err[3], rx_overrun[2], rx_valid[1], tx_busy[0]}`.

Repo cocotb rules: the `Clock` is restarted in every test's `setup`
(`start_soon` tasks are cancelled per-test); every coroutine ends with an
unconditional trailing `await ClockCycles(dut.clk, 1)` after any `try/except`
(the known Icarus/cocotb FST-teardown segfault guard).

## Scenarios

| # | Test | What it checks |
|---|---|---|
| 1 | `test_div_reset_and_rw` | `UART_DIV` reads `DIV_RST` after reset; write→readback round-trips; no clamp (write 0 reads 0). |
| 2 | `test_tx_write_loopback` | A `UART_DATA` write launches a frame; `UartRxModel` on `tx` reads the byte back (LSB-first, 8N1) across several divisors. |
| 3 | `test_tx_busy_poll` | `tx_busy` (STATUS bit0) rises after a DATA write, stays high across the frame, drops at the stop bit. |
| 4 | `test_tx_write_while_busy_dropped` | A second `UART_DATA` write while `tx_busy` is **dropped** (poll-first): only the first byte appears on `tx`, no second start bit. |
| 5 | `test_rx_receive_and_pop` | A byte driven on `rx` sets `rx_valid` (bit1); a `UART_DATA` read returns it **and** pops (`rx_valid`→0); a second read returns 0. |
| 6 | `test_rx_read_empty` | Reading `UART_DATA` while `rx_valid==0` returns 0 with no side-effect (state unchanged). |
| 7 | `test_rx_overrun` | Two bytes with no intervening read → `rx_overrun` (bit2) set, first byte retained; STATUS read **clears** overrun. |
| 8 | `test_rx_frame_err` | A byte with a bad stop bit (`stop=0`) sets `rx_frame_err` (bit3), sticky; STATUS read clears it; a clean byte leaves it 0. |
| 9 | `test_status_bit_layout` | Directed: status bits land in `[3:0]`, upper bits read 0; a write to STATUS (read-only) and to an undefined offset are ignored. |
| 10 | `test_collision_4a_no_spurious_overrun` | RX `strobe` lands the same cycle a `UART_DATA` read pops: the read returns the old byte, the new byte occupies the buffer, `rx_valid` stays 1, **no overrun**. |
| 11 | `test_collision_4b_set_wins` | An RX error byte completes the same cycle STATUS is read: that read returns the pre-edge (clean) status, and the error is **visible on the next STATUS read** (the set survived the clear). |
| 12 | `test_full_loopback_fuzz` | Randomized (seeded): interleave TX writes and RX-driven bytes at random divisors, poll-first on TX, drain RX on `rx_valid`; assert every TX byte is read on `tx` and every RX byte is read from `UART_DATA`. |

## Coverage rationale

- Tests **10** and **11** are the two agreed corner-case rulings
  (`docs/microarchitecture/uart.md` §"RX collision rules"). They are directed
  because they are single-cycle-timing-precise and the fuzz test (12) will not
  reliably reproduce the exact-cycle collisions.
- Undefined-offset / read-only-write behavior is folded into test **9** rather
  than a standalone test.
- `DIV_RST` and `OPT_GOAL` stay at defaults; divisor and mid-bit sampling
  behavior is already owned by the halves' unit benches, so this bench does not
  sweep them for their own sake — only enough divisors to confirm the glue
  forwards `div` to both halves.

## Exit criterion (build-module)

Verification **runs cleanly** (completes with no simulator/teardown error); it
need not pass yet. Failures are root-caused later via the `verify-module`
workflow.
