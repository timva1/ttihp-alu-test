# `uart_rx.v` — verification plan

Verification contract for the UART receiver (`docs/microarchitecture/uart_rx.md`,
refining `docs/architecture.md` §6.1 / §9.1). Kept in sync with
`test/test_uart_rx.py` + `test/test_modules/uart_rx_tb.v` +
`test/common/uart_model.py`.

## Method

- **Tool: cocotb** (Icarus). A single module under test — not an integration of
  >2 independently-developed submodules — so cocotb, not UVM (§9 convention).
- **Reference: a Python UART *transmitter* model** (`test/common/uart_model.py`)
  that bit-bangs `dut.rx` for one 8N1 frame at a given `div`:
  idle-high → start(0) → 8 data bits **LSB-first** → stop(1), holding each level
  for `div+1` sysclk cycles (driven off `ClockCycles`). UART is an identity
  channel, so this TX model *is* the golden reference — there is no separate
  golden function. The model also supports driving a **bad stop bit** and
  injecting a **single-sysclk glitch** at a named bit's center, for the
  frame-error and SPEED-noise tests.
- The model lives in a **reusable `test/common/` package**, deliberately
  decoupled from the `uart_rx` tests: `uart.v` and future integration benches
  reuse the same `UartTxModel`, and a symmetric `UartRxModel` (line sampler) can
  join it later without touching the receiver tests.

## Scaffolding

- `test/common/uart_model.py` — reusable model. `UartTxModel` / async
  `send_frame(dut_rx, clk, byte, div, *, stop=1, glitch=None)` (imported as
  `from common.uart_model import ...`). Not `test_`-prefixed and not a cocotb
  test module, so it is never collected as a test.
- `test/common/__init__.py` — marks `common` a package so the subdir import
  resolves regardless of cocotb's invocation cwd.
- `test/test_modules/uart_rx_tb.v` — **two `uart_rx` instances** sharing
  `clk`/`rst_n`/`div`/`rx` (à la `decoder_tb`): `area` (`OPT_GOAL="AREA"`) and
  `speed` (`OPT_GOAL="SPEED"`), each with its own
  `strobe_*/data_*/frame_err_*` bundle. Sharing the stimulus lets the noise test
  contrast the two sampling strategies directly. Dumps `waves/uart_rx_tb.vcd`.
- `test/test_uart_rx.py` — the tests below.
- **Small divisors** (`div ∈ {3, 7, 15}`, not the 433 reset value) keep the sim
  fast; `div` only scales bit timing, so small values fully exercise the FSM and
  mid-bit alignment.

## Scenarios

| # | Test | What it asserts | Why |
|---|---|---|---|
| 1 | Single byte loopback | `data == sent`, `frame_err == 0`, one `strobe` (both instances) | basic receive path |
| 2 | LSB-first order | `0x01`, `0x80`, `0xA5` decode correctly | first bit received → `data[0]` |
| 3 | Divisor sweep | correct across `div ∈ {3,7,15}` | mid-bit alignment scales with `div` |
| 4 | Back-to-back bytes | N consecutive frames each correct, one `strobe` each | returns to idle and re-arms cleanly |
| 5 | Frame error | bad stop bit → `frame_err == 1`, `strobe` still pulses, `data` still = sent bits | stop-bit check / §6.1 `rx_frame_err` source |
| 6 | False-start rejection | `rx` dips low < half a bit then returns high → **no** `strobe`, stays idle | start-bit mid-sample confirm |
| 7 | Idle / reset | after reset, idle-high line → no spurious `strobe`, `data` held | idle spec |
| 8 | `strobe` shape | pulses **exactly one cycle**; `data`/`frame_err` valid that cycle | `uart.v`-facing contract |
| 9 | SPEED noise rejection | single-sysclk glitch at one data-bit center: `speed` byte **correct**; contrast that `area` mis-samples the same glitch | the sole reason `OPT_GOAL="SPEED"` exists here (majority vote earns its gates) |
| 10 | Randomized fuzz | ~200 random `(byte, div ∈ {3,7,15})`, both instances correct | broad data × divisor volume |

## Coverage rationale

Directed tests 1–8 pin framing, ordering, timing, error, and idle corners
exactly; test 9 makes the AREA/SPEED distinction observable — the only reason
`OPT_GOAL` is a knob in this module — by exploiting that both instances see one
stimulus; fuzz (10) adds data × divisor volume. `div` coverage is
small-but-representative because the divisor only scales timing, never logic.

### Note on test 9's contrast

The glitch is placed at the synchronized bit-center sample so AREA's single
sample flips while SPEED's 3-vote survives. **Hard requirement:** the SPEED byte
is correct under the glitch. The AREA-mis-samples half is illustrative — if
aligning it deterministically proves finicky, it drops to a soft/observational
check rather than a gate.

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
