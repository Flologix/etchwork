"""The fab-profile eval, pre-registered in the spec's §6: six real projects
(board + .kicad_pro), DRC'd as saved and with the profile, plus a synthetic
outline board. DRC only, so it runs with the suite; the one test that routes
is opt-in (CIRCUIT_ROUTING_EVAL=1).

Spec: docs/superpowers/specs/2026-09-24-circuit-fab-profile-design.md.
"""
import json
import os
import re
import shutil

import pytest

from circuit_mcp import fab, kicad, service
from conftest import FIXTURES

PROJECTS = FIXTURES / "projects"
NAMES = ["release", "release2", "release3", "stop1", "test", "mycircuit7"]
# mycircuit7's agent passed rules itself; they are re-applied on top.
AGENT = {"mycircuit7": {"clearance_mm": 0.2, "edge_clearance_mm": 0.4,
                        "via_diameter_mm": 0.6, "annular_ring_mm": 0.15}}
_ACTUAL = re.compile(r"actual ([\d.]+) mm")


def _items(v):
    return [i.get("description", "") for i in v.get("items") or []]


def _npth(v):
    return any(i.startswith("NPTH pad") for i in _items(v))


def _copper(v):
    return any(i.startswith(("Track", "Arc")) for i in _items(v))


def _all_pth(v):
    items = _items(v)
    return bool(items) and all(i.startswith("PTH pad") for i in items)


def jlc(v, rules):
    """JLCPCB's number for the rule a violation belongs to, or None."""
    hole = rules.get("hole_clearance_mm")
    kind = v.get("type")
    if kind == "copper_edge_clearance":
        if _npth(v):
            return hole if hole is not None else fab.value("npth_hole_clearance_mm")
        edge = rules.get("edge_clearance_mm")
        return edge if edge is not None else fab.value("edge_clearance_mm")
    if kind == "hole_clearance":
        if _npth(v) and _copper(v):
            return hole if hole is not None else fab.value("npth_hole_clearance_mm")
        if any(i.startswith("PTH pad") for i in _items(v)) and _copper(v):
            return hole if hole is not None else fab.value("pth_hole_clearance_mm")
        return hole if hole is not None else fab.value("hole_clearance_mm")
    if kind == "clearance":
        clearance = rules.get("clearance_mm")
        return clearance if clearance is not None else fab.value("clearance_mm")
    if kind == "track_width":
        track = rules.get("track_width_mm")
        return track if track is not None else fab.value("track_width_mm")
    if kind == "via_diameter":
        via = rules.get("via_diameter_mm")
        return via if via is not None else fab.value("via_diameter_mm")
    if kind == "annular_width":
        ring = rules.get("annular_ring_mm")
        return ring if ring is not None else fab.value("annular_ring_mm")
    if kind == "hole_to_hole":
        h2h = rules.get("hole_to_hole_mm")
        if h2h is not None:
            return h2h
        return fab.value("pth_hole_to_hole_mm") if _all_pth(v) else fab.value("hole_to_hole_mm")
    if kind in ("drill_out_of_range", "hole_size"):
        drill = rules.get("through_hole_drill_mm")
        if drill is not None:
            return drill
        return fab.value("npth_min_drill_mm") if _npth(v) else fab.value("via_drill_mm")
    return None


# --- jlc() must have a number for every kind the profile actually sets ------

def _violation(kind, *item_descriptions):
    return {"type": kind, "items": [{"description": d} for d in item_descriptions]}


def test_jlc_covers_via_diameter():
    v = _violation("via_diameter", "Via")
    assert jlc(v, {}) == 0.45
    assert jlc(v, {"via_diameter_mm": 0.6}) == 0.6


def test_jlc_covers_annular_width():
    v = _violation("annular_width", "Via")
    assert jlc(v, {}) == 0.075
    assert jlc(v, {"annular_ring_mm": 0.15}) == 0.15


def test_jlc_covers_hole_to_hole_between_two_pth_pads():
    v = _violation("hole_to_hole", "PTH pad 1 of J1", "PTH pad 2 of J1")
    assert jlc(v, {}) == 0.45
    assert jlc(v, {"hole_to_hole_mm": 0.3}) == 0.3


def test_jlc_covers_hole_to_hole_when_not_both_pth():
    v = _violation("hole_to_hole", "PTH pad 1 of J1", "Via")
    assert jlc(v, {}) == 0.20
    assert jlc(v, {"hole_to_hole_mm": 0.3}) == 0.3


@pytest.mark.parametrize("kind", ["drill_out_of_range", "hole_size"])
def test_jlc_covers_through_hole_drill_kinds(kind):
    npth = _violation(kind, "NPTH pad 1 of J1")
    pth = _violation(kind, "PTH pad 1 of J1")
    assert jlc(npth, {}) == 0.50
    assert jlc(pth, {}) == 0.30
    assert jlc(npth, {"through_hole_drill_mm": 0.35}) == 0.35
    assert jlc(pth, {"through_hole_drill_mm": 0.35}) == 0.35


def _copy(workspace, name, folder):
    (workspace / folder).mkdir()
    for f in (PROJECTS / name).iterdir():
        shutil.copy(f, workspace / folder / f.name)
    return f"{folder}/board.kicad_pcb"


def _drc(workspace, board, out):
    report = f"{board.rsplit('/', 1)[0]}/{out}"
    service.call("drc", {"board": board, "out": report})
    return json.loads((workspace / report).read_text())


def _errors(report):
    return {(v.get("type"), tuple(sorted(i.get("uuid", "") for i in v.get("items") or []))): v
            for v in kicad.violations(report)
            if v.get("severity") == "error" and v.get("type") != "unconnected_items"}


def _before_after(workspace, name):
    board = _copy(workspace, name, name)
    before = _drc(workspace, board, "before.json")
    service.call("rules", {"board": board, "rules": AGENT.get(name, {})})
    return before, _drc(workspace, board, "after.json")


def _actual(v):
    match = _ACTUAL.search(v.get("description", ""))
    return float(match.group(1)) if match else None


def judge(b, a, rules):
    """Compare error sets before and after the profile. Returns the four lists
    the bar requires to be empty: hidden, false, unmapped, no_actual_disappeared."""
    # Dangerous error: a violation disappears while still below the fab's
    # number -- or `jlc()` has no number for it at all, which is just as
    # dangerous: don't crash on the missing mapping, don't skip it either.
    hidden = []
    for k in b.keys() - a.keys():
        actual = _actual(b[k])
        if actual is None:
            continue
        limit = jlc(b[k], rules)
        if limit is None or actual < limit:
            hidden.append((k[0], actual, limit))

    # Every violation that appears is real under the fab's number. One with
    # an actual but no `jlc()` mapping is not silently skipped either -- it is
    # unmapped, and the eval has to say so rather than wave it through.
    false, unmapped = [], []
    for k in a.keys() - b.keys():
        actual = _actual(a[k])
        if actual is None:
            continue
        limit = jlc(a[k], rules)
        if limit is None:
            unmapped.append((k[0], actual))
        elif actual >= limit:
            false.append((k[0], actual, limit))

    # A violation with no "actual" (so nothing to compare against a number)
    # must not simply disappear either; unconnected_items is excluded from
    # _errors and compared by count instead.
    no_actual_disappeared = [k[0] for k in b.keys() - a.keys() if _actual(b[k]) is None]
    return hidden, false, unmapped, no_actual_disappeared


# --- judge() must be able to fail: synthetic before/after sets ---------------

def _err(kind, actual, *items, uid="u1"):
    v = _violation(kind, *items)
    if actual is not None:
        v["description"] = f"{kind} (board setting 0.5 mm; actual {actual} mm)"
    return {(kind, (uid,)): v}


def test_judge_flags_a_hidden_violation_below_the_fab_number():
    before = _err("hole_to_hole", 0.30, "PTH pad 1 of J1", "PTH pad 2 of J1")
    assert judge(before, {}, {})[0] == [("hole_to_hole", 0.30, 0.45)]


def test_judge_passes_a_disappearing_violation_at_or_above_the_fab_number():
    before = _err("via_diameter", 0.46, "Via")
    assert judge(before, {}, {}) == ([], [], [], [])


def test_judge_counts_an_unmapped_disappearance_as_hidden():
    before = _err("silk_overlap", 0.10, "Text")
    assert judge(before, {}, {})[0] == [("silk_overlap", 0.10, None)]


def test_judge_flags_a_false_new_violation():
    after = _err("annular_width", 0.10, "Via")
    assert judge({}, after, {})[1] == [("annular_width", 0.10, 0.075)]


def test_judge_lists_an_unmapped_new_violation():
    after = _err("silk_overlap", 0.10, "Text")
    assert judge({}, after, {})[2] == [("silk_overlap", 0.10)]


def test_judge_flags_a_disappearing_violation_without_an_actual():
    before = _err("drill_out_of_range", None, "NPTH pad 1 of J1")
    assert judge(before, {}, {})[3] == ["drill_out_of_range"]


@pytest.mark.parametrize("name", NAMES)
def test_nothing_the_fab_rejects_is_hidden_and_nothing_new_is_false(workspace, name):
    before, after = _before_after(workspace, name)
    hidden, false, unmapped, no_actual_disappeared = judge(
        _errors(before), _errors(after), AGENT.get(name, {}))
    assert hidden == []
    assert false == []
    assert unmapped == []
    assert no_actual_disappeared == []

    # The ratsnest may re-pair after the step re-saves the board; the count holds.
    assert len(after.get("unconnected_items") or []) == len(before.get("unconnected_items") or [])


def test_npth_holes_are_judged_by_the_hole_rule_not_the_outline(workspace):
    before, after = _before_after(workspace, "release2")
    edge = lambda r: [v for v in _errors(r).values()
                      if v["type"] == "copper_edge_clearance" and _npth(v)]
    assert len(edge(before)) == 13  # KiCad's 0.5 edge default
    remaining = edge(after)
    assert all(_actual(v) < fab.value("npth_hole_clearance_mm") for v in remaining)
    assert len(remaining) == 7  # the tracks inside 0.254: real, and kept


_PROBE_TRACKS = """
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
w = pcbnew.FromMM(0.2)
edge = b.GetBoardEdgesBoundingBox()
npth = next(p for fp in b.GetFootprints() for p in fp.Pads()
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH)

def track(x, y0, y1):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(x, y0)); t.SetEnd(pcbnew.VECTOR2I(x, y1))
    t.SetWidth(w); t.SetLayer(pcbnew.B_Cu)
    b.Add(t)
    return t.m_Uuid.AsString()

# 0.15 mm of copper-to-outline gap: below JLCPCB's 0.2, must fail.
x = edge.GetRight() - pcbnew.FromMM(0.15) - w // 2
near_edge = track(x, edge.GetCenter().y, edge.GetCenter().y + pcbnew.FromMM(1))
# 0.30 mm from an NPTH hole: under KiCad's 0.5 edge default, over the 0.254 hole rule.
c, r = npth.GetPosition(), npth.GetDrillSize().x // 2
near_npth = track(c.x + r + pcbnew.FromMM(0.30) + w // 2, c.y - pcbnew.FromMM(0.5), c.y + pcbnew.FromMM(0.5))
pcbnew.SaveBoard(sys.argv[1], b)
print(json.dumps({"edge": near_edge, "npth": near_npth}))
"""


def test_the_outline_is_still_enforced_and_an_npth_hole_is_not_an_outline(workspace):
    board = _copy(workspace, "release3", "probe")
    code, out = service.python(_PROBE_TRACKS, str(workspace / board))
    assert code == 0, out
    probe = json.loads(next(line for line in reversed(out.splitlines()) if line.startswith("{")))
    service.call("rules", {"board": board, "rules": {}})

    edges = [v for v in _errors(_drc(workspace, board, "check.json")).values()
             if v["type"] == "copper_edge_clearance"]
    touched = lambda uuid: any(uuid in [i.get("uuid") for i in v["items"]] for v in edges)
    assert touched(probe["edge"])
    assert not touched(probe["npth"])


def test_an_agents_own_rules_survive_the_profile(workspace):
    _before_after(workspace, "mycircuit7")
    code, out = service.python(
        "import json, sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1]); ds = b.GetDesignSettings()\n"
        "print(json.dumps([pcbnew.ToMM(ds.m_CopperEdgeClearance), pcbnew.ToMM(ds.m_MinClearance)]))\n",
        str(workspace / "mycircuit7" / "board.kicad_pcb"))
    assert code == 0, out
    assert json.loads(next(l for l in reversed(out.splitlines()) if l.startswith("["))) == [0.4, 0.2]


# --- the profile and the router, together (opt-in: real routing) ----------------
#
# Under the profile the board's hole clearance floor is 0.20, so route's NPTH
# keepout shrinks to 0.20 while the custom rules check NPTH-to-track at 0.254.
# Measured 2026-09-25 on release2: nothing lands in that band.

_STRIP = """
import sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
for item in list(b.GetTracks()) + list(b.Zones()):
    b.Delete(item)
pcbnew.SaveBoard(sys.argv[1], b)
print("stripped")
"""


@pytest.mark.skipif(os.environ.get("CIRCUIT_ROUTING_EVAL") != "1",
                    reason="real routing; set CIRCUIT_ROUTING_EVAL=1")
def test_routing_under_the_profile_keeps_clear_of_the_npth_hole_rule(workspace):
    from circuit_mcp import rules as rules_mod
    board = _copy(workspace, "release2", "routed")
    code, out = service.python(_STRIP, str(workspace / board))
    assert code == 0, out
    service.call("rules", {"board": board, "rules": {}})
    service.route(board, service.ROUTER_PASSES)
    rules_mod.finish(board)

    report = _drc(workspace, board, "check.json")
    against_npth = [v for v in _errors(report).values()
                    if v["type"] in ("hole_clearance", "copper_edge_clearance")
                    and _npth(v) and _copper(v)]
    assert against_npth == []
    assert len(report.get("unconnected_items") or []) == 0
