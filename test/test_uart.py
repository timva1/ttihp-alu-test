"""cocotb tests for uart.v — the UART register/buffer glue (8N1).

Refines docs/verification/uart.md. A single `uart` DUT is exercised through its
register-access port (access/addr/we/wdata → rdata) and its serial pins. The
reusable models in test/common/uart_model.py are the golden references:
UartRxModel samples dut.tx (what a correct transmitter put on the wire) and
UartTxModel drives dut.rx (bytes into the receiver, with stop=0 to force a
framing error).

Scope: this is an *integration* bench for the glue's policy — the three
registers, the RX buffer, the status bits, the decode, and the two agreed
collision rules. The halves' bit-timing/sampling is already covered by
test_uart_tx / test_uart_rx, so small divisors are used purely to keep frames
short.

Repo cocotb rules: the Clock is restarted in every test's setup (start_soon
tasks are cancelled per-test); every coroutine ends with an unconditional
trailing `await ClockCycles(dut.clk, 1)` after any try/except (the known
Icarus/cocotb FST-teardown segfault guard).
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from common.uart_model import UartTxModel, UartRxModel

# Register offsets within the UART window (req_addr[3:0]).
DATA = 0x0
STATUS = 0x4
DIV = 0x8

# Status bit positions.
ST_TX_BUSY = 0
ST_RX_VALID = 1
ST_RX_OVERRUN = 2
ST_RX_FRAME_ERR = 3

DIV_RST = 433          # uart.v DIV_RST default (UART_DIV_RST)
DIVS = [3, 7, 15]      # short frames for the glue-level bench


# ---------------------------------------------------------------------------
# Register-access helpers — drive the single-cycle `access` strobe edge-aligned.
# ---------------------------------------------------------------------------
async def bus_write(dut, off, val):
    """Write `val` to register `off` (one accepted access cycle)."""
    await RisingEdge(dut.clk)
    dut.addr.value = off
    dut.wdata.value = val
    dut.we.value = 1
    dut.access.value = 1
    await RisingEdge(dut.clk)            # write registered on this edge
    dut.access.value = 0
    dut.we.value = 0


async def bus_read(dut, off):
    """Read register `off`. Returns the combinational `rdata` (pre-edge state);
    the read side effect (RX pop / sticky clear) fires on the closing edge."""
    await RisingEdge(dut.clk)
    dut.addr.value = off
    dut.we.value = 0
    dut.access.value = 1
    await Timer(1, unit="ns")           # let rdata settle combinationally
    val = int(dut.rdata.value)
    await RisingEdge(dut.clk)           # read side effect registered on this edge
    dut.access.value = 0
    return val


async def setup(dut, div=7, write_div=True):
    """Restart the clock, reset, idle the bus and rx line; optionally program
    UART_DIV to a short working divisor."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.access.value = 0
    dut.we.value = 0
    dut.addr.value = 0
    dut.wdata.value = 0
    dut.rx.value = 1                     # idle high
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    if write_div:
        await bus_write(dut, DIV, div)


# ---------------------------------------------------------------------------
# 1. UART_DIV: reset value, read/write round-trip, no clamp
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_div_reset_and_rw(dut):
    await setup(dut, write_div=False)
    assert (await bus_read(dut, DIV)) == DIV_RST, "UART_DIV reset != DIV_RST"
    for v in (7, 0, 0x00FF, 0xFFFF, 0x1234):
        await bus_write(dut, DIV, v)
        # bits[15:0] round-trip, upper bits read 0, no clamp (0 stays 0).
        assert (await bus_read(dut, DIV)) == v, f"UART_DIV rw failed for {v:#06x}"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 2. TX: a UART_DATA write is loopback-read on `tx` across divisors
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_tx_write_loopback(dut):
    await setup(dut)
    for div in DIVS:
        await bus_write(dut, DIV, div)
        for byte in (0x5A, 0x01, 0x80, 0xFF):
            rxm = UartRxModel(dut.tx, dut.clk)
            cap = cocotb.start_soon(rxm.recv_frame(div))
            await bus_write(dut, DATA, byte)
            got, stop = await cap
            assert (got, stop) == (byte, 1), \
                f"div {div} byte 0x{byte:02X}: got {(got, stop)}"
            # wait out the frame so the next write sees an idle transmitter
            while (await bus_read(dut, STATUS)) & (1 << ST_TX_BUSY):
                pass
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 3. tx_busy (STATUS bit0): rises after a DATA write, drops at frame end
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_tx_busy_poll(dut):
    div = 7
    await setup(dut, div)
    assert (await bus_read(dut, STATUS)) & (1 << ST_TX_BUSY) == 0, "busy set while idle"
    await bus_write(dut, DATA, 0x5A)
    assert (await bus_read(dut, STATUS)) & (1 << ST_TX_BUSY), "tx_busy not set after write"
    dropped = False
    for _ in range(12 * (div + 1)):
        if (await bus_read(dut, STATUS)) & (1 << ST_TX_BUSY) == 0:
            dropped = True
            break
    assert dropped, "tx_busy never dropped"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 4. Write while tx_busy is dropped (poll-first): only the first byte is sent
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_tx_write_while_busy_dropped(dut):
    div = 7
    await setup(dut, div)
    rxm = UartRxModel(dut.tx, dut.clk)
    cap = cocotb.start_soon(rxm.recv_frame(div))
    await bus_write(dut, DATA, 0xA1)                # frame A
    await ClockCycles(dut.clk, 3 * (div + 1))       # mid-frame A, still busy
    assert (await bus_read(dut, STATUS)) & (1 << ST_TX_BUSY), "expected still busy"
    await bus_write(dut, DATA, 0x3C)                # B: must be dropped
    got, stop = await cap
    assert (got, stop) == (0xA1, 1), f"in-flight byte disturbed: {(got, stop)}"
    # No second frame: tx must stay idle-high (no start bit) for a full frame.
    saw_low = False
    for _ in range(12 * (div + 1)):
        if int(dut.tx.value) == 0:
            saw_low = True
        await RisingEdge(dut.clk)
    assert not saw_low, "second write was not dropped (B transmitted)"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 5. RX: a driven byte sets rx_valid, is read from UART_DATA, and pops
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_rx_receive_and_pop(dut):
    div = 7
    await setup(dut, div)
    txm = UartTxModel(dut.rx, dut.clk)
    byte = 0x9C
    await txm.send_frame(byte, div)
    await ClockCycles(dut.clk, 2)                   # let the byte land in the buffer
    assert (await bus_read(dut, STATUS)) & (1 << ST_RX_VALID), "rx_valid not set"
    assert (await bus_read(dut, DATA)) & 0xFF == byte, "wrong byte read"
    # popped: rx_valid clear, DATA reads 0
    assert (await bus_read(dut, STATUS)) & (1 << ST_RX_VALID) == 0, "rx_valid not popped"
    assert (await bus_read(dut, DATA)) == 0, "empty read did not return 0"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 6. Reading UART_DATA while empty returns 0 with no side effect
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_rx_read_empty(dut):
    await setup(dut)
    assert int(dut.rx_valid_o.value) == 0, "rx_valid set after reset"
    for _ in range(3):
        assert (await bus_read(dut, DATA)) == 0, "empty DATA read != 0"
        assert int(dut.rx_valid_o.value) == 0, "empty read had a side effect"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 7. Overrun: a byte arriving while the buffer is full is dropped, sets bit2
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_rx_overrun(dut):
    div = 7
    await setup(dut, div)
    txm = UartTxModel(dut.rx, dut.clk)
    first, second = 0x33, 0xCC
    await txm.send_frame(first, div)                # buffer now full
    await txm.send_frame(second, div)               # arrives while full → dropped
    await ClockCycles(dut.clk, 2)
    st = await bus_read(dut, STATUS)                 # captures overrun, then clears it
    assert st & (1 << ST_RX_OVERRUN), "overrun not set on second byte"
    assert st & (1 << ST_RX_VALID), "rx_valid lost"
    assert (await bus_read(dut, STATUS)) & (1 << ST_RX_OVERRUN) == 0, \
        "overrun not cleared on STATUS read"
    assert (await bus_read(dut, DATA)) & 0xFF == first, "buffer did not retain first byte"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 8. Framing error: a bad stop bit sets bit3 (sticky), cleared on STATUS read
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_rx_frame_err(dut):
    div = 7
    await setup(dut, div)
    txm = UartTxModel(dut.rx, dut.clk)
    await txm.send_frame(0x5A, div, stop=0)         # framing error
    await ClockCycles(dut.clk, 2)
    assert (await bus_read(dut, STATUS)) & (1 << ST_RX_FRAME_ERR), "frame_err not set"
    assert (await bus_read(dut, STATUS)) & (1 << ST_RX_FRAME_ERR) == 0, \
        "frame_err not cleared on STATUS read"
    await bus_read(dut, DATA)                        # drain the errored byte
    # A subsequent clean byte leaves frame_err clear.
    await txm.send_frame(0x42, div)
    await ClockCycles(dut.clk, 2)
    assert (await bus_read(dut, STATUS)) & (1 << ST_RX_FRAME_ERR) == 0, \
        "frame_err set on a clean byte"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 9. Status bit layout, read-only STATUS, undefined offsets
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_status_layout_and_undefined(dut):
    div = 7
    await setup(dut, div)
    # DIV occupies bits[15:0]; upper bits read 0.
    await bus_write(dut, DIV, 0xABCD)
    assert (await bus_read(dut, DIV)) == 0x0000ABCD, "DIV value/upper-bits wrong"
    await bus_write(dut, DIV, div)                  # restore a working divisor
    # Receive a byte → rx_valid in bit1, all bits >= 4 zero.
    txm = UartTxModel(dut.rx, dut.clk)
    await txm.send_frame(0x11, div)
    await ClockCycles(dut.clk, 2)
    st = await bus_read(dut, STATUS)
    assert (st >> ST_RX_VALID) & 1 == 1, "rx_valid not in bit1"
    assert (st >> 4) == 0, "status upper bits not zero"
    # A write to (read-only) STATUS must not disturb state.
    await bus_write(dut, STATUS, 0xFFFFFFFF)
    assert (await bus_read(dut, STATUS)) & (1 << ST_RX_VALID), "STATUS write cleared rx_valid"
    # Undefined offset: read 0, write ignored (no crash, no state change).
    await bus_write(dut, 0xC, 0xDEADBEEF)
    assert (await bus_read(dut, 0xC)) == 0, "undefined offset did not read 0"
    assert (await bus_read(dut, DATA)) & 0xFF == 0x11, "byte corrupted by stray accesses"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 10. Collision 4a: RX byte lands the same cycle a UART_DATA read pops it →
#     old byte returned, new byte buffered, no spurious overrun
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_collision_4a_no_spurious_overrun(dut):
    div = 7
    await setup(dut, div)
    old_byte, new_byte = 0xA5, 0x3C
    txm = UartTxModel(dut.rx, dut.clk)
    await txm.send_frame(old_byte, div)             # preload the buffer
    await ClockCycles(dut.clk, 2)
    assert int(dut.rx_valid_o.value) == 1, "preload failed"
    # Second frame: catch the exact cycle its strobe fires and, in that same
    # cycle, issue the popping UART_DATA read so the DUT sees rx_strobe & rd_data
    # together on the next (collision) edge. Trigger on the strobe's own rising
    # edge, not a clock poll: an immediate post-RisingEdge(clk) read of the
    # strobe returns the pre-edge value in this Icarus/cocotb build, which would
    # detect the 1-cycle pulse a cycle late and land the pop after the byte has
    # already been dropped.
    send = cocotb.start_soon(txm.send_frame(new_byte, div))
    await RisingEdge(dut.dbg_rx_strobe)             # strobe just went high (cycle E)
    dut.addr.value = DATA
    dut.we.value = 0
    dut.access.value = 1                           # asserted DURING the strobe-high cycle
    await Timer(1, unit="ns")
    got_old = int(dut.rdata.value) & 0xFF           # pre-latch state → old byte
    await RisingEdge(dut.clk)                        # edge ending cycle E → collision resolves
    dut.access.value = 0
    await send
    assert got_old == old_byte, f"racing read returned 0x{got_old:02X}, not old byte"
    assert int(dut.rx_valid_o.value) == 1, "buffer went empty (new byte lost)"
    assert (await bus_read(dut, STATUS)) & (1 << ST_RX_OVERRUN) == 0, \
        "spurious overrun on pop/arrive collision"
    assert (await bus_read(dut, DATA)) & 0xFF == new_byte, "new byte not buffered"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 11. Collision 4b: RX error byte completes the same cycle STATUS is read →
#     that read sees the clean pre-edge status; the error survives (set wins)
#     and is visible on the next STATUS read
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_collision_4b_set_wins(dut):
    div = 7
    await setup(dut, div)
    txm = UartTxModel(dut.rx, dut.clk)
    send = cocotb.start_soon(txm.send_frame(0x66, div, stop=0))   # bad-stop byte
    # Trigger on the strobe edge (see test_collision_4a): a clock poll would
    # detect the 1-cycle strobe a cycle late, after the byte already latched.
    await RisingEdge(dut.dbg_rx_strobe)            # strobe just went high (cycle E)
    dut.addr.value = STATUS
    dut.we.value = 0
    dut.access.value = 1                          # STATUS read DURING the strobe-high cycle
    await Timer(1, unit="ns")
    racing = int(dut.rdata.value)                   # pre-latch status: error not yet set
    await RisingEdge(dut.clk)                        # set-wins: frame_err latches here
    dut.access.value = 0
    await send
    assert (racing >> ST_RX_FRAME_ERR) & 1 == 0, \
        "racing STATUS read should not yet see the framing error"
    st = await bus_read(dut, STATUS)                 # error visible now
    assert (st >> ST_RX_FRAME_ERR) & 1 == 1, "frame_err lost (set did not win over clear)"
    st2 = await bus_read(dut, STATUS)               # and cleared by that read
    assert (st2 >> ST_RX_FRAME_ERR) & 1 == 0, "frame_err not cleared on read"
    await ClockCycles(dut.clk, 1)


# ---------------------------------------------------------------------------
# 12. Randomized full loopback: interleave TX and RX at random divisors
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_full_loopback_fuzz(dut):
    random.seed(0x0A27)
    await setup(dut, DIVS[0])
    for _ in range(40):
        div = random.choice(DIVS)
        await bus_write(dut, DIV, div)
        if random.random() < 0.5:
            byte = random.randint(0, 255)
            while (await bus_read(dut, STATUS)) & (1 << ST_TX_BUSY):   # poll-first
                pass
            rxm = UartRxModel(dut.tx, dut.clk)
            cap = cocotb.start_soon(rxm.recv_frame(div))
            await bus_write(dut, DATA, byte)
            got, stop = await cap
            assert (got, stop) == (byte, 1), f"TX div {div} 0x{byte:02X}: {(got, stop)}"
            while (await bus_read(dut, STATUS)) & (1 << ST_TX_BUSY):
                pass
        else:
            byte = random.randint(0, 255)
            txm = UartTxModel(dut.rx, dut.clk)
            await txm.send_frame(byte, div)
            await ClockCycles(dut.clk, 2)
            assert (await bus_read(dut, STATUS)) & (1 << ST_RX_VALID), \
                f"RX div {div} 0x{byte:02X}: rx_valid not set"
            got = await bus_read(dut, DATA)
            assert got & 0xFF == byte, f"RX div {div}: got 0x{got:02X}, expected 0x{byte:02X}"
        await ClockCycles(dut.clk, 2)
    await ClockCycles(dut.clk, 1)
