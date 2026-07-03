# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.triggers import Timer

# ALU op codes — match the case statement in src/alu.v.
# Note: 4'b0000 uses | (OR) and 4'b0001 uses & (AND).
ALU_OP_OR   = 0x0  # 4'b0000: a | b
ALU_OP_AND  = 0x1  # 4'b0001: a & b
ALU_OP_XOR  = 0x2  # 4'b0010: a ^ b
ALU_OP_ADD  = 0x4  # 4'b0100: a + b  (wraps mod 2^32)
ALU_OP_SUB  = 0x5  # 4'b0101: a - b  (wraps mod 2^32)
ALU_OP_SLL  = 0x8  # 4'b1000: a << b[4:0]
ALU_OP_SRL  = 0xA  # 4'b1010: a >> b[4:0]  (logical, zero-fill)
ALU_OP_SRA  = 0xB  # 4'b1011: a >>> b[4:0] (arithmetic, sign-fill)
ALU_OP_SLTU = 0xC  # 4'b1100: (unsigned) a < b ? 1 : 0
ALU_OP_SLT  = 0xD  # 4'b1101: (signed)   a < b ? 1 : 0

MASK32 = 0xFFFF_FFFF


async def apply(dut, op, a, b):
    """Drive ALU inputs and wait 1 ns for combinational logic to settle."""
    dut.alu_op.value = op
    dut.alu_input_a.value = a & MASK32
    dut.alu_input_b.value = b & MASK32
    await Timer(1, unit='ns')
    return int(dut.alu_output.value), int(dut.alu_output_zero.value)


# ---------------------------------------------------------------------------
# OR
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_or(dut):
    print("\n\n")
    dut._log.info("=== OR ===")

    out, z = await apply(dut, ALU_OP_OR, 0xFFFF_0000, 0x0000_FFFF)
    dut._log.info(f"0xFFFF0000 | 0x0000FFFF = 0x{out:08X}, zero={z}")
    assert out == 0xFFFF_FFFF and z == 0

    out, z = await apply(dut, ALU_OP_OR, 0xA5A5_A5A5, 0x5A5A_5A5A)
    dut._log.info(f"0xA5A5A5A5 | 0x5A5A5A5A = 0x{out:08X}, zero={z} (complementary masks)")
    assert out == 0xFFFF_FFFF and z == 0

    out, z = await apply(dut, ALU_OP_OR, 0x0000_0000, 0x0000_0000)
    dut._log.info(f"0x00000000 | 0x00000000 = 0x{out:08X}, zero={z} (expect zero flag)")
    assert out == 0x0000_0000 and z == 1

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# AND
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_and(dut):
    print("\n\n")
    dut._log.info("=== AND ===")

    out, z = await apply(dut, ALU_OP_AND, 0xFFFF_FFFF, 0x1234_5678)
    dut._log.info(f"0xFFFFFFFF & 0x12345678 = 0x{out:08X}, zero={z}")
    assert out == 0x1234_5678 and z == 0

    out, z = await apply(dut, ALU_OP_AND, 0xA5A5_A5A5, 0x5A5A_5A5A)
    dut._log.info(f"0xA5A5A5A5 & 0x5A5A5A5A = 0x{out:08X}, zero={z} (complementary masks → 0)")
    assert out == 0x0000_0000 and z == 1

    out, z = await apply(dut, ALU_OP_AND, 0xFFFF_0000, 0x0000_FFFF)
    dut._log.info(f"0xFFFF0000 & 0x0000FFFF = 0x{out:08X}, zero={z} (no overlap → 0)")
    assert out == 0x0000_0000 and z == 1

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# XOR
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_xor(dut):
    print("\n\n")
    dut._log.info("=== XOR ===")

    out, z = await apply(dut, ALU_OP_XOR, 0xFFFF_FFFF, 0xFFFF_FFFF)
    dut._log.info(f"0xFFFFFFFF ^ 0xFFFFFFFF = 0x{out:08X}, zero={z} (same inputs → 0)")
    assert out == 0x0000_0000 and z == 1

    out, z = await apply(dut, ALU_OP_XOR, 0xA5A5_A5A5, 0x5A5A_5A5A)
    dut._log.info(f"0xA5A5A5A5 ^ 0x5A5A5A5A = 0x{out:08X}, zero={z} (complementary → all ones)")
    assert out == 0xFFFF_FFFF and z == 0

    out, z = await apply(dut, ALU_OP_XOR, 0x1234_5678, 0x1234_5678)
    dut._log.info(f"0x12345678 ^ 0x12345678 = 0x{out:08X}, zero={z} (same inputs → 0)")
    assert out == 0x0000_0000 and z == 1

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# ADD
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_add(dut):
    print("\n\n")
    dut._log.info("=== ADD ===")

    out, z = await apply(dut, ALU_OP_ADD, 3, 4)
    dut._log.info(f"3 + 4 = {out}, zero={z}")
    assert out == 7 and z == 0

    out, z = await apply(dut, ALU_OP_ADD, 0, 0)
    dut._log.info(f"0 + 0 = {out}, zero={z} (expect zero flag)")
    assert out == 0 and z == 1

    out, z = await apply(dut, ALU_OP_ADD, 0xFFFF_FFFF, 1)
    dut._log.info(f"0xFFFFFFFF + 1 = 0x{out:08X}, zero={z} (unsigned overflow wraps to 0)")
    assert out == 0x0000_0000 and z == 1

    out, z = await apply(dut, ALU_OP_ADD, 0x7FFF_FFFF, 0x7FFF_FFFF)
    dut._log.info(f"0x7FFFFFFF + 0x7FFFFFFF = 0x{out:08X}, zero={z} (signed overflow)")
    assert out == 0xFFFF_FFFE and z == 0

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# SUB
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_sub(dut):
    print("\n\n")
    dut._log.info("=== SUB ===")

    out, z = await apply(dut, ALU_OP_SUB, 7, 3)
    dut._log.info(f"7 - 3 = {out}, zero={z}")
    assert out == 4 and z == 0

    out, z = await apply(dut, ALU_OP_SUB, 5, 5)
    dut._log.info(f"5 - 5 = {out}, zero={z} (expect zero flag)")
    assert out == 0 and z == 1

    out, z = await apply(dut, ALU_OP_SUB, 0, 1)
    dut._log.info(f"0 - 1 = 0x{out:08X}, zero={z} (unsigned underflow wraps to 0xFFFFFFFF)")
    assert out == 0xFFFF_FFFF and z == 0

    out, z = await apply(dut, ALU_OP_SUB, 0x8000_0000, 1)
    dut._log.info(f"0x80000000 - 1 = 0x{out:08X}, zero={z} (signed underflow → 0x7FFFFFFF)")
    assert out == 0x7FFF_FFFF and z == 0

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# SLL  (shift left logical)
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_sll(dut):
    print("\n\n")
    dut._log.info("=== SLL (shift left logical) ===")

    out, z = await apply(dut, ALU_OP_SLL, 1, 1)
    dut._log.info(f"1 << 1 = {out}, zero={z}")
    assert out == 2 and z == 0

    out, z = await apply(dut, ALU_OP_SLL, 1, 31)
    dut._log.info(f"1 << 31 = 0x{out:08X}, zero={z}")
    assert out == 0x8000_0000 and z == 0

    out, z = await apply(dut, ALU_OP_SLL, 0x8000_0000, 1)
    dut._log.info(f"0x80000000 << 1 = 0x{out:08X}, zero={z} (MSB shifted out → 0, overflow)")
    assert out == 0x0000_0000 and z == 1

    # only b[4:0] used; b=32 (0b100000) has b[4:0]=0 → shift by 0 (no-op)
    out, z = await apply(dut, ALU_OP_SLL, 0xDEAD_BEEF, 32)
    dut._log.info(f"0xDEADBEEF << 32: b[4:0]=0, shift is no-op → 0x{out:08X}, zero={z}")
    assert out == 0xDEAD_BEEF and z == 0

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# SRL  (shift right logical)
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_srl(dut):
    print("\n\n")
    dut._log.info("=== SRL (shift right logical) ===")

    out, z = await apply(dut, ALU_OP_SRL, 4, 1)
    dut._log.info(f"4 >> 1 = {out}, zero={z}")
    assert out == 2 and z == 0

    out, z = await apply(dut, ALU_OP_SRL, 0x8000_0000, 1)
    dut._log.info(f"0x80000000 >> 1 = 0x{out:08X}, zero={z} (logical: MSB becomes 0)")
    assert out == 0x4000_0000 and z == 0

    out, z = await apply(dut, ALU_OP_SRL, 0xFFFF_FFFF, 31)
    dut._log.info(f"0xFFFFFFFF >> 31 = {out}, zero={z}")
    assert out == 1 and z == 0

    out, z = await apply(dut, ALU_OP_SRL, 1, 1)
    dut._log.info(f"1 >> 1 = {out}, zero={z} (expect zero flag)")
    assert out == 0 and z == 1

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# SRA  (shift right arithmetic)
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_sra(dut):
    print("\n\n")
    dut._log.info("=== SRA (shift right arithmetic) ===")

    out, z = await apply(dut, ALU_OP_SRA, 4, 1)
    dut._log.info(f"4 >>> 1 = {out}, zero={z}")
    assert out == 2 and z == 0

    out, z = await apply(dut, ALU_OP_SRA, 0xFFFF_FFFC, 1)  # -4 >>> 1 = -2
    dut._log.info(f"0xFFFFFFFC(-4) >>> 1 = 0x{out:08X}, zero={z} (sign replicated → 0xFFFFFFFE)")
    assert out == 0xFFFF_FFFE and z == 0

    out, z = await apply(dut, ALU_OP_SRA, 0x8000_0000, 31)
    dut._log.info(f"0x80000000 >>> 31 = 0x{out:08X}, zero={z} (all sign bits → 0xFFFFFFFF)")
    assert out == 0xFFFF_FFFF and z == 0

    out, z = await apply(dut, ALU_OP_SRA, 1, 1)
    dut._log.info(f"1 >>> 1 = {out}, zero={z} (expect zero flag)")
    assert out == 0 and z == 1

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# SLTU  (set less than, unsigned)
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_sltu(dut):
    print("\n\n")
    dut._log.info("=== SLTU (set less than, unsigned) ===")

    out, z = await apply(dut, ALU_OP_SLTU, 1, 2)
    dut._log.info(f"1 <u 2 = {out}, zero={z}")
    assert out == 1 and z == 0

    out, z = await apply(dut, ALU_OP_SLTU, 2, 1)
    dut._log.info(f"2 <u 1 = {out}, zero={z} (expect 0)")
    assert out == 0 and z == 1

    out, z = await apply(dut, ALU_OP_SLTU, 1, 1)
    dut._log.info(f"1 <u 1 = {out}, zero={z} (equal, expect 0)")
    assert out == 0 and z == 1

    out, z = await apply(dut, ALU_OP_SLTU, 1, 0xFFFF_FFFF)
    dut._log.info(f"1 <u 0xFFFFFFFF = {out}, zero={z} (0xFFFFFFFF is max unsigned)")
    assert out == 1 and z == 0

    out, z = await apply(dut, ALU_OP_SLTU, 0xFFFF_FFFF, 1)
    dut._log.info(f"0xFFFFFFFF <u 1 = {out}, zero={z} (unsigned: 0xFFFFFFFF > 1, expect 0)")
    assert out == 0 and z == 1

    await Timer(1, unit='ns')


# ---------------------------------------------------------------------------
# SLT  (set less than, signed)
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_slt(dut):
    print("\n\n")
    dut._log.info("=== SLT (set less than, signed) ===")

    out, z = await apply(dut, ALU_OP_SLT, 1, 2)
    dut._log.info(f"1 <s 2 = {out}, zero={z}")
    assert out == 1 and z == 0

    out, z = await apply(dut, ALU_OP_SLT, 2, 1)
    dut._log.info(f"2 <s 1 = {out}, zero={z} (expect 0)")
    assert out == 0 and z == 1

    out, z = await apply(dut, ALU_OP_SLT, 1, 1)
    dut._log.info(f"1 <s 1 = {out}, zero={z} (equal, expect 0)")
    assert out == 0 and z == 1

    out, z = await apply(dut, ALU_OP_SLT, 0xFFFF_FFFF, 1)  # -1 <s 1 → true
    dut._log.info(f"0xFFFFFFFF(-1) <s 1 = {out}, zero={z} (signed: -1 < 1)")
    assert out == 1 and z == 0

    out, z = await apply(dut, ALU_OP_SLT, 1, 0xFFFF_FFFF)  # 1 <s -1 → false
    dut._log.info(f"1 <s 0xFFFFFFFF(-1) = {out}, zero={z} (signed: 1 > -1, expect 0)")
    assert out == 0 and z == 1

    out, z = await apply(dut, ALU_OP_SLT, 0xFFFF_FFFE, 0xFFFF_FFFF)  # -2 <s -1 → true
    dut._log.info(f"0xFFFFFFFE(-2) <s 0xFFFFFFFF(-1) = {out}, zero={z}")
    assert out == 1 and z == 0

    await Timer(1, unit='ns')
