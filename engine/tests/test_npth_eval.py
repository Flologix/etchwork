"""The NPTH-keepout evaluation covers five real boards,
stripped of copper and routed by freerouting with and without keepouts, then
checked by KiCad's DRC.
Real routing takes about four minutes, so it runs only when asked:

    CIRCUIT_ROUTING_EVAL=1 python3 -m pytest tests/test_npth_eval.py
"""
import collections
import json
import os
import shutil

import pytest

from circuit_mcp import kicad, rules, service
from conftest import FIXTURES

pytestmark = pytest.mark.skipif(os.environ.get("CIRCUIT_ROUTING_EVAL") != "1",
                                reason="real routing; set CIRCUIT_ROUTING_EVAL=1")

BOARDS = FIXTURES / "boards"
# Read before any test monkeypatches it: the margin that ships.
SHIPPED = service.NPTH_MARGIN
_STRIP = """
import sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
for item in list(b.GetTracks()) + list(b.Zones()):
    b.Delete(item)
pcbnew.SaveBoard(sys.argv[1], b)
print("stripped")
"""


def _track_to_npth(violation, types=("hole_clearance", "copper_edge_clearance")):
    items = [item.get("description", "") for item in violation.get("items") or []]
    return (violation.get("type") in types
            and any("NPTH pad" in item for item in items)
            and any(item.startswith(("Track", "Via", "Arc")) for item in items))


def _route(workspace, name, margin, monkeypatch, run="a"):
    """Strip a fresh copy of the board, route it, DRC it; the numbers the bar reads."""
    folder = workspace / f"{margin}-{run}"
    folder.mkdir()
    shutil.copy(BOARDS / f"{name}.kicad_pcb", folder / "board.kicad_pcb")
    code, out = service.python(_STRIP, str(folder / "board.kicad_pcb"))
    assert code == 0, out
    board = f"{folder.name}/board.kicad_pcb"
    monkeypatch.setattr(service, "NPTH_MARGIN", margin)
    answer = service.route(board, service.ROUTER_PASSES)
    # What ships: run_layout finishes every routed board before its DRC.
    rules.finish(board)
    service.call("drc", {"board": board, "out": f"{folder.name}/check.json"})
    report = json.loads((folder / "check.json").read_text())
    violations = report.get("violations") or []
    return {
        # `edge` is recorded, not gated (spec §5.2): the edge-only leftovers
        # are 3b's, with the fab rules.
        "hole": sum(1 for v in violations if _track_to_npth(v, ("hole_clearance",))),
        "edge": sum(1 for v in violations if _track_to_npth(v, ("copper_edge_clearance",))),
        "unconnected": len(report.get("unconnected_items") or []),
        "tally": collections.Counter(v.get("type") for v in kicad.violations(report)
                                     if v.get("severity") == "error"),
        "keepouts": answer["npth_keepouts"],
        "left": service.NPTH_KEEPOUT in (folder / "board.kicad_pcb").read_text(),
    }


def test_freerouting_is_deterministic_on_release2(workspace, monkeypatch):
    # Step 0: if two identical routes differ, every comparison below is noise.
    first = _route(workspace, "release2", None, monkeypatch, "a")
    second = _route(workspace, "release2", None, monkeypatch, "b")
    assert first == second


@pytest.mark.parametrize("name", ["release2", "release", "release3", "stop1", "test"])
def test_keepouts_clear_the_holes_without_costing_a_connection(workspace, name, monkeypatch):
    base = _route(workspace, name, None, monkeypatch)
    fenced = _route(workspace, name, SHIPPED, monkeypatch)

    assert fenced["hole"] == 0
    assert fenced["unconnected"] <= base["unconnected"]
    grown = {t: (base["tally"][t], n) for t, n in fenced["tally"].items()
             if n > base["tally"][t]}
    assert grown == {}
    assert fenced["left"] is False


def test_the_edge_margin_costs_connections_which_is_why_hole_ships(workspace, monkeypatch):
    # The spec's pre-set fallback, kept reproducible: at max(hole, edge) the
    # keepout fences release2's J1 pads in and the router loses connections.
    base = _route(workspace, "release2", None, monkeypatch)
    edge = _route(workspace, "release2", "auto", monkeypatch)
    assert edge["unconnected"] > base["unconnected"]
    assert SHIPPED == "hole"
