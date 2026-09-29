"""make_part (datasheet-parts design §5.2, §6): a footprint computed from a
part spec, checked against IPC nominal and the fab profile -- routability
included -- and written only when every check passes."""
import math
import re
import shutil

import pytest

from circuit_mcp import events, makepart, parts, partspec
from conftest import FIXTURES
from test_partspec import ODU, _edit

SPEC = "parts/GK1YAR-P16UC00-000L.part.toml"
HAND_DRAWN = FIXTURES / "kicad" / "ODU.pretty" / "ODU_AMC_GK1YAR-P16UC00-000L.kicad_mod"


@pytest.fixture
def odu(workspace):
    (workspace / "parts").mkdir()
    (workspace / "datasheets").mkdir()
    (workspace / "datasheets" / "GK1YAR-P16UC00-000L.pdf").write_bytes(b"%PDF-1.4\n")
    shutil.copy(ODU, workspace / SPEC)
    return workspace / SPEC


def _load(path):
    spec, problems = partspec.load(SPEC)
    assert problems == [], problems
    return spec


def _ring_part(d):
    """A part that routes: one ring of 8 pins, Ø10 mm, drills for a 0.8 mm lead."""
    d["package"]["rings"] = [{"diameter_mm": 10.0, "count": 8, "start_deg": 90,
                              "direction": "cw", "first_pin": 1, "drill_mm": 1.0,
                              "source": "p.2"}]
    d["package"].pop("slots")
    d["pins"] = [{"number": str(n), "name": f"P{n}", "type": "passive"} for n in range(1, 9)]
    d["fab"] = {"min_lead_mm": 0.8, "source": "p.1"}
    d["name"] = "RING8"


def _pad(pads, number):
    return next(p for p in pads if p["number"] == number)


# --- geometry -------------------------------------------------------------------

def test_a_ring_matches_the_hand_drawn_odu_footprint_in_top_view(odu):
    """The live run's trigonometry, done by the model, now computed: same
    outer ring, pad for pad (the drawing's inner ring started elsewhere)."""
    _edit(odu, lambda d: d["package"].update(view="top"))
    pads = makepart.pads(_load(odu))
    drawn = {m[0]: (float(m[1]), float(m[2])) for m in re.findall(
        r'\(pad "(\d+)" \S+ \S+ \(at ([-\d.]+) ([-\d.]+)\)', HAND_DRAWN.read_text())}
    for n in range(1, 12):
        pad = _pad(pads, str(n))
        assert (pad["x"], pad["y"]) == pytest.approx(drawn[str(n)], abs=1e-3), n


def test_a_bottom_view_is_mirrored_into_the_top_view_footprints_are_drawn_in(odu):
    spec = _load(odu)
    bottom = makepart.pads(spec)
    spec["package"]["view"] = "top"
    top = makepart.pads(spec)
    for a, b in zip(top, bottom):
        assert (a["number"], -a["x"], a["y"]) == pytest.approx((b["number"], b["x"], b["y"]))


def test_counter_clockwise_numbers_the_other_way_round(odu):
    spec = _load(odu)
    spec["package"]["view"] = "top"
    spec["package"]["rings"][0]["direction"] = "ccw"
    assert _pad(makepart.pads(spec), "2")["x"] < 0


def test_pin_1_is_the_square_pad(odu):
    pads = makepart.pads(_load(odu))
    assert _pad(pads, "1")["shape"] == "rect"
    assert {p["shape"] for p in pads if p["number"] not in ("1", "17")} == {"circle"}


def test_a_pad_size_the_datasheet_leaves_out_is_ipc_nominal(odu):
    pads = makepart.pads(_load(odu))
    assert _pad(pads, "2")["size"] == pytest.approx((0.6 + 0.60, 0.6 + 0.60))


def test_a_pad_size_the_datasheet_gives_is_used(odu):
    _edit(odu, lambda d: d["package"]["rings"][0].update(pad_mm=0.85))
    assert _pad(makepart.pads(_load(odu)), "2")["size"] == pytest.approx((0.85, 0.85))


def test_a_slot_is_an_oval_hole_in_an_oval_pad(odu):
    slot = _pad(makepart.pads(_load(odu)), "17")
    assert slot["shape"] == "oval"
    assert slot["drill"] == pytest.approx((1.9, 0.65))
    assert slot["size"] == pytest.approx((1.9 + 0.60, 0.65 + 0.60))


# --- the checks ------------------------------------------------------------------

def test_ipc_nominal_pads_that_crowd_each_other_are_refused_with_the_way_out(odu, workspace):
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED"), out
    assert "pads 1 and 2" in out and "pad_mm" in out, out
    assert not (workspace / "fp").exists()


def test_pads_that_overlap_are_said_to_overlap(odu):
    out = makepart.make_part(SPEC)
    assert "pads 1 and 12 overlap" in out, out
    assert "0.00 mm apart" not in out, out


def test_the_odu_inner_ring_is_refused_as_unroutable_with_the_arithmetic(odu, workspace):
    """Design §2.2: between two outer drills there is 0.631 mm; one 0.10 mm
    track needs 0.10 + 2 x 0.28 (JLCPCB's PTH hole clearance) = 0.66 mm."""
    _edit(odu, lambda d: d["package"]["rings"][0].update(pad_mm=0.85))
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED"), out
    assert "cannot be routed out" in out and "5 pads" in out, out
    assert "0.631" in out and "0.28" in out, out
    assert "via-in-pad" in out or "layers" in out, out
    assert not (workspace / "fp").exists()


def test_a_routable_ring_part_is_made_and_the_tools_can_use_it(odu, workspace):
    _edit(odu, _ring_part)
    out = makepart.make_part(SPEC)
    assert out.startswith("MADE"), out
    made = workspace / "fp" / "ODU.pretty" / "RING8.kicad_mod"
    assert made.is_file()
    assert "8 electrical pads" in parts.describe_footprint("ODU:RING8")
    text = made.read_text()
    assert 'generator "etchwork-make-part"' in text
    assert '"F.CrtYd"' in text and '"F.SilkS"' in text and '"F.Fab"' in text


def test_the_made_footprint_is_rendered_for_comparison_with_the_datasheet(odu, workspace):
    _edit(odu, _ring_part)
    out = makepart.make_part(SPEC)
    svg = workspace / "parts" / "RING8.footprint.svg"
    assert svg.is_file() and svg.read_text().lstrip().startswith("<?xml"), out
    assert "parts/RING8.footprint.svg" in out


def test_the_courtyard_is_the_pads_plus_the_nominal_excess(odu):
    _edit(odu, _ring_part)
    spec = _load(odu)
    pads = makepart.pads(spec)
    reach = max(math.hypot(p["x"], p["y"]) + p["size"][0] / 2 for p in pads)
    assert makepart.courtyard(spec, pads) == pytest.approx((-reach - 0.25, -reach - 0.25,
                                                            reach + 0.25, reach + 0.25))


def test_what_the_datasheet_did_not_state_is_listed_for_the_human(odu):
    _edit(odu, _ring_part)
    out = makepart.make_part(SPEC)
    assert "awaiting your verification" in out and "numbering" in out, out


def test_a_drill_the_lead_cannot_pass_through_is_refused(odu):
    def change(d):
        _ring_part(d)
        d["fab"]["min_lead_mm"] = 1.0
    _edit(odu, change)
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED") and "lead" in out, out


def test_a_drill_tighter_than_ipc_nominal_is_made_with_a_note(odu):
    def change(d):
        _ring_part(d)
        d["fab"]["min_lead_mm"] = 0.9       # nominal hole 1.1; the datasheet says 1.0
    _edit(odu, change)
    out = makepart.make_part(SPEC)
    assert out.startswith("MADE") and "tighter than IPC-7251 nominal" in out, out


def test_a_drill_below_the_fabs_standard_is_refused(odu):
    def change(d):
        _ring_part(d)
        d["package"]["rings"][0].update(drill_mm=0.25, pad_mm=0.8)
        d["fab"]["min_lead_mm"] = 0.02
    _edit(odu, change)
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED") and "0.3" in out and "drill" in out, out


def test_an_annular_ring_the_fab_cannot_make_is_refused(odu):
    def change(d):
        _ring_part(d)
        d["package"]["rings"][0].update(pad_mm=1.1)     # (1.1 - 1.0) / 2 = 0.05
    _edit(odu, change)
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED") and "annular ring" in out, out


def test_holes_too_close_together_are_refused(odu):
    def change(d):
        _ring_part(d)
        d["package"]["rings"][0].update(diameter_mm=3.0, pad_mm=1.1, drill_mm=1.0)
    _edit(odu, change)
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED") and "drill to drill" in out, out


def test_a_spec_with_problems_is_refused_with_them_and_nothing_is_written(odu, workspace):
    _edit(odu, lambda d: d["package"].pop("view"))
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED") and "view" in out, out
    assert not (workspace / "fp").exists()


def test_a_footprint_make_part_did_not_write_is_never_replaced(odu, workspace):
    """The live run hand-drew fp/ODU.pretty/... before make_part existed."""
    _edit(odu, lambda d: (_ring_part(d), d.update(name="ODU_AMC_GK1YAR-P16UC00-000L")))
    target = workspace / "fp" / "ODU.pretty" / "ODU_AMC_GK1YAR-P16UC00-000L.kicad_mod"
    target.parent.mkdir(parents=True)
    shutil.copy(HAND_DRAWN, target)
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED") and "not made by make_part" in out, out
    assert target.read_text() == HAND_DRAWN.read_text()


def test_its_own_footprint_is_replaced_on_a_rerun(odu, workspace):
    _edit(odu, _ring_part)
    makepart.make_part(SPEC)
    _edit(odu, lambda d: d["package"]["rings"][0].update(count=6) or d.update(
        pins=[p for p in d["pins"] if int(p["number"]) <= 6]))
    assert makepart.make_part(SPEC).startswith("MADE")
    assert "6 electrical pads" in parts.describe_footprint("ODU:RING8")


def test_an_explicit_pad_list_is_made_with_smd_and_through_hole_pads(odu, workspace):
    def change(d):
        d["package"] = {"family": "pads", "view": "top", "source": "p.3, land pattern"}
        d.pop("numbering")
        d["name"] = "TWO_PLUS_TAB"
        d["pads"] = [
            {"number": "1", "at_mm": [-2.54, 0], "size_mm": [1.7, 1.7], "drill_mm": 1.0,
             "shape": "rect", "source": "p.3"},
            {"number": "2", "at_mm": [0, 0], "size_mm": [1.7, 1.7], "drill_mm": 1.0,
             "source": "p.3"},
            {"number": "3", "at_mm": [3.5, 0], "size_mm": [2.0, 3.0], "shape": "roundrect",
             "source": "p.3"}]
        d["pins"] = [{"number": str(n), "name": f"P{n}", "type": "passive"} for n in (1, 2, 3)]
        d["fab"] = {"min_lead_mm": 0.64, "source": "p.1"}
    _edit(odu, change)
    out = makepart.make_part(SPEC)
    assert out.startswith("MADE"), out
    text = (workspace / "fp" / "ODU.pretty" / "TWO_PLUS_TAB.kicad_mod").read_text()
    assert re.search(r'\(pad "3" smd roundrect .*\(layers "F.Cu" "F.Paste" "F.Mask"\)', text), text
    assert re.search(r'\(pad "1" thru_hole rect', text), text
    assert "3 electrical pads" in parts.describe_footprint("ODU:TWO_PLUS_TAB")


@pytest.mark.parametrize("answer, outcome", [
    ("MADE fp/ODU.pretty/RING8.kicad_mod: 8 pads", "ok"),
    ("REFUSED:\n- the 5 pads inside ring 1 cannot be routed out", "refused"),
])
def test_make_part_answers_are_logged_with_the_right_outcome(answer, outcome):
    assert events.outcome(answer) == outcome
