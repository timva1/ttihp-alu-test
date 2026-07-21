# SPDX-License-Identifier: Apache-2.0
#
# Verification for src/periph/i2c_master.v — the I2C master (single-primitive
# command engine: START / WRITE / READ_ACK / READ_NACK / STOP).
#
# The reference is a Python I2C slave model (I2CSlave). It watches the two
# open-drain lines (modeled in i2c_master_tb.v as a wired-AND with pull-ups):
# detects START/STOP (SDA edge while SCL high), shifts the master's byte in
# MSB-first on SCL rising edges, drives ACK on the 9th clock, and on reads
# shifts a byte out MSB-first (changing SDA while SCL is low) while sampling the
# master's ACK/NACK. It is the golden reference for the byte-level protocol —
# the same shape spi_phy uses for its SPI slave model. See
# docs/verification/i2c_master.md.
#
# Repo cocotb rules: the clock and the slave task are restarted per-test in
# setup(); every test ends with an unconditional trailing await.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Edge, First, ClockCycles, Timer
from cocotb.utils import get_sim_time

# ---- register offsets (req_addr[3:0]) ----
ADDR_CMD    = 0x0
ADDR_STATUS = 0x4
ADDR_DATA   = 0x8
ADDR_DIV    = 0xC

# ---- command codes (I2C_CMD[10:8]) ----
CMD_START     = 0
CMD_WRITE     = 1
CMD_READ_ACK  = 2
CMD_READ_NACK = 3
CMD_STOP      = 4

POLL_CAP = 100_000  # safety cap on busy-poll loops (cycles)


# ============================================================================
# Bus helpers (mirror the periph_regs single-cycle access strobe)
# ============================================================================

async def reg_write(dut, addr, data):
    """One-cycle register write."""
    dut.access.value = 1
    dut.addr.value = addr & 0xF
    dut.we.value = 1
    dut.wdata.value = data & 0xFFFFFFFF
    await RisingEdge(dut.clk)          # RTL samples access/addr/we/wdata here
    dut.access.value = 0
    dut.we.value = 0
    await RisingEdge(dut.clk)


async def reg_read(dut, addr):
    """Combinational register read (this module has no read side effects)."""
    dut.access.value = 0
    dut.we.value = 0
    dut.addr.value = addr & 0xF
    await Timer(1, unit='ns')
    return int(dut.rdata.value)


async def status(dut):
    return await reg_read(dut, ADDR_STATUS)


async def set_div(dut, div):
    await reg_write(dut, ADDR_DIV, div)


async def i2c_cmd(dut, cmd, data=0):
    """Issue one I2C primitive and wait for busy to clear (the software
    poll-first loop)."""
    await reg_write(dut, ADDR_CMD, (cmd << 8) | (data & 0xFF))
    for _ in range(POLL_CAP):
        if (await status(dut)) & 1 == 0:
            return
        await RisingEdge(dut.clk)
    assert False, "busy never cleared"


# ============================================================================
# Python I2C slave model — the golden reference for the byte-level protocol
# ============================================================================

class I2CSlave:
    """Open-drain I2C slave. Drives slave_scl_low / slave_sda_low in the tb.

    ack:            ACK (True) or NACK (False) bytes it receives.
    tx_queue:       bytes to return on reads (consumed in order).
    regs:           if given, reads return regs[reg_ptr++]; a written data byte
                    (after the address) sets reg_ptr.
    stretch_cycles: if >0, hold SCL low this many sysclk cycles at the start of
                    every low period (clock stretching).
    """

    def __init__(self, dut, ack=True, tx_queue=None, regs=None, stretch_cycles=0):
        self.dut = dut
        self.ack = ack
        self.tx_queue = list(tx_queue or [])
        self.regs = regs
        self.stretch_cycles = stretch_cycles
        # observations
        self.saw_start = 0
        self.saw_stop = 0
        self.rx_bytes = []       # every fully received byte (address + data)
        self.addr_bytes = []     # first byte after each (repeated) start
        self.tx_bytes = []       # every byte transmitted on reads
        self.master_acks = []    # ack bit seen on reads: 0 = ACK, 1 = NACK
        self.reg_ptr = 0

    def _reset(self):
        self.mode = 'IDLE'       # IDLE / RX (slave receives) / TX (slave sends)
        self.n = 0               # bits clocked in the current byte (0..8)
        self.shift = 0
        self.first_byte = False
        self.byte_done = False
        self.rw = 0
        self.tx_shift = 0
        self.dut.slave_sda_low.value = 0
        self.dut.slave_scl_low.value = 0

    def _load_tx(self):
        if self.regs is not None:
            b = self.regs.get(self.reg_ptr, 0)
            self.reg_ptr = (self.reg_ptr + 1) & 0xFF
        elif self.tx_queue:
            b = self.tx_queue.pop(0)
        else:
            b = 0xFF
        self.tx_shift = b & 0xFF
        self.tx_bytes.append(self.tx_shift)

    def _drive_bit(self):
        """Set SDA for the upcoming SCL-high period, given mode and bit index."""
        if self.mode == 'RX':
            if self.n < 8:
                self.dut.slave_sda_low.value = 0            # release; master drives data
            else:                                           # ack clock
                self.dut.slave_sda_low.value = 1 if self.ack else 0
        elif self.mode == 'TX':
            if self.n < 8:
                bit = (self.tx_shift >> (7 - self.n)) & 1
                self.dut.slave_sda_low.value = 0 if bit else 1   # drive low for a 0
            else:                                           # ack clock: master drives
                self.dut.slave_sda_low.value = 0

    def _on_rising(self, sda):
        """A bit is clocked (SCL just went high)."""
        if self.n < 8:
            if self.mode == 'RX':
                self.shift = ((self.shift << 1) | sda) & 0xFF
            self.n += 1
            if self.n == 8 and self.mode == 'RX':
                byte = self.shift
                self.rx_bytes.append(byte)
                if self.first_byte:
                    self.addr_bytes.append(byte)
                    self.rw = byte & 1
                elif self.regs is not None:
                    self.reg_ptr = byte                     # register pointer write
        elif self.n == 8:                                   # ack clock
            if self.mode == 'TX':
                self.master_acks.append(sda)                # 0 = ACK, 1 = NACK
            self.byte_done = True

    def _on_falling(self):
        """SCL just went low: finish a byte if due, then set up the next bit."""
        if self.byte_done:
            self.byte_done = False
            self.first_byte = False
            self.n = 0
            self.shift = 0
            if self.mode == 'RX' and self.rw == 1:
                self.mode = 'TX'                            # address said read
                self._load_tx()
            elif self.mode == 'TX':
                if self.master_acks and self.master_acks[-1] == 0:
                    self._load_tx()                         # master ACKed: more bytes
                else:
                    self.mode = 'IDLE'                      # NACK: end of read
                    self.dut.slave_sda_low.value = 0
                    return
        self._drive_bit()

    async def _stretch(self):
        self.dut.slave_scl_low.value = 1
        await ClockCycles(self.dut.clk, self.stretch_cycles)
        self.dut.slave_scl_low.value = 0

    async def run(self):
        dut = self.dut
        self._reset()
        prev_scl = int(dut.scl_i.value)
        prev_sda = int(dut.sda_i.value)
        while True:
            await First(Edge(dut.scl_i), Edge(dut.sda_i))
            scl = int(dut.scl_i.value)
            sda = int(dut.sda_i.value)
            scl_rose = prev_scl == 0 and scl == 1
            scl_fell = prev_scl == 1 and scl == 0
            sda_fell = prev_sda == 1 and sda == 0
            sda_rose = prev_sda == 0 and sda == 1

            # START / STOP: an SDA transition while SCL is high.
            if scl == 1 and (sda_fell or sda_rose):
                if sda_fell:
                    self.saw_start += 1
                    self.mode = 'RX'
                    self.n = 0
                    self.shift = 0
                    self.first_byte = True
                    self.byte_done = False
                    dut.slave_sda_low.value = 0
                else:
                    self.saw_stop += 1
                    self._reset()
                prev_scl, prev_sda = scl, sda
                continue

            if self.mode != 'IDLE':
                if scl_rose:
                    self._on_rising(sda)
                if scl_fell:
                    if self.stretch_cycles > 0:
                        cocotb.start_soon(self._stretch())
                    self._on_falling()
            prev_scl, prev_sda = scl, sda


# ============================================================================
# Test setup
# ============================================================================

async def setup(dut, slave=None):
    """Per-test: (re)start the clock, apply reset, and (re)start the slave task.
    The clock and slave MUST be restarted every test — start_soon tasks are
    cancelled at test end."""
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    dut.access.value = 0
    dut.addr.value = 0
    dut.we.value = 0
    dut.wdata.value = 0
    dut.slave_scl_low.value = 0
    dut.slave_sda_low.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    if slave is not None:
        cocotb.start_soon(slave.run())
        await ClockCycles(dut.clk, 1)


# ============================================================================
# Scenarios
# ============================================================================

@cocotb.test()
async def test_reset_idle(dut):
    """1. After reset both lines are released and status is clear."""
    await setup(dut)
    assert int(dut.scl_oe.value) == 0, "SCL not released after reset"
    assert int(dut.sda_oe.value) == 0, "SDA not released after reset"
    st = await status(dut)
    assert st & 1 == 0, "busy set after reset"
    assert (st >> 1) & 1 == 0, "nack set after reset"
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_start(dut):
    """2. A START primitive produces a start condition."""
    slave = I2CSlave(dut)
    await setup(dut, slave)
    await set_div(dut, 2)
    await i2c_cmd(dut, CMD_START)
    await ClockCycles(dut.clk, 5)
    assert slave.saw_start == 1, f"expected 1 start, saw {slave.saw_start}"
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_stop(dut):
    """3. A STOP primitive produces a stop condition and releases the bus."""
    slave = I2CSlave(dut)
    await setup(dut, slave)
    await set_div(dut, 2)
    await i2c_cmd(dut, CMD_START)
    await i2c_cmd(dut, CMD_STOP)
    await ClockCycles(dut.clk, 5)
    assert slave.saw_stop >= 1, "no stop condition seen"
    assert int(dut.scl_oe.value) == 0, "SCL not released after STOP"
    assert int(dut.sda_oe.value) == 0, "SDA not released after STOP"
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_write_ack(dut):
    """4. WRITE: the slave receives the byte (MSB first) and ACKs -> nack==0."""
    slave = I2CSlave(dut, ack=True)
    await setup(dut, slave)
    await set_div(dut, 2)
    await i2c_cmd(dut, CMD_START)
    await i2c_cmd(dut, CMD_WRITE, 0x5A)
    st = await status(dut)
    assert (st >> 1) & 1 == 0, "nack set despite slave ACK"
    assert slave.rx_bytes[-1] == 0x5A, f"slave saw {slave.rx_bytes[-1]:#04x}, want 0x5a"
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_write_nack(dut):
    """5. WRITE with a non-acking slave sets nack, sticky across STATUS reads."""
    slave = I2CSlave(dut, ack=False)
    await setup(dut, slave)
    await set_div(dut, 2)
    await i2c_cmd(dut, CMD_START)
    await i2c_cmd(dut, CMD_WRITE, 0x3C)
    st1 = await status(dut)
    st2 = await status(dut)
    assert (st1 >> 1) & 1 == 1, "nack not set on NACK"
    assert (st2 >> 1) & 1 == 1, "nack not sticky across reads"
    assert slave.rx_bytes[-1] == 0x3C
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_read_ack(dut):
    """6. READ_ACK returns the slave's byte and the master ACKs it."""
    slave = I2CSlave(dut, ack=True, tx_queue=[0x99])
    await setup(dut, slave)
    await set_div(dut, 2)
    await i2c_cmd(dut, CMD_START)
    await i2c_cmd(dut, CMD_WRITE, (0x50 << 1) | 1)     # address + R
    await i2c_cmd(dut, CMD_READ_ACK)
    data = await reg_read(dut, ADDR_DATA)
    assert (data & 0xFF) == 0x99, f"I2C_DATA={data:#04x}, want 0x99"
    assert slave.master_acks[-1] == 0, "master did not ACK on READ_ACK"
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_read_nack(dut):
    """7. READ_NACK returns the byte and the master NACKs it (last read)."""
    slave = I2CSlave(dut, ack=True, tx_queue=[0xC3])
    await setup(dut, slave)
    await set_div(dut, 2)
    await i2c_cmd(dut, CMD_START)
    await i2c_cmd(dut, CMD_WRITE, (0x50 << 1) | 1)
    await i2c_cmd(dut, CMD_READ_NACK)
    data = await reg_read(dut, ADDR_DATA)
    assert (data & 0xFF) == 0xC3, f"I2C_DATA={data:#04x}, want 0xc3"
    assert slave.master_acks[-1] == 1, "master did not NACK on READ_NACK"
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_register_read(dut):
    """8. A full 4-byte register read: START, WR addr|W, WR reg, repeated START,
    WR addr|R, READ_ACK x3, READ_NACK, STOP."""
    regs = {0x10: 0x11, 0x11: 0x22, 0x12: 0x33, 0x13: 0x44}
    slave = I2CSlave(dut, ack=True, regs=regs)
    await setup(dut, slave)
    await set_div(dut, 2)
    DEV = 0x50
    await i2c_cmd(dut, CMD_START)
    await i2c_cmd(dut, CMD_WRITE, (DEV << 1) | 0)      # address + W
    await i2c_cmd(dut, CMD_WRITE, 0x10)                # register pointer
    await i2c_cmd(dut, CMD_START)                      # repeated start
    await i2c_cmd(dut, CMD_WRITE, (DEV << 1) | 1)      # address + R
    got = []
    for cmd in (CMD_READ_ACK, CMD_READ_ACK, CMD_READ_ACK, CMD_READ_NACK):
        await i2c_cmd(dut, cmd)
        got.append(await reg_read(dut, ADDR_DATA) & 0xFF)
    await i2c_cmd(dut, CMD_STOP)
    assert got == [0x11, 0x22, 0x33, 0x44], f"read {got}"
    assert slave.saw_start == 2, f"expected 2 starts, saw {slave.saw_start}"
    assert slave.addr_bytes == [(DEV << 1) | 0, (DEV << 1) | 1]
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_clock_stretching(dut):
    """9. The master honors a slave holding SCL low; the read is still correct."""
    slave = I2CSlave(dut, ack=True, tx_queue=[0xA6], stretch_cycles=5)
    await setup(dut, slave)
    await set_div(dut, 2)
    await i2c_cmd(dut, CMD_START)
    await i2c_cmd(dut, CMD_WRITE, (0x50 << 1) | 1)
    await i2c_cmd(dut, CMD_READ_NACK)
    data = await reg_read(dut, ADDR_DATA)
    assert (data & 0xFF) == 0xA6, f"stretched read={data:#04x}, want 0xa6"
    assert slave.master_acks[-1] == 1
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_scl_timing(dut):
    """10. SCL period == 4*(div+1) sysclk, swept over a few divisors."""
    for div in (1, 2, 4):
        await setup(dut)                                # no slave (SCL free-runs)
        await set_div(dut, div)
        # Kick off a WRITE so SCL toggles; don't poll (we measure mid-transfer).
        await reg_write(dut, ADDR_CMD, (CMD_WRITE << 8) | 0xFF)
        for _ in range(3):                              # skip startup, reach steady state
            await RisingEdge(dut.scl_i)
        # Measure the sim-time delta between two SCL rises. scl_i is a
        # combinational function of the registered scl_oe_r, so its edges are
        # coincident with clk edges; counting clk edges relative to that would
        # be off by one, so use the time delta and divide by the clock period.
        t0 = get_sim_time('ns')
        await RisingEdge(dut.scl_i)
        t1 = get_sim_time('ns')
        n = round((t1 - t0) / 10)                       # 10 ns clock period
        assert n == 4 * (div + 1), f"div={div}: SCL period {n} cycles, want {4*(div+1)}"
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_write_while_busy(dut):
    """11. A CMD write while busy is ignored; the in-flight byte is unaffected."""
    slave = I2CSlave(dut, ack=True)
    await setup(dut, slave)
    await set_div(dut, 4)
    await i2c_cmd(dut, CMD_START)
    n_before = len(slave.rx_bytes)
    await reg_write(dut, ADDR_CMD, (CMD_WRITE << 8) | 0xAA)   # launch, don't poll
    await ClockCycles(dut.clk, 10)                            # mid transfer, busy high
    assert (await status(dut)) & 1 == 1, "expected busy mid-transfer"
    await reg_write(dut, ADDR_CMD, (CMD_WRITE << 8) | 0x55)   # must be ignored
    for _ in range(POLL_CAP):
        if (await status(dut)) & 1 == 0:
            break
        await RisingEdge(dut.clk)
    assert slave.rx_bytes[n_before:] == [0xAA], f"got {slave.rx_bytes[n_before:]}"
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_undefined_command(dut):
    """12. An undefined command (101..111) is ignored: no busy, no bus activity."""
    slave = I2CSlave(dut)
    await setup(dut, slave)
    await set_div(dut, 2)
    for code in (0b101, 0b110, 0b111):
        await reg_write(dut, ADDR_CMD, (code << 8) | 0x00)
        await ClockCycles(dut.clk, 20)
        assert (await status(dut)) & 1 == 0, f"cmd {code:#05b} set busy"
        assert int(dut.scl_oe.value) == 0 and int(dut.sda_oe.value) == 0, \
            f"cmd {code:#05b} drove the bus"
    assert slave.saw_start == 0
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_nack_clears_on_next_command(dut):
    """13. A NACKed WRITE sets nack; the next accepted command clears it."""
    slave = I2CSlave(dut, ack=False)
    await setup(dut, slave)
    await set_div(dut, 2)
    await i2c_cmd(dut, CMD_START)
    await i2c_cmd(dut, CMD_WRITE, 0x11)
    assert (await status(dut) >> 1) & 1 == 1, "nack not set after NACKed WRITE"
    await i2c_cmd(dut, CMD_START)                        # any command clears nack
    assert (await status(dut) >> 1) & 1 == 0, "nack not cleared by next command"
    await ClockCycles(dut.clk, 1)
