# SPDX-License-Identifier: Apache-2.0
#
# Verification for src/core/decoder.v — combinational RV32E instruction decode.
#
# A Python golden model (`golden_decode`) reimplements the decode contract from
# docs/microarchitecture/decoder.md and is compared field-by-field against the
# RTL across three parameter configurations instantiated in decoder_tb.v:
#     'd' = dflt  (RV32E=1, ENABLE_SUBWORD=1)
#     'i' = rv32i (RV32E=0)
#     'n' = nosub (ENABLE_SUBWORD=0)
#
# For LEGAL encodings the full output bundle is checked; for ILLEGAL encodings
# only `illegal` is checked (the control path halts on it and never consumes
# the rest). Directed tests additionally assert hand-computed expectations on
# discriminating fields, which validates the golden model itself.

import random

import cocotb
from cocotb.triggers import Timer

MASK32 = 0xFFFF_FFFF

# ---- Opcodes ----
OP_LUI, OP_AUIPC = 0b0110111, 0b0010111
OP_JAL, OP_JALR = 0b1101111, 0b1100111
OP_BRANCH, OP_LOAD, OP_STORE = 0b1100011, 0b0000011, 0b0100011
OP_OP_IMM, OP_OP = 0b0010011, 0b0110011
OP_MISC_MEM, OP_SYSTEM = 0b0001111, 0b1110011

# ---- Output-bundle encodings (mirror decoder.md) ----
A_RS1, A_PC = 0, 1
B_RS2, B_IMM = 0, 1
RES_ALU, RES_MEM, RES_PC4, RES_IMM = 0, 1, 2, 3
ALU_ADD, ALU_SUB, ALU_SLT, ALU_SLTU = 0b0000, 0b1000, 0b0010, 0b0011

# All output signals (base names; the TB suffixes each with _d/_i/_n).
OUTPUTS = [
    "rs1_addr", "rs2_addr", "rd_addr", "imm", "alu_op",
    "alu_a_sel", "alu_b_sel", "result_sel", "rd_wen",
    "mem_read", "mem_write", "mem_size", "mem_unsigned",
    "is_branch", "branch_cond", "is_jal", "is_jalr",
    "is_ecall", "is_ebreak", "illegal",
]

CONFIGS = {"d": (1, 1), "i": (0, 1), "n": (1, 0)}  # suffix -> (rv32e, subword)


# ---------------------------------------------------------------------------
# Bit helpers
# ---------------------------------------------------------------------------
def bit(x, n):
    return (x >> n) & 1


def bits(x, hi, lo):
    return (x >> lo) & ((1 << (hi - lo + 1)) - 1)


def sext(val, width):
    if val & (1 << (width - 1)):
        return (val - (1 << width)) & MASK32
    return val & MASK32


# ---------------------------------------------------------------------------
# Instruction assemblers
# ---------------------------------------------------------------------------
def enc_r(opcode, rd, funct3, rs1, rs2, funct7):
    return ((funct7 << 25) | (rs2 << 20) | (rs1 << 15)
            | (funct3 << 12) | (rd << 7) | opcode)


def enc_i(opcode, rd, funct3, rs1, imm):
    imm &= 0xFFF
    return (imm << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode


def enc_s(opcode, funct3, rs1, rs2, imm):
    imm &= 0xFFF
    return (((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15)
            | (funct3 << 12) | ((imm & 0x1F) << 7) | opcode)


def enc_b(opcode, funct3, rs1, rs2, imm):
    imm &= 0x1FFF  # 13-bit, bit0 ignored (always 0)
    return ((bit(imm, 12) << 31) | (bits(imm, 10, 5) << 25) | (rs2 << 20)
            | (rs1 << 15) | (funct3 << 12) | (bits(imm, 4, 1) << 8)
            | (bit(imm, 11) << 7) | opcode)


def enc_u(opcode, rd, imm20):
    return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | opcode


def enc_j(opcode, rd, imm):
    imm &= 0x1FFFFF  # 21-bit, bit0 ignored
    return ((bit(imm, 20) << 31) | (bits(imm, 10, 1) << 21) | (bit(imm, 11) << 20)
            | (bits(imm, 19, 12) << 12) | (rd << 7) | opcode)


# ---------------------------------------------------------------------------
# Golden model
# ---------------------------------------------------------------------------
def golden_decode(instr, rv32e, subword):
    """Reference RV32E decode. Returns the expected output bundle as a dict."""
    instr &= MASK32
    opcode = bits(instr, 6, 0)
    funct3 = bits(instr, 14, 12)
    funct7 = bits(instr, 31, 25)
    rs1 = bits(instr, 19, 15)
    rs2 = bits(instr, 24, 20)
    rd = bits(instr, 11, 7)

    imm_i = sext(bits(instr, 31, 20), 12)
    imm_s = sext((bits(instr, 31, 25) << 5) | bits(instr, 11, 7), 12)
    imm_b = sext((bit(instr, 31) << 12) | (bit(instr, 7) << 11)
                 | (bits(instr, 30, 25) << 5) | (bits(instr, 11, 8) << 1), 13)
    imm_u = (bits(instr, 31, 12) << 12) & MASK32
    imm_j = sext((bit(instr, 31) << 20) | (bits(instr, 19, 12) << 12)
                 | (bit(instr, 20) << 11) | (bits(instr, 30, 21) << 1), 21)

    # Default (inactive) bundle — mirrors the RTL's default assignments.
    d = dict(
        rs1_addr=rs1, rs2_addr=rs2, rd_addr=rd,
        imm=imm_i, alu_op=ALU_ADD, alu_a_sel=A_RS1, alu_b_sel=B_IMM,
        result_sel=RES_ALU, rd_wen=0, mem_read=0, mem_write=0,
        mem_size=funct3 & 0b11, mem_unsigned=(funct3 >> 2) & 1,
        is_branch=0, branch_cond=funct3, is_jal=0, is_jalr=0,
        is_ecall=0, is_ebreak=0, illegal=0,
    )
    uses_rs1 = False
    uses_rs2 = False

    if opcode == OP_LUI:
        d.update(imm=imm_u, result_sel=RES_IMM, rd_wen=1)

    elif opcode == OP_AUIPC:
        d.update(imm=imm_u, alu_a_sel=A_PC, alu_b_sel=B_IMM,
                 alu_op=ALU_ADD, result_sel=RES_ALU, rd_wen=1)

    elif opcode == OP_JAL:
        d.update(imm=imm_j, alu_a_sel=A_PC, alu_b_sel=B_IMM, alu_op=ALU_ADD,
                 result_sel=RES_PC4, rd_wen=1, is_jal=1)

    elif opcode == OP_JALR:
        d.update(imm=imm_i, alu_a_sel=A_RS1, alu_b_sel=B_IMM, alu_op=ALU_ADD,
                 result_sel=RES_PC4, rd_wen=1, is_jalr=1)
        uses_rs1 = True
        if funct3 != 0b000:
            d["illegal"] = 1

    elif opcode == OP_BRANCH:
        d.update(imm=imm_b, alu_a_sel=A_RS1, alu_b_sel=B_RS2,
                 is_branch=1, branch_cond=funct3)
        uses_rs1 = uses_rs2 = True
        if funct3 in (0b000, 0b001):
            d["alu_op"] = ALU_SUB
        elif funct3 in (0b100, 0b101):
            d["alu_op"] = ALU_SLT
        elif funct3 in (0b110, 0b111):
            d["alu_op"] = ALU_SLTU
        else:  # 010 / 011 reserved
            d["illegal"] = 1

    elif opcode == OP_LOAD:
        d.update(imm=imm_i, alu_a_sel=A_RS1, alu_b_sel=B_IMM, alu_op=ALU_ADD,
                 result_sel=RES_MEM, rd_wen=1, mem_read=1)
        uses_rs1 = True
        if funct3 in (0b011, 0b110, 0b111):
            d["illegal"] = 1
        if not subword and (funct3 & 0b11) != 0b10:
            d["illegal"] = 1

    elif opcode == OP_STORE:
        d.update(imm=imm_s, alu_a_sel=A_RS1, alu_b_sel=B_IMM, alu_op=ALU_ADD,
                 mem_write=1)
        uses_rs1 = uses_rs2 = True
        if (funct3 >> 2) & 1 or funct3 == 0b011:
            d["illegal"] = 1
        if not subword and (funct3 & 0b11) != 0b10:
            d["illegal"] = 1

    elif opcode == OP_OP_IMM:
        d.update(imm=imm_i, alu_a_sel=A_RS1, alu_b_sel=B_IMM,
                 result_sel=RES_ALU, rd_wen=1)
        uses_rs1 = True
        if funct3 in (0b001, 0b101):  # shift-immediate
            d["alu_op"] = (bit(instr, 30) << 3) | funct3
            if funct3 == 0b001:
                if funct7 != 0b0000000:
                    d["illegal"] = 1
            else:
                if funct7 not in (0b0000000, 0b0100000):
                    d["illegal"] = 1
        else:
            d["alu_op"] = funct3  # mod = 0

    elif opcode == OP_OP:
        d.update(alu_a_sel=A_RS1, alu_b_sel=B_RS2, result_sel=RES_ALU, rd_wen=1)
        uses_rs1 = uses_rs2 = True
        d["alu_op"] = (bit(instr, 30) << 3) | funct3
        if funct7 == 0b0000000:
            pass
        elif funct7 == 0b0100000 and funct3 in (0b000, 0b101):
            pass
        else:
            d["illegal"] = 1

    elif opcode == OP_MISC_MEM:
        if funct3 != 0b000:  # FENCE.I / reserved
            d["illegal"] = 1
        # funct3 == 000 (FENCE): NOP bubble, all defaults.

    elif opcode == OP_SYSTEM:
        if funct3 == 0b000 and rs1 == 0 and rd == 0:
            funct12 = bits(instr, 31, 20)
            if funct12 == 0x000:
                d["is_ecall"] = 1
            elif funct12 == 0x001:
                d["is_ebreak"] = 1
            else:
                d["illegal"] = 1
        else:
            d["illegal"] = 1

    else:
        d["illegal"] = 1

    if rv32e and ((uses_rs1 and bit(rs1, 4))
                  or (uses_rs2 and bit(rs2, 4))
                  or (d["rd_wen"] and bit(rd, 4))):
        d["illegal"] = 1

    return d


# ---------------------------------------------------------------------------
# Driver / checker
# ---------------------------------------------------------------------------
def read_bundle(dut, suf):
    return {name: int(getattr(dut, f"{name}_{suf}").value) for name in OUTPUTS}


async def check(dut, instr, expect=None, note=""):
    """Drive `instr`, compare every config against the golden model.

    If `expect` is given, additionally assert those specific fields on the
    default ('d') instance (hand-computed oracle that also validates golden).
    """
    instr &= MASK32
    dut.instr.value = instr
    await Timer(1, unit="ns")

    for suf, (rv32e, subword) in CONFIGS.items():
        exp = golden_decode(instr, rv32e, subword)
        got = read_bundle(dut, suf)
        cfg = f"cfg={suf}(rv32e={rv32e},subword={subword})"
        if exp["illegal"]:
            assert got["illegal"] == 1, (
                f"{note} {cfg} instr=0x{instr:08X}: expected illegal, "
                f"got illegal={got['illegal']}")
        else:
            for name in OUTPUTS:
                assert got[name] == exp[name], (
                    f"{note} {cfg} instr=0x{instr:08X}: {name} "
                    f"got=0x{got[name]:X} exp=0x{exp[name]:X}")

    if expect is not None:
        got = read_bundle(dut, "d")
        for name, val in expect.items():
            assert got[name] == val, (
                f"{note} instr=0x{instr:08X}: {name} got=0x{got[name]:X} "
                f"exp=0x{val:X} (directed)")


# ---------------------------------------------------------------------------
# Directed: one encoding of every RV32E instruction, key fields hand-checked
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_directed_instructions(dut):
    dut._log.info("=== directed per-instruction decode ===")

    # Upper-immediate
    await check(dut, enc_u(OP_LUI, 5, 0xABCDE),
                expect=dict(result_sel=RES_IMM, rd_wen=1, illegal=0), note="LUI")
    await check(dut, enc_u(OP_AUIPC, 5, 0x12345),
                expect=dict(alu_a_sel=A_PC, alu_b_sel=B_IMM, alu_op=ALU_ADD,
                            result_sel=RES_ALU, rd_wen=1, illegal=0), note="AUIPC")

    # Jumps
    await check(dut, enc_j(OP_JAL, 1, 0x2C),
                expect=dict(alu_a_sel=A_PC, alu_op=ALU_ADD, result_sel=RES_PC4,
                            rd_wen=1, is_jal=1, illegal=0), note="JAL")
    await check(dut, enc_i(OP_JALR, 1, 0b000, 2, 0x10),
                expect=dict(alu_a_sel=A_RS1, alu_b_sel=B_IMM, alu_op=ALU_ADD,
                            result_sel=RES_PC4, rd_wen=1, is_jalr=1, illegal=0),
                note="JALR")

    # Branches: funct3 -> compare op
    for funct3, alu_op, name in ((0b000, ALU_SUB, "BEQ"), (0b001, ALU_SUB, "BNE"),
                                 (0b100, ALU_SLT, "BLT"), (0b101, ALU_SLT, "BGE"),
                                 (0b110, ALU_SLTU, "BLTU"), (0b111, ALU_SLTU, "BGEU")):
        await check(dut, enc_b(OP_BRANCH, funct3, 2, 3, 0x40),
                    expect=dict(is_branch=1, branch_cond=funct3, alu_op=alu_op,
                                alu_a_sel=A_RS1, alu_b_sel=B_RS2, rd_wen=0, illegal=0),
                    note=name)

    # Loads
    for funct3, name in ((0b000, "LB"), (0b001, "LH"), (0b010, "LW"),
                         (0b100, "LBU"), (0b101, "LHU")):
        await check(dut, enc_i(OP_LOAD, 4, funct3, 2, 0x7FF),
                    expect=dict(mem_read=1, result_sel=RES_MEM, rd_wen=1,
                                alu_op=ALU_ADD, mem_size=funct3 & 0b11,
                                mem_unsigned=(funct3 >> 2) & 1, illegal=0), note=name)

    # Stores
    for funct3, name in ((0b000, "SB"), (0b001, "SH"), (0b010, "SW")):
        await check(dut, enc_s(OP_STORE, funct3, 2, 3, 0x7FF),
                    expect=dict(mem_write=1, rd_wen=0, alu_op=ALU_ADD,
                                mem_size=funct3 & 0b11, illegal=0), note=name)

    # ALU immediate
    for funct3, name in ((0b000, "ADDI"), (0b010, "SLTI"), (0b011, "SLTIU"),
                         (0b100, "XORI"), (0b110, "ORI"), (0b111, "ANDI")):
        await check(dut, enc_i(OP_OP_IMM, 6, funct3, 7, 0x123),
                    expect=dict(alu_op=funct3, alu_a_sel=A_RS1, alu_b_sel=B_IMM,
                                result_sel=RES_ALU, rd_wen=1, illegal=0), note=name)
    # Shift immediates
    await check(dut, enc_i(OP_OP_IMM, 6, 0b001, 7, 0x00 << 5 | 3),  # SLLI shamt=3
                expect=dict(alu_op=0b0001, rd_wen=1, illegal=0), note="SLLI")
    await check(dut, enc_r(OP_OP_IMM, 6, 0b101, 7, 3, 0b0000000),  # SRLI shamt=3
                expect=dict(alu_op=0b0101, rd_wen=1, illegal=0), note="SRLI")
    await check(dut, enc_r(OP_OP_IMM, 6, 0b101, 7, 3, 0b0100000),  # SRAI shamt=3
                expect=dict(alu_op=0b1101, rd_wen=1, illegal=0), note="SRAI")

    # ALU register
    for funct3, funct7, alu_op, name in (
            (0b000, 0b0000000, 0b0000, "ADD"), (0b000, 0b0100000, 0b1000, "SUB"),
            (0b001, 0b0000000, 0b0001, "SLL"), (0b010, 0b0000000, 0b0010, "SLT"),
            (0b011, 0b0000000, 0b0011, "SLTU"), (0b100, 0b0000000, 0b0100, "XOR"),
            (0b101, 0b0000000, 0b0101, "SRL"), (0b101, 0b0100000, 0b1101, "SRA"),
            (0b110, 0b0000000, 0b0110, "OR"), (0b111, 0b0000000, 0b0111, "AND")):
        await check(dut, enc_r(OP_OP, 6, funct3, 7, 8, funct7),
                    expect=dict(alu_op=alu_op, alu_a_sel=A_RS1, alu_b_sel=B_RS2,
                                result_sel=RES_ALU, rd_wen=1, illegal=0), note=name)

    # System / misc
    await check(dut, enc_i(OP_MISC_MEM, 0, 0b000, 0, 0),  # FENCE -> nop
                expect=dict(illegal=0, rd_wen=0, mem_read=0, mem_write=0,
                            is_branch=0), note="FENCE")
    await check(dut, 0x00000073,  # ECALL
                expect=dict(is_ecall=1, is_ebreak=0, illegal=0, rd_wen=0), note="ECALL")
    await check(dut, 0x00100073,  # EBREAK
                expect=dict(is_ebreak=1, is_ecall=0, illegal=0, rd_wen=0), note="EBREAK")

    await Timer(1, unit="ns")


# ---------------------------------------------------------------------------
# Directed: immediate generation (hand-computed values)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_immediates(dut):
    dut._log.info("=== immediate generation ===")

    # I-type sign extension
    await check(dut, enc_i(OP_OP_IMM, 1, 0b000, 2, 0x7FF),
                expect=dict(imm=0x0000_07FF), note="I +2047")
    await check(dut, enc_i(OP_OP_IMM, 1, 0b000, 2, -1),
                expect=dict(imm=0xFFFF_FFFF), note="I -1")
    await check(dut, enc_i(OP_OP_IMM, 1, 0b000, 2, -2048),
                expect=dict(imm=0xFFFF_F800), note="I -2048")

    # S-type
    await check(dut, enc_s(OP_STORE, 0b010, 2, 3, -4),
                expect=dict(imm=0xFFFF_FFFC), note="S -4")
    await check(dut, enc_s(OP_STORE, 0b010, 2, 3, 0x7FF),
                expect=dict(imm=0x0000_07FF), note="S +2047")

    # B-type (bit0 always 0)
    await check(dut, enc_b(OP_BRANCH, 0b000, 2, 3, -2),
                expect=dict(imm=0xFFFF_FFFE), note="B -2")
    await check(dut, enc_b(OP_BRANCH, 0b000, 2, 3, 0x0FFE),
                expect=dict(imm=0x0000_0FFE), note="B +4094 (max positive)")

    # U-type (no sign extension; low 12 bits zero)
    await check(dut, enc_u(OP_LUI, 1, 0xFFFFF),
                expect=dict(imm=0xFFFF_F000), note="U top")
    await check(dut, enc_u(OP_LUI, 1, 0x00001),
                expect=dict(imm=0x0000_1000), note="U 1")

    # J-type
    await check(dut, enc_j(OP_JAL, 1, -2),
                expect=dict(imm=0xFFFF_FFFE), note="J -2")
    await check(dut, enc_j(OP_JAL, 1, 0x000FFFFE & ~1),
                expect=dict(imm=0x000F_FFFE), note="J +max")

    await Timer(1, unit="ns")


# ---------------------------------------------------------------------------
# Directed: illegal encodings
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_illegal(dut):
    dut._log.info("=== illegal encodings ===")

    illegal_words = [
        ("MUL (M-ext)",        enc_r(OP_OP, 6, 0b000, 7, 8, 0b0000001)),
        ("OP stray funct7",    enc_r(OP_OP, 6, 0b001, 7, 8, 0b0100000)),
        ("SLLI bad funct7",    enc_r(OP_OP_IMM, 6, 0b001, 7, 3, 0b0100000)),
        ("SRLI/SRAI bad f7",   enc_r(OP_OP_IMM, 6, 0b101, 7, 3, 0b0010000)),
        ("SLLI shamt[5] set",  enc_r(OP_OP_IMM, 6, 0b001, 7, 0b100000 & 0x1F,
                                     0b0000000) | (1 << 25)),
        ("LOAD funct3=011",    enc_i(OP_LOAD, 4, 0b011, 2, 0)),
        ("LOAD funct3=111",    enc_i(OP_LOAD, 4, 0b111, 2, 0)),
        ("STORE funct3=011",   enc_s(OP_STORE, 0b011, 2, 3, 0)),
        ("STORE funct3=100",   enc_s(OP_STORE, 0b100, 2, 3, 0)),
        ("BRANCH funct3=010",  enc_b(OP_BRANCH, 0b010, 2, 3, 0)),
        ("BRANCH funct3=011",  enc_b(OP_BRANCH, 0b011, 2, 3, 0)),
        ("JALR funct3=001",    enc_i(OP_JALR, 1, 0b001, 2, 0)),
        ("CSRRW (Zicsr)",      enc_i(OP_SYSTEM, 1, 0b001, 2, 0)),
        ("FENCE.I (Zifencei)", enc_i(OP_MISC_MEM, 0, 0b001, 0, 0)),
        ("malformed ECALL",    0x00000073 | (1 << 15)),  # rs1 != 0
        ("bad SYSTEM funct12", 0x00200073),
        ("opcode low bits !=11", 0x00000000),
        ("unknown opcode",     0x0000007B),  # opcode 1111011
    ]
    for name, word in illegal_words:
        # every config must flag it (subword/rv32e don't make these legal)
        for suf, (rv32e, subword) in CONFIGS.items():
            assert golden_decode(word, rv32e, subword)["illegal"] == 1, \
                f"golden model bug: {name} should be illegal"
        await check(dut, word, note=name)

    await Timer(1, unit="ns")


# ---------------------------------------------------------------------------
# Directed: subword gating (ENABLE_SUBWORD)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_subword_gating(dut):
    dut._log.info("=== subword gating ===")

    subword_ops = [
        enc_i(OP_LOAD, 4, 0b000, 2, 0),   # LB
        enc_i(OP_LOAD, 4, 0b001, 2, 0),   # LH
        enc_i(OP_LOAD, 4, 0b100, 2, 0),   # LBU
        enc_i(OP_LOAD, 4, 0b101, 2, 0),   # LHU
        enc_s(OP_STORE, 0b000, 2, 3, 0),  # SB
        enc_s(OP_STORE, 0b001, 2, 3, 0),  # SH
    ]
    for word in subword_ops:
        # legal with subword on ('d'), illegal with it off ('n')
        assert golden_decode(word, 1, 1)["illegal"] == 0
        assert golden_decode(word, 1, 0)["illegal"] == 1
        await check(dut, word, note="subword op")

    # Word accesses remain legal regardless of ENABLE_SUBWORD
    for word in (enc_i(OP_LOAD, 4, 0b010, 2, 0), enc_s(OP_STORE, 0b010, 2, 3, 0)):
        assert golden_decode(word, 1, 0)["illegal"] == 0
        await check(dut, word, note="word op")

    await Timer(1, unit="ns")


# ---------------------------------------------------------------------------
# Directed: RV32E x16-x31 range check
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_rv32e_range(dut):
    dut._log.info("=== RV32E x16-x31 range check ===")

    # Used field referencing x16+ -> illegal under RV32E, legal under RV32I.
    cases = [
        ("rd=x16 ADDI",  enc_i(OP_OP_IMM, 16, 0b000, 1, 0)),
        ("rs1=x16 ADDI", enc_i(OP_OP_IMM, 1, 0b000, 16, 0)),
        ("rs2=x16 ADD",  enc_r(OP_OP, 1, 0b000, 2, 16, 0b0000000)),
        ("rs1=x31 SW",   enc_s(OP_STORE, 0b010, 31, 3, 0)),
        ("rs2=x31 SW",   enc_s(OP_STORE, 0b010, 2, 31, 0)),
    ]
    for name, word in cases:
        assert golden_decode(word, 1, 1)["illegal"] == 1, f"{name} rv32e"
        assert golden_decode(word, 0, 1)["illegal"] == 0, f"{name} rv32i"
        await check(dut, word, note=name)

    # Negative controls: bit-4 set on an UNUSED field must stay legal.
    neg = [
        # LUI: rs1/rs2 fields unused; put x31 pattern in them (bits 19:15).
        ("LUI unused rs1", enc_u(OP_LUI, 1, 0xABCDE) | (31 << 15)),
        # STORE: rd field (bits 11:7) is immediate, not a register use.
        # imm=0x010 puts bit 4 in the rd-field position; must stay legal.
        ("SW rd-field bits", enc_s(OP_STORE, 0b010, 2, 3, 0x010)),
    ]
    for name, word in neg:
        assert golden_decode(word, 1, 1)["illegal"] == 0, f"{name} should stay legal"
        await check(dut, word, note=name)

    await Timer(1, unit="ns")


# ---------------------------------------------------------------------------
# Fuzz: random words vs golden model across all configs
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_fuzz(dut):
    dut._log.info("=== random fuzz vs golden model ===")
    rng = random.Random(0xDEC0DE)
    N = 4000

    opcode_seen = set()
    n_legal = n_illegal = 0
    for _ in range(N):
        word = rng.getrandbits(32)
        await check(dut, word, note="fuzz")
        opcode_seen.add(bits(word, 6, 0))
        if golden_decode(word, 1, 1)["illegal"]:
            n_illegal += 1
        else:
            n_legal += 1

    dut._log.info(f"fuzz: {N} words, {len(opcode_seen)} distinct opcodes, "
                  f"legal(dflt)={n_legal}, illegal(dflt)={n_illegal}")

    await Timer(1, unit="ns")
