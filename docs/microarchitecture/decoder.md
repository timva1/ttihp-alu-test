# `decoder.v` — combinational instruction decode

Refinement of `docs/architecture.md` §4.1 (the decoder is one of the blocks
shared by both `CORE_ARCH` variants) and the ALU re-encoding in §4.1's
`alu_op` table. This document is the implementation contract for the module.

## Role

Pure **combinational** RV32E instruction decode: instruction word in, control
bundle out. No PC input, no state, no clock. One decoder serves both the
`MULTICYCLE` and `PIPELINED` cores — the FSM/pipeline only *sequences* its
outputs differently, so the decoder itself is cycle-agnostic.

## Parameters

| Param | Default | Effect |
|---|---|---|
| `RV32E` | 1 | When 1, any *used* register field referencing `x16`–`x31` (bit 4 set) makes the instruction `illegal` (§2.2). 0 disables the check (RV32I, all 32 registers legal). |
| `ENABLE_SUBWORD` | 1 | When 0, `LB/LH/LBU/LHU/SB/SH` decode to `illegal` (§2.1, §2.2); only `LW`/`SW` remain. |

## Interface

Input: `instr[31:0]`.

Outputs (the control bundle):

| Signal | Width | Meaning |
|---|---|---|
| `rs1_addr`, `rs2_addr`, `rd_addr` | 5 | Raw ISA register fields (`instr[19:15]`, `[24:20]`, `[11:7]`). Feed `rf` directly; `rf` masks to 4 bits under `USE_E_EXT`. |
| `imm` | 32 | Sign-extended immediate; format (I/S/B/U/J) selected by opcode. 0 for R-type. |
| `alu_op` | 4 | ALU op for the **primary pass** (below), in the `{mod,funct3}` encoding of §4.1. |
| `alu_a_sel` | 1 | Primary ALU input A source: `RS1` (0) / `PC` (1). |
| `alu_b_sel` | 1 | Primary ALU input B source: `RS2` (0) / `IMM` (1). |
| `result_sel` | 2 | Writeback source: `ALU` (0) / `MEM` (1) / `PC_PLUS_4` (2) / `IMM` (3). |
| `rd_wen` | 1 | Instruction writes `rd`. |
| `mem_read`, `mem_write` | 1 | Load / store. |
| `mem_size` | 2 | `funct3[1:0]` — 00=byte, 01=half, 10=word. |
| `mem_unsigned` | 1 | `funct3[2]` — unsigned load (LBU/LHU). |
| `is_branch` | 1 | Conditional branch. |
| `branch_cond` | 3 | `funct3` — the branch's compare/polarity code, for the control path to evaluate against the ALU flags. |
| `is_jal`, `is_jalr` | 1 | Jumps (redirect + link). |
| `is_ecall`, `is_ebreak` | 1 | Environment ops → HALTED (§2.2). Kept distinct from `illegal` so a future Zicsr/trap unit can assign them separate causes without re-decoding. |
| `illegal` | 1 | Unknown/unimplemented encoding or a §2.2 halt-by-illegal cause. |

`FENCE` decodes as a NOP: all control outputs inactive, `illegal = 0` — it
falls through as a bubble.

## Primary-pass convention

The `alu_*` / `result_sel` selects describe each instruction's **single
primary datapath computation**:

| Instruction(s) | `alu_a_sel` | `alu_b_sel` | `alu_op` | `result_sel` | Notes |
|---|---|---|---|---|---|
| `LUI` | — | — | — | `IMM` | ALU bypassed |
| `AUIPC` | `PC` | `IMM` | ADD | `ALU` | PC + imm |
| `OP` (R-type ALU) | `RS1` | `RS2` | `{funct7[5],funct3}` | `ALU` | |
| `OP-IMM` | `RS1` | `IMM` | `{mod,funct3}` (mod=`instr[30]` for shifts, else 0) | `ALU` | |
| `LOAD` | `RS1` | `IMM` | ADD | `MEM` | address = rs1+imm |
| `STORE` | `RS1` | `IMM` | ADD | — | address = rs1+imm |
| `JAL` | `PC` | `IMM` | ADD | `PC_PLUS_4` | ALU computes target; rd = PC+4 |
| `JALR` | `RS1` | `IMM` | ADD | `PC_PLUS_4` | ALU computes target; control clears bit 0 (§2.2); rd = PC+4 |
| `BRANCH` | `RS1` | `RS2` | SUB (BEQ/BNE), SLT (BLT/BGE), SLTU (BLTU/BGEU) | — | primary pass is the *comparison* |

**The one secondary pass.** A taken branch also needs its target `PC + imm`.
That is the **only** case whose operands differ from the decoder's primary
selects, and it is driven by the control path (FSM cycle / pipeline stage)
keyed off `is_branch` — not by a decoder mux. Every *other* redirect target is
exactly the primary ALU result (`JAL`/`JALR`). This keeps the decoder
stateless and cycle-agnostic so both cores can share it unchanged.

### `alu_op` encoding (from §4.1)

`alu_op = {mod, funct3}`:

| `alu_op` | Op | | `alu_op` | Op |
|---|---|---|---|---|
| `0_000` | ADD | | `1_000` | SUB |
| `0_001` | SLL | | `1_101` | SRA |
| `0_010` | SLT | | | |
| `0_011` | SLTU | | | |
| `0_100` | XOR | | | |
| `0_101` | SRL | | | |
| `0_110` | OR | | | |
| `0_111` | AND | | | |

- `OP`: `mod = funct7[5]` (distinguishes ADD/SUB and SRL/SRA).
- `OP-IMM`: `mod = 0` for all except the shift-immediates; for `SLLI/SRLI/SRAI`
  `mod = instr[30]` (so SRAI vs SRLI). SLLI has only the `mod=0` form.
- `BRANCH`: mapped from `funct3` to the comparison op producing the flag the
  control path needs (SUB → zero flag for BEQ/BNE; SLT/SLTU → result bit 0 for
  the signed/unsigned orderings).
- `LOAD/STORE/AUIPC/JAL/JALR`: always ADD.

## `illegal` conditions

`illegal` is the OR of:

- `instr[1:0] != 2'b11` (not a base 32-bit instruction) or an unknown
  `opcode[6:2]`.
- `OP` with `funct7 == 0000001` (M-extension) or any `funct7` outside
  `{0000000, 0100000}`.
- `OP-IMM` shift (`SLLI/SRLI/SRAI`) with a bad `funct7` (not `0000000`, and not
  `0100000` for `SRAI`) or `shamt[5]` (`instr[25]`) set.
- Reserved `funct3`: load `011/110/111`, store `≠000/001/010`, branch
  `010/011`, `JALR ≠ 000`.
- `SYSTEM` CSR ops (any `funct3 != 000`) and any non-`ECALL`/`EBREAK` encoding
  in the `funct3==000` SYSTEM space; `FENCE.I` (`MISC-MEM funct3==001`) — no
  Zicsr/Zifencei in v1.
- Subword load/store (`mem_size != 10`, i.e. byte/half) when
  `ENABLE_SUBWORD = 0`.
- `RV32E = 1` and any *used* register field has bit 4 set (references
  `x16`–`x31`). "Used" is per-class: `rd` for rd-writers, `rs1` for
  rs1-users, `rs2` for rs2-users — so e.g. `LUI`/`AUIPC`/`JAL` check only `rd`,
  stores/branches check `rs1`/`rs2`, U/J immediates never gate on rs fields.

`ECALL`/`EBREAK` are **not** `illegal` — they are reported on their own flags
and halt via a distinct cause.

## Internal structure

Flat combinational logic, optimized for readability and one-to-one
correspondence with the tables above (same philosophy as `alu.v`, no explicit
resource sharing):

1. Field extracts: `opcode`, `funct3`, `funct7`, `rs1/rs2/rd`.
2. One immediate-format mux (I/S/B/U/J) → `imm`.
3. A per-opcode `case` producing the control bundle, with `illegal` accumulated
   from the per-opcode legality checks and the `RV32E` register-range check.

Purely combinational → zero latency; the consuming FSM/pipeline registers the
bundle.
