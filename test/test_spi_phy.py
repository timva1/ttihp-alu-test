# SPDX-License-Identifier: Apache-2.0
#
# Verification for src/mem/spi_phy.v — SPI mode-0 byte shifter + SCK divider.
#
# The reference is a Python SPI mode-0 slave model. SPI is a full-duplex identity
# shift (MSB first), so the slave model *is* the golden reference: it presents
# its first MISO bit the moment `start` is asserted (before the first rising
# edge — what makes div=1 correct), advances MISO on each SCK falling edge, and
# captures MOSI on each SCK rising edge (the edge a real 23-/25-series chip
# samples). See docs/verification/spi_phy.md.
#
# Repo cocotb rules: the clock is restarted per-test in setup(); every test ends
# with an unconditional trailing `await ClockCycles(dut.clk, 1)`.

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer


def eff_of(div):
    """Effective divisor after the hardware 0->1 clamp (Section 5.2)."""
    div &= 0xFF
    return div if div != 0 else 1


async def setup(dut):
    """Per-test setup: (re)start the clock and apply reset. The clock MUST be
    restarted every test — start_soon tasks are cancelled at test end."""
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    dut.div.value = 4
    dut.start.value = 0
    dut.tx_byte.value = 0
    dut.miso.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 1)


async def do_transfer(dut, tx, resp, div):
    """Frame one byte over the start/busy/done handshake while acting as the
    SPI mode-0 slave. Returns (mosi_seen_by_slave, rx_byte)."""
    dut.div.value = div
    dut.tx_byte.value = tx
    dut.miso.value = (resp >> 7) & 1      # present bit7 before the first rising edge
    dut.start.value = 1
    await RisingEdge(dut.clk)              # start accepted here
    dut.start.value = 0

    captured = 0
    for i in range(8):
        await RisingEdge(dut.sck)          # master samples MISO; slave samples MOSI
        captured = (captured << 1) | int(dut.mosi.value)
        if i < 7:
            await FallingEdge(dut.sck)      # slave drives next MISO bit
            dut.miso.value = (resp >> (6 - i)) & 1

    # After the 8th rising edge, the final falling edge (16th half-period) pulses done.
    # Settle past the done clock edge before sampling rx_byte: both are updated by
    # the same non-blocking assignment, so reading in that exact delta would catch
    # the pre-update value. rx_byte is held after done, so the settle is safe.
    await RisingEdge(dut.done)
    await Timer(1, units='ns')
    rx = int(dut.rx_byte.value)
    return captured, rx


async def measure_latency(dut, div):
    """Cycles from start-accept to the done pulse. Data-independent."""
    dut.div.value = div
    dut.tx_byte.value = 0xA5
    dut.miso.value = 0
    dut.start.value = 1
    await RisingEdge(dut.clk)              # accept (e0)
    dut.start.value = 0
    cycles = 0
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, units='ns')   # settle past the NBA before sampling done
        cycles += 1
        if int(dut.done.value) == 1:
            break
    return cycles


# ---------------------------------------------------------------------------
# 1-3: data path (MOSI, MISO->rx_byte, full-duplex)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_mosi_and_miso(dut):
    await setup(dut)
    for tx, resp in [(0x00, 0xFF), (0xFF, 0x00), (0xA5, 0x5A),
                     (0x01, 0x80), (0x80, 0x01), (0x3C, 0xC3)]:
        mosi, rx = await do_transfer(dut, tx, resp, div=2)
        assert mosi == tx, f"MOSI: sent 0x{tx:02X}, slave saw 0x{mosi:02X}"
        assert rx == resp, f"rx_byte: slave sent 0x{resp:02X}, got 0x{rx:02X}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 4: handshake shape (busy span, single-cycle done, rx valid at done)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_handshake(dut):
    await setup(dut)
    dut.div.value = 2
    dut.tx_byte.value = 0x3C
    dut.miso.value = 0
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    await Timer(1, units='ns')
    assert int(dut.busy.value) == 1, "busy should be high right after accept"

    await RisingEdge(dut.done)
    await Timer(1, units='ns')
    assert int(dut.busy.value) == 0, "busy must be low in the done cycle"
    assert int(dut.done.value) == 1, "done must be asserted this cycle"

    await RisingEdge(dut.clk)
    await Timer(1, units='ns')
    assert int(dut.done.value) == 0, "done must be a single-cycle pulse"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 5: latency == 16 * eff_div across divisors
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_latency(dut):
    await setup(dut)
    for div in (1, 2, 4, 8):
        cycles = await measure_latency(dut, div)
        exp = 16 * eff_of(div)
        assert cycles == exp, f"div={div}: latency {cycles} cycles, expected {exp}"
        await ClockCycles(dut.clk, 2)
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 6: divisor 0 is clamped to 1 (reserved value, Section 5.2)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_divisor_clamp(dut):
    await setup(dut)
    dut.div.value = 0
    await Timer(1, units='ns')
    assert int(dut.eff_div.value) == 1, "div=0 must clamp eff_div to 1"
    cycles = await measure_latency(dut, 0)
    assert cycles == 16, f"div=0 latency {cycles}, expected 16 (same as div=1)"
    # data still correct at the clamped rate
    mosi, rx = await do_transfer(dut, 0x9E, 0x71, div=0)
    assert mosi == 0x9E and rx == 0x71
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 7: eff_div export == (div==0 ? 1 : div)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_eff_div_export(dut):
    await setup(dut)
    for div in (0, 1, 2, 4, 7, 8, 16, 255):
        dut.div.value = div
        await Timer(1, units='ns')
        assert int(dut.eff_div.value) == eff_of(div), \
            f"div={div}: eff_div={int(dut.eff_div.value)}, expected {eff_of(div)}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 8: idle output levels after reset and between transfers
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_idle_levels(dut):
    await setup(dut)
    await Timer(1, units='ns')
    assert int(dut.sck.value) == 0 and int(dut.mosi.value) == 0, "idle sck/mosi must be low"
    assert int(dut.busy.value) == 0 and int(dut.done.value) == 0, "idle busy/done must be low"

    await do_transfer(dut, 0x12, 0x34, div=4)
    await ClockCycles(dut.clk, 3)
    await Timer(1, units='ns')
    assert int(dut.sck.value) == 0 and int(dut.mosi.value) == 0, "post-transfer sck/mosi must be low"
    assert int(dut.busy.value) == 0 and int(dut.done.value) == 0, "post-transfer busy/done must be low"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 9: back-to-back transfers (controller streaming a frame; gaps allowed)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_back_to_back(dut):
    await setup(dut)
    frame = [(0x03, 0x00), (0x12, 0xAA), (0x34, 0x55), (0x56, 0xF0)]
    for tx, resp in frame:
        mosi, rx = await do_transfer(dut, tx, resp, div=2)
        assert mosi == tx, f"stream MOSI: sent 0x{tx:02X}, saw 0x{mosi:02X}"
        assert rx == resp, f"stream rx: sent 0x{resp:02X}, got 0x{rx:02X}"
        # re-issue immediately on the cycle after done (no idle gap required)
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 10: start ignored while busy; mid-transfer tx_byte change has no effect
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_start_ignored_while_busy(dut):
    await setup(dut)
    tx0, resp0 = 0xAA, 0x55
    dut.div.value = 2
    dut.tx_byte.value = tx0
    dut.miso.value = (resp0 >> 7) & 1
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    captured = 0
    for i in range(8):
        await RisingEdge(dut.sck)
        captured = (captured << 1) | int(dut.mosi.value)
        if i == 3:
            # spurious re-start with a different byte, mid-transfer
            dut.tx_byte.value = 0xFF
            dut.start.value = 1
        if i < 7:
            await FallingEdge(dut.sck)
            dut.miso.value = (resp0 >> (6 - i)) & 1
        if i == 3:
            dut.start.value = 0

    await RisingEdge(dut.done)
    await Timer(1, units='ns')       # settle before sampling rx_byte (see do_transfer)
    rx = int(dut.rx_byte.value)
    assert captured == tx0, f"in-flight byte corrupted: saw 0x{captured:02X}, expected 0x{tx0:02X}"
    assert rx == resp0, f"rx corrupted: got 0x{rx:02X}, expected 0x{resp0:02X}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 11: SCK waveform shape — exactly 8 rising edges, starts and ends low
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sck_shape(dut):
    await setup(dut)
    await Timer(1, units='ns')
    assert int(dut.sck.value) == 0, "sck must idle low before transfer"

    rises = 0

    async def count_rises():
        nonlocal rises
        while True:
            await RisingEdge(dut.sck)
            rises += 1

    counter = cocotb.start_soon(count_rises())
    mosi, rx = await do_transfer(dut, 0x5A, 0xC3, div=2)
    await ClockCycles(dut.clk, 3)          # allow any spurious extra edge to appear
    counter.cancel()

    assert rises == 8, f"expected 8 SCK rising edges per byte, counted {rises}"
    assert int(dut.sck.value) == 0, "sck must return low after transfer"
    assert mosi == 0x5A and rx == 0xC3
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 12: randomized fuzz over data x divisor
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_fuzz(dut):
    await setup(dut)
    rnd = random.Random(0xC0FFEE)
    for _ in range(200):
        tx = rnd.randint(0, 0xFF)
        resp = rnd.randint(0, 0xFF)
        div = rnd.choice([0, 1, 2, 4])
        mosi, rx = await do_transfer(dut, tx, resp, div)
        assert mosi == tx, f"fuzz MOSI div={div}: sent 0x{tx:02X}, saw 0x{mosi:02X}"
        assert rx == resp, f"fuzz rx div={div}: sent 0x{resp:02X}, got 0x{rx:02X}"
    await ClockCycles(dut.clk, 1)
