"""Board checks, phase B (docs/superpowers/specs/2026-09-29-board-checks-design.md
§5): every routed net measured -- length, vias, narrowest width, path
resistance, voltage drop -- and checked against its class. Every check warns;
none blocks DONE (§3.4)."""
import json
import math
import shutil

import pytest

from circuit_mcp import boardgeom, checks, done, events, paths, service, summary, tools
from conftest import FIXTURES


# --- the geometry reader, against pcbnew itself -----------------------------------

_PCBNEW_PADS = r"""
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
pads = [[fp.GetReference(), p.GetNumber(), round(pcbnew.ToMM(p.GetPosition().x), 4),
         round(pcbnew.ToMM(p.GetPosition().y), 4)] for fp in board.GetFootprints() for p in fp.Pads()]
tracks = {}
for t in board.GetTracks():
    if t.GetClass() == "PCB_TRACK":
        tracks[t.GetNetname()] = tracks.get(t.GetNetname(), 0) + pcbnew.ToMM(t.GetLength())
print(json.dumps({"pads": pads, "tracks": tracks}))
"""


def test_pad_positions_and_track_lengths_match_pcbnews(workspace):
    """release2 has footprints turned 90, 180 and -90 degrees."""
    board = FIXTURES / "boards" / "release2.kicad_pcb"
    code, out = service.python(_PCBNEW_PADS, str(board))
    truth = json.loads(out.strip().splitlines()[-1])
    geometry = boardgeom.read(board.read_text())
    mine, theirs = {}, {}
    for p in geometry.pads:              # several unnumbered pads (mounting holes) per part
        mine.setdefault((p.ref, p.number), []).append((p.x, p.y))
    for ref, number, x, y in truth["pads"]:
        theirs.setdefault((ref, number), []).append((x, y))
    assert mine.keys() == theirs.keys()
    for key, points in theirs.items():
        assert sorted(mine[key]) == pytest.approx(sorted(points), abs=1e-3), key
    lengths = {}
    for s in geometry.segments:
        lengths[s.net] = lengths.get(s.net, 0) + s.length
    for net, length in truth["tracks"].items():
        assert lengths[net] == pytest.approx(length, abs=1e-3), net


# --- a small board, written here ---------------------------------------------------

def _pad(number, net, x, y, size=1.0, layers='"F.Cu" "F.Mask"'):
    return (f'\t\t(pad "{number}" smd rect\n\t\t\t(at {x} {y})\n\t\t\t(size {size} {size})\n'
            f'\t\t\t(layers {layers})\n\t\t\t(net "{net}")\n\t\t)\n')


def _footprint(ref, x, y, pads, rotation=0):
    at = f"{x} {y}" + (f" {rotation}" if rotation else "")
    body = "".join(pads)
    return (f'\t(footprint "L:{ref}"\n\t\t(layer "F.Cu")\n\t\t(at {at})\n'
            f'\t\t(property "Reference" "{ref}")\n{body}\t)\n')


def _segment(net, x1, y1, x2, y2, width=0.25, layer="F.Cu"):
    return (f'\t(segment\n\t\t(start {x1} {y1})\n\t\t(end {x2} {y2})\n\t\t(width {width})\n'
            f'\t\t(layer "{layer}")\n\t\t(net "{net}")\n\t)\n')


def _via(net, x, y):
    return f'\t(via\n\t\t(at {x} {y})\n\t\t(size 0.6)\n\t\t(drill 0.3)\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "{net}")\n\t)\n'


def _board(*items):
    return "(kicad_pcb\n" + "".join(items) + ")\n"


def _netlist(nets):
    """nets: {name: [(ref, pin, pintype)]}."""
    refs = sorted({ref for nodes in nets.values() for ref, _, _ in nodes})
    comps = "".join(f'(comp (ref "{r}") (value "x") (footprint "L:{r}"))' for r in refs)
    body = "".join(
        f'(net (code "{i}") (name "{name}") '
        + "".join(f'(node (ref "{r}") (pin "{p}") (pintype "{t}"))' for r, p, t in nodes) + ")"
        for i, (name, nodes) in enumerate(nets.items(), 1))
    return f"(export (components {comps}) (nets {body}))"


def _classes(**nets):
    return {name: {"class": cls, "tier": tier} for name, (cls, tier) in nets.items()}


def _measure(board, nets, classes):
    return checks.measure(boardgeom.read(board), _netlist(nets), classes)


def test_a_net_is_measured_length_vias_and_narrowest_width():
    board = _board(_footprint("U1", 0, 0, [_pad("1", "SIG", 0, 0)]),
                   _footprint("U2", 30, 0, [_pad("1", "SIG", 0, 0)]),
                   _segment("SIG", 0, 0, 10, 0, 0.25), _via("SIG", 10, 0),
                   _segment("SIG", 10, 0, 30, 0, 0.2, "B.Cu"))
    facts = _measure(board, {"SIG": [("U1", "1", "output"), ("U2", "1", "input")]},
                     _classes(SIG=("signal", "lt_0_5a")))["SIG"]
    assert facts.length == pytest.approx(30.0)
    assert (facts.vias, facts.narrowest) == (1, 0.2)


def test_resistance_is_copper_at_one_ounce():
    """0.25 mm x 35 um copper: 1.92 milliohm per mm."""
    assert checks.resistance_mohm(10.0, 0.25) == pytest.approx(19.2, abs=0.05)


def _rail(length, width=0.25, loads=((10, 0),), tier="lt_0_5a", name="+3V3", source="power_out"):
    items = [_footprint("U1", 0, 0, [_pad("2", name, 0, 0)])]
    nets = {name: [("U1", "2", source)]}
    for i, (x, y) in enumerate(loads, 2):
        items.append(_footprint(f"U{i}", x, y, [_pad("1", name, 0, 0)]))
        nets[name].append((f"U{i}", "1", "power_in"))
    x0 = 0
    for x, y in loads:
        items.append(_segment(name, x0, 0, x, 0, width))
        x0 = x
    classes = _classes(**{name: ("power_logic", tier)})
    return _board(*items), nets, classes


def test_a_power_rails_drop_is_measured_to_its_farthest_load():
    board, nets, classes = _rail(0, loads=((20, 0), (100, 0)))
    facts = _measure(board, nets, classes)["+3V3"]
    assert facts.path_mohm == pytest.approx(192, abs=0.5)          # 100 mm of 0.25 mm
    assert facts.drop_mv == pytest.approx(96, abs=0.5)              # at 0.5 A
    assert facts.farthest == "U3 pin 1"


def test_skidls_pin_type_spelling_is_understood():
    """SKiDL's netlists say POWER-OUT / POWER-IN; on the first real boards the
    drop was never computed because only power_out / power_in were matched."""
    board, nets, classes = _rail(0, loads=((100, 0),))
    nets = {n: [(r, p, t.upper().replace("_", "-")) for r, p, t in nodes] for n, nodes in nets.items()}
    assert _measure(board, nets, classes)["+3V3"].drop_mv == pytest.approx(96, abs=0.5)


def test_a_drop_over_two_percent_of_the_rail_warns():
    board, nets, classes = _rail(0, loads=((100, 0),))
    warned = checks.warnings(_measure(board, nets, classes), None)
    assert any(w.startswith("power: +3V3") and "2.9 %" in w and "3.3 V" in w for w in warned), warned


def test_a_small_drop_does_not_warn():
    board, nets, classes = _rail(0, loads=((20, 0),))
    assert checks.warnings(_measure(board, nets, classes), None) == []


def test_a_rail_with_no_regulator_is_fed_from_its_connector():
    board, nets, classes = _rail(0, loads=((20, 0),), name="VBUS", source="passive")
    nets = {"VBUS": [("J1", "2", "passive") if r == "U1" else (r, p, t) for r, p, t in nets["VBUS"]]}
    board = board.replace('(property "Reference" "U1")', '(property "Reference" "J1")')
    facts = _measure(board, nets, classes)["VBUS"]
    assert facts.drop_mv == pytest.approx(19.2, abs=0.5)


def test_a_poured_net_is_not_given_a_track_only_drop():
    board, nets, classes = _rail(0, loads=((100, 0),), name="GND")
    pour = '\t(zone\n\t\t(net "GND")\n\t\t(layer "B.Cu")\n\t\t(filled_polygon\n\t\t\t(pts (xy 0 0))\n\t\t)\n\t)\n'
    board = board[:-2] + pour + ")\n"
    facts = _measure(board, nets, classes)["GND"]
    assert facts.drop_mv is None and "pour" in facts.note


def test_a_long_crystal_net_warns_and_a_short_one_does_not():
    def crystal(length):
        board = _board(_footprint("U1", 0, 0, [_pad("7", "XTAL1", 0, 0)]),
                       _footprint("Y1", length, 0, [_pad("1", "XTAL1", 0, 0)]),
                       _segment("XTAL1", 0, 0, length, 0))
        return checks.warnings(_measure(board, {"XTAL1": [("U1", "7", "input"), ("Y1", "1", "passive")]},
                                        _classes(XTAL1=("crystal", "lt_0_5a"))), None)
    assert any("crystal: XTAL1 is 15.0 mm" in w for w in crystal(15))
    assert crystal(8) == []


def test_a_diff_pair_out_of_match_warns():
    def pair(other):
        board = _board(_footprint("J1", 0, 0, [_pad("A6", "USB_DP", 0, 0), _pad("A7", "USB_DN", 0, 2)]),
                       _footprint("U1", 20, 0, [_pad("13", "USB_DP", 0, 0), _pad("12", "USB_DN", 0, 2)]),
                       _segment("USB_DP", 0, 0, 20, 0), _segment("USB_DN", 0, 2, 20 + other, 2),
                       _segment("USB_DN", 20 + other, 2, 20, 2))
        nets = {"USB_DP": [("J1", "A6", "bidirectional"), ("U1", "13", "bidirectional")],
                "USB_DN": [("J1", "A7", "bidirectional"), ("U1", "12", "bidirectional")]}
        return checks.warnings(_measure(board, nets, _classes(USB_DP=("diff_pair", "lt_0_5a"),
                                                              USB_DN=("diff_pair", "lt_0_5a"))), None)
    warned = pair(2.5)
    assert any(w.startswith("diff pair: USB_DP") and "5.00 mm apart" in w for w in warned), warned
    assert pair(0.4) == []


def test_a_clearance_shortfall_is_reported_with_its_size():
    report = {"violations": [
        {"type": "clearance", "severity": "error",
         "description": "Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.1990 mm)",
         "items": [{"description": "Track [MISO] on F.Cu"}, {"description": "Pad 1 [MOSI] of U1 on F.Cu"}]}]}
    warned = checks.warnings({}, report)
    assert any("clearance: 1 violation(s), worst 0.001 mm short" in w and "U1" in w for w in warned), warned


# --- where it shows ------------------------------------------------------------------

def test_run_layout_reports_the_board_checks(workspace):
    """A real layout, routed: two resistors in series off a 3-pin header."""
    (workspace / "board.py").write_text(
        "from skidl import *\n"
        "R = 'Resistor_SMD:R_0805_2012Metric'\n"
        "r1 = Part('Device', 'R', ref='R1', footprint=R)\n"
        "r2 = Part('Device', 'R', ref='R2', footprint=R)\n"
        "j = Part('Connector_Generic', 'Conn_01x03', ref='J1',\n"
        "         footprint='Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical')\n"
        "a, b, c = Net('A'), Net('B'), Net('C')\n"
        "a += j[1], r1[1]; b += r1[2], r2[1]; c += r2[2], j[3]\n"
        "NC += j[2]\n"
        "generate_netlist(file_='board.net')\n")
    assert tools.run_skidl("board.py").startswith("netlist: board.net")

    out = tools.run_layout("board.net", 30, 20,
                           positions={"J1": [6, 10, 0], "R1": [16, 7, 0], "R2": [16, 13, 0]})

    line = next((l for l in out.splitlines() if l.startswith("board checks:")), None)
    assert line and "3 routed nets" in line and "0 warning(s)" in line, out


def test_check_done_and_the_readme_carry_the_board_checks(workspace):
    project = FIXTURES / "projects" / "backoff1"
    shutil.copytree(project, workspace, dirs_exist_ok=True)
    summary.summarize_project("board.net")
    readme = (workspace / "README.md").read_text()
    assert "## Board checks" in readme and "| Net |" in readme.split("## Board checks")[1]
    assert "rule of thumb" in readme.split("## Board checks")[1]


def test_the_warning_count_is_logged():
    answer = "PASS — DRC clean, 0 unconnected\nboard checks: 12 routed nets, 180 mm of track, 4 vias; 2 warning(s)"
    assert events.metrics(answer)["board_check_warnings"] == 2
