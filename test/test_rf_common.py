import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer

async def setup_rf(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    dut.rd_wen.value = 0
    dut.rd_addr.value = 0
    dut.rd_data.value = 0
    dut.rs1_addr.value = 0
    dut.rs2_addr.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 1)


async def write_reg(dut, addr, data, write_edge):
    dut.rd_addr.value = addr
    dut.rd_data.value = data
    dut.rd_wen.value = 1
    if write_edge == "negedge":
        await FallingEdge(dut.clk)
    else:
        await RisingEdge(dut.clk)
    dut.rd_wen.value = 0
    await Timer(1, units='ns')


async def read_rs(dut, rs1, rs2=0, verbose=False):
    dut.rs1_addr.value = rs1
    dut.rs2_addr.value = rs2
    dut.rd_wen.value = 0
    if verbose:
        dut._log.info(f"Reading rs1={rs1}, rs2={rs2}")
    await Timer(1, units='ns')
    rs1_data = int(dut.rs1_data.value)
    rs2_data = int(dut.rs2_data.value)
    if verbose:
        dut._log.info(f"Read rs1: 0x{rs1_data:08X}, rs2: 0x{rs2_data:08X}")
    return rs1_data, rs2_data


async def test_reset_func(dut, num_regs):
    await setup_rf(dut)
    for i in range(num_regs):
        rs1, _ = await read_rs(dut, i)
        assert rs1 == 0, f"reg[{i}] = 0x{rs1:08X} after reset, expected 0"
    await ClockCycles(dut.clk, 1)


async def test_x0_readonly_func(dut, write_edge):
    await setup_rf(dut)
    await write_reg(dut, 0, 0xDEAD_BEEF, write_edge)
    rs1, _ = await read_rs(dut, 0, verbose=True)
    assert rs1 == 0, f"x0 should remain 0 after write, got 0x{rs1:08X}"
    await ClockCycles(dut.clk, 1)


async def test_write_read_func(dut, write_edge, num_regs):
    await setup_rf(dut)
    for i in range(1, num_regs):
        await write_reg(dut, i, 0xA000_0000 | i, write_edge)
    for i in range(1, num_regs):
        rs1, _ = await read_rs(dut, i)
        assert rs1 == (0xA000_0000 | i), \
            f"reg[{i}]: expected 0x{(0xA000_0000 | i):08X}, got 0x{rs1:08X}"
    await ClockCycles(dut.clk, 1)


async def test_dual_port_func(dut, write_edge):
    await setup_rf(dut)
    await write_reg(dut, 1, 0xAAAA_AAAA, write_edge)
    await write_reg(dut, 2, 0x5555_5555, write_edge)
    dut.rs1_addr.value = 1
    dut.rs2_addr.value = 2
    dut.rd_wen.value = 0
    await Timer(1, units='ns')
    rs1 = int(dut.rs1_data.value)
    rs2 = int(dut.rs2_data.value)
    assert rs1 == 0xAAAA_AAAA, f"rs1_data: expected 0xAAAAAAAA, got 0x{rs1:08X}"
    assert rs2 == 0x5555_5555, f"rs2_data: expected 0x55555555, got 0x{rs2:08X}"
    await ClockCycles(dut.clk, 1)


async def test_forwarding_func(dut, write_edge):
    """posedge: rs1_data is forwarded from rd_data when wen=1 and rs1_addr==rd_addr.
    negedge: no forwarding; rs1_data comes from the register file directly."""
    await setup_rf(dut)
    await write_reg(dut, 3, 0x1111_1111, write_edge)
    dut.rd_addr.value = 3
    dut.rd_data.value = 0xFACE_FACE
    dut.rd_wen.value = 1
    dut.rs1_addr.value = 3
    await Timer(1, units='ns')
    rs1 = int(dut.rs1_data.value)
    if write_edge == "posedge":
        assert rs1 == 0xFACE_FACE, \
            f"posedge forwarding: expected 0xFACEFACE, got 0x{rs1:08X}"
    else:
        assert rs1 == 0x1111_1111, \
            f"negedge no-forwarding: expected 0x11111111, got 0x{rs1:08X}"
    dut.rd_wen.value = 0
    await ClockCycles(dut.clk, 1)


async def test_write_edge_timing_func(dut, write_edge):
    """Verify the write latches on exactly the configured edge and not the opposite."""
    await setup_rf(dut)
    await write_reg(dut, 5, 0x1111_1111, write_edge)
    dut.rd_addr.value = 5
    dut.rd_data.value = 0x2222_2222
    dut.rd_wen.value = 1
    if write_edge == "posedge":
        # Deassert after negedge — register must NOT have been written yet
        await FallingEdge(dut.clk)
        dut.rd_wen.value = 0
        await Timer(1, units='ns')
        rs1, _ = await read_rs(dut, 5)
        assert rs1 == 0x1111_1111, \
            f"posedge mode: write must not latch on negedge, got 0x{rs1:08X}"
        # Now commit via posedge
        await write_reg(dut, 5, 0x2222_2222, write_edge)
        rs1, _ = await read_rs(dut, 5)
        assert rs1 == 0x2222_2222, \
            f"posedge mode: write must latch on posedge, got 0x{rs1:08X}"
    else:
        # Deassert after posedge — register must NOT have been written yet
        await RisingEdge(dut.clk)
        dut.rd_wen.value = 0
        await Timer(1, units='ns')
        rs1, _ = await read_rs(dut, 5)
        assert rs1 == 0x1111_1111, \
            f"negedge mode: write must not latch on posedge, got 0x{rs1:08X}"
        # Now commit via negedge
        await write_reg(dut, 5, 0x2222_2222, write_edge)
        rs1, _ = await read_rs(dut, 5)
        assert rs1 == 0x2222_2222, \
            f"negedge mode: write must latch on negedge, got 0x{rs1:08X}"
    await ClockCycles(dut.clk, 1)


async def test_reset_after_write_func(dut, write_edge, num_regs):
    await setup_rf(dut)
    for i in range(1, min(4, num_regs)):
        await write_reg(dut, i, 0xBEEF_0000 | i, write_edge)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 1)
    for i in range(num_regs):
        rs1, _ = await read_rs(dut, i)
        assert rs1 == 0, f"reg[{i}] = 0x{rs1:08X} after second reset, expected 0"
    await ClockCycles(dut.clk, 1)


async def test_e_addr_masking_func(dut, write_edge):
    """RV32E: 5-bit addr inputs are masked to [3:0]; bit[4] is ignored.
    addr 17 (5'b10001) aliases reg[1]; addr 16 (5'b10000) aliases x0."""
    await setup_rf(dut)
    await write_reg(dut, 1, 0xBEEF_BEEF, write_edge)
    rs1, _ = await read_rs(dut, 17)
    assert rs1 == 0xBEEF_BEEF, \
        f"E-ext: rs1_addr=17 should alias reg[1] (0xBEEFBEEF), got 0x{rs1:08X}"
    rs1, _ = await read_rs(dut, 16)
    assert rs1 == 0, \
        f"E-ext: rs1_addr=16 should alias x0 (0), got 0x{rs1:08X}"
    # Write via aliased address; should land in reg[1]
    await write_reg(dut, 17, 0xCAFE_CAFE, write_edge)
    rs1, _ = await read_rs(dut, 1)
    assert rs1 == 0xCAFE_CAFE, \
        f"E-ext: rd_addr=17 should write reg[1], got 0x{rs1:08X}"
    await ClockCycles(dut.clk, 1)
