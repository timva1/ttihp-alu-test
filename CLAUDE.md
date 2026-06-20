# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A [Tiny Tapeout](https://tinytapeout.com) project: a small Verilog design (`tt_um_example`, instantiating a 32-bit ALU) that gets hardened into a GDS and fabricated as one tile on a shared IHP sg13g2 shuttle. The repo is a fork of the standard TT Verilog project template — most of the scaffolding (CI workflows, `src/config.json`, devcontainer) is shared boilerplate, not project-specific code.

## Commands

All test commands are run from the `test/` directory.

```sh
cd test
make -B              # run RTL simulation (cocotb + Icarus Verilog)
make clean           # clean sim_build/ and generated waveform/results files
make -B FST=         # dump real VCD instead of FST (see dumpfile note below)
make -B GATES=yes    # gate-level simulation (requires gate_level_netlist.v, copied from a hardened build)
```

- Simulator is Icarus Verilog (`SIM=icarus`) driven via cocotb; test entry point is `test/test.py`, the Verilog testbench wrapper is `test/tb.v`.
- `COCOTB_TEST_MODULES = test` in `test/Makefile` controls which Python test module(s) run.
- Test results land in `test/results.xml` (JUnit format) and waveforms in `test/tb.fst`/`tb.vcd`. View with `gtkwave tb.fst tb.gtkw` or `surfer tb.fst`.
- Python deps: `pip install -r test/requirements.txt` (cocotb 2.0.1, pytest).
- `PROJECT_SOURCES` in `test/Makefile` must be kept in sync with `source_files` in `info.yaml` whenever Verilog files are added/renamed.
- `test/Makefile` includes makefiles from OSS CAD suite, located at `../oss-cad-suite/lib/python3.11/site-packages/cocotb-2.1.0.dev0+41564633-py3.11-darwin-aarch64.egg/cocotb_tools/makefiles`. When using Icarus, `simulators/Makefile.icarus` is of interest
- `test/tb.v` names its dumpfile `tb.vcd`, but the Makefile's default `FST=-fst` still loads the FST plugin, so that file is actually FST-formatted despite the `.vcd` extension. Pass `FST=` (empty) to get a real VCD at that path, or rename the dumpfile to `tb.fst` to match the default.

There is no separate lint/build step to run locally beyond simulation — synthesis/hardening (LibreLane) and DRC/LVS run only in CI (`gds` workflow) against the IHP PDK.

## Architecture

- `src/alu.v` — `alu` module: a combinational 32-bit ALU (`alu_op[3:0]` selects OR/AND/XOR/ADD/SUB/SLL/SRL/SRA/SLTU/SLT), deliberately written with no explicit resource sharing between ops, used to observe how well synthesis optimizes it.
- `src/project.v` — `tt_um_example`: the actual TinyTapeout top module. It must keep the fixed TT pin interface (`ui_in`, `uo_out`, `uio_in`/`uio_out`/`uio_oe`, `ena`, `clk`, `rst_n`). It expands the 8 dedicated inputs (`ui_in`) into the ALU's two 32-bit operands by replicating/inverting nibbles (`alu_input_a`/`alu_input_b` construction), takes the op code from `uio_in[3:0]`, and exposes only the low byte of `alu_output` on `uo_out`. All unused signals (high bits of `alu_output`, `uio_in[7:4]`, `ena`, `clk`, `rst_n`) are tied into a single `_unused` wire to suppress lint warnings — extend that pattern rather than leaving new unused signals dangling.
- `test/tb.v` — thin Verilog wrapper instantiating `tt_um_example` and dumping waveforms; update the instantiated module name here if the top module is renamed.
- `test/test.py` — cocotb testbench. Mirrors the bit-mangling logic from `project.v` in Python (`calc_alu_input_a`/`calc_alu_input_b`/`calc_alu_expected_result`) to compute expected ALU results from `ui_in`/`uio_in` and compare against `uo_out`. Keep these Python helpers in sync with any change to the operand-construction logic in `project.v`.
- `info.yaml` — TinyTapeout project metadata consumed by the build/docs/GDS pipelines: `top_module`, `source_files` (must list every Verilog file under `src/`, one per line), pin descriptions for the datasheet, clock frequency, tile count. This is the source of truth the CI actions read — editing Verilog without updating `source_files` here breaks the hardening/docs workflows.
- `docs/info.md` — project datasheet description ("How it works" / "How to test" sections), currently unfilled placeholder text; rendered into the public docs by the `docs` workflow.
- `src/config.json` — LibreLane hardening configuration (density, clock period, margins, etc.). Marked "do not edit unless you know what you are doing"; only touch when fixing a specific hardening failure (e.g. placement/timing violations), not for general changes.

## CI workflows (`.github/workflows/`)

- `test.yaml` — runs the cocotb RTL simulation on every push (`cd test && make clean && make`), fails the build if `results.xml` contains a `failure`.
- `gds.yaml` — hardens the design via LibreLane (`TinyTapeout/tt-gds-action`, IHP sg13g2 PDK), runs Tiny Tapeout precheck, gate-level test, and deploys a viewer to GitHub Pages. This is the authoritative ASIC build; there's no equivalent full local flow.
- `docs.yaml` — builds the public datasheet from `docs/info.md` + `info.yaml`.
- `fpga.yaml` — builds an FPGA (ICE40UP5K) bitstream; disabled on push by default (`branches: none`), runs only via `workflow_dispatch`.

## Conventions specific to this repo

- **Every `@cocotb.test()` coroutine must end with an `await` (e.g. `await ClockCycles(dut.clk, 1)`) that runs unconditionally — never let the coroutine return in the same delta cycle as the last `dut.<signal>.value = ...` write, including on an exception path (e.g. a caught `AssertionError`).** Doing so corrupts this Icarus/cocotb build's (`cocotb-2.1.0.dev0+41564633`, bundled with the `oss-cad-suite` darwin-aarch64 toolchain) waveform-dump teardown: the test reports "passed" and the simulation appears to finish cleanly, but `vvp` segfaults moments later while closing the FST/VCD dump (right after the "dumpfile ... opened for output" line). If you add a new test or wrap an assertion in `try/except`, put the trailing `await` after the `try/except`, not only inside the success path.
- Top module name must start with `tt_um_` and stay unique (TinyTapeout convention) — currently `tt_um_example`; if renaming, update it in `info.yaml` (`top_module`), `src/project.v` (module name), and `test/tb.v` (instantiation).
- Bidirectional IO (`uio_out`, `uio_oe`) must always be fully assigned even when unused (currently `uio_out` is tied to 0 in `project.v`).
- ALU op encoding (`alu_op[3:0]`) is defined in both `src/alu.v` (Verilog case statement) and `test/test.py` (`ALU_OP_*` constants) — keep both in sync if op codes change.
