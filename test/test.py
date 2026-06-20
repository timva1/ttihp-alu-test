# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

ALU_OP_AND = 0x0
ALU_OP_OR = 0x1
ALU_OP_XOR = 0x2
ALU_OP_ADD = 0x4
ALU_OP_SUB = 0x5
ALU_OP_SLL = 0x8
ALU_OP_SRL = 0xA
ALU_OP_SRA = 0xB
ALU_OP_SLTU = 0xC
ALU_OP_SLT = 0xD

def calc_alu_input_a(ui_in):
    ui_in_bottom = ui_in & 0x0F
    ui_in_bottom_n = ~ui_in & 0x0F
    ui_in_top = (ui_in >> 4) & 0x0F
    ui_in_top_n = (~ui_in >> 4) & 0x0F
    alu_inp_a_bottom = (ui_in_top << 12) + (ui_in_top_n << 8) + (ui_in_bottom << 4) + ui_in_bottom_n
    return alu_inp_a_bottom

def calc_alu_input_b(ui_in):
    ui_in_bottom = ui_in & 0x0F
    ui_in_bottom_n = ~ui_in & 0x0F
    ui_in_top = (ui_in >> 4) & 0x0F
    ui_in_top_n = (~ui_in >> 4) & 0x0F
    alu_inp_b_bottom = (ui_in_bottom_n << 12) + (ui_in_top << 8) + (ui_in_top_n << 4) + ui_in_bottom
    return alu_inp_b_bottom

def calc_alu_expected_result(ui_in, uio_in):
    alu_op = uio_in & 0x0F
    alu_input_a = calc_alu_input_a(ui_in)
    alu_input_b = calc_alu_input_b(ui_in)
    if alu_op == ALU_OP_AND:
        return alu_input_a & alu_input_b
    elif alu_op == ALU_OP_OR:
        return alu_input_a | alu_input_b
    elif alu_op == ALU_OP_XOR:
        return alu_input_a ^ alu_input_b
    elif alu_op == ALU_OP_ADD:
        return (alu_input_a + alu_input_b) & 0xFFFFFFFF
    elif alu_op == ALU_OP_SUB:
        return (alu_input_a - alu_input_b) & 0xFFFFFFFF
    elif alu_op == ALU_OP_SLL:
        return (alu_input_a << (alu_input_b & 0x1F)) & 0xFFFFFFFF
    elif alu_op == ALU_OP_SRL:
        return (alu_input_a >> (alu_input_b & 0x1F)) & 0xFFFFFFFF
    elif alu_op == ALU_OP_SRA:
        return (alu_input_a >> (alu_input_b & 0x1F)) | (0xFFFFFFFF << (32 - (alu_input_b & 0x1F))) if alu_input_a & 0x80000000 else (alu_input_a >> (alu_input_b & 0x1F))
    elif alu_op == ALU_OP_SLTU:
        return 1 if alu_input_a < alu_input_b else 0

@cocotb.test()
async def test_project(dut):
    dut._log.info("Start")

    # Set the clock period to 10 us (100 KHz)
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    # Reset
    dut._log.info("Reset")
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    dut._log.info("Test project behavior")

    # Set the input values you want to test
    
    dut.ui_in.value = 0xBC
    dut.uio_in.value = ALU_OP_ADD  # ALU operation code for addition (example)
    print("CHECKPOINT: line 80: ui_in = 0xBC, uio_in = ALU_OP_ADD")
    try:
        assert dut.uo_out.value == (0xBC + 0x32) & 0xFF  # Expected output for addition (example)

        # The following assersion is just an example of how to check the output values.
        # Change it to match the actual expected output of your module:
        # assert dut.uo_out.value == 50
    except AssertionError:
        dut._log.info("ABC")
        # dut._log.error(f"Assertion failed: uo_out = {dut.uo_out.value}, expected 50")
        # raise

    # Always await at least one more clock edge before the test coroutine returns:
    # ending the test in the same delta cycle as the dut.uio_in.value write above
    # crashes this Icarus/cocotb build's waveform-dump teardown.
    await ClockCycles(dut.clk, 1)
