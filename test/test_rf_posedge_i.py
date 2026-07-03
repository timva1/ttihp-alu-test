import cocotb
import test_rf_common as _c

WRITE_EDGE = "posedge"
NUM_REGS = 32


@cocotb.test()
async def test_reset(dut):
    await _c.test_reset_func(dut, NUM_REGS)


@cocotb.test()
async def test_x0_readonly(dut):
    dut._log.info(f"Testing x0 readonly with write_edge={WRITE_EDGE}")
    await _c.test_x0_readonly_func(dut, WRITE_EDGE)


@cocotb.test()
async def test_write_read(dut):
    dut._log.info(f"Testing write/read with write_edge={WRITE_EDGE}")
    await _c.test_write_read_func(dut, WRITE_EDGE, NUM_REGS)


@cocotb.test()
async def test_dual_port(dut):
    await _c.test_dual_port_func(dut, WRITE_EDGE)


@cocotb.test()
async def test_forwarding(dut):
    await _c.test_forwarding_func(dut, WRITE_EDGE)


@cocotb.test()
async def test_write_edge_timing(dut):
    await _c.test_write_edge_timing_func(dut, WRITE_EDGE)


@cocotb.test()
async def test_reset_after_write(dut):
    await _c.test_reset_after_write_func(dut, WRITE_EDGE, NUM_REGS)
