# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A [Tiny Tapeout](https://tinytapeout.com) project: a small Verilog design (`tt_um_example`, instantiating a 32-bit ALU) that gets hardened into a GDS and fabricated as one tile on a shared IHP sg13g2 shuttle. The repo is a fork of the standard TT Verilog project template — most of the scaffolding (CI workflows, `src/config.json`, devcontainer) is shared boilerplate, not project-specific code.

## Commands

All test commands are run from the `test/` directory.

```sh
cd test
source ~/Documents/TinyTapeout/oss-cad-suite/environment # activate the OSS CAD suite (needed to run all tests)
make -B              # run RTL simulation (cocotb + Icarus Verilog)
make clean           # clean sim_build/ and generated waveform/results files
make -B FST=         # dump real VCD instead of FST (see dumpfile note below)
make -B GATES=yes    # gate-level simulation (requires gate_level_netlist.v, copied from a hardened build)
make -B test_<name>  # run with a custom testbench pair: <name>_tb.v + test_<name>.py
```

The `test_<name>` target works by re-invoking make with `CUSTOM_TB=<name>`, which swaps in `test/<name>_tb.v` as the Verilog toplevel (module name `<name>_tb`) and `test/test_<name>.py` as the cocotb test module. All other flags compose normally, e.g. `make -B test_foo GATES=yes FST=`.

- Simulator is Icarus Verilog (`SIM=icarus`) driven via cocotb; default test entry point is `test/test.py`, default Verilog testbench wrapper is `test/tb.v`.
- `COCOTB_TEST_MODULES` in `test/Makefile` controls which Python test module(s) run; it is set automatically by `test_<name>` targets.
- Test results land in `test/results.xml` (JUnit format) and waveforms in `test/tb.fst`/`tb.vcd`. View with `gtkwave tb.fst tb.gtkw` or `surfer tb.fst`.
- Python deps: `pip install -r test/requirements.txt` (cocotb 2.0.1, pytest).
- `PROJECT_SOURCES` in `test/Makefile` must be kept in sync with `source_files` in `info.yaml` whenever Verilog files are added/renamed.
- `test/Makefile` includes makefiles from OSS CAD suite, located at `../oss-cad-suite/lib/python3.11/site-packages/cocotb-2.1.0.dev0+41564633-py3.11-darwin-aarch64.egg/cocotb_tools/makefiles`. When using Icarus, `simulators/Makefile.icarus` is of interest
- `test/tb.v` names its dumpfile `tb.vcd`, but the Makefile's default `FST=-fst` still loads the FST plugin, so that file is actually FST-formatted despite the `.vcd` extension. Pass `FST=` (empty) to get a real VCD at that path, or rename the dumpfile to `tb.fst` to match the default.
- `test/tools/vcd_trace.py` — dumps chosen signals from a **real VCD** as a per-clock-edge text table (`--list` to discover hierarchical names; `--clk`/`--signals` globs/`--window START_NS END_NS`). Use it for CLI root-causing when you can't read a waveform interactively — cycle-relationship bugs (a strobe pulse vs. a bus access landing a cycle late), NBA/edge-sampling races, "which cycle did this register actually change". It reads the artifact a prior run already produced, so first regenerate a real VCD with the `FST=` override (the default dump is FST despite the `.vcd` name — see above), e.g. `make -B test_uart FST=` then `python tools/vcd_trace.py waves/uart_tb.vcd --clk uart_tb.clk --signals 'uart_tb.uut.rx_*' --window 22170 22260`. Prefer this over gtkwave/surfer when working non-interactively; prefer live cocotb logging when you'd otherwise re-run anyway.

There is no separate lint/build step to run locally beyond simulation — synthesis/hardening (LibreLane) and DRC/LVS run only in CI (`gds` workflow) against the IHP PDK.

## Architecture

- `src/alu.v` — `alu` module: a combinational 32-bit ALU (`alu_op[3:0]` selects OR/AND/XOR/ADD/SUB/SLL/SRL/SRA/SLTU/SLT), deliberately written with no explicit resource sharing between ops, used to observe how well synthesis optimizes it.
- `src/project.v` — `tt_um_example`: the actual TinyTapeout top module. It must keep the fixed TT pin interface (`ui_in`, `uo_out`, `uio_in`/`uio_out`/`uio_oe`, `ena`, `clk`, `rst_n`). It expands the 8 dedicated inputs (`ui_in`) into the ALU's two 32-bit operands by replicating/inverting nibbles (`alu_input_a`/`alu_input_b` construction), takes the op code from `uio_in[3:0]`, and exposes only the low byte of `alu_output` on `uo_out`. All unused signals (high bits of `alu_output`, `uio_in[7:4]`, `ena`, `clk`, `rst_n`) are tied into a single `_unused` wire to suppress lint warnings — extend that pattern rather than leaving new unused signals dangling.
- `test/tb.v` — thin Verilog wrapper instantiating `tt_um_example` and dumping waveforms; update the instantiated module name here if the top module is renamed.
- `test/test.py` — cocotb testbench. Mirrors the bit-mangling logic from `project.v` in Python (`calc_alu_input_a`/`calc_alu_input_b`/`calc_alu_expected_result`) to compute expected ALU results from `ui_in`/`uio_in` and compare against `uo_out`. Keep these Python helpers in sync with any change to the operand-construction logic in `project.v`.
- `info.yaml` — TinyTapeout project metadata consumed by the build/docs/GDS pipelines: `top_module`, `source_files` (must list every Verilog file under `src/`, one per line), pin descriptions for the datasheet, clock frequency, tile count. This is the source of truth the CI actions read — editing Verilog without updating `source_files` here breaks the hardening/docs workflows.
- `docs/info.md` — project datasheet description ("How it works" / "How to test" sections), currently unfilled placeholder text; rendered into the public docs by the `docs` workflow.
- `docs/architecture.md` — the whole-design specification/implementation contract for the parametrizable RV32E SoC (module boundaries, interfaces, parameters, register maps, pinout). The higher-level plan every module refines.
- `docs/microarchitecture/<module>.md` — per-module microarchitectural plan (interfaces, internal structure, timing, key decisions), agreed before RTL and kept in sync with it; written by the `build-module` skill.
- `docs/verification/<module>.md` — per-module verification plan (scenarios, coverage, cocotb-vs-UVM), agreed before test code and kept in sync with it; written by the `build-module` skill.
- `src/config.json` — LibreLane hardening configuration (density, clock period, margins, etc.). Marked "do not edit unless you know what you are doing"; only touch when fixing a specific hardening failure (e.g. placement/timing violations), not for general changes.

## CI workflows (`.github/workflows/`)

- `test.yaml` — runs the cocotb RTL simulation on every push (`cd test && make clean && make`), fails the build if `results.xml` contains a `failure`.
- `gds.yaml` — hardens the design via LibreLane (`TinyTapeout/tt-gds-action`, IHP sg13g2 PDK), runs Tiny Tapeout precheck, gate-level test, and deploys a viewer to GitHub Pages. This is the authoritative ASIC build; there's no equivalent full local flow.
- `docs.yaml` — builds the public datasheet from `docs/info.md` + `info.yaml`.
- `fpga.yaml` — builds an FPGA (ICE40UP5K) bitstream; disabled on push by default (`branches: none`), runs only via `workflow_dispatch`.

## Conventions specific to this repo

- **Every `@cocotb.test()` coroutine must end with an `await` (e.g. `await ClockCycles(dut.clk, 1)`) that runs unconditionally — never let the coroutine return in the same delta cycle as the last `dut.<signal>.value = ...` write, including on an exception path (e.g. a caught `AssertionError`).** Doing so corrupts this Icarus/cocotb build's (`cocotb-2.1.0.dev0+41564633`, bundled with the `oss-cad-suite` darwin-aarch64 toolchain) waveform-dump teardown: the test reports "passed" and the simulation appears to finish cleanly, but `vvp` segfaults moments later while closing the FST/VCD dump (right after the "dumpfile ... opened for output" line). If you add a new test or wrap an assertion in `try/except`, put the trailing `await` after the `try/except`, not only inside the success path.
- **`cocotb.start_soon` tasks (including `Clock`) are cancelled at the end of the test that created them.** Never guard clock startup with a module-level `_clock_started` flag — the clock must be restarted in every test's setup function (or a fixture that runs per-test), otherwise subsequent tests run without a clock and the simulator terminates with "ran out of events".
- Top module name must start with `tt_um_` and stay unique (TinyTapeout convention) — currently `tt_um_example`; if renaming, update it in `info.yaml` (`top_module`), `src/project.v` (module name), and `test/tb.v` (instantiation).
- Bidirectional IO (`uio_out`, `uio_oe`) must always be fully assigned even when unused (currently `uio_out` is tied to 0 in `project.v`).
- ALU op encoding (`alu_op[3:0]`) is defined in both `src/alu.v` (Verilog case statement) and `test/test.py` (`ALU_OP_*` constants) — keep both in sync if op codes change.
- New RTL modules (and their verification) go through two skills, both invoked explicitly by the user per module, never automatically: `.claude/skills/build-module` (plan → implement RTL → plan → implement verification, exit criteria is verification that *runs*), then `.claude/skills/verify-module` (root-causes failures one at a time, with sign-off, until verification *passes*).
