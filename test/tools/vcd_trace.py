#!/usr/bin/env python3
"""vcd_trace.py — dump chosen signals from a VCD as a per-clock-edge text table.

A root-causing aid for the verify-module workflow: when a cocotb test fails on a
timing/sampling corner (the recurring NBA/edge-sampling races in this repo), a
greppable text trace of a few hierarchical signals sampled at each clock edge is
far easier to reason over from the CLI than an interactive gtkwave/surfer
session. This reads the VCD an already-run sim produced — no re-instrumenting.

Requires a *real* VCD, not an FST. The Makefile's default dumps FST-format data
into a `.vcd`-named file (see CLAUDE.md), so re-run the failing bench with the
`FST=` override first, e.g.:

    make -B test_uart FST=            # dumps a real VCD to test/waves/uart_tb.vcd

Usage:
    python tools/vcd_trace.py waves/uart_tb.vcd --list
    python tools/vcd_trace.py waves/uart_tb.vcd \
        --clk uart_tb.clk \
        --signals 'uart_tb.uut.u_rx.strobe' 'uart_tb.uut.access' \
                  'uart_tb.uut.rx_*' \
        --window 21900 22260

Notes:
- Signal names are full dotted hierarchical paths (use --list to discover them);
  --signals accepts fnmatch globs. With no --signals, every signal is tracked.
- --window bounds are in nanoseconds (the $timescale is parsed and applied).
- Vectors print as hex when fully 0/1, else as their raw VCD bit string.
"""

import argparse
import fnmatch
import sys

# $timescale unit → nanoseconds-per-tick multiplier.
_UNIT_NS = {"fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}


def parse_header(lines):
    """Return (code2names, timescale_ns). code2names maps a VCD identifier code
    to the list of full hierarchical signal names sharing it."""
    code2names = {}
    scope = []
    ts_ns = 1.0
    it = iter(lines)
    for raw in it:
        s = raw.strip()
        if s.startswith("$timescale"):
            body = s[len("$timescale"):].replace("$end", "").strip()
            while not body:                       # magnitude/unit may be on next line
                body = next(it).strip().replace("$end", "").strip()
            num = "".join(c for c in body if c.isdigit()) or "1"
            unit = "".join(c for c in body if c.isalpha()).lower()
            ts_ns = int(num) * _UNIT_NS.get(unit, 1.0)
        elif s.startswith("$scope"):
            scope.append(s.split()[2])
        elif s.startswith("$upscope"):
            if scope:
                scope.pop()
        elif s.startswith("$var"):
            p = s.split()
            code, name = p[3], p[4]                # p[5:] may hold a [msb:lsb] range
            full = ".".join(scope + [name])
            code2names.setdefault(code, []).append(full)
        elif s.startswith("$enddefinitions"):
            break
    return code2names, ts_ns


def fmt(val):
    """Format a captured value: scalar char, hex for clean vectors, else raw bits."""
    if len(val) == 1:
        return val
    if all(c in "01" for c in val):
        return f"0x{int(val, 2):X}"
    return val


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vcd", help="path to a real (text) VCD file")
    ap.add_argument("--clk", help="full name of the clock signal to sample on")
    ap.add_argument("--edge", choices=["posedge", "negedge", "both"],
                    default="posedge", help="clock edge to print a row on")
    ap.add_argument("--signals", nargs="+", default=["*"],
                    help="full hierarchical names or fnmatch globs to show")
    ap.add_argument("--window", nargs=2, type=float, metavar=("START_NS", "END_NS"),
                    help="only rows with START_NS <= t <= END_NS")
    ap.add_argument("--limit", type=int, default=0, help="max rows (0 = no limit)")
    ap.add_argument("--list", action="store_true",
                    help="list all signal names and exit")
    args = ap.parse_args(argv)

    with open(args.vcd) as f:
        lines = f.read().splitlines()

    code2names, ts_ns = parse_header(lines)

    if args.list:
        for name in sorted({n for names in code2names.values() for n in names}):
            print(name)
        return 0

    # Resolve the tracked signals (ordered, de-duplicated) and clock code.
    all_names = sorted({n for names in code2names.values() for n in names})
    tracked, seen = [], set()
    for pat in args.signals:
        for name in all_names:
            if fnmatch.fnmatch(name, pat) and name not in seen:
                tracked.append(name)
                seen.add(name)
    name2code = {n: c for c, names in code2names.items() for n in names}
    clk_code = name2code.get(args.clk) if args.clk else None
    if args.clk and clk_code is None:
        ap.error(f"clock signal {args.clk!r} not found (try --list)")

    state = {}                                    # code -> current value string
    t_ns = 0.0
    prev_clk = None
    rows = 0

    def emit():
        cells = " ".join(f"{n.rsplit('.', 1)[-1]}={fmt(state.get(name2code[n], 'x'))}"
                         for n in tracked)
        print(f"t={t_ns:10.2f}ns  {cells}")

    def in_window():
        return args.window is None or args.window[0] <= t_ns <= args.window[1]

    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if s[0] == "#":
            t_ns = int(s[1:]) * ts_ns
            continue
        # Value-change lines.
        if s[0] in "01xzXZ":
            code, val = s[1:], s[0]
        elif s[0] in "bBrR":
            parts = s.split()
            if len(parts) != 2:
                continue
            code, val = parts[1], parts[0][1:]
        else:
            continue                              # $dumpvars/$end etc.
        state[code] = val

        if clk_code is not None and code == clk_code:
            rise = prev_clk == "0" and val == "1"
            fall = prev_clk == "1" and val == "0"
            prev_clk = val
            hit = (args.edge == "posedge" and rise) or \
                  (args.edge == "negedge" and fall) or \
                  (args.edge == "both" and (rise or fall))
            if hit and in_window():
                emit()
                rows += 1
                if args.limit and rows >= args.limit:
                    break

    if clk_code is None:                          # no clock: dump final state once
        if in_window():
            emit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
