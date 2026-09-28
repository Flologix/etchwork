"""Runs inside the engine image: one delivered board -> the JSON contract/board.py reads.

The only grader code that touches pcbnew or circuit_mcp. The pcbnew half runs
through service.python, as its own subprocess, so a board that crashes pcbnew
fails the dump rather than this process; it writes JSON to a file, because
pcbnew's import noise lands on the subprocess's output. The pin-name half
reads the netlist's libsources through parts.pin_lookup, the same machinery
the engine's own pin-function check uses (design spec §5.2).

    python3 -m contract.dump <board.kicad_pcb> [<board.net>]
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

_PCB = r"""
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
mm = pcbnew.ToMM
def pt(p): return [round(mm(p.x), 4), round(mm(p.y), 4)]
def ring(chain): return [pt(chain.CPoint(i)) for i in range(chain.PointCount())]
copper = set(board.GetEnabledLayers().CuStack())
def layers(item): return [board.GetLayerName(l) for l in item.GetLayerSet().CuStack() if l in copper]

edges = pcbnew.SHAPE_POLY_SET()
outline = []
if board.GetBoardPolygonOutlines(edges, False) and edges.OutlineCount():
    outline = ring(edges.Outline(0))

footprints, pads = [], []
for fp in board.GetFootprints():
    court = fp.GetCourtyard(pcbnew.F_CrtYd)
    if not court.OutlineCount():
        court = fp.GetCourtyard(pcbnew.B_CrtYd)
    footprints.append({
        "ref": fp.GetReference(), "value": fp.GetValue(),
        "lib_id": str(fp.GetFPID().GetUniStringLibId()),
        "pos": pt(fp.GetPosition()), "rot": fp.GetOrientationDegrees(),
        "courtyard": ring(court.Outline(0)) if court.OutlineCount() else [],
        "pad_count": len(fp.Pads())})
    for pad in fp.Pads():
        pads.append({"ref": fp.GetReference(), "number": pad.GetNumber(),
                     "pos": pt(pad.GetPosition()), "layers": layers(pad),
                     "net": pad.GetNetname(), "pinfunction": pad.GetPinFunction()})

tracks = [{"layer": board.GetLayerName(t.GetLayer()), "width": mm(t.GetWidth()),
           "start": pt(t.GetStart()), "end": pt(t.GetEnd()), "net": t.GetNetname()}
          for t in board.GetTracks() if t.GetClass() in ("PCB_TRACK", "PCB_ARC")]

zones = []
for z in board.Zones():
    shape = z.Outline()
    zones.append({"layers": layers(z), "net": z.GetNetname(),
                  "outline": ring(shape.COutline(0)) if shape.OutlineCount() else [],
                  "filled": bool(z.IsFilled()), "rule_area": bool(z.GetIsRuleArea())})

with open(sys.argv[2], "w") as out:
    json.dump({"outline": outline, "footprints": footprints, "pads": pads,
               "tracks": tracks, "zones": zones}, out)
"""


class DumpError(Exception):
    pass


def dump(pcb, netlist=None) -> dict:
    from circuit_mcp import kicad_lib, parts, service

    pcb = pathlib.Path(pcb)
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "board.json"
        code, text = service.python(_PCB, str(pcb), str(out))
        if code != 0 or not out.is_file():
            raise DumpError(f"pcbnew could not read {pcb.name} (exit {code}): {text[-2000:]}")
        data = json.loads(out.read_text())

    sources = {}
    if netlist is not None:
        sources = kicad_lib.read_libsources(pathlib.Path(netlist).read_text(errors="replace"))
    lookups = {ref: parts.pin_lookup(lib, symbol) for ref, (lib, symbol) in sources.items()}
    for pad in data["pads"]:
        names = [pad.pop("pinfunction")]
        found = lookups.get(pad["ref"])
        if found is not None:
            names.append(found.names.get(pad["number"], ""))
            names.extend(found.functions.get(pad["number"], ()))
        pad["names"] = [n for n in dict.fromkeys(names) if n]
    return data


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print("usage: python3 -m contract.dump <board.kicad_pcb> [<board.net>]", file=sys.stderr)
        return 2
    try:
        data = dump(argv[0], argv[1] if len(argv) == 2 else None)
    except Exception as e:  # everything is reported; the host turns it into a harness error
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    json.dump(data, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
