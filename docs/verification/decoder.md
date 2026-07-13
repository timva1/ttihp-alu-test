# `decoder.v` — verification plan

Verification of the combinational RV32E decoder (`src/core/decoder.v`, spec in
`docs/microarchitecture/decoder.md`). Implements the decoder unit-level item of
`docs/architecture.md` §9.1: golden-model compare over all opcodes plus fuzzed
illegal encodings.

## Approach

Plain **cocotb** — a single combinational module with no submodule
integration, so the UVM heuristic (>2 independently-developed submodules) does
not apply. A **Python golden model** (`golden_decode`) reimplements the decode
*contract* independently from the spec/tables in `decoder.md` (not transcribed
from the RTL structure), and is compared against the RTL field-by-field.
Combinational, so the pattern mirrors `test_alu.py`: drive `instr`,
`await Timer(1, 'ns')`, read outputs.

**Legal vs. illegal checking.** For **legal** encodings the *full* output
bundle is checked. For **illegal** encodings only `illegal == 1` is asserted —
the control path halts on `illegal` and never consumes the rest of the bundle,
so pinning those outputs would couple the test to arbitrary RTL choices rather
than the spec. The `illegal` flag itself is checked in *both* directions (a
golden-legal word asserts `illegal == 0` as part of its full-bundle check; a
golden-illegal word asserts `illegal == 1`), so a legal/illegal disagreement is
caught either way.

The decoder does **not** special-case `x0` (zero-suppression is `rf`'s job), so
`rd_wen` stays 1 for `rd = x0`; the golden model matches that.

## Files

Repo `test_<name>` pattern (`make -B test_decoder`):

- `test/test_modules/decoder_tb.v` — module `decoder_tb`; one shared `instr`
  input reg feeds **three parameter instances** so both parameter axes are
  covered in one run:
  - `dflt` (`d`) — `RV32E=1, ENABLE_SUBWORD=1` (baseline / tape-out build)
  - `rv32i` (`i`) — `RV32E=0` (x16–x31 legal)
  - `nosub` (`n`) — `ENABLE_SUBWORD=0` (subword loads/stores illegal)
- `test/test_decoder.py` — golden model `decode(instr, rv32e, subword)` plus the
  tests below. A shared `check()` drives one `instr` and compares every config
  against its own golden result; directed tests additionally assert
  hand-computed values on discriminating fields (which also validates the
  golden model itself).

## Test scenarios

1. **Directed per-instruction** — ≥1 encoding of all 40 RV32E instructions;
   full bundle checked (`alu_op`, `alu_a_sel`/`alu_b_sel`, `result_sel`,
   `rd_wen`, `mem_*`, `is_*`, `imm`, reg addrs), with key fields hand-asserted.
2. **Immediate generation** — targeted I/S/B/U/J vectors: sign extension
   (negative, max-positive) and the B/J bit-scramble; `imm` checked exactly.
3. **`alu_op` encoding** — `{mod,funct3}` across all OP/OP-IMM funct3, incl.
   ADD/SUB, SRL/SRA, and SRLI/SRAI distinguished by `instr[30]`; branch
   `funct3` → SUB/SLT/SLTU.
4. **Illegal (directed)** — M-ext `funct7=0000001`; stray OP funct7; bad
   shift-immediate funct7/`shamt[5]`; reserved load/store/branch funct3;
   `JALR funct3≠0`; CSR ops; `FENCE.I`; unknown opcode; `instr[1:0]≠11`.
5. **ECALL/EBREAK** — exact encodings set `is_ecall`/`is_ebreak` (and *not*
   `illegal`); malformed variants (nonzero rs1/rd, other funct12) → `illegal`.
6. **Subword gating** — under `nosub`: `LB/LH/LBU/LHU/SB/SH` → `illegal`,
   `LW/SW` legal; under `dflt` all legal.
7. **RV32E range check** — under `dflt`, a *used* field referencing x16–x31 →
   `illegal`; the *same* word under `rv32i` → legal. Includes negative controls
   proving *unused* fields don't trip it (e.g. `LUI` with rs1-field = x31 stays
   legal; a store's rd-field bits are immediate, not a register use).
8. **Fuzz** — a few thousand random 32-bit words, field-by-field vs. the golden
   model across all three configs (§9.1's "fuzzed illegal encodings").

## Coverage

Directed set (every instruction + every illegal category) plus the fuzz sweep,
with a tally printed at the end (distinct opcodes seen, legal-vs-illegal
counts) — a coverage-style summary, not formal coverage collection.

## Conventions

No clock (combinational). Every `@cocotb.test()` ends with an unconditional
`await Timer(...)`, and any trailing `await` sits after a `try/except` rather
than only on a success path (see CLAUDE.md).

## Exit criterion (build-module)

Tests **run cleanly to completion** (no simulator/teardown error); pass/fail is
reported but failures are not root-caused here (that is the verify-module
workflow). As implemented, `make -B test_decoder` runs clean and all six tests
pass.
