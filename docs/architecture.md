# RV32E CPU — Architecture Specification

A cacheless, parametrizable 32-bit RISC-V (RV32E) system-on-chip targeting a
[Tiny Tapeout](https://tinytapeout.com) tile on the IHP sg13g2 process. All
program and data memory lives off-chip behind a standard 4-wire SPI master;
on-chip peripherals are a UART (TX + RX) and an I2C master, both polled via
memory-mapped registers.

This document is the implementation contract: module boundaries, interfaces,
parameters, register maps, and pinout are specified here before Verilog is
written. Of the code currently in `src/`, only `alu.v` and `rf.v`
(`register_file`) are verified and reused; everything else in this document is
specified fresh, and the existing unverified modules (`spi_master.v`,
`basic_modules/*`) are free to be rewritten or dropped.

## 1. Overview and goals

```
                 ┌────────────────────────── tt_um_* (TT wrapper) ──────────────────────────┐
                 │  ┌──────────────────────────── soc_top ─────────────────────────────┐    │
                 │  │   ┌────────────── cpu_core ─────────────┐                        │    │
                 │  │   │ prefetch ─ decoder ─ ALU ─ regfile  │                        │    │
                 │  │   │     │                   LSU         │                        │    │
                 │  │   └─────┼────────────────────┼──────────┘                        │    │
                 │  │      instr bus            data bus                               │    │
                 │  │         └───────┬────────────┼──────────────┐                    │    │
                 │  │                 ▼            ▼              ▼                    │    │
                 │  │            bus arbiter ◄─ addr decode   peripheral bus           │    │
                 │  │                 │                        │       │      │        │    │
                 │  │                 ▼                        ▼       ▼      ▼        │    │
                 │  │           spi_mem_ctrl                 uart  i2c_master spi_cfg  │    │
                 │  └─────────────────┼────────────────────────┼───────┼───────────────┘    │
                 └────────────────────┼────────────────────────┼───────┼────────────────────┘
                            SCK/MOSI/MISO/CS_n[1:0]          TX/RX   SDA/SCL
                          (external SPI flash / SRAM)
```

Design goals, in priority order:

1. **Fit a TinyTapeout tile** (1x1 if possible, 1x2 acceptable). Area is the
   primary constraint; the register file (16 x 32 = 512 flops) and prefetch
   FIFO dominate.
2. **Parametrizable trade-offs.** One codebase, multiple hardening
   configurations: area-optimized (multi-cycle, minimal prefetch) vs.
   speed-optimized (pipelined, deep prefetch).
3. **Extension-ready.** Only RV32E is implemented now, but decode/execute are
   structured so M, C, and Zicsr can be added behind parameters later without
   restructuring.

Non-goals (v1): caches, MMU/PMP, interrupts, CSRs, privileged architecture,
multi-hart, debug module.

### Why cacheless + prefetch

Every memory access crosses the SPI bus. A random 32-bit read costs
8 (command) + 24 (address) + 32 (data) = **64 SPI clocks**; at the maximum SPI
rate of sysclk/2 that is ~128 system clocks. A cache would help but costs area
we don't have. Instead, a small **sequential prefetch FIFO** exploits SPI
sequential-read mode (keep CS asserted and keep clocking: each further word
costs only 32 SPI clocks, no command/address) to roughly halve fetch cost for
straight-line code. CPI of the core barely matters against this backdrop —
which is why the area-optimized multi-cycle core is the sensible default.

## 2. ISA

### 2.1 Base: RV32E

RV32E = RV32I with 16 registers (`x0`–`x15`). `x0` is hardwired to zero.
All 40 base instructions are implemented:

| Group | Instructions |
|---|---|
| Upper immediate | `LUI`, `AUIPC` |
| Jumps | `JAL`, `JALR` |
| Branches | `BEQ`, `BNE`, `BLT`, `BGE`, `BLTU`, `BGEU` |
| Loads | `LW` always; `LB`, `LH`, `LBU`, `LHU` iff `ENABLE_SUBWORD` |
| Stores | `SW` always; `SB`, `SH` iff `ENABLE_SUBWORD` |
| ALU immediate | `ADDI`, `SLTI`, `SLTIU`, `XORI`, `ORI`, `ANDI`, `SLLI`, `SRLI`, `SRAI` |
| ALU register | `ADD`, `SUB`, `SLL`, `SLT`, `SLTU`, `XOR`, `SRL`, `SRA`, `OR`, `AND` |
| System/misc | `FENCE` (NOP — no caches, single hart), `ECALL`, `EBREAK` (both halt) |

### 2.2 Exceptional behavior without traps

There is no trap machinery in v1, so anything exceptional funnels into a
single **HALTED state**: the core stops fetching, all bus activity ceases
(SPI CS_n deasserted), and the `halted` status output pin goes high. Only
reset leaves HALTED. This is cheap (one state + one pin) and makes failures
observable both on silicon and in cocotb tests.

Halt causes:

- Illegal / unimplemented instruction (including subword loads/stores when
  `ENABLE_SUBWORD = 0`, and any encoding referencing `x16`–`x31`).
- Misaligned load/store (natural alignment is required: halfwords 2-byte,
  words 4-byte aligned) and misaligned jump target (`JALR`/branch to a
  non-4-byte-aligned address).
- `ECALL`, `EBREAK` — defined as "halt" so software has a deliberate way to
  stop (useful as an end-of-test marker).

### 2.3 Future extensions (design hooks, not implemented)

- **M** (mul/div): plugs in as a second execute unit beside the ALU; the
  multi-cycle FSM gains an iterative-multiply state, the pipeline stalls in
  EX. Decoder reserves the `OP`/`funct7=0000001` space now (decodes to
  illegal).
- **C** (compressed): a pre-decoder between the prefetch FIFO and the decoder
  expanding 16-bit forms to 32-bit; the FIFO becomes 16-bit-granular. The
  prefetch unit's output interface (Section 4.4) is defined word-wide with a
  PC-low-bit note so this can be retrofitted.
- **Zicsr + interrupts**: a CSR file and trap unit attach to the writeback
  path; the HALTED state generalizes into trap entry. Peripherals already
  have internal `*_event` conditions (rx_valid, tx idle, i2c done) that would
  become interrupt lines.

## 3. Parameters

All parameters live on `soc_top` and are plumbed downward; the TT wrapper
instantiates `soc_top` with the chosen tape-out configuration. No `` `define``
based configuration (the existing `AREA_OPT` ifdef style in `spi_master.v` is
explicitly retired) — everything is Verilog parameters so cocotb benches can
instantiate multiple configurations.

| Parameter | Type / values | Default | Effect |
|---|---|---|---|
| `CORE_ARCH` | `"MULTICYCLE"` \| `"PIPELINED"` | `"MULTICYCLE"` | Control-path implementation (Section 4.2 / 4.3) |
| `OPT_GOAL` | `"AREA"` \| `"SPEED"` | `"AREA"` | Sub-block micro-choices: serial vs. barrel shifter, shared vs. dedicated adders, FIFO implementation style |
| `ENABLE_SUBWORD` | 0 \| 1 | 1 | Byte/halfword loads and stores; 0 removes the LSU lane mux/sign-extend logic and those opcodes halt as illegal |
| `PREFETCH_DEPTH` | 0, 2, 4, 8 (words) | 2 | Prefetch FIFO depth; 0 = fetch-on-demand, no FIFO |
| `MEM_CONFIG` | `"RAM_ONLY"` \| `"FLASH_RAM"` | `"FLASH_RAM"` | One CS (SPI SRAM holds code+data) vs. two CS (flash = code, SRAM = data) |
| `RESET_PC` | 32-bit | `32'h0000_0000` | Boot address |
| `SPI_CLK_DIV_RST` | 8-bit | `8'd4` | SPI SCK divisor after reset (runtime-writable, Section 6.4) |
| `UART_DIV_RST` | 16-bit | `16'd434` | UART baud divisor after reset (runtime-writable; 434 ≈ 115200 baud @ 50 MHz) |

Interactions worth noting:

- `OPT_GOAL` never changes architecturally visible behavior, only
  implementation (a given program produces identical results, in a different
  number of cycles). `CORE_ARCH`, `ENABLE_SUBWORD`, `MEM_CONFIG` *are*
  architecturally visible.
- `PREFETCH_DEPTH` is honored by both core variants; `"PIPELINED"` with
  `PREFETCH_DEPTH = 0` is legal but pointless (IF starves).
- Tape-out presets: **area build** = `MULTICYCLE, AREA, PREFETCH_DEPTH=2`;
  **speed build** = `PIPELINED, SPEED, PREFETCH_DEPTH=8`.

## 4. CPU core microarchitecture

### 4.1 Blocks shared by both `CORE_ARCH` variants

- **Decoder** (`decoder.v`, new): pure combinational; instruction word in,
  control bundle out (ALU op, operand selects, immediate, regfile addresses,
  mem op, branch type, `illegal` flag). One decoder serves both core
  variants — the FSM/pipeline only sequences it differently.
- **ALU** (`alu.v`, reuse — re-encoded): the existing verified 32-bit ALU,
  with its `alu_op` encoding changed to derive directly from the instruction:
  `alu_op = {mod, funct3}` where `mod = funct7[5]` (for `SUB`/`SRA`) and 0
  for immediates except `SRAI`:

  | `alu_op` | Operation | | `alu_op` | Operation |
  |---|---|---|---|---|
  | `0_000` | ADD | | `1_000` | SUB |
  | `0_001` | SLL | | `1_101` | SRA |
  | `0_010` | SLT | | | |
  | `0_011` | SLTU | | | |
  | `0_100` | XOR | | | |
  | `0_101` | SRL | | | |
  | `0_110` | OR | | | |
  | `0_111` | AND | | | |

  Under `OPT_GOAL = "AREA"` the shifter may be implemented iteratively
  (1 bit/cycle, the core waits); under `"SPEED"` it is a barrel shifter.
  Branch comparisons reuse the ALU (`SUB`/`SLT`/`SLTU` + zero flag) rather
  than a dedicated comparator.
- **Register file** (`rf.v`, reuse as-is): `register_file` with
  `USE_E_EXT = 1` (16 x 32). The posedge variant's write-through bypass
  (`rf.v:57`) gives the pipelined core same-cycle read-after-write for free.
- **LSU** (`lsu.v`, new): address = ALU result; drives the data bus port.
  When `ENABLE_SUBWORD = 1`, adds byte-lane extract + sign/zero extension on
  loads and passes a size code on stores. The external SPI SRAM is
  byte-addressable, so subword stores map directly to 1- or 2-byte SPI writes
  — **no read-modify-write is ever needed**; the entire cost of
  `ENABLE_SUBWORD` is the register-side lane logic. Checks alignment and
  raises `halt_misaligned`.
- **Prefetch unit** (Section 4.4): sole owner of the instruction bus port.

### 4.2 `CORE_ARCH = "MULTICYCLE"` (area-optimized)

One instruction in flight; classic FSM:

```
        ┌────────────────────────────────────────────────┐
        ▼                                                │
      FETCH ──► DECODE ──► EXECUTE ──► MEM ──► WRITEBACK ┘
        (any halt cause) ──► HALTED (exit: reset only)
```

- **FETCH**: pop next word from prefetch FIFO (stall while empty).
- **DECODE**: register the decoder's control bundle; read rs1/rs2.
- **EXECUTE**: one pass through the shared ALU. The same ALU computes,
  on successive cycles where needed, the arithmetic result, the branch/jump
  target (`PC + imm`), and the link value (`PC + 4`) — no second adder.
  Taken branches/jumps redirect the prefetch unit here.
- **MEM**: only for loads/stores; issue on the data bus and wait for the
  response (dozens of cycles — SPI). Skipped otherwise.
- **WRITEBACK**: regfile write, PC update, back to FETCH.

CPI is 3–5 plus memory wait states, which is irrelevant next to 64+ SPI-clock
fetches. This is the default and the recommended first tape-out.

### 4.3 `CORE_ARCH = "PIPELINED"` (speed-optimized)

Three stages, one instruction per stage:

```
  IF (pop prefetch FIFO) ─► EX (decode + regread + ALU + mem issue) ─► WB
```

- **Hazards**: WB→EX forwarding comes free from the regfile write-through
  bypass (posedge write mode). Load results are not forwardable — EX stalls
  on load-use until the data bus responds (it stalls on *every* load anyway,
  since SPI takes far longer than a cycle; the interlock is the same stall).
- **Control**: branches resolve in EX; on taken branch/jump, the one
  instruction in IF is flushed and the prefetch unit redirected
  (1-cycle bubble + refetch latency).
- Purpose: shorter critical path (decode+execute split from fetch alignment)
  → higher f_max, and it overlaps fetch with execute so the prefetch FIFO
  actually stays ahead. Only worth pairing with `PREFETCH_DEPTH >= 4`.

### 4.4 Prefetch unit (`prefetch.v`, new)

- FIFO of `PREFETCH_DEPTH` 32-bit words plus a fetch PC register.
- Issues sequential word reads on the instruction bus whenever the FIFO has
  space, using **burst mode** (Section 5.2): the SPI memory controller keeps
  CS asserted between consecutive-address reads from the same master, so
  words 2..N of a run cost 32 SPI clocks instead of 64.
- **Redirect** (taken branch, `JALR`, `JAL`): flush the FIFO, abort any
  in-flight burst (controller deasserts CS), restart from the new PC.
- **Halt**: on entry to HALTED the prefetcher stops issuing and the
  controller closes the burst.
- Output interface to the core: `instr_valid`, `instr[31:0]`,
  `instr_pc[31:0]`, `instr_ready` (pop). Defined word-wide; a future C
  extension inserts an aligner between FIFO and decoder without changing the
  bus side.
- `PREFETCH_DEPTH = 0`: the FIFO degenerates to a single staging register
  and every fetch is a full random access — smallest, slowest.

Sizing guidance: straight-line code consumes one word per instruction
(~3–5 core cycles multi-cycle, ~1–2 pipelined) while a burst supplies one
word per 32 SPI clocks (64+ core cycles at div=2·2). The FIFO therefore
*never* gets ahead by much during execution — its real value is (a) hiding
the command/address overhead via bursts and (b) letting fetch continue during
EXECUTE/WRITEBACK. Depth 2 captures most of the benefit; 8 only pays off for
the pipelined core with a fast SPI clock.

## 5. Memory system

### 5.1 Internal bus

One simple single-outstanding-transaction channel, two instances
(instruction port from the prefetcher, data port from the LSU):

| Signal | Dir (master→slave) | Width | Meaning |
|---|---|---|---|
| `req_valid` | → | 1 | Request present; held until `req_ready` |
| `req_ready` | ← | 1 | Slave accepts the request this cycle |
| `req_addr` | → | 32 | Byte address |
| `req_we` | → | 1 | 1 = store |
| `req_size` | → | 2 | `00`=byte, `01`=halfword, `10`=word |
| `req_wdata` | → | 32 | Store data, LSB-aligned |
| `rsp_valid` | ← | 1 | One-cycle pulse; read data valid / write complete |
| `rsp_rdata` | ← | 32 | Load data, LSB-aligned, extended by the LSU |

Rules: at most one outstanding request per master; `rsp_valid` always
eventually follows an accepted request; no bursts *at this layer* (bursting
is an optimization inside the SPI controller, invisible to the protocol).
This deliberately minimal bus is trivial to arbitrate and to model in cocotb.

**Address decode** (top 2 bits, `req_addr[31:30]`):

| Range | Region | `FLASH_RAM` | `RAM_ONLY` |
|---|---|---|---|
| `0x0000_0000`–`0x3FFF_FFFF` | Code | SPI flash, CS0, read-only (writes → halt) | Alias of the SPI SRAM (same chip, CS0) |
| `0x4000_0000`–`0x7FFF_FFFF` | Data | SPI SRAM, CS1, read/write | SPI SRAM, CS0, read/write |
| `0x8000_0000`–`0xBFFF_FFFF` | Peripherals | Memory-mapped registers (Section 6); data port only — a fetch from this region halts the core | same |
| `0xC000_0000`– | Reserved | Access → halt | same |

In `RAM_ONLY`, `addr[31:30]` is simply dropped for SPI addressing, so code
and data regions are two views of the same SRAM (self-modifying code and
data-in-code both work; there is no cache to go stale).

**Arbiter** (`bus_arbiter.v`, new): fixed priority, **data port wins**. A
data request aborts any prefetch burst in progress (the controller finishes
the current 8-bit SPI beat, deasserts CS, then services the data request).
Rationale: loads/stores are on the program's critical path; prefetch is
speculative and can re-burst afterwards. Since both external chips share
SCK/MOSI/MISO, fetch and data can never overlap anyway — even in
`FLASH_RAM` — so the arbiter is genuinely just a 2:1 mux with abort.

### 5.2 SPI memory controller (`spi_mem_ctrl.v`, new — replaces `spi_master.v`)

The existing `spi_master.v` is an unverified simulation model and is
**not** the basis for this block; it will be rewritten against this spec.

- **PHY**: SPI mode 0 (CPOL=0, CPHA=0), MSB first. MOSI transitions on the
  falling SCK edge, MISO sampled on the rising edge. SCK = sysclk /
  (2 · `spi_clk_div`), where `spi_clk_div` is the runtime register
  (Section 6.4) reset to `SPI_CLK_DIV_RST`; divisor value 1 gives
  SCK = sysclk/2, the maximum. Internally an 8-bit shift register and a
  small clock-divider counter (the divider is specified here as part of the
  controller, not inherited from `basic_modules/clk_div.v`).
- **Command layer** — targets 25-series NOR flash and 23-series serial SRAM,
  both of which use the same basic opcodes with 24-bit addresses:
  - Read: `03h`, A[23:0], then N data bytes. Used for both chips (no
    fast-read/dummy cycles; keeps SCK ≤ sysclk/2 which is well inside both
    chips' plain-read limits).
  - Write (SRAM only): `02h`, A[23:0], then N data bytes. The 23-series
    SRAM must be configured for sequential mode, which is its power-on
    default on the common parts; a mode-set command at boot is not required.
    Flash writes are **not** supported in v1 (no page-program/erase state
    machine) — the code region is read-only, per the decode table.
- **Burst support**: after completing a read for master M at address A, if
  the next accepted request is also a read from M at A+4 (A+size), the
  controller skips command+address and just clocks out more data. Any other
  request, a write, or an explicit `burst_abort` (prefetch redirect, arbiter
  preemption, halt) first raises CS_n for the required deselect time
  (≥ 1 SCK period, generated by the same divider).
- **Chip selects**: `MEM_CONFIG = "FLASH_RAM"` → CS0_n = flash (code
  region), CS1_n = SRAM (data region). `"RAM_ONLY"` → CS0_n = SRAM
  (both regions), CS1_n tied inactive (pin becomes spare).
- Reset/idle state: CS_n all high, SCK low, MOSI low.

**Latency budget** (sysclk cycles, `spi_clk_div` = d, so one SPI bit = 2d):

| Access | SPI clocks | sysclk @ d=1 | sysclk @ d=4 (reset) |
|---|---|---|---|
| Random 32-bit read | 8+24+32 = 64 | 128 | 512 |
| Burst continuation word | 32 | 64 | 256 |
| Byte write | 8+24+8 = 40 | 80 | 320 |

At 50 MHz sysclk, d=1, straight-line code: ~780 k instructions/s
(fetch-bound). This table is the number to beat with `PREFETCH_DEPTH` and is
why nothing else in the core needs to be fast.

### 5.3 Boot

- `FLASH_RAM`: true XIP — reset vector `RESET_PC` (default 0) reads straight
  from flash. Program the flash externally (flashrom/programmer or the TT
  demo board RP2040) before releasing `rst_n`.
- `RAM_ONLY`: the SRAM must be preloaded while the core is in reset. During
  reset all SPI outputs idle (CS_n high, SCK/MOSI low), but the CPU still
  *drives* those output pins — an external loader sharing the bus (e.g. the
  RP2040 on the TT demo board) needs its own CS line to the SRAM and must
  tolerate the CPU driving SCK/MOSI low, or the board must isolate the CPU
  with series resistors. **This is an open integration issue** (Section 10);
  the clean workaround is to use the `FLASH_RAM` configuration for tape-out
  and keep `RAM_ONLY` primarily as the fast-simulation configuration.

## 6. Peripherals

All peripheral registers are 32-bit, word-aligned, on the data port only.
Reads of undefined offsets return 0; writes to undefined offsets are ignored
(they do **not** halt — keeps the decode cheap). Only word accesses
(`req_size = 10`) are meaningful; the low byte carries the payload unless
noted. No interrupts anywhere — software polls status bits.

Base `0x8000_0000`, decode on `req_addr[7:0]`:

| Offset | Name | Access | Contents |
|---|---|---|---|
| `0x00` | `UART_DATA` | R/W | W: byte to transmit. R: received byte (pops the RX buffer) |
| `0x04` | `UART_STATUS` | R | bit0 `tx_busy`, bit1 `rx_valid`, bit2 `rx_overrun` (cleared on read), bit3 `rx_frame_err` (cleared on read) |
| `0x08` | `UART_DIV` | R/W | bits[15:0] baud divisor, reset `UART_DIV_RST` |
| `0x10` | `I2C_CMD` | W | bits[7:0] data byte, bits[10:8] command (below); writing starts the operation |
| `0x14` | `I2C_STATUS` | R | bit0 `busy`, bit1 `nack` (last addr/data byte not acked; sticky until next command) |
| `0x18` | `I2C_DATA` | R | bits[7:0] byte received by the last READ command |
| `0x1C` | `I2C_DIV` | R/W | bits[15:0] SCL half-period divisor |
| `0x20` | `SPI_DIV` | R/W | bits[7:0] SPI clock divisor, reset `SPI_CLK_DIV_RST` (Section 5.2) |
| `0x24` | `SYS_ID` | R | Constant: 8-bit version, 8-bit config fingerprint (`CORE_ARCH`, `ENABLE_SUBWORD`, `MEM_CONFIG`, `PREFETCH_DEPTH` encoded) — lets software and tests identify the build |

### 6.1 UART (`uart.v`, new)

- 8N1 fixed format. Baud = sysclk / (`UART_DIV` + 1); RX oversamples at the
  same divisor using a mid-bit sample counter (16x oversampling under
  `OPT_GOAL="SPEED"`, simple mid-bit counting under `"AREA"`).
- Single-byte TX holding register + shift register (write while `tx_busy`
  is ignored — poll first). Single-byte RX buffer: a byte completing while
  `rx_valid` is still set sets `rx_overrun` and drops the new byte.
  No FIFOs in v1 (area); the register map doesn't change if small FIFOs are
  added later — only the status semantics of `rx_overrun` get softer.

### 6.2 I2C master (`i2c_master.v`, new)

- Open-drain on both lines via the TT bidirectional pins: drive low =
  (`uio_out` = 0, `uio_oe` = 1), release = (`uio_oe` = 0, external pull-up).
  SCL is also open-drain, and the master samples SCL after releasing it,
  which provides **clock stretching** support for free.
- SCL frequency = sysclk / (4 · (`I2C_DIV` + 1)) (four divider ticks per SCL
  period: low, rise, high, fall).
- Command set (`I2C_CMD[10:8]`), one primitive per register write, `busy`
  polled between them:

  | Code | Command | Action |
  |---|---|---|
  | `000` | START | (Repeated) start condition |
  | `001` | WRITE | Send `I2C_CMD[7:0]`, capture ACK into `nack` |
  | `010` | READ_ACK | Clock in one byte into `I2C_DATA`, send ACK |
  | `011` | READ_NACK | Clock in one byte into `I2C_DATA`, send NACK (last read) |
  | `100` | STOP | Stop condition |

  A full transaction is composed by software (START, WRITE addr, …, STOP),
  which keeps the hardware a small FSM and puts protocol variety in software
  where it's free.

### 6.3 GPIO / spare pins

Leftover `ui_in` pins are readable and leftover `uo_out` pins writable
through a `SYS_GPIO` register if pin budget allows after final pinout —
deliberately unspecified in v1; the offsets `0x28+` are reserved for it.

### 6.4 SPI configuration

`SPI_DIV` (offset `0x20`) is the runtime knob for memory bus speed: boot
conservatively at `SPI_CLK_DIV_RST`, then software cranks it down (typically
to 1) once running. Writes take effect at the next CS assertion, never
mid-transfer.

## 7. TinyTapeout top level and pinout

The TT wrapper (top module renamed from `tt_um_example` to
**`tt_um_timva1_rv32e`** — update `info.yaml` `top_module`, `src/project.v`,
and `test/tb.v` together, per repo convention) contains only pin mapping and
the `soc_top` instance with the tape-out parameter set.

| Pin | Direction | Function | Notes |
|---|---|---|---|
| `uo_out[0]` | out | SPI SCK | |
| `uo_out[1]` | out | SPI MOSI | |
| `uo_out[2]` | out | SPI CS0_n | Flash (`FLASH_RAM`) or SRAM (`RAM_ONLY`) |
| `uo_out[3]` | out | SPI CS1_n | SRAM (`FLASH_RAM`); constant 1 in `RAM_ONLY` |
| `uo_out[4]` | out | UART TX | |
| `uo_out[5]` | out | `halted` | High in HALTED state (Section 2.2) |
| `uo_out[6]` | out | spare / GPIO out | 0 until Section 6.3 lands |
| `uo_out[7]` | out | spare / GPIO out | 0 until Section 6.3 lands |
| `ui_in[0]` | in | SPI MISO | |
| `ui_in[1]` | in | UART RX | Idle high |
| `ui_in[2..7]` | in | spare / GPIO in | Unused → `_unused` wire |
| `uio[0]` | bidir | I2C SDA | Open-drain (Section 6.2), needs external pull-up |
| `uio[1]` | bidir | I2C SCL | Open-drain, needs external pull-up |
| `uio[2..7]` | bidir | unused | `uio_out = 0`, `uio_oe = 0` |

Budget check: both `MEM_CONFIG` variants fit with pins to spare
(RAM_ONLY frees `uo_out[3]`). TT rules honored: `uio_out`/`uio_oe` fully
assigned, `ena` ignored into `_unused`, async active-low `rst_n` used as the
single reset (synchronized internally with a 2-flop synchronizer before use).

**Clocking**: one clock domain (`clk`); SPI SCK and I2C SCL are generated as
divided *enables*, not derived clocks — no CDC anywhere. Target
`clock_hz`: 25–50 MHz pending hardening results (the multi-cycle core with
an iterative shifter should close 50 MHz on sg13g2 comfortably; final number
goes into `info.yaml`).

**Area expectations** (for the 1x1-vs-1x2 decision, to be confirmed by a
hardening run): register file 512 DFF + prefetch FIFO (`32·DEPTH` DFF) +
~150 misc control/datapath flops. At `PREFETCH_DEPTH = 2` that's roughly
750 flops plus combinational logic — plausibly 1x1 for the area build;
the speed build (barrel shifter, depth-8 FIFO) likely needs 1x2.

## 8. Module hierarchy and file plan

```
src/
  project.v            tt_um_timva1_rv32e — TT wrapper, pin map        (rewrite)
  soc_top.v            parameters, bus wiring, address decode          (new)
  core/
    cpu_core.v         generate: multicycle or pipelined control       (new)
    decoder.v          combinational instruction decode                (new)
    prefetch.v         fetch PC + FIFO + burst sequencing              (new)
    lsu.v              subword lanes, alignment check                  (new)
    alu.v              verified — re-encode alu_op per Section 4.1     (adapt, keep verified tests)
    rf.v               verified — use USE_E_EXT=1, posedge             (reuse as-is)
  bus/
    bus_arbiter.v      2:1 fixed priority, burst abort                 (new)
  mem/
    spi_mem_ctrl.v     command layer + burst logic                     (new, replaces spi_master.v)
    spi_phy.v          shift register + SCK divider                    (new)
  periph/
    uart.v, uart_tx.v, uart_rx.v                                       (new)
    i2c_master.v                                                       (new)
    periph_regs.v      register decode, SPI_DIV, SYS_ID                (new)
```

`src/spi_master.v` and `src/basic_modules/` are retired (unverified; their
roles are absorbed by `spi_phy.v` and per-block dividers). When files are
added/removed, update `info.yaml` `source_files` **and** `PROJECT_SOURCES`
in `test/Makefile` together — the CI hardening flow reads `info.yaml`.

## 9. Verification strategy

Bottom-up, using the repo's existing `test_<name>` pattern
(`test/test_modules/<name>_tb.v` + `test/test_<name>.py`,
run via `make -B test_<name>`), observing the repo's cocotb rules
(unconditional trailing `await` in every test, clocks restarted per-test).

1. **Unit level** (per new module): decoder (golden-model compare against a
   Python decoder over all opcodes + fuzzed illegal encodings), spi_phy /
   spi_mem_ctrl (against a Python SPI SRAM/flash slave model driving MISO —
   checks command bytes, burst continuation, CS deselect timing), uart
   (loopback TX→RX at several divisors, framing/overrun errors), i2c_master
   (Python I2C slave model, incl. clock stretching and NACK), prefetch +
   arbiter (burst abort on redirect, data-beats-fetch priority).
   The ALU and regfile keep their existing verified benches; the ALU bench
   is updated for the new `alu_op` encoding.
2. **Core level**: `soc_top` with the behavioral SPI memory model preloaded
   from a `.hex` file. Directed assembly tests per instruction class
   (assembled with `riscv32-unknown-elf-gcc -march=rv32e -mabi=ilp32e`),
   convention: test writes a result signature to a fixed SRAM address and
   executes `EBREAK`; cocotb waits for the `halted` pin and checks the
   signature through the memory model. Halt-cause tests (illegal op,
   misalignment, subword-when-disabled).
3. **Configuration matrix**: the core-level suite runs across
   {`MULTICYCLE`,`PIPELINED`} x {`ENABLE_SUBWORD` 0,1} x
   {`RAM_ONLY`,`FLASH_RAM`} x `PREFETCH_DEPTH` {0,2,8} — results must be
   identical, only cycle counts differ. This is the payoff of keeping
   `OPT_GOAL` non-architectural.
4. **Compliance**: riscv-arch-test RV32E suite, adapted to the
   signature+EBREAK convention (no CSRs → use the halt-based test end).
5. **Gate level**: the standard TT `GATES=yes` flow on the tape-out
   configuration after hardening.

## 10. Open questions / future work

- **`RAM_ONLY` boot path**: bus sharing with an external loader during reset
  (Section 5.3) — needs a demo-board-level answer (series resistors, or a
  dedicated loader CS) before `RAM_ONLY` is tape-out-viable.
- **Tile count**: 1x1 vs 1x2 pending first hardening run of the area build.
- **Interrupts + Zicsr**: next architectural step; peripheral event bits are
  already positioned to become IRQ sources (Section 2.3).
- **M and C extensions**: hooks documented in Section 2.3.
- **QSPI upgrade**: 4x the fetch bandwidth for 2 extra pins (`uio`) and a
  wider PHY; the command layer / burst logic is structured so only
  `spi_phy.v` and the pin map change.
- **UART/RX FIFOs, GPIO register**: reserved offsets exist (Sections 6.1,
  6.3); add if area allows after the core hardens.
