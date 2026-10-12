"""Board checks, phase A: placement rules, checked on the placed board before routing, where a
fix costs a moved part. Every rule warns; none blocks DONE."""
import json

import pytest

from circuit_mcp import boardgeom, checks, events, service, tools
from conftest import FIXTURES
from test_board_extraction import _footprint, _netlist, _pad

# --- the geometry phase A needs, against pcbnew ---------------------------------

_PCBNEW = r"""
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
out = {"outline": None, "courtyards": {}}
box = board.GetBoardEdgesBoundingBox()
out["outline"] = [pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop()),
                  pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())]
for fp in board.GetFootprints():
    court = fp.GetCourtyard(pcbnew.F_CrtYd)
    if court.OutlineCount():
        b = court.BBox()
        out["courtyards"][fp.GetReference()] = [pcbnew.ToMM(b.GetLeft()), pcbnew.ToMM(b.GetTop()),
                                                pcbnew.ToMM(b.GetRight()), pcbnew.ToMM(b.GetBottom())]
print(json.dumps(out))
"""


def test_courtyards_and_the_outline_match_pcbnews():
    """release2's footprints are turned 90, 180 and -90 degrees."""
    board = FIXTURES / "boards" / "release2.kicad_pcb"
    code, out = service.python(_PCBNEW, str(board))
    truth = json.loads(out.strip().splitlines()[-1])
    geometry = boardgeom.read(board.read_text())
    # The Edge.Cuts line has width; pcbnew's box includes half of it.
    assert geometry.outline == pytest.approx(truth["outline"], abs=0.06)
    mine = {f.ref: f.courtyard for f in geometry.footprints if f.courtyard}
    assert mine.keys() == truth["courtyards"].keys()
    # pcbnew grows its courtyard polygon 0.045 mm past the drawn outline, on
    # every side of every footprint (measured here); this reads the outline.
    for ref, box in truth["courtyards"].items():
        grown = (mine[ref][0] - 0.045, mine[ref][1] - 0.045, mine[ref][2] + 0.045, mine[ref][3] + 0.045)
        assert grown == pytest.approx(box, abs=0.002), ref


# --- a small placed board -------------------------------------------------------------

def _court(half):
    return (f'\t\t(fp_rect\n\t\t\t(start {-half} {-half})\n\t\t\t(end {half} {half})\n'
            f'\t\t\t(stroke (width 0.05) (type solid))\n\t\t\t(layer "F.CrtYd")\n\t\t)\n')


def _placed(*footprints, keepout=None):
    edge = "".join(f'\t(gr_line\n\t\t(start {a} {b})\n\t\t(end {c} {d})\n\t\t(layer "Edge.Cuts")\n\t)\n'
                   for a, b, c, d in ((0, 0, 40, 0), (40, 0, 40, 30), (40, 30, 0, 30), (0, 30, 0, 0)))
    zone = ""
    if keepout:
        x1, y1, x2, y2 = keepout
        zone = (f'\t(zone\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(keepout (tracks not_allowed))\n'
                f'\t\t(polygon\n\t\t\t(pts (xy {x1} {y1}) (xy {x2} {y1}) (xy {x2} {y2}) (xy {x1} {y2}))\n'
                f'\t\t)\n\t)\n')
    return "(kicad_pcb\n" + edge + "".join(footprints) + zone + ")\n"


def _fp(ref, x, y, pads, half=1.0, lib="L"):
    text = _footprint(ref, x, y, pads).replace('\t(footprint "L:', f'\t(footprint "{lib}:', 1)
    return text[:-3] + _court(half) + "\t)\n"


MCU_NETS = {"VCC": [("U1", "8", "POWER-IN"), ("C1", "1", "PASSIVE")],
            "GND": [("U1", "4", "POWER-IN"), ("C1", "2", "PASSIVE")]}


def _mcu_board(cap_x):
    return _placed(
        _fp("U1", 20, 15, [_pad("8", "VCC", 2, -1.5), _pad("4", "GND", -2, 1.5), _pad("1", "X", -2, -1.5)], 3),
        _fp("C1", cap_x, 13.5, [_pad("1", "VCC", -0.5, 0), _pad("2", "GND", 0.5, 0)]))


def _placement(board, nets):
    return checks.placement(boardgeom.read(board), _netlist(nets))


def test_a_decoupling_cap_far_from_its_ic_warns():
    warned = _placement(_mcu_board(30), MCU_NETS)
    assert any(w.startswith("placement: C1 (decoupling U1 pin 8, VCC) is 7.5 mm away; the rule is 3 mm")
               for w in warned), warned


def test_a_decoupling_cap_next_to_its_ic_does_not():
    assert _placement(_mcu_board(23), MCU_NETS) == []


def test_a_regulators_output_cap_and_a_connectors_bulk_cap_are_where_they_belong():
    """Live boards (2026-09-29): a regulator's output cap beside its VO pin,
    and a bulk cap at the USB connector, were reported 38 mm and 8 mm from
    the nearest load's power pin. Next to any pin on its supply is right."""
    board = _placed(
        _fp("U1", 5, 15, [_pad("2", "+3V3", 1, 0), _pad("1", "GND", -1, 0), _pad("3", "VBUS", 0, 1)], 2),
        _fp("C2", 8, 15, [_pad("1", "+3V3", -0.5, 0), _pad("2", "GND", 0.5, 0)]),
        _fp("U2", 35, 15, [_pad("3", "+3V3", 0, 0), _pad("1", "GND", 0, 1), _pad("9", "X", 1, 0)], 2),
        _fp("J1", 2, 25, [_pad("A4", "VBUS", 0, 0), _pad("A1", "GND", 0, 1)], 1.5, lib="Connector_USB"),
        _fp("C1", 4, 25, [_pad("1", "VBUS", -0.5, 0), _pad("2", "GND", 0.5, 0)]))
    nets = {"+3V3": [("U1", "2", "POWER-OUT"), ("C2", "1", "PASSIVE"), ("U2", "3", "POWER-IN")],
            "VBUS": [("U1", "3", "POWER-IN"), ("J1", "A4", "PASSIVE"), ("C1", "1", "PASSIVE")],
            "GND": [("U1", "1", "POWER-IN"), ("C2", "2", "PASSIVE"), ("U2", "1", "POWER-IN"),
                    ("J1", "A1", "PASSIVE"), ("C1", "2", "PASSIVE")]}
    assert [w for w in _placement(board, nets) if w.startswith(("placement: C1", "placement: C2"))] == []


def test_a_crystal_far_from_its_mcu_warns_and_a_near_one_does_not():
    def board(x):
        return _placed(
            _fp("U1", 20, 15, [_pad("2", "XTAL1", -2, 0), _pad("3", "XTAL2", -2, 1),
                               _pad("8", "VCC", 2, 0)], 3),
            _fp("Y1", x, 15, [_pad("1", "XTAL1", 0, 0), _pad("2", "XTAL2", 0, 1)]))
    nets = {"XTAL1": [("U1", "2", "INPUT"), ("Y1", "1", "PASSIVE")],
            "XTAL2": [("U1", "3", "OUTPUT"), ("Y1", "2", "PASSIVE")]}
    warned = _placement(board(8), nets)
    assert any(w.startswith("placement: Y1 (crystal, XTAL1 to U1 pin 2) is 10.0 mm away") for w in warned), warned
    assert _placement(board(15), nets) == []


def test_a_connector_away_from_the_edge_warns_and_one_on_it_does_not():
    def board(x):
        return _placed(_fp("J1", x, 15, [_pad("1", "SIG", 0, 0)], 2, lib="Connector_USB"),
                       _fp("R1", 20, 15, [_pad("1", "SIG", 0, 0)]))
    nets = {"SIG": [("J1", "1", "PASSIVE"), ("R1", "1", "PASSIVE")]}
    warned = _placement(board(10), nets)
    assert any(w.startswith("placement: J1 (connector) is 8.0 mm from the nearest board edge") for w in warned), warned
    assert _placement(board(2.5), nets) == []


def test_a_part_inside_an_antenna_keepout_warns():
    board = _placed(_fp("U1", 20, 5, [_pad("1", "SIG", 0, 0)], 3, lib="RF_Module"),
                    _fp("R1", 22, 4, [_pad("1", "SIG", 0, 0)]), keepout=(10, 0, 30, 8))
    warned = _placement(board, {"SIG": [("U1", "1", "PASSIVE"), ("R1", "1", "PASSIVE")]})
    assert any(w.startswith("placement: R1 is inside a keepout") for w in warned), warned


def test_placement_warnings_are_logged():
    answer = "PASS — placed\nplacement checks: 2 warning(s)\n  - placement: C1 ..."
    assert events.metrics(answer)["placement_warnings"] == 2


# --- in run_layout ----------------------------------------------------------------------

def test_a_placement_only_layout_reports_the_rules_before_any_routing(workspace):
    (workspace / "board.py").write_text(
        "from skidl import *\n"
        "u = Part('MCU_Microchip_ATtiny', 'ATtiny85-20S', ref='U1',\n"
        "         footprint='Package_SO:SOIC-8_5.3x5.3mm_P1.27mm')\n"
        "c = Part('Device', 'C', ref='C1', footprint='Capacitor_SMD:C_0805_2012Metric')\n"
        "vcc, gnd = Net('VCC'), Net('GND')\n"
        "vcc += u[8], c[1]; gnd += u[4], c[2]\n"
        "NC += u[1], u[2], u[3], u[5], u[6], u[7]\n"
        "generate_netlist(file_='board.net')\n")
    assert tools.run_skidl("board.py").startswith("netlist: board.net")

    out = tools.run_layout("board.net", 40, 30, route=False,
                           positions={"U1": [10, 15, 0], "C1": [32, 15, 0]})

    assert "placement checks: 1 warning(s)" in out, out
    assert "placement: C1 (decoupling U1 pin" in out, out
