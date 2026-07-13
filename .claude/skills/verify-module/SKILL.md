---
name: verify-module
description: Root-causes failing verification for one module, one failure at a time, until its test suite passes. Only run when the user explicitly invokes it for a specific module whose verification already exists and runs (via build-module) but doesn't fully pass yet — not for writing new verification scenarios from scratch.
---

This picks up where `build-module` leaves off: that skill's exit criteria is verification code that *runs*, not that it *passes*. This skill drives it the rest of the way to passing, one failure at a time, with an explicit sign-off on each root cause before any fix is written — this is where a lot of the real architecture/verification learning in this project happens, so don't compress it into a batch.

## Steps

1. **Run the module's verification suite** and collect the current list of failing tests/scenarios.

2. **Pick one failing test** (or a tight cluster of failures with an obviously shared symptom — same signal, same corner case) to investigate next. Never batch unrelated failures together.

3. **Investigate without assuming which side is wrong.** Read waveforms/logs, trace signal values, and form a root-cause hypothesis. The bug is equally likely to be in the RTL (`src/`) or in the verification model/testbench (`test/`) — treat both as suspects until the evidence points one way.

4. **Present the hypothesis and evidence, then stop.** Show the user the specific evidence (waveform/log excerpts, signal values, the doc section it contradicts) and wait for explicit confirmation of the root cause before writing any fix. Do not implement a fix on a hypothesis the user hasn't confirmed.

5. **If the confirmed root cause traces back to a flaw in the agreed microarchitectural plan** (`docs/microarchitecture/<module_name>.md`), rather than just an implementation slip:
   - Stop before implementing anything.
   - Propose the corrected plan section to the user and get explicit re-confirmation on the revised plan — same bar as the original planning step in `build-module`.
   - Update the plan doc once agreed, before touching code.

6. **Implement the minimal, targeted fix** for the confirmed root cause — in `src/` or `test/`, whichever the diagnosis pointed to — following this repo's existing conventions (e.g. cocotb's trailing unconditional `await`, per-test clock restart; see `CLAUDE.md`). Re-run the verification suite.

7. **Repeat steps 2–6** for the next failing test, one at a time, until every test in the module's verification plan passes.

8. **Exit criteria: all planned tests pass.** Update the module's entry in the "verified modules" project memory to reflect it's now verified, and call out in your summary whether the microarchitecture or verification plan docs were revised along the way.

## Out of scope

- Writing new verification scenarios / expanding coverage from scratch is `build-module`'s verification-planning step, not this skill. If root-causing surfaces a real coverage gap (a scenario that should exist but wasn't planned), flag it to the user as a proposed addition rather than silently adding it.
- Switching verification approach/scale (e.g. cocotb → UVM) is a `build-module`-level decision, not something to change mid-debug here.
