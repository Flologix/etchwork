"""Board checks, phase C (docs/superpowers/specs/2026-09-29-board-checks-design.md
§6): EMC. A trace radiates according to its signal's edge time and the loop
its return current takes, so the checks are: a fast net over its ground
plane, not crossing a gap in it, not electrically long; and no stubs or
copper islands. Every check warns; none blocks DONE."""
import json

import pytest

from circuit_mcp import boardgeom, checks, service
from conftest import FIXTURES
from test_board_extraction import _footprint, _netlist, _pad, _segment


# --- ground fills, against pcbnew's own hit test ---------------------------------

_PCBNEW_HITS = r"""
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
points = json.loads(sys.argv[2])
out = []
for layer_name, x, y in points:
    layer = board.GetLayerID(layer_name)
    p = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
    out.append(any(z.GetNetname() == "GND" and z.IsOnLayer(layer) and z.HitTestFilledArea(layer, p)
                   for z in board.Zones()))
print(json.dumps(out))
"""


def test_a_point_is_on_the_ground_fill_exactly_when_pcbnew_says_so():
    board = FIXTURES / "projects" / "backoff1" / "board.kicad_pcb"
    geometry = boardgeom.read(board.read_text())
    left, top, right, bottom = geometry.outline
    points = [(layer, round(left + (right - left) * i / 23, 3), round(top + (bottom - top) * j / 17, 3))
              for layer in ("F.Cu", "B.Cu") for i in range(1, 23) for j in range(1, 17)]
    code, out = service.python(_PCBNEW_HITS, str(board), json.dumps(points))
    truth = json.loads(out.strip().splitlines()[-1])
    mine = [checks.on_fill(geometry, "GND", layer, x, y) for layer, x, y in points]
    assert sum(truth) > 50                                   # the board is poured
    assert mine == truth


# --- a small board, written here ---------------------------------------------------

def _fill(layer, box, holes=()):
    """A GND fill on `layer` covering `box`, minus rectangular `holes`, as
    KiCad writes it: one fractured outline per filled region."""
    x1, y1, x2, y2 = box
    regions = []
    xs = [x1] + [c for h in holes for c in (h[0], h[2])] + [x2]
    for a, b in zip(xs[::2], xs[1::2]):
        regions.append((a, y1, b, y2))
    body = "".join(f'\t\t(filled_polygon\n\t\t\t(layer "{layer}")\n\t\t\t(pts (xy {a} {c}) (xy {b} {c}) '
                   f'(xy {b} {d}) (xy {a} {d}))\n\t\t)\n' for a, c, b, d in regions)
    return f'\t(zone\n\t\t(net "GND")\n\t\t(layer "{layer}")\n{body}\t)\n'


def _emc(board, nets, classes=None, drc=None):
    geometry = boardgeom.read(board)
    return checks.emc(geometry, _netlist(nets), classes or {}, drc)


def _clock_board(length=30, fill=None):
    items = [_footprint("U1", 5, 10, [_pad("1", "SPI_SCK", 0, 0)]),
             _footprint("U2", 5 + length, 10, [_pad("1", "SPI_SCK", 0, 0)]),
             _segment("SPI_SCK", 5, 10, 5 + length, 10)]
    if fill:
        items.append(fill)
    return "(kicad_pcb\n" + "".join(items) + ")\n"


SCK = {"SPI_SCK": [("U1", "1", "OUTPUT"), ("U2", "1", "INPUT")],
       "GND": [("U1", "2", "POWER-IN")]}


def test_a_fast_net_over_solid_ground_is_fine():
    assert _emc(_clock_board(fill=_fill("B.Cu", (0, 0, 60, 20))), SCK) == []


def test_a_fast_net_mostly_off_its_ground_plane_warns():
    warned = _emc(_clock_board(fill=_fill("B.Cu", (0, 0, 20, 20))), SCK)
    assert any(w.startswith("emc: SPI_SCK runs over ground for 50 % of its length") for w in warned), warned


def test_a_fast_net_crossing_a_gap_in_the_pour_warns():
    """The return current under it has to go round the slot: a loop."""
    warned = _emc(_clock_board(fill=_fill("B.Cu", (0, 0, 60, 20), holes=[(18, 0, 22, 20)])), SCK)
    assert any(w.startswith("emc: SPI_SCK crosses a 4.0 mm gap in the ground pour") for w in warned), warned


def test_a_short_break_at_an_antipad_is_not_a_gap():
    warned = _emc(_clock_board(fill=_fill("B.Cu", (0, 0, 60, 20), holes=[(19.5, 0, 20.5, 20)])), SCK)
    assert [w for w in warned if "gap" in w] == []


def test_an_electrically_long_fast_net_warns():
    """2 ns edges at 150 mm/ns: 50 mm is where a trace stops being short."""
    warned = _emc(_clock_board(length=80, fill=_fill("B.Cu", (0, 0, 100, 20))), SCK)
    assert any(w.startswith("emc: SPI_SCK is 80.0 mm, electrically long for a ~2 ns edge (over 50 mm)")
               for w in warned), warned


def test_a_slow_net_is_not_checked():
    board = _clock_board(fill=_fill("B.Cu", (0, 0, 20, 20))).replace("SPI_SCK", "LED_DRV")
    nets = {"LED_DRV": SCK["SPI_SCK"]}
    assert _emc(board, nets) == []


def test_i2c_is_not_a_fast_net():
    """I2C lines rise through pull-up resistors: the spec allows 1000 ns
    (standard mode) or 300 ns (fast mode), not the ~2 ns of a push-pull pin."""
    board = _clock_board(length=80, fill=_fill("B.Cu", (0, 0, 20, 20))).replace("SPI_SCK", "I2C_SCL")
    assert _emc(board, {"I2C_SCL": SCK["SPI_SCK"]}) == []


def test_a_diff_pair_is_fast_by_its_class():
    board = _clock_board(length=120, fill=_fill("B.Cu", (0, 0, 140, 20))).replace("SPI_SCK", "USB_DP")
    warned = _emc(board, {"USB_DP": SCK["SPI_SCK"]}, {"USB_DP": {"class": "diff_pair", "tier": "lt_0_5a"}})
    assert any(w.startswith("emc: USB_DP is 120.0 mm") for w in warned), warned


def test_stubs_and_islands_from_drc_are_reported():
    drc = {"violations": [
        {"type": "track_dangling", "severity": "warning", "description": "Track has unconnected end",
         "items": [{"description": "Track [SIG] on F.Cu, length 1.2 mm", "pos": {"x": 12.5, "y": 8.0}}]},
        {"type": "isolated_copper", "severity": "warning", "description": "Isolated copper fill",
         "items": [{"description": "Zone [GND] on B.Cu", "pos": {"x": 30.0, "y": 4.0}}]}]}
    warned = _emc("(kicad_pcb\n)\n", {}, drc=drc)
    assert any(w.startswith("emc: a dangling track") and "SIG" in w for w in warned), warned
    assert any(w.startswith("emc: an isolated copper island") and "GND" in w for w in warned), warned
