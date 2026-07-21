# `i2c_master.v` — verification plan

Verification contract for the I2C master (`docs/microarchitecture/i2c_master.md`,
refining `docs/architecture.md` §6.2 / §9). Kept in sync with
`test/test_i2c_master.py` + `test/test_modules/i2c_master_tb.v`.

## Method

- **Tool: cocotb** (Icarus). A single module under test — not an integration of
  >2 independently-developed submodules — so cocotb, not UVM (§9 convention).
- **Reference: a Python I2C slave model** (`I2CSlave`), the golden reference for
  the byte-level protocol — the same shape `spi_phy` uses for its SPI slave
  model. §9 calls for exactly this: "Python I2C slave model, incl. clock
  stretching and NACK." The slave:
  - detects a **START** (SDA falls while SCL high) and **STOP** (SDA rises while
    SCL high);
  - after a start, shifts the master's byte in **MSB first** on each SCL rising
    edge (8 data bits), then drives **ACK** by pulling SDA low on the 9th clock
    (or withholds it to NACK, configurable);
  - on a read, shifts a byte out MSB first, changing SDA while SCL is low, and
    on the 9th clock samples the master's ACK/NACK;
  - can optionally **stretch** by holding SCL low for a configurable number of
    sysclk cycles after the master releases it;
  - records every byte/direction/ack it saw, and exposes a small register file
    so a full register-read transaction can be checked end to end.

## Scaffolding

- `test/test_modules/i2c_master_tb.v` — one `i2c_master` instance. Models the
  **open-drain wired-AND with pull-ups**: the master exposes `scl_oe`/`sda_oe`;
  the Python slave drives `slave_scl_low`/`slave_sda_low` (`reg`, default 0);
  the tb forms
  `scl_i = ~(scl_oe | slave_scl_low)`, `sda_i = ~(sda_oe | slave_sda_low)`
  (released = high via the modeled pull-up). `access`/`addr`/`we`/`wdata`/`clk`/
  `rst_n` are `reg`; `rdata`/`scl_oe`/`sda_oe` are `wire`. Dumps
  `waves/i2c_master_tb.vcd`.
- `test/test_i2c_master.py` — the `I2CSlave` model, the helpers, and the tests
  below.

### Helpers

- `reg_write(dut, addr, data)` / `reg_read(dut, addr)` — single-cycle `access`
  strobes that mirror the `periph_regs` contract (one pulse per bus access).
- `i2c_cmd(dut, cmd, data)` — writes `I2C_CMD = (cmd<<8)|data`, then polls
  `I2C_STATUS.busy` until 0 (the software poll-first loop).
- Tests write a **small `I2C_DIV`** (e.g. 2) before transfers to keep sims fast;
  `DIV_RST`'s 124 is exercised only implicitly (register readback), not for byte
  content. Timing test 10 sweeps small divisors.

## Scenarios

| # | Test | What it asserts | Why |
|---|---|---|---|
| 1 | Reset / idle levels | after reset: `scl_oe = sda_oe = 0` (released), `busy = 0`, `nack = 0` | reset spec, open-drain idle |
| 2 | START edge | slave detects SDA↓ while SCL high after a `START` primitive | start condition generation |
| 3 | STOP edge | slave detects SDA↑ while SCL high; both lines released afterwards | stop condition + bus-idle release |
| 4 | WRITE + ACK | slave captures 8 bits == data (MSB first); `nack == 0` | TX path, bit order, ack sampling |
| 5 | WRITE + NACK | slave withholds ACK → `nack == 1`, still set across repeated `STATUS` reads | NACK capture, sticky until next command (§6.2) |
| 6 | READ_ACK | `I2C_DATA == ` slave's byte (MSB first); slave sees master ACK (SDA low on 9th clock) | RX path, bit order, master-driven ACK |
| 7 | READ_NACK | byte correct; slave sees master NACK (SDA released on 9th clock) | last-byte NACK |
| 8 | Full register read | `START, WRITE(addr\|W), WRITE(reg), START, WRITE(addr\|R), READ_ACK×3, READ_NACK, STOP` → 4 assembled bytes == the slave's register contents | repeated-START as a first-class primitive + the inter-primitive line-state contract |
| 9 | Clock stretching | slave holds SCL low N sysclk cycles after the master releases it (RISE phase); the master waits (SCL high time extends by ~N) and the transferred byte/ack is still correct | clock stretching for free (§6.2) |
| 10 | SCL timing | measured SCL period == `4·(div+1)` sysclk, swept div ∈ {1, 2, 4} | quarter-period phase engine (§6.2) |
| 11 | Write-while-busy ignored | a mid-primitive `I2C_CMD` write with a different cmd/data neither restarts nor corrupts the in-flight byte or its timing | poll-first rule (§6.2) |
| 12 | Undefined command | writing cmd `101`–`111`: `busy` never asserts and the bus stays idle (no `scl_oe`/`sda_oe` activity) | undefined encodings ignored (§6) |
| 13 | nack clears on next command | after a NACKed WRITE (`nack == 1`), the next accepted command clears `nack` to 0 (before any new WRITE could re-raise it) | sticky-`nack` semantics (agreed decision #2) |

The `I2CSlave` model is the golden reference for scenarios 2–9; the remaining
scenarios (10–13) are directed checks of the register/timing contract that the
slave model does not by itself observe.

## cocotb rules (repo conventions, CLAUDE.md)

- Every `@cocotb.test()` restarts the clock in its per-test setup (no
  module-level `_clock_started` flag) and restarts the `I2CSlave` task via
  `start_soon`.
- Every test ends with an **unconditional** trailing `await` after any
  `try/except`, so the coroutine never returns in the same delta cycle as its
  last signal write (avoids the FST/VCD teardown segfault).
