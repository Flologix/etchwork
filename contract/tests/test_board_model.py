import pytest

from contract.board import Board, BoardFormatError
from contract.tests.boards import fp, make, pad, rect


def test_outline_bbox_and_size():
    o = make(outline=rect(37.0, 23.4, x=5.0, y=2.0)).outline()
    assert o.bbox == (5.0, 2.0, 42.0, pytest.approx(25.4))
    assert o.width == pytest.approx(37.0)
    assert o.height == pytest.approx(23.4)


def test_missing_outline_has_no_bbox():
    o = make(outline=[]).outline()
    assert o.bbox is None
    assert o.width == 0.0 and o.height == 0.0


def test_nets_group_pads_and_skip_unconnected():
    b = make(pads=[pad("R1", 1, "VCC"), pad("C1", 1, "VCC"),
                   pad("R1", 2, ""), pad("C1", 2, "GND")])
    assert b.nets() == {"VCC": frozenset({("R1", "1"), ("C1", "1")}),
                        "GND": frozenset({("C1", "2")})}


def test_duplicate_pad_numbers_collapse_in_nets():
    # A coin-cell holder has two pads both numbered 1.
    b = make(pads=[pad("BT1", 1, "VCC", pos=(0, 0)), pad("BT1", 1, "VCC", pos=(9, 0))])
    assert b.nets()["VCC"] == frozenset({("BT1", "1")})
    assert len(b.pads()) == 2


def test_footprint_fields():
    b = make(footprints=[fp("R1", "330k", "Resistor_SMD:R_0603_1608Metric", pos=(1, 2))])
    (f,) = b.footprints()
    assert (f.ref, f.value, f.lib_id, f.pos, f.pad_count) == (
        "R1", "330k", "Resistor_SMD:R_0603_1608Metric", (1.0, 2.0), 2)


def test_pad_names_are_a_tuple():
    b = make(pads=[pad("U1", 2, "NET3", names=["OSC"])])
    assert b.pads()[0].names == ("OSC",)


def test_tracks_and_zones():
    b = make(tracks=[{"layer": "F.Cu", "width": 0.25, "start": [0, 0],
                      "end": [1, 0], "net": "VCC"}],
             zones=[{"layers": ["F.Cu", "B.Cu"], "net": "GND", "outline": rect(10, 10),
                     "filled": True, "rule_area": False}])
    (t,) = b.tracks()
    assert t.width == 0.25 and t.end == (1.0, 0.0)
    (z,) = b.zones()
    assert z.layers == ("F.Cu", "B.Cu") and z.filled and not z.rule_area


def test_nets_returns_a_copy():
    b = make(pads=[pad("R1", 1, "VCC")])
    b.nets().clear()
    assert "VCC" in b.nets()


def test_malformed_dump_is_a_format_error():
    with pytest.raises(BoardFormatError):
        Board({"outline": [], "footprints": [{"ref": "R1"}], "pads": [],
               "tracks": [], "zones": []})
    with pytest.raises(BoardFormatError):
        Board({"footprints": []})
