"""cocotb tests for uart_rx.v (8N1, LSB-first UART receiver).

Refines docs/verification/uart_rx.md. Two DUT instances (OPT_GOAL "AREA" and
"SPEED") share one stimulus so the SPEED majority vote can be contrasted with
the AREA single sample on identical input. The reusable UART transmitter model
(test/common/uart_model.py) drives dut.rx and is the golden reference.

Repo cocotb rules: the Clock is restarted in every test's setup (start_soon
tasks are cancelled at end of each test); every coroutine ends with an
unconditional trailing `await ClockCycles(dut.clk, 1)` after any try/except to
avoid the known Icarus/cocotb FST-teardown segfault.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

from common.uart_model import UartTxModel

DIVS = [3, 7, 15]


class Collector:
    """Background task recording one entry per strobe on each instance.

    Because both instances share timing (the FSM advances on `cnt`/`div`, never
    on the sampled data), on any given frame they strobe on the same cycle; only
    the captured `data`/`frame_err` can differ (the SPEED-vs-AREA contrast).
    One event per byte => also proves `strobe` is a single-cycle pulse.
    """

    def __init__(self, dut):
        self.dut = dut
        self.events_a = []   # (data, frame_err) per strobe, AREA instance
        self.events_s = []   # (data, frame_err) per strobe, SPEED instance

    async def run(self):
        while True:
            await RisingEdge(self.dut.clk)
            if int(self.dut.strobe_a.value) == 1:
                self.events_a.append(
                    (int(self.dut.data_a.value), int(self.dut.frame_err_a.value)))
            if int(self.dut.strobe_s.value) == 1:
                self.events_s.append(
                    (int(self.dut.data_s.value), int(self.dut.frame_err_s.value)))


async def setup(dut, div):
    """Restart the clock, apply reset, idle the line high, arm the collector."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.div.value = div
    dut.rx.value = 1                 # idle high
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    tx = UartTxModel(dut.rx, dut.clk)
    col = Collector(dut)
    cocotb.start_soon(col.run())
    return tx, col


async def send(dut, tx, col, byte, div, **kw):
    """Send one frame, then settle so its strobe is recorded before we return."""
    await tx.send_frame(byte, div, **kw)
    await ClockCycles(dut.clk, 3)


# ---------------------------------------------------------------------------
# 1. Single byte loopback (both instances)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_single_byte(dut):
    div = 7
    tx, col = await setup(dut, div)
    val = 0x5A
    await send(dut, tx, col, val, div)
    assert col.events_a == [(val, 0)], f"AREA {col.events_a}"
    assert col.events_s == [(val, 0)], f"SPEED {col.events_s}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 2. LSB-first bit ordering
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lsb_first_order(dut):
    div = 7
    tx, col = await setup(dut, div)
    for val in (0x01, 0x80, 0xA5):
        await send(dut, tx, col, val, div)
    expected = [(0x01, 0), (0x80, 0), (0xA5, 0)]
    assert col.events_a == expected, f"AREA {col.events_a}"
    assert col.events_s == expected, f"SPEED {col.events_s}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 3. Divisor sweep (mid-bit alignment scales with div)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_divisor_sweep(dut):
    tx, col = await setup(dut, DIVS[0])
    expected = []
    for div in DIVS:
        dut.div.value = div
        await ClockCycles(dut.clk, 2)   # let div settle before the frame
        val = 0x3C ^ div
        await send(dut, tx, col, val, div)
        expected.append((val, 0))
    assert col.events_a == expected, f"AREA {col.events_a}"
    assert col.events_s == expected, f"SPEED {col.events_s}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 4. Back-to-back bytes (returns to idle and re-arms; one strobe each)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_back_to_back(dut):
    div = 5
    tx, col = await setup(dut, div)
    vals = [0x00, 0xFF, 0x55, 0xAA, 0x42]
    for val in vals:
        await send(dut, tx, col, val, div)
        await tx.idle(div, bits=1)      # clean high gap between frames
    expected = [(v, 0) for v in vals]
    assert col.events_a == expected, f"AREA {col.events_a}"
    assert col.events_s == expected, f"SPEED {col.events_s}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 5. Frame error: bad stop bit still delivers the byte with frame_err set
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_frame_error(dut):
    div = 7
    tx, col = await setup(dut, div)
    val = 0x96
    await send(dut, tx, col, val, div, stop=0)
    assert col.events_a == [(val, 1)], f"AREA {col.events_a}"
    assert col.events_s == [(val, 1)], f"SPEED {col.events_s}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 6. False start: a sub-half-bit low glitch must not start a frame
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_false_start(dut):
    div = 15
    tx, col = await setup(dut, div)
    # Dip low for a few cycles (well under half a bit = (div+1)/2 = 8), release.
    dut.rx.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rx.value = 1
    await ClockCycles(dut.clk, (div + 1) * 12)   # wait out a would-be frame
    assert col.events_a == [], f"AREA spurious {col.events_a}"
    assert col.events_s == [], f"SPEED spurious {col.events_s}"
    # A real byte afterward is still received (line recovered cleanly).
    await send(dut, tx, col, 0x7E, div)
    assert col.events_a == [(0x7E, 0)], f"AREA {col.events_a}"
    assert col.events_s == [(0x7E, 0)], f"SPEED {col.events_s}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 7. Idle/reset: an idle-high line produces no strobe
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_idle_no_strobe(dut):
    div = 7
    tx, col = await setup(dut, div)
    await ClockCycles(dut.clk, (div + 1) * 12)
    assert col.events_a == [], f"AREA spurious {col.events_a}"
    assert col.events_s == [], f"SPEED spurious {col.events_s}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 8. strobe shape: exactly one pulse per byte (collector length == 1)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_strobe_single_pulse(dut):
    div = 7
    tx, col = await setup(dut, div)
    await send(dut, tx, col, 0xC3, div)
    assert len(col.events_a) == 1, f"AREA strobe count {len(col.events_a)}"
    assert len(col.events_s) == 1, f"SPEED strobe count {len(col.events_s)}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 9. SPEED noise rejection: a single-cycle glitch at a data-bit center is
#    voted out by SPEED; AREA (single sample) is the contrast.
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_speed_noise_rejection(dut):
    div = 7                              # odd div → receiver samples land on
    tx, col = await setup(dut, div)      #   the model's bit centers
    val = 0xFF                           # every data bit = 1
    # Flip bit d3 low for exactly one cycle at its center.
    await send(dut, tx, col, val, div, glitch=("d3", 0))
    # Hard requirement: the majority vote recovers the correct byte.
    assert col.events_s == [(val, 0)], f"SPEED failed to reject glitch {col.events_s}"
    # Illustrative contrast: AREA's single sample is expected to mis-read d3.
    # Kept observational (not a gate) — see docs/verification/uart_rx.md.
    area_val, _ = col.events_a[0]
    if area_val == val:
        cocotb.log.info("AREA happened to sample cleanly (glitch missed its "
                        "sample instant); SPEED still verified robust")
    else:
        cocotb.log.info("AREA mis-sampled the glitch (0x%02X vs 0x%02X); SPEED "
                        "majority vote rejected it", area_val, val)
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 10. Randomized fuzz over data x divisor
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_random_fuzz(dut):
    random.seed(0x5A17)
    div = DIVS[0]
    tx, col = await setup(dut, div)
    expected = []
    for _ in range(200):
        div = random.choice(DIVS)
        dut.div.value = div
        await ClockCycles(dut.clk, 2)
        val = random.randint(0, 255)
        await send(dut, tx, col, val, div)
        await tx.idle(div, bits=1)
        expected.append((val, 0))
    assert col.events_a == expected, "AREA fuzz mismatch"
    assert col.events_s == expected, "SPEED fuzz mismatch"
    await ClockCycles(dut.clk, 1)
