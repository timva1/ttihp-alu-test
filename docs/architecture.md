# RV32E CPU — Architecture Specification

A parametrizable 32-bit RISC-V (RV32E) system-on-chip targeting a small
[Tiny Tapeout](https://tinytapeout.com) footprint (budget 5–10 tiles) on the
IHP sg13g2 process. All program and data memory lives off-chip behind a
standard 4-wire SPI master; that bus is the dominant cost, so it is hidden by
a sequential prefetch FIFO, a posting load/store queue, and — when area
allows — optional small instruction/data caches (all parameterized, and
off/minimal in the baseline build). On-chip peripherals are a UART (TX + RX)
and an I2C master, both polled via memory-mapped registers.

This document is the implementation contract: module boundaries, interfaces,
parameters, register maps, and pinout are specified here before Verilog is
written. Of the code currently in `src/`, only `alu.v` and `rf.v`
(`register_file`) are verified and reused; everything else in this document is
specified fresh, and the existing unverified modules (`spi_master.v`,
`basic_modules/*`) are free to be rewritten or dropped.

## 1. Overview and goals

```
                 ┌────────────────────────── tt_um_* (TT wrapper) ───────────────────────────┐
                 │  ┌───────────────────────────── soc_top ─────────────────────────────┐    │
                 │  │   ┌────────────── cpu_core ─────────────┐                         │    │
                 │  │   │ prefetch ─ decoder ─ ALU ─ regfile  │                         │    │
                 │  │   │     │         LSU ─ LSQ             │                         │    │
                 │  │   └─────┼────────────────────┼──────────┘                         │    │
                 │  │      instr bus            data bus                                │    │
                 │  │         │                    ▼                                    │    │
                 │  │         │               addr decode ───► peripheral bus           │    │
                 │  │         │           (mem regions only)      │     │         │     │    │
                 │  │         ▼                    ▼              ▼     ▼         ▼     │    │
                 │  │     ┌───────┐            ┌───────┐        uart   i2c     spi_cfg  │    │
                 │  │     │I$(opt)│            │D$(opt)│          │  master             │    │
                 │  │     └───┬───┘            └───┬───┘          │     │               │    │
                 │  │         └───────┬────────────┘              │     │               │    │
                 │  │                 ▼                           │     │               │    │
                 │  │            bus arbiter                      │     │               │    │
                 │  │                 │                           │     │               │    │
                 │  │                 ▼                           │     │               │    │
                 │  │           spi_mem_ctrl                      │     │               │    │
                 │  └─────────────────┼───────────────────────────┼─────┼───────────────┘    │
                 └────────────────────┼───────────────────────────┼─────┼────────────────────┘
                           SCK/MOSI/MISO/CS_n[1:0]           TX/RX SDA/SCL
                         (external SPI flash / SRAM)
```

The **LSQ** (load/store queue, Section 4.5) and the **I$/D$** caches
(`(opt)`, Section 5.4) are parameter-gated: the caches default off
(`*CACHE_SIZE = 0`, i.e. a transparent bypass) and the queue defaults shallow,
so the baseline build is the prefetch FIFO plus a depth-2 load/store queue,
with the caches bypassed.

Design goals, in priority order:

1. **Fit a 5–10 tile TinyTapeout footprint.** One tile is only
   ~100 µm × 160 µm (~1000 gates), so the whole SoC spans several tiles; the
   exact count is pending the first hardening run, and optional blocks (caches,
   prefetch/LSQ depth) are dialed in or out to stay inside that budget. Area is
   still a first-order constraint — the register file (16 × 32 = 512 flops),
   any enabled caches, and the prefetch/LSQ FIFOs dominate.
2. **Parametrizable trade-offs.** One codebase, multiple hardening
   configurations: area-optimized (multi-cycle, minimal prefetch) vs.
   speed-optimized (pipelined, deep prefetch).
3. **Extension-ready.** Only RV32E is implemented now, but decode/execute are
   structured so Zicsr + interrupts can be added behind parameters later
   without restructuring. (Mul/div (M) and compressed (C) are explicitly out
   of scope — no hooks — to save area; see Section 2.3.)

Non-goals (v1): MMU/PMP, interrupts, CSRs, privileged architecture,
multi-hart, debug module, the M and C ISA extensions. (Caches and the
load/store queue *are* in v1, but as parameters — caches default off, the
queue defaults shallow — so the baseline build is effectively cacheless.)

### Why prefetch + load/store queue, and caches only on demand

Every memory access crosses the SPI bus. A random 32-bit read costs
8 (command) + 24 (address) + 32 (data) = **64 SPI clocks**; at the maximum SPI
rate of sysclk/2 that is ~128 system clocks. Two cheap structures hide most of
this without paying for storage: a small **sequential prefetch FIFO**
(Section 4.4) exploits SPI sequential-read mode — keep CS asserted and keep
clocking, so each further word costs only 32 SPI clocks with no
command/address — roughly halving fetch cost for straight-line code; and a
**posting load/store queue** (Section 4.5) lets stores retire into a FIFO so
the core runs on while they drain, and forwards store→load hits without a bus
trip. CPI of the core barely matters against this backdrop — which is why the
area-optimized multi-cycle core is the sensible default.

Optional **instruction/data caches** (Section 5.4) go further, turning repeat
accesses (loop bodies, the stack) into single-cycle hits, but they are off by
default: on sg13g2 the TT flow has no small SRAM macro, so a cache is a
flip-flop array — expensive — and a miss still pays the full 64-SPI-clock
latency. They earn their area only for working sets that fit, which is exactly
why the tile budget grew to 5–10 (Section 1) and why `*CACHE_SIZE` defaults
to 0.

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
| System/misc | `FENCE` (NOP — single hart, in-order; no `FENCE.I`/Zifencei in v1), `ECALL`, `EBREAK` (both halt) |

### 2.2 Exceptional behavior without traps

There is no trap machinery in v1, so anything exceptional funnels into a
single **HALTED state**, entered in two steps: the core stops fetching
immediately, then waits for the load/store queue to **drain completely**
(Section 4.5) — queued stores are never discarded. Only after the last queued
store has completed does all bus activity cease (SPI CS_n deasserted) and the
`halted` status output pin go high. Because `halted` asserts only after the
drain, memory is guaranteed consistent with program order the moment the pin
rises — which is what makes it usable as an end-of-test marker (Section 9).
Only reset leaves HALTED. This is cheap (one state + one pin + a drain-wait)
and makes failures observable both on silicon and in cocotb tests.

Halt causes:

- Illegal / unimplemented instruction (including subword loads/stores when
  `ENABLE_SUBWORD = 0`, and any encoding referencing `x16`–`x31`).
- Misaligned load/store (natural alignment is required: halfwords 2-byte,
  words 4-byte aligned) and misaligned control transfer: a taken branch,
  `JAL`, or `JALR` whose target has bit 1 set (no C extension, so targets
  must be 4-byte aligned). Per the RISC-V spec, `JALR` first **clears bit 0**
  of its computed target (`(rs1 + imm) & ~1`) — an odd `rs1 + imm` is *not*
  a halt cause (riscv-arch-test exercises exactly this).
- Bus-region violations (Section 5.1): attempting to execute from the
  peripheral or reserved regions (raised at execute, never by speculative
  prefetch — Section 4.4), a store to the read-only code region
  (`FLASH_RAM`), or any access to the reserved region. Data-side region
  checks happen in the LSU **at issue time**, before the access enters the
  load/store queue (Section 4.1) — the halt is precise: the violating access
  is never posted and no younger instruction retires.
- `ECALL`, `EBREAK` — defined as "halt" so software has a deliberate way to
  stop (useful as an end-of-test marker).

### 2.3 Future extensions (design hooks, not implemented)

The **M** (mul/div) and **C** (compressed) extensions are deliberately *not*
planned: an iterative multiplier/divider and a 16-bit pre-decoder each cost
more hardware than the area budget justifies, so no space or interface is
reserved for them, and the M (`funct7=0000001`) and C (16-bit) encodings simply
decode to illegal (halt). The one hook that remains:

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
| `UART_DIV_RST` | 16-bit | `16'd433` | UART baud divisor after reset (runtime-writable; baud = sysclk/(div+1), so 433 → 50 MHz/434 ≈ 115200 baud) |
| `LSQ_DEPTH` | 0, 2, 4, 8 (entries) | 2 | Load/store queue depth (Section 4.5); 0 = no queue, the LSU blocks on every access |
| `LSQ_FORWARD` | 0 \| 1 | 1 | RAW store→load forwarding inside the queue; 0 drops the address-match/forward logic and a load instead waits for all older stores to drain first |
| `ICACHE_SIZE` | 0, 32, 64, 128, 256 (bytes) | 0 | Instruction-cache size (Section 5.4); 0 = no I$ (prefetch-only) |
| `DCACHE_SIZE` | 0, 32, 64, 128, 256 (bytes) | 0 | Data-cache size; 0 = no D$ |
| `DCACHE_WRITE_POLICY` | `"WRITETHROUGH"` \| `"WRITEALLOCATE"` | `"WRITETHROUGH"` | D$ store-miss policy (Section 5.4): `"WRITETHROUGH"` = store misses bypass the cache (no allocate); `"WRITEALLOCATE"` = a store miss fills the line first, then updates it. Every store writes through to memory in both variants (never write-back) |
| `CACHE_WAYS` | 1 \| 2 | 1 | Associativity of *both* caches (1 = direct-mapped, 2 = 2-way + 1 LRU bit) |
| `CACHE_BLOCK` | 4 \| 8 (bytes) | 4 | Line size of *both* caches (1 or 2 words) |

Interactions worth noting:

- `OPT_GOAL`, `PREFETCH_DEPTH`, `LSQ_DEPTH`, `LSQ_FORWARD`, `DCACHE_SIZE`,
  `DCACHE_WRITE_POLICY` and
  the shared cache-geometry parameters never change architecturally visible
  results, only cycle counts (a given program produces identical results).
  `CORE_ARCH`, `ENABLE_SUBWORD`, `MEM_CONFIG` *are* architecturally visible.
- **One exception:** `ICACHE_SIZE > 0` is architecturally visible for code
  written at runtime — self-modifying code in `RAM_ONLY`, or executing from
  the writable data region after storing to it in either `MEM_CONFIG` —
  because the split I$ has no `FENCE.I` to flush it (Section 5.4): a store is
  not guaranteed to be seen by a later fetch of the same address.
- `PREFETCH_DEPTH` is honored by both core variants; `"PIPELINED"` with
  `PREFETCH_DEPTH = 0` is legal but pointless (IF starves). `LSQ_DEPTH` is
  likewise honored by both.
- Tape-out presets: **area build** = `MULTICYCLE, AREA, PREFETCH_DEPTH=2,
  LSQ_DEPTH=2`, caches off; **speed build** = `PIPELINED, SPEED,
  PREFETCH_DEPTH=8, LSQ_DEPTH=8, ICACHE_SIZE=DCACHE_SIZE=256, CACHE_WAYS=2,
  CACHE_BLOCK=8`.

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
- **LSU** (`lsu.v`, new): address = ALU result; hands each access to the
  load/store queue (Section 4.5), which owns the data bus port. When
  `ENABLE_SUBWORD = 1`, adds byte-lane extract + sign/zero extension on
  loads and passes a size code on stores. The external SPI SRAM is
  byte-addressable, so subword stores map directly to 1- or 2-byte SPI writes
  — **no read-modify-write is ever needed**; the entire cost of
  `ENABLE_SUBWORD` is the register-side lane logic. Checks alignment
  (`halt_misaligned`) and region legality (a combinational compare on
  `addr[31:30]`, Section 5.1) **at issue time, before the access enqueues**:
  a store to the read-only code region (`FLASH_RAM`) or any reserved-region
  access halts precisely — the violating access never enters the queue
  (Section 2.2).
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
- **MEM**: only for loads/stores; hand the access to the LSQ (Section 4.5).
  A store posts and the FSM continues immediately (blocking only if the queue
  is full); a load blocks here until the LSQ returns data — forwarded, a D$
  hit, or after the SPI read (dozens of cycles). Skipped otherwise.
- **WRITEBACK**: regfile write, PC update, back to FETCH.

CPI is 3–5 plus memory wait states, which is irrelevant next to 64+ SPI-clock
fetches. This is the default and the recommended first tape-out.

### 4.3 `CORE_ARCH = "PIPELINED"` (speed-optimized)

Three stages, one instruction per stage:

```
  IF (pop prefetch FIFO) ─► EX (decode + regread + ALU + mem issue) ─► WB
```

- **Hazards**: WB→EX forwarding comes free from the regfile write-through
  bypass (posedge write mode). Stores post to the LSQ and don't stall EX
  (unless the queue is full). Loads stall EX until the LSQ returns data — the
  full SPI latency on a miss (so the load-use interlock is usually hidden by
  that wait), but as little as a cycle or two on a store-forward or D$ hit;
  either way the same WB→EX bypass then forwards the loaded value.
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
  in-flight burst (controller deasserts CS), restart from the new PC. The
  bus protocol has no cancel: if a fetch request is outstanding when the
  redirect arrives, the prefetcher waits for its `rsp_valid` and **discards
  the data** — the stale word never enters the FIFO and never affects
  `instr_pc`.
- **Halt**: on entry to HALTED the prefetcher stops issuing and the
  controller closes the burst.
- Output interface to the core: `instr_valid`, `instr[31:0]`,
  `instr_pc[31:0]`, `instr_ready` (pop) — word-wide (RV32E has no 16-bit
  instructions).
- **I-cache**: when `ICACHE_SIZE > 0` (Section 5.4) the prefetch unit's bus
  requests are served by the I$ first; a redirect that lands on a still-cached
  line refills from the cache instead of re-bursting from SPI.
- **Region boundaries — fetch never halts speculatively**: sequential
  prefetch stops (holds, issues nothing) when the fetch PC reaches the
  peripheral-region boundary (`0x8000_0000`); it never speculatively touches
  the peripheral or reserved regions. The "execute from peripheral/reserved
  region" halt (Sections 2.2, 5.1) is raised only when the core actually
  attempts to consume an instruction at such an address (e.g. a redirect
  lands there) — a word prefetched past a taken branch can never halt the
  core. Fetching from the *data* region is legal (executing from SRAM works,
  in both `MEM_CONFIG`s, subject to the I$ staleness caveat of Section 5.4).
- `PREFETCH_DEPTH = 0`: the FIFO degenerates to a single staging register
  and every fetch is a full random access — smallest, slowest.

Sizing guidance: straight-line code consumes one word per instruction
(~3–5 core cycles multi-cycle, ~1–2 pipelined) while a burst supplies one
word per 32 SPI clocks (64+ core cycles at div=2·2). The FIFO therefore
*never* gets ahead by much during execution — its real value is (a) hiding
the command/address overhead via bursts and (b) letting fetch continue during
EXECUTE/WRITEBACK. Depth 2 captures most of the benefit; 8 only pays off for
the pipelined core with a fast SPI clock.

### 4.5 Load/store queue (`lsq.v`, new)

The data-side counterpart of the prefetch unit: it decouples the core from SPI
data latency and is the **sole owner of the data bus port**.

- FIFO of `LSQ_DEPTH` entries, each `{addr, we, size, wdata}` (plus a
  destination tag for loads). Entries drain to the data bus **strictly in the
  order they were enqueued** — loads and stores never reorder past one another,
  and stores are **never coalesced or write-combined** (two stores to the same
  address both reach memory, in order).
- **Stores are posted**: the LSU enqueues a store and the core continues
  immediately (the store "retires" into the queue); it reaches SPI when it gets
  to the head. The core only stalls on a store when the queue is full.
- **Loads block for their data** but can skip the bus two ways. If
  `LSQ_FORWARD = 1`, a load first checks the queue for the youngest older store
  that fully covers it — **exact address equality** (`store.addr == load.addr`)
  **and** store size ≥ load size; a narrower load takes the low bytes of the
  store data (both are LSB-aligned, matching the bus). Partial overlaps —
  different addresses even within the same word, or a narrower store — never
  forward; such a load waits for the drain point like any forward miss. On a
  forward hit the value is forwarded with no bus access. On a forward miss the load first
  waits until **every older store ahead of it has drained**, and only then —
  at the drain point — consults the D$ (Section 5.4) and, failing that,
  issues to the bus. The D$ is **never consulted while an older store is
  still queued**: a queued store has not yet updated its cache line, so an
  early lookup could return a stale line in exactly the cases forwarding
  cannot catch (e.g. a queued `SB` followed by a `LW` of the same word, or
  any partial overlap). All D$ interaction — lookup, hit-update, and
  `"WRITEALLOCATE"` fills — happens at the LSQ drain point, in program order.
  With `LSQ_FORWARD = 0` the forward check is omitted entirely and every load
  likewise waits for all older stores to drain first.
- **Peripheral accesses are never forwarded**: store→load forwarding applies
  only to memory-region addresses. A load from the peripheral region always
  drains behind all older stores and then performs a real bus access, so
  MMIO read side effects (Section 6) always occur and never observe queued
  store data. (Peripheral accesses also bypass the caches — Section 5.1.)
- **Ordering vs. fetch**: a queued **memory-region** data access still
  preempts an in-flight prefetch burst (data-beats-fetch, Section 5.1).
  Peripheral accesses never touch SPI: they complete on the peripheral bus
  while a fetch burst continues undisturbed, so a UART/I2C polling loop
  costs the fetch stream nothing.
- **Halt**: entry to HALTED waits for the queue to drain completely
  (Section 2.2); queued stores are never discarded. `halted` asserts only
  once the queue is empty and the last SPI write has finished.
- `LSQ_DEPTH = 0`: the queue degenerates to a single staging register — every
  load and store blocks the core until it completes, i.e. the original
  in-order, one-outstanding behavior.

Sizing guidance: a burst of stores (register spills, `memcpy`) is the main
beneficiary — depth 2–4 lets the core keep running through a short run of
stores while they trickle out over SPI, and consecutive-address runs drain
as a single SPI **write burst** (Section 5.2), 32 SPI clocks per word after
the first instead of 64. Forwarding mainly helps the common
spill-then-reload and stack-frame patterns. Like the prefetch FIFO the queue
cannot outrun SPI for long, so depth 8 only pays off for the pipelined core.

## 5. Memory system

### 5.1 Internal bus

One simple single-outstanding-transaction channel, two instances
(instruction port from the prefetcher, data port from the LSQ):

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
| `0x8000_0000`–`0xBFFF_FFFF` | Peripherals | Memory-mapped registers (Section 6); data port only, **uncacheable and never store→load forwarded** (Section 4.5) — attempting to *execute* from this region halts the core (raised at execute, Section 4.4) | same |
| `0xC000_0000`– | Reserved | Access → halt | same |

**Decode sits in front of the caches.** Each port decodes the region *before*
its cache: peripheral and reserved accesses are routed to the peripheral bus
(or halt) without ever touching a cache, so MMIO is architecturally
**uncacheable** — a read of `UART_DATA` always reaches the UART (its side
effects always happen) and never allocates a D$ line. Only code/data-region
accesses proceed into the I$/D$ and on to the arbiter. The caches index and
tag the **post-decode physical address**: in `RAM_ONLY`, `addr[31:30]` is
dropped *before* the caches, so the code- and data-region views of one SRAM
word share a single line in each cache and the D$ stays coherent across the
aliases (keeping `DCACHE_SIZE` architecturally invisible, Section 3).

In `RAM_ONLY`, `addr[31:30]` is simply dropped for SPI addressing, so code
and data regions are two views of the same SRAM (self-modifying code and
data-in-code both work *at the memory level*). Caution: with
`ICACHE_SIZE > 0` the I-cache can still hold a stale copy after a store — the
I$ never observes data-port traffic, and there is no `FENCE.I` in v1 to flush
it (Section 5.4).

**Arbiter** (`bus_arbiter.v`, new): fixed priority, **data port wins**. Its
two masters are the prefetch unit (backed by the I$) and the load/store queue
(backed by the D$); a cache hit is satisfied inside those blocks and never
reaches the arbiter at all. Peripheral accesses bypass the arbiter entirely
(decode routes them to the peripheral bus before the caches) and proceed
**in parallel** with a fetch burst — only memory-region data requests
arbitrate for SPI. A memory-region data request aborts any prefetch burst in
progress at **word granularity**: the in-flight fetch word always completes
and its `rsp_valid` is delivered (worst case 3 more bytes, 24 SPI clocks),
then the controller deasserts CS and services the data request — the
protocol invariant "every accepted request gets a response" holds, and the
completed word is still usable (FIFO/I$). Two things are therefore never
torn by preemption: an accepted request (above), and a **multi-word cache
fill** (`CACHE_BLOCK = 8`), which the arbiter treats as atomic — all its
words complete before the other master is served (Section 5.4), so no
partially-filled line can ever exist.
Rationale: loads/stores are on the program's critical path; prefetch is
speculative and can re-burst afterwards. Since both external chips share
SCK/MOSI/MISO, fetch and memory-region data can never overlap anyway — even
in `FLASH_RAM` — so the arbiter is genuinely just a 2:1 mux with abort;
peripheral traffic is the one thing that genuinely runs concurrently with
fetch, and it never enters the arbiter.

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
    fast-read/dummy cycles).
  - Write (SRAM only): `02h`, A[23:0], then N data bytes. The 23-series
    SRAM must be configured for sequential mode, which is its power-on
    default on the common parts; a mode-set command at boot is not required.
    Flash writes are **not** supported in v1 (no page-program/erase state
    machine) — the code region is read-only, per the decode table.
- **Address mapping**: the 24-bit SPI address is `addr[23:0]`; `addr[29:24]`
  are ignored, so each memory region aliases every 16 MB (and the attached
  chip's own decoding may alias further below that). An access with
  `addr[29:24] ≠ 0` does **not** halt — consistent with the cheap-decode
  philosophy of Section 6 — and the cocotb memory model implements the same
  aliasing rule so RTL and model agree.
- **Burst support (reads and SRAM writes)**: after completing an access for
  master M at address A, if the next accepted request is from M, in the
  **same direction** (read→read or write→write), targeting the **same chip
  select** at the **consecutive 24-bit SPI address** (`A[23:0] + size`), the
  controller skips command+address and keeps clocking data. The continuation
  check is on the post-mapping `(CS, SPI address)` pair, **never the 32-bit
  bus address**: bus addresses that are consecutive across a region boundary
  (e.g. `0x3FFF_FFFC → 0x4000_0000`) switch chips in `FLASH_RAM` and wrap the
  SPI address (`0xFFFFFC → 0x000000`) in `RAM_ONLY`, so they always break the
  burst — continuing on the bus address there would silently transfer the
  wrong data. Burst rules by direction —
  reads burst on both chips; writes burst on the SRAM (its sequential mode
  accepts continuous data while CS stays low, and flash is read-only so a
  write burst can only ever target the SRAM). A continuation word costs
  32 SPI clocks instead of 64 in either direction. Any other request, a
  direction change, a chip-select or SPI-address discontinuity, or an
  explicit `burst_abort` (prefetch redirect, arbiter
  preemption, halt) first raises CS_n for the required deselect time
  (≥ 1 SCK period, generated by the same divider). Aborts take effect at
  **word granularity**: an accepted request always finishes and delivers its
  response before CS_n rises (Section 5.1).
- **SCK limits / divisor floor**: the divisor is global (both chips share
  SCK), so `spi_clk_div` must respect the *slowest* attached chip. 25-series
  NOR flash typically supports the plain `03h` read at 33–50 MHz, but
  23-series serial SRAM is rated **20 MHz** — so above a 40 MHz sysclk,
  `spi_clk_div = 1` (SCK = sysclk/2) is out of spec for the SRAM. At the
  target 50 MHz sysclk, every configuration with an SRAM attached (i.e. both
  `MEM_CONFIG` values) must run **`spi_clk_div ≥ 2`** (SCK ≤ 12.5 MHz);
  `spi_clk_div = 1` is usable only at sysclk ≤ 40 MHz or with faster-rated
  parts. Hardware does not enforce the floor — software honors it when
  writing `SPI_DIV` (Section 6.4); the reset value (`SPI_CLK_DIV_RST = 4`)
  is always safe.
- A divisor value of **`0` is reserved and treated as `1`** (clamped in
  hardware, SCK = sysclk/2) — no divisor setting can stop the bus.
- **Chip selects**: `MEM_CONFIG = "FLASH_RAM"` → CS0_n = flash (code
  region), CS1_n = SRAM (data region). `"RAM_ONLY"` → CS0_n = SRAM
  (both regions), CS1_n tied inactive (pin becomes spare).
- Reset/idle state: CS_n all high, SCK low, MOSI low.

**Latency budget** (sysclk cycles, `spi_clk_div` = d, so one SPI bit = 2d):

| Access | SPI clocks | sysclk @ d=1 | @ d=2 (SRAM floor) | @ d=4 (reset) |
|---|---|---|---|---|
| Random 32-bit read | 8+24+32 = 64 | 128 | 256 | 512 |
| Random 32-bit write | 8+24+32 = 64 | 128 | 256 | 512 |
| Burst continuation word (read, or write to SRAM) | 32 | 64 | 128 | 256 |
| Byte write | 8+24+8 = 40 | 80 | 160 | 320 |

At 50 MHz sysclk and the d=2 SRAM divisor floor, straight-line code runs at
~390 k instructions/s (fetch-bound); d=1 (~780 k) is available only at
sysclk ≤ 40 MHz or with faster-rated memory parts. This table is the number
to beat with `PREFETCH_DEPTH` and is why nothing else in the core needs to
be fast.

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

### 5.4 Caches (`icache.v` / `dcache.v`, new — optional)

Two independent, optional caches, each its own module in its own file: an
**I$** (`icache.v`) in front of the prefetch unit's bus side and a **D$**
(`dcache.v`) in front of the load/store queue's bus side. They are separate
designs, not two instances of one shared module, because their contracts
differ fundamentally:

- The **I$ is read-only**: it has no write port and no store-handling logic of
  any kind. It assumes instructions never become stale — nothing ever
  invalidates or updates a line after fill (only reset clears it). That
  assumption is safe under `FLASH_RAM` (code region is read-only in hardware)
  and is exactly the self-modifying-code caveat under `RAM_ONLY` (see the
  coherence bullet below).
- The **D$ carries the store path**: hit-update logic, the write-through port
  into the LSQ drain path, and the `DCACHE_WRITE_POLICY` miss behavior.

Splitting them keeps the I$ at its true cost (tags + valid + data, no lane
write-enables or store datapath) instead of dragging disabled write logic
through synthesis. Each is enabled by its own size parameter (`ICACHE_SIZE`,
`DCACHE_SIZE`); `0` makes that cache a pure pass-through with zero flops. Both
share the geometry parameters `CACHE_WAYS` and `CACHE_BLOCK`.

**Placement — why the caches sit on the memory side of the prefetch/LSQ, not
between them and the core.** The pipeline is
`core → prefetch/LSQ → addr decode → [cache] → arbiter → SPI` (decode before
the caches, so only code/data-region accesses ever reach a cache and the
caches see post-decode physical addresses — Section 5.1), i.e. the prefetch
FIFO and the load/store queue are core-facing and each cache is a bolt-on
accelerator behind its port. Three reasons, strongest first:

- **Data-side correctness (decisive).** The LSQ is the single point of
  load/store ordering and RAW forwarding (Section 4.5), and the D$ does *not*
  reflect a store still sitting in the queue — write-through updates the line
  only when the store drains. A load must therefore forward from older queued
  stores *before* the cache is consulted; if the D$ were in front of the LSQ a
  load could hit a stale line while an older store to the same address is still
  queued. Keeping the D$ behind the LSQ leaves all ordering/forwarding in one
  block and consults the cache only on an already-ordered access — concretely,
  **all D$ interaction (lookup, hit-update, allocate) happens at the LSQ
  drain point** (Section 4.5), never while an older store is still queued.
- **Parametrization.** Caches default off (`*CACHE_SIZE = 0`) while the
  prefetch FIFO and LSQ default on, so those two are the baseline latency-hiders
  and the cache is optional storage stacked behind them. Core-facing prefetch/
  LSQ means the core sees the *same* interface whether or not a cache is
  present; a core-facing cache would force the default cacheless build to use a
  different core-side contract than a cached build.
- **Arbiter simplicity.** Each bus port stays single-master (prefetch owns the
  instruction port, LSQ the data port, Section 5.1), so the arbiter is a 2:1
  mux with burst-abort and the caches add no new arbiter clients.

For the I$ the first reason does not apply (instructions are not written, modulo
the self-modifying-code caveat below), so I$-behind-prefetch is chosen for
symmetry and the parametrization/arbiter reasons; the cost is that the prefetch
FIFO and I$ briefly hold the same words on a cold run. A stream-buffer-beside-
cache organization would avoid that redundancy but couple the prefetcher to the
cache and break the clean `SIZE = 0` bypass — not worth it at these sizes.

- **Geometry**: `sets = SIZE / (CACHE_WAYS · CACHE_BLOCK)`. Index =
  `addr[log2(sets·BLOCK)-1 : log2(BLOCK)]`, tag = the bits above, where
  `addr` is the **post-decode physical address** (Section 5.1 — in `RAM_ONLY`
  the region bits are already dropped, so aliases share lines). Example:
  256 B, 2-way, 8 B block → 16 lines, 8 sets, 3 index bits, 1 LRU bit/set.
  Direct-mapped (`CACHE_WAYS = 1`) carries no replacement state.
- **Fill**: a miss allocates a line and reads `CACHE_BLOCK` bytes with a single
  SPI (burst) access through the arbiter, then satisfies the access. Block 8 B
  = one 2-word burst; block 4 B = one word. Fills are **atomic**: the arbiter
  never preempts a multi-word fill (Section 5.1), so a line is always either
  entirely valid or entirely absent — no partial-line state exists and lines
  need no per-word valid bits.
- **Write policy (D$ only — the I$ has no write path)**: always
  **write-through** to memory: a store updates its line on a hit and *always*
  also posts to memory through the LSQ. There are no dirty bits and no
  writeback bursts (area), SPI stays the single source of truth, and the LSQ
  remains the one place that serializes writes to memory.
  `DCACHE_WRITE_POLICY` selects only the store-*miss* behavior:
  - `"WRITETHROUGH"` (default) = no-write-allocate — a store miss goes
    straight to memory and does not touch the cache.
  - `"WRITEALLOCATE"` — a store miss first fills the line (one `CACHE_BLOCK`
    SPI burst read through the arbiter), then updates it, still posting the
    store to memory. This helps store-then-load patterns (stack frames,
    spills) at the cost of a fill burst per store miss.

  Neither variant is write-back; the parameter never changes architecturally
  visible results (Section 3), only which lines are resident.
- **Coherence / self-modifying code**: single-hart, so D$↔memory stays coherent
  for free via write-through (under either `DCACHE_WRITE_POLICY`). The
  **I$ is *not* coherent** with stores — by construction it has no snoop or
  invalidate hardware at all (the never-stale assumption above), and there is
  no `FENCE.I`/Zifencei in v1 to flush it from software. The staleness caveat
  therefore applies to executing from any **writable** region: code-region
  fetches under `FLASH_RAM` can never go stale (that region is read-only in
  hardware), but the data region is writable and legal to execute from in
  *both* `MEM_CONFIG`s (Section 4.4), and in `RAM_ONLY` everything is
  writable. Consequences: with `ICACHE_SIZE > 0`, code that was stored or
  patched at runtime is not guaranteed to be fetched fresh — if you run
  stored/patched code (self-modifying code, or a loader copying code into
  SRAM and jumping to it), keep `ICACHE_SIZE = 0` or reset the core between
  code loads (reset clears all cache state). A `FLASH_RAM` build that only
  ever executes from flash is unconditionally safe. This is the sole
  architecturally-visible cache effect (Section 3) and is flagged in the
  address-decode notes (Section 5.1).
- **Area**: with no SRAM macro on sg13g2, storage is flip-flops — a 256 B cache
  is ~2048 data flops plus tags/valid/LRU, i.e. roughly four register files,
  and I$+D$ at 256 B each roughly *doubles* the whole design's flop count. That
  is why both default to `0` and why enabling them is what pushes the design
  toward the upper end of the 5–10 tile budget (Section 7).
- Reset/idle: all valid bits cleared on reset; caches come up empty and cold.

## 6. Peripherals

All peripheral registers are 32-bit, word-aligned, on the data port only.
Reads of undefined offsets return 0; writes to undefined offsets are ignored
(they do **not** halt — keeps the decode cheap). Only word accesses
(`req_size = 10`) are meaningful; the low byte carries the payload unless
noted. No interrupts anywhere — software polls status bits.

Base `0x8000_0000`, decode on `req_addr[7:0]`:

| Offset | Name | Access | Contents |
|---|---|---|---|
| `0x00` | `UART_DATA` | R/W | W: byte to transmit. R: received byte (pops the RX buffer); reading while `rx_valid` = 0 returns 0 and has no side effect |
| `0x04` | `UART_STATUS` | R | bit0 `tx_busy`, bit1 `rx_valid`, bit2 `rx_overrun` (cleared on read), bit3 `rx_frame_err` (cleared on read) |
| `0x08` | `UART_DIV` | R/W | bits[15:0] baud divisor, reset `UART_DIV_RST` |
| `0x10` | `I2C_CMD` | W | bits[7:0] data byte, bits[10:8] command (below); writing starts the operation — a write while `busy` = 1 is ignored (poll first, matching the UART TX rule) |
| `0x14` | `I2C_STATUS` | R | bit0 `busy`, bit1 `nack` (last addr/data byte not acked; sticky until next command) |
| `0x18` | `I2C_DATA` | R | bits[7:0] byte received by the last READ command |
| `0x1C` | `I2C_DIV` | R/W | bits[15:0] SCL half-period divisor |
| `0x20` | `SPI_DIV` | R/W | bits[7:0] SPI clock divisor, reset `SPI_CLK_DIV_RST` (Section 5.2) |
| `0x24` | `SYS_ID` | R | Constant: 8-bit version (bits[31:24]), bits[23:16] reserved (read 0), 16-bit config fingerprint (bits[15:0]: `CORE_ARCH`, `ENABLE_SUBWORD`, `MEM_CONFIG`, `PREFETCH_DEPTH`, `LSQ_DEPTH`, `LSQ_FORWARD`, and cache config including `DCACHE_WRITE_POLICY` encoded) — lets software and tests identify the build |

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
  polled between them (a write while `busy` is ignored):

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

Leftover `ui_in` pins are readable through a `SYS_GPIO` register if pin
budget allows after final pinout — deliberately unspecified in v1; the
offsets `0x28+` are reserved for it. The non-UART/`halted` `uo_out` pins
carry debug status outputs in v1 (Section 7); if `SYS_GPIO` is added later
it can take those pins over (a register write overrides the debug function)
without changing the pinout.

### 6.4 SPI configuration

`SPI_DIV` (offset `0x20`) is the runtime knob for memory bus speed: boot
conservatively at `SPI_CLK_DIV_RST`, then software cranks it down once
running — typically to the divisor floor for the attached chips (2 at
50 MHz sysclk with a 23-series SRAM; see Section 5.2). Hardware clamps a
written value of 0 to 1 but does not enforce the chip-rating floor. Writes
take effect at the next CS assertion, never mid-transfer.

## 7. TinyTapeout top level and pinout

The TT wrapper (top module renamed from `tt_um_example` to
**`tt_um_timva1_rv32e`** — update `info.yaml` `top_module`, `src/project.v`,
and `test/tb.v` together, per repo convention) contains only pin mapping and
the `soc_top` instance with the tape-out parameter set.

Pin assignment follows the [TT recommended pinouts](https://tinytapeout.com/specs/pinouts/)
wherever this design's signal set fits them: UART uses the dedicated
`ui_in`/`uo_out` option (so it costs no `uio` pins), SPI takes the standard
Pmod "top row" `uio[0:3]`, and I2C takes the "bottom row" `uio[6:7]` slot
(the matching positions to `uio[2:3]` on the top row). The one deviation is
`SPI CS1_n`: TT's bottom row would duplicate a whole second SPI bus at
`uio[4:7]`, but this design only ever needs one extra chip-select line (for
`FLASH_RAM`'s second chip), so it borrows just the bottom row's CS position
(`uio[4]`) and leaves the unused MOSI/MISO/SCK duplicate positions spare.

| Pin | Direction | Function | Notes |
|---|---|---|---|
| `uo_out[0]` | out | `dbg_fetch_stall` | Debug: core is waiting for an instruction (prefetch FIFO empty) |
| `uo_out[1]` | out | `dbg_mem_stall` | Debug: core is waiting on a load (LSQ data pending) |
| `uo_out[2]` | out | `dbg_lsq_busy` | Debug: load/store queue non-empty (posted stores draining) |
| `uo_out[3]` | out | `dbg_retire` | Debug: toggles on each retired instruction (heartbeat) |
| `uo_out[4]` | out | UART TX | TT recommended UART pinout (option 1) |
| `uo_out[5]` | out | `halted` | High in HALTED state (Section 2.2); not a TT-standardized signal |
| `uo_out[6]` | out | `dbg_rx_valid` | Debug: mirror of UART `rx_valid` status bit |
| `uo_out[7]` | out | `dbg_spi_active` | Debug: SPI transaction in progress (any CS_n low) |
| `ui_in[0..2]` | in | spare / GPIO in | Unused → `_unused` wire |
| `ui_in[3]` | in | UART RX | TT recommended UART pinout (option 1); idle high |
| `ui_in[4..7]` | in | spare / GPIO in | Unused → `_unused` wire |
| `uio[0]` | bidir | SPI CS0_n | TT recommended SPI pinout, top row; flash (`FLASH_RAM`) or SRAM (`RAM_ONLY`) |
| `uio[1]` | bidir | SPI MOSI | TT recommended SPI pinout, top row |
| `uio[2]` | bidir | SPI MISO | TT recommended SPI pinout, top row |
| `uio[3]` | bidir | SPI SCK | TT recommended SPI pinout, top row |
| `uio[4]` | bidir | SPI CS1_n | Bottom-row SPI CS position, repurposed as the second chip select; SRAM (`FLASH_RAM`), constant 1 in `RAM_ONLY` |
| `uio[5]` | bidir | spare | Bottom-row SPI MOSI/MISO/SCK duplicate positions are unused (one logical SPI bus) |
| `uio[6]` | bidir | I2C SCL | TT recommended I2C pinout, bottom row; open-drain (Section 6.2), needs external pull-up |
| `uio[7]` | bidir | I2C SDA | TT recommended I2C pinout, bottom row; open-drain, needs external pull-up |

The six `dbg_*` outputs are pure status taps (no new state except the
retire-toggle flop) and exist for silicon bring-up: together with `halted`
and a logic analyzer they distinguish "core running", "starved on fetch",
"stuck on a load", and "SPI hung" without any software running. If
`SYS_GPIO` (Section 6.3) is added later it takes these pins over — a
register write overrides the debug function, so the pinout doesn't change.

Budget check: both `MEM_CONFIG` variants fit
(`RAM_ONLY` frees `uio[4]`); the caches and load/store queue add no pins.
TT rules honored: `uio_out`/`uio_oe` fully assigned, `ena` ignored into
`_unused`, async active-low `rst_n` used as the single reset (synchronized
internally with a 2-flop synchronizer before use). VGA, audio, and gamepad
recommendations don't apply — this design has none of those interfaces.

**Clocking**: one clock domain (`clk`); SPI SCK and I2C SCL are generated as
divided *enables*, not derived clocks — no CDC anywhere. Target
`clock_hz`: 25–50 MHz pending hardening results (the multi-cycle core with
an iterative shifter should close 50 MHz on sg13g2 comfortably; final number
goes into `info.yaml`).

**Area expectations** (for placing the design in the 5–10 tile budget, to be
confirmed by a hardening run): register file 512 DFF + prefetch FIFO
(`32·PREFETCH_DEPTH` DFF) + load/store queue (~`65·LSQ_DEPTH` DFF, an
`{addr, wdata, size, we}` entry each) + any enabled caches (~`8·SIZE` data
flops each, plus tags/valid/LRU) + ~150 misc
control/datapath flops. The baseline area build (caches off, depth-2
prefetch/LSQ) is ~900 flops plus combinational logic; a fully-loaded speed
build (256 B 2-way I$ **and** D$) adds ~4000 flops on top. Because sg13g2's TT
flow offers no small SRAM macro, those cache flops are real flip-flops — the
main reason the target footprint is 5–10 tiles rather than 1–2, and why caches
default off.

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
    lsq.v              load/store queue: in-order FIFO + RAW forward    (new)
    alu.v              verified — re-encode alu_op per Section 4.1     (adapt, keep verified tests)
    rf.v               verified — use USE_E_EXT=1, posedge             (reuse as-is)
  bus/
    bus_arbiter.v      2:1 fixed priority, burst abort                 (new)
  cache/
    icache.v           read-only I$: no write/invalidate path           (new)
    dcache.v           D$: write-through, opt. write-allocate on miss   (new)
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
   checks command bytes, read *and* write burst continuation, direction-change
   burst breaks, burst breaks at region/chip boundaries despite consecutive
   bus addresses — continuation is keyed on the `(CS, SPI address)` pair —,
   the 16 MB address-aliasing rule, CS deselect timing), uart
   (loopback TX→RX at several divisors, framing/overrun errors), i2c_master
   (Python I2C slave model, incl. clock stretching and NACK), prefetch +
   arbiter (burst abort on redirect at word granularity — the in-flight word
   completes and delivers its response before CS_n rises —, stale-response
   discard after a redirect, data-beats-fetch priority, atomic multi-word
   cache fills under data-request preemption pressure, and a peripheral
   access completing in parallel without disturbing an active fetch burst),
   lsq
   (in-order drain, RAW forward hit/miss with/without `LSQ_FORWARD`, full-queue
   stall, a directed check that same-address stores are *not* coalesced,
   D$ consulted only at the drain point — a queued `SB` then `LW` of the same
   word must return the merged value, never a stale line —, no forwarding for
   peripheral-region loads, and halt-entry drain: queued stores complete
   before `halted` may assert),
   icache (hit/miss/fill, direct-mapped vs 2-way LRU eviction, and the
   documented staleness after a store to a cached address — the never-stale
   assumption made observable), dcache (hit/miss/fill and eviction as above,
   write-through update-on-hit, plus both `DCACHE_WRITE_POLICY` values:
   store miss leaves the cache untouched under `"WRITETHROUGH"`, fills and
   updates the line under `"WRITEALLOCATE"`, with memory contents identical
   either way).
   The ALU and regfile keep their existing verified benches; the ALU bench
   is updated for the new `alu_op` encoding.
2. **Core level**: `soc_top` with the behavioral SPI memory model preloaded
   from a `.hex` file. Directed assembly tests per instruction class
   (assembled with `riscv32-unknown-elf-gcc -march=rv32e -mabi=ilp32e`),
   convention: test writes a result signature to a fixed SRAM address and
   executes `EBREAK`; cocotb waits for the `halted` pin — which asserts only
   after the LSQ has drained (Section 2.2), so the signature is guaranteed
   visible in the memory model — and checks the signature. Halt-cause tests
   (illegal op, misalignment, subword-when-disabled), plus a directed
   `JALR`-to-odd-address test verifying bit 0 is cleared rather than halting.
3. **Configuration matrix**: the core-level suite runs across
   {`MULTICYCLE`,`PIPELINED`} x {`ENABLE_SUBWORD` 0,1} x
   {`RAM_ONLY`,`FLASH_RAM`} x `PREFETCH_DEPTH` {0,2,8} x `LSQ_DEPTH` {0,2} x
   cache {off, 256 B/2-way} x `DCACHE_WRITE_POLICY`
   {`WRITETHROUGH`,`WRITEALLOCATE`} (cached configurations only) — results
   must be identical, only cycle counts differ. The one carve-out is the self-modifying-code tests, which run only
   with the I$ off or under `FLASH_RAM`, since `ICACHE_SIZE > 0` on writable
   code is the documented incoherent case (Sections 3, 5.4). This identical-
   results property is the payoff of keeping everything but those axes
   non-architectural.
4. **Compliance**: riscv-arch-test RV32E suite, adapted to the
   signature+EBREAK convention (no CSRs → use the halt-based test end). Runs
   only on `ENABLE_SUBWORD = 1` builds — the suite exercises
   `LB`/`LH`/`LBU`/`LHU`/`SB`/`SH`, which halt as illegal when subword
   support is compiled out.
5. **Gate level**: the standard TT `GATES=yes` flow on the tape-out
   configuration after hardening.

## 10. Open questions / future work

- **`RAM_ONLY` boot path**: bus sharing with an external loader during reset
  (Section 5.3) — needs a demo-board-level answer (series resistors, or a
  dedicated loader CS) before `RAM_ONLY` is tape-out-viable.
- **Tile count**: where in the 5–10 tile budget each build lands (and how much
  cache the upper builds can afford) is pending the first hardening run.
- **`FENCE.I` / I$ invalidation**: v1 has no way to make the I$ coherent with
  stores, so `ICACHE_SIZE > 0` + self-modifying code is unsupported
  (Section 5.4). A Zifencei instruction or an I$-flush register would lift that
  restriction if it's ever needed.
- **Cache storage**: caches are flip-flop arrays today (no sg13g2 SRAM macro in
  the TT flow). Whether a latch-based array or a macro is worth the effort is
  open (Section 7).
- **Interrupts + Zicsr**: next architectural step; peripheral event bits are
  already positioned to become IRQ sources (Section 2.3).
- **QSPI upgrade**: 4x the fetch bandwidth for 2 extra pins (`uio`) and a
  wider PHY; the command layer / burst logic is structured so only
  `spi_phy.v` and the pin map change.
- **UART/RX FIFOs, GPIO register**: reserved offsets exist (Sections 6.1,
  6.3); add if area allows after the core hardens.
