---
name: build-module
description: Plan-then-implement workflow for adding one new RTL module and its verification to this RISC-V/TinyTapeout project. Only run when the user explicitly invokes this skill for a specific module they've named — not a default for small edits or bug fixes.
---

This project exists to build the user's understanding of computer architecture and verification methodology, not just to accumulate RTL. That learning is protected by an explicit plan-and-feedback gate before *both* the RTL and its verification — never skip a gate to save a round-trip. One module per invocation; the user names it.

## Steps

1. **Confirm scope.** Identify the named module (purpose, where it sits in the architecture). Ask only if ambiguous.

2. **Propose a microarchitectural plan**, as a refinement of `docs/architecture.md` (the whole-design plan), not an independent derivation. Read that doc *surgically*, not whole: §8 maps modules→files, and grepping the module name finds the sections that specify it — read those plus what they cross-reference. Flag any module/architecture conflict before proceeding. Cover: interfaces (ports, handshake, timing), internal structure (datapath, FSM, key registers), timing (comb vs. registered, latency), and key decisions + rejected alternatives. Iterate until the user **explicitly agrees**; don't implement before that.

3. **Persist the agreed plan** to `docs/microarchitecture/<module>.md` (create the dir if needed) — the durable reference the RTL must track; keep it in sync if the plan later changes.

4. **Implement the RTL** in `src/`, structured/named so the correspondence to the plan doc is obvious. Update `info.yaml` `source_files` and `test/Makefile` `PROJECT_SOURCES` (see CLAUDE.md).

5. **Propose a verification plan** at similar detail: what's checked and why, scenarios/corner cases, coverage approach, and cocotb vs. UVM (UVM only when verifying means integrating **>2 independently-developed submodules**; user may override). Iterate until the user **explicitly agrees** before writing test code.

6. **Persist the agreed verification plan** to `docs/verification/<module>.md` (create the dir if needed) — the durable reference the test code must track; keep it in sync if the plan later changes.

7. **Implement verification** to match the plan, readable and clearly corresponding to it, following the repo's cocotb rules (per-test clock restart, trailing unconditional `await`, etc. — CLAUDE.md).

8. **Exit when the verification runs cleanly** (completes with no simulator/teardown error) — it need **not** pass yet. Report pass/fail; do not root-cause or fix failures here.

## Out of scope

- Fixing failing verification until it passes → the separate verify-module workflow.
- Updating the "verified modules" memory → end of *that* workflow, not this one.
