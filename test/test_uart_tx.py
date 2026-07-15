"""cocotb tests for uart_tx.v (8N1, LSB-first UART transmitter).

Refines docs/verification/uart_tx.md. A single DUT instance (no OPT_GOAL) is
driven via `start`/`data`/`div`; the reusable UART receiver model
(test/common/uart_model.py, UartRxModel) samples dut.tx and is the golden
reference — whatever byte a correct transmitter puts on the wire, a correct
sampler reads back.

Repo cocotb rules: the Clock is restarted in every test's setup (start_soon
tasks are cancelled at end of each test); every coroutine ends with an
unconditional trailing `await ClockCycles(dut.clk, 1)` after any try/except to
avoid the known Icarus/cocotb FST-teardown segfault.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from common.uart_model import UartRxModel

DIVS = [3, 7, 15]


async def setup(dut, div):
    """Restart the clock, apply reset, idle the transmitter (no start)."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.div.value = div
    dut.data.value = 0
    dut.start.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def wait_idle(dut):
    """Poll tx_busy until the transmitter is idle — the real uart.v "poll first"
    rule. `recv_frame` returns at the *center* of the stop bit, ~half a bit
    before the DUT drops `busy` and returns to IDLE, so callers must wait out the
    rest of the frame before writing the next byte (else the still-busy DUT
    correctly ignores the new start and no frame is sent)."""
    while int(dut.busy.value) == 1:
        await RisingEdge(dut.clk)


async def pulse_start(dut, byte):
    """Drive a one-cycle `start` with `data`, edge-aligned so the DUT samples it
    cleanly. Setting `start` right after an edge holds it stable for a full cycle
    before the accept edge, avoiding the cocotb write/next-edge race."""
    await RisingEdge(dut.clk)
    dut.data.value = byte
    dut.start.value = 1
    await RisingEdge(dut.clk)                # DUT accepts start on this edge
    dut.start.value = 0


async def xmit(dut, byte, div):
    """Transmit one byte and return what the receiver model reads: (byte, stop).

    Honors the poll-first protocol: waits for the transmitter to be idle, arms
    the sampler *before* pulsing start (so it latches the idle-high line first),
    then waits out the rest of the frame so the next call sees an idle DUT.
    """
    dut.div.value = div
    await wait_idle(dut)                     # poll-first before writing
    rx = UartRxModel(dut.tx, dut.clk)
    cap = cocotb.start_soon(rx.recv_frame(div))
    await pulse_start(dut, byte)
    result = await cap
    await wait_idle(dut)                     # let the frame fully complete
    return result


# ---------------------------------------------------------------------------
# 1. Single byte loopback
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_single_byte(dut):
    div = 7
    await setup(dut, div)
    val = 0x5A
    got = await xmit(dut, val, div)
    assert got == (val, 1), f"got {got}, expected {(val, 1)}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 2. LSB-first bit ordering
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lsb_first_order(dut):
    div = 7
    await setup(dut, div)
    for val in (0x01, 0x80, 0xA5):
        got = await xmit(dut, val, div)
        assert got == (val, 1), f"val 0x{val:02X}: got {got}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 3. Divisor sweep (bit timing scales with div)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_divisor_sweep(dut):
    await setup(dut, DIVS[0])
    for div in DIVS:
        val = 0x3C ^ div
        got = await xmit(dut, val, div)
        assert got == (val, 1), f"div {div}: got {got}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 4. Back-to-back frames (returns to idle and re-arms; busy low between)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_back_to_back(dut):
    div = 5
    await setup(dut, div)
    for val in (0x00, 0xFF, 0x55, 0xAA, 0x42):
        got = await xmit(dut, val, div)
        assert got == (val, 1), f"val 0x{val:02X}: got {got}"
        assert int(dut.busy.value) == 0, "busy still set after frame"
        await ClockCycles(dut.clk, 2)       # clean idle gap between frames
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 5. busy timing: high for the whole frame, drops at the stop bit
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_busy_timing(dut):
    div = 7
    period = div + 1
    await setup(dut, div)
    dut.div.value = div
    rx = UartRxModel(dut.tx, dut.clk)
    cap = cocotb.start_soon(rx.recv_frame(div))
    await pulse_start(dut, 0x5A)            # accepted on the second edge
    await Timer(1, unit="ns")  # let the accept edge propagate to busy
    assert int(dut.busy.value) == 1, "busy did not rise on accepted start"
    # busy must stay high across the frame's interior bit periods.
    for _ in range(8 * period):
        assert int(dut.busy.value) == 1, "busy dropped mid-frame"
        await RisingEdge(dut.clk)
    got = await cap
    assert got == (0x5A, 1), f"got {got}"
    # busy must deassert by the end of the stop bit (within one more bit period).
    fell = False
    for _ in range(2 * period + 2):
        if int(dut.busy.value) == 0:
            fell = True
            break
        await RisingEdge(dut.clk)
    assert fell, "busy did not drop after the frame"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 6. start ignored while busy: a second start mid-frame is dropped
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_start_ignored_while_busy(dut):
    div = 7
    period = div + 1
    await setup(dut, div)
    dut.div.value = div
    rx = UartRxModel(dut.tx, dut.clk)
    cap = cocotb.start_soon(rx.recv_frame(div))
    await pulse_start(dut, 0xA1)             # frame A
    # Mid-frame A, try to inject byte B — must be ignored (DUT not idle).
    await ClockCycles(dut.clk, 3 * period)
    await pulse_start(dut, 0x3C)             # frame B (should be dropped)
    got = await cap
    assert got == (0xA1, 1), f"in-flight byte disturbed: {got}"
    # No second frame: tx must stay idle-high (no start-bit low) for a frame.
    saw_low = False
    for _ in range(12 * period):
        if int(dut.tx.value) == 0:
            saw_low = True
        await RisingEdge(dut.clk)
    assert not saw_low, "second start was not ignored (B transmitted)"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 7. Idle/reset: idle-high tx, busy low, no frame without start
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_idle_reset(dut):
    div = 7
    period = div + 1
    await setup(dut, div)
    assert int(dut.tx.value) == 1, "tx not idle-high after reset"
    assert int(dut.busy.value) == 0, "busy set after reset"
    for _ in range(12 * period):
        assert int(dut.tx.value) == 1, "spurious tx activity with no start"
        await RisingEdge(dut.clk)
    assert int(dut.busy.value) == 0, "busy set with no start"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 8. Randomized fuzz over data x divisor
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_random_fuzz(dut):
    random.seed(0x7A11)
    await setup(dut, DIVS[0])
    for _ in range(200):
        div = random.choice(DIVS)
        val = random.randint(0, 255)
        got = await xmit(dut, val, div)
        assert got == (val, 1), f"div {div} val 0x{val:02X}: got {got}"
        await ClockCycles(dut.clk, 2)
    await ClockCycles(dut.clk, 1)
