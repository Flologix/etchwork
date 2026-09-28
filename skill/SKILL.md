---
name: etchwork
description: Design a printed circuit board in this project with the etchwork tools (SKiDL, KiCad, freerouting, net classification). Use when asked to design, lay out, route or check a PCB.
---

# Designing a board

You design; the tools do everything algorithmic. The etchwork tools appear as
`mcp__etchwork__<name>`. Never copy libraries,
start containers, or write your own toolchain scripts: if a tool is missing
or the etchwork server did not start, stop and say so.

## Order of work
1. Write `expected.netlist.toml`: the wiring you intend (`[nets]`, `NAME = ["U1:3", "R1:2"]`, pins as numbers).
2. Find parts with `find_part`, check pins with `describe_part` and sizes with `describe_footprint`. `describe_part` lists each pin's functions (MOSI, SCK, SDA…); wire by those, never from memory.
3. Write the SKiDL script (e.g. `board.py`) with named nets and real footprints, grouped into `@subcircuit` functions by role — power input, regulator, MCU, each connector or header, indicators — each taking the nets it connects to as arguments; the schematic is drawn one block per subcircuit. End with `generate_netlist(file_="board.net")`.
4. `run_skidl`, then `check_netlist` against `expected.netlist.toml`. Fix wiring until it matches. `check_netlist` also fails when a net's name (`ISP_SCK`) or a named header pin says a function that no MCU pin on it can carry. Fix the wiring; only if the net is meant to be a plain GPIO (a bit-banged bus, a button), drop the function word from its name.
5. `export_schematic` (script, netlist): draws `board.kicad_sch` and `board-schematic.pdf` and checks the drawing matches the netlist. Run it again whenever the circuit changes. It is a reference drawing; never edit it to change the circuit. If it reports `SCHEMATIC NOT AVAILABLE`, continue: don't redesign the circuit to get a drawing, and say in the final answer that the board has no schematic, and why.
6. Classify the nets: call `net_evidence` on the netlist (follow `next_offset` until it is null). For every open net pick one class and one tier from the menu, with a one-line reason; the tier sets the trace width; when unsure, pick the higher tier and set `review: true`. Then call `set_net_classes` with all picks. If it answers REFUSED, fix exactly what it lists and call it again. It writes `<board>.netclasses.json`; read which nets await the human's decision.
7. `run_layout` with a board size and `arrange` operations (rotate / align / space_evenly). Prefer `arrange` to raw `positions`.
8. `fill_zones` (GND), then `run_drc`. Set `rules` once to your fab's real capabilities (for example clearance 0.15, track 0.15, via 0.6/0.3, hole clearance 0.2) — that is allowed. Loosening a rule only to silence a specific error is not: fix the placement or the board size instead. For JLCPCB — the default — pass no `rules`: DRC already checks its standard 2-layer tier (no surcharge). Pass `rules` only when the human names a different fab, and then on every `run_layout` call. `fill_zones` repairs starved thermal reliefs itself (it makes only the named pads solid); don't move parts to fix a starved thermal.
9. `export_gerbers`, then `summarize_project`, then in README.md replace the line `_Describe what this board does._` with what the board does, and add your design notes (outside the generated block).
10. `check_done` on the netlist. If it says NOT DONE, do what each line says and call it again.

Changes go through the SKiDL script, never by editing the board file. If `run_layout` refuses because the board changed since the tools last wrote it, the human edited it in KiCad: ask them before calling it again with `overwrite=true`.

## Finishing
Done means `check_done` returns DONE. Then:
- write the final answer: what the board is, where the files are (including the schematic PDF), and the nets awaiting the human's decision exactly as `check_done` listed them;
- stop.

After DONE, KiCad's DRC is the only authority on clearances and connectivity. Do not write your own clearance, netlist or gerber checkers: a home-made checker once invented a short that did not exist.
After DONE, run no commands on the project's files: the board facts in `check_done`'s output (size, layers, parts, DRC counts) are what your final answer states.
A design concern you notice after DONE (a connector's orientation, a part choice) goes into the final answer as a note for the human; do not rework the board for it unless the human asks.
Never report success while DRC has errors or unconnected items; say plainly what is left.

## SKiDL pitfalls
- `NC += u1["PB4"]` marks a pin unconnected; `.no_connect()` does not exist.
- Pins are 1-indexed: `r[1]`, `r[2]`.
- Name every net: `Net("LED_A")`; unnamed nets become `N$1` and carry no intent.
- `vcc.drive = POWER` on a rail nothing drives, or ERC reports insufficient drive.
- A function that adds parts must be passed the rails it connects to.
- `generate_netlist(file_="x.net")`; without `file_` the netlist is named after the script.
- A subcircuit is a function decorated with `@subcircuit` that creates its parts and connects them to nets passed in: `@subcircuit\ndef leds(drive, gnd): r = Part("Device", "R", value="330", footprint=...); d = Part("Device", "LED", footprint=...); drive & r & d["A,K"] & gnd`.
- Inside a `@subcircuit` function, `NC += u[...]` needs `global NC` first, or Python raises `UnboundLocalError`: `+=` makes `NC` local to the function otherwise.
