# `uart_tx.v` — verification plan

Verification contract for the UART transmitter (`docs/microarchitecture/uart_tx.md`,
refining `docs/architecture.md` §6.1 / §9.1). Kept in sync with
`test/test_uart_tx.py` + `test/test_modules/uart_tx_tb.v` +
`test/common/uart_model.py`.

## Method

- **Tool: cocotb** (Icarus). A single module under test — not an integration of
  >2 independently-developed submodules — so cocotb, not UVM (§9 convention).
  Same structure as the `uart_rx` bench.
- **Reference: a Python UART *receiver* model** (`UartRxModel` in
  `test/common/uart_model.py`) that samples `dut.tx` for one 8N1 frame: it
  anchors on the exact start-bit falling edge, centers (`period//2`), then
  samples 8 data bits **LSB-first** and the stop bit at their centers
  (`+(div+1)` each), returning `(byte, stop)`. UART is an identity channel, so
  this receiver *is* the golden reference — whatever the DUT transmitted, a
  correct sampler must read back. The RX doc anticipated this model ("a
  symmetric `UartRxModel` (line sampler) can join it later").
- The model lives in the reusable `test/common/` package, so `uart.v` and future
  TX↔RX loopback integration benches reuse both `UartTxModel` and `UartRxModel`
  without touching these tests.

## Scaffolding

- `test/common/uart_model.py` — **`UartRxModel`** added alongside the existing
  `UartTxModel` (unchanged). Async `recv_frame(div)` anchored on the exact `tx`
  falling edge, so bit-center sampling has no drift even at small `div`.
- `test/test_modules/uart_tx_tb.v` — a **single** `uart_tx` instance (no
  AREA/SPEED variants — the transmitter has no `OPT_GOAL`), ports
  `clk/rst_n/div/start/data/tx/busy`. Dumps `waves/uart_tx_tb.vcd`.
- `test/test_uart_tx.py` — the tests below. Helper `xmit(dut, byte, div)`:
  `start_soon` the receiver capture (so it latches idle-high `prev` before the
  start bit), pulse `start` for one cycle with `data` set, then `await` the
  captured `(byte, stop)`.
- **Small divisors** (`div ∈ {3, 7, 15}`, not the 433 reset value) keep the sim
  fast; `div` only scales bit timing, so small values fully exercise the FSM.

## Scenarios

| # | Test | What it asserts | Why |
|---|---|---|---|
| 1 | Single byte loopback | receiver reads back the sent byte, `stop == 1` | basic transmit path |
| 2 | LSB-first order | `0x01`, `0x80`, `0xA5` transmit correctly | first bit out → `data[0]` |
| 3 | Divisor sweep | correct across `div ∈ {3,7,15}` | bit timing scales with `div` |
| 4 | Back-to-back frames | N consecutive bytes each read back correct; `busy` low between | returns to idle and re-arms |
| 5 | `busy` timing | `busy` rises after `start`, stays high the whole frame (≈ `10·(div+1)`), drops at the stop bit | `tx_busy` contract for `uart.v` |
| 6 | `start` ignored while busy | a second `start` (byte B) pulsed mid-frame → in-flight byte A transmits unchanged and **no** second frame follows (B dropped) | §6.1 "write-while-busy dropped, poll first" |
| 7 | Idle / reset | after reset, `tx` idle-high, `busy == 0`, no frame without `start` | idle spec |
| 8 | Randomized fuzz | ~200 random `(byte, div ∈ {3,7,15})`, all read back correct | data × divisor volume |

## Coverage rationale

Directed tests 1–7 pin framing, ordering, timing, the `busy`/drop-policy
contract, and idle exactly; fuzz (8) adds data × divisor volume. `div` coverage
is small-but-representative because the divisor only scales timing, never logic —
same rationale as `uart_rx`.

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
