import json
import shutil
import pytest

from circuit_mcp import rules as rules_mod
from circuit_mcp import service
from conftest import FIXTURES


def test_unknown_steps_are_refused(workspace):
    with pytest.raises(ValueError):
        service.call("teleport", {})


def test_paths_outside_the_workspace_are_refused(workspace):
    with pytest.raises(ValueError):
        service.call("outline", {"board": "../x.kicad_pcb", "width_mm": 10, "height_mm": 10})


def test_a_missing_field_is_a_value_error_not_a_key_error(workspace):
    with pytest.raises(ValueError, match="width_mm"):
        service.call("outline", {"board": "b.kicad_pcb"})


def test_netlist_becomes_a_board_with_an_outline_and_a_drc_report(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    built = service.call("pcb", {"netlist": "blinker.net"})
    assert built["board"] == "blinker.kicad_pcb"
    service.call("outline", {"board": "blinker.kicad_pcb", "width_mm": 36, "height_mm": 28})
    report = service.call("drc", {"board": "blinker.kicad_pcb"})
    assert (workspace / report["report"]).is_file()


def test_hole_clearance_is_a_board_rule(workspace):
    """The first release check failed on hole_clearance errors and there was
    no knob for it: the fab's real hole clearance has to be settable like the
    other minimums, through pcbnew's m_HoleClearance."""
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    service.call("pcb", {"netlist": "blinker.net"})
    service.call("rules", {"board": "blinker.kicad_pcb", "rules": {"hole_clearance_mm": 0.2}})
    code, out = service.python(
        "import sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "print(pcbnew.ToMM(b.GetDesignSettings().m_HoleClearance))\n",
        str(workspace / "blinker.kicad_pcb"))
    assert code == 0, out
    assert float(out.strip().splitlines()[-1]) == pytest.approx(0.2)


def test_the_fab_clearance_reaches_every_net_class(workspace):
    """DRC measures a net against its CLASS clearance, not the board minimum.
    In release check 2 the flai classes kept 0.2 mm, and a USB-C receptacle
    whose own pads sit 0.156 mm apart failed 30 clearance checks that no
    placement could fix. The fab's clearance is a property of the fab, so it
    applies to every class, the flai classes included."""
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    service.call("pcb", {"netlist": "blinker.net"})
    (workspace / "blinker.netclasses.json").write_text(json.dumps({"nets": {
        "GND": {"netclass": "ground", "width_mm": 0.5, "clearance_mm": 0.2}}}))
    rules_mod.apply_netclasses("blinker.kicad_pcb", "blinker.netclasses.json")
    service.call("rules", {"board": "blinker.kicad_pcb", "rules": {"clearance_mm": 0.15}})
    code, out = service.python(
        "import json, sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "print(json.dumps({str(n): pcbnew.ToMM(c.GetClearance())"
        " for n, c in b.GetAllNetClasses().items()}))\n",
        str(workspace / "blinker.kicad_pcb"))
    assert code == 0, out
    clearances = json.loads(out.strip().splitlines()[-1])
    assert "ground" in clearances, clearances
    assert clearances == {name: pytest.approx(0.15) for name in clearances}


# --- NPTH keepouts for the router ---------------------------------------------
#
# KiCad's DSN export makes a round NPTH hole a keepout but an oval one netless
# copper, so freerouting ran tracks inside release check 2's hole clearance.

import pathlib
import subprocess


# The keepouts are never saved, so a test sees them the way the export makes
# them: the shared zone-building snippet run on a board loaded in memory.
# argv[4] is Python run on `board` first, to reshape a pad.
_INSPECT = """
import json, sys
board = pcbnew.LoadBoard(sys.argv[1])
exec(sys.argv[4])
count, margin = add_npth_keepouts(board, sys.argv[2], sys.argv[3])
zones = []
for z in board.Zones():
    if z.GetZoneName() != sys.argv[3]:
        continue
    box = z.GetBoundingBox()
    zones.append([z.GetZoneName(), z.GetIsRuleArea(), z.GetDoNotAllowTracks(),
                  z.GetDoNotAllowVias(), z.GetDoNotAllowZoneFills(),
                  z.Outline().Outline(0).PointCount(),
                  pcbnew.ToMM(box.GetWidth()), pcbnew.ToMM(box.GetHeight())])
print(json.dumps([{"keepouts": count, "margin_mm": round(pcbnew.ToMM(margin), 4)}, zones]))
"""


def _keepouts_on(workspace, margin, tweak=""):
    shutil.copy(FIXTURES / "boards" / "release2.kicad_pcb", workspace / "copy.kicad_pcb")
    code, out = service.python("import pcbnew\n" + service._ADD_NPTH_KEEPOUTS + _INSPECT,
                               str(workspace / "copy.kicad_pcb"), margin,
                               service.NPTH_KEEPOUT, tweak)
    assert code == 0, out
    answer, zones = json.loads(next(line for line in reversed(out.splitlines())
                                    if line.startswith("[")))
    return answer, zones


def test_every_npth_hole_gets_a_track_and_via_keepout(workspace):
    answer, zones = _keepouts_on(workspace, "hole")
    # release2's J1 has one round and one oval NPTH.
    assert answer == {"keepouts": 2, "margin_mm": 0.25}
    assert len(zones) == 2
    for name, rule_area, no_tracks, no_vias, no_fills, *_ in zones:
        assert (rule_area, no_tracks, no_vias, no_fills) == (True, True, True, False)


def test_a_keepout_follows_the_holes_shape_not_its_bounding_box(workspace):
    _, zones = _keepouts_on(workspace, "hole")
    # A box would be 4 points; a grown circle or stadium is a polygon of many.
    assert all(zone[5] > 4 for zone in zones)


# The first NPTH pad of release2's J1: its round 0.65 mm hole.
_FIRST_NPTH = ("pad = [p for fp in board.GetFootprints() for p in fp.Pads()\n"
               "       if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH][0]\n")


def test_a_keepout_grows_the_drill_hole_not_the_pads_copper(workspace):
    # DRC measures hole clearance from the hole, so a pad drawn smaller than
    # its drill is still fenced at drill + 2 x margin = 0.65 + 2 x 0.25.
    _, zones = _keepouts_on(workspace, "hole", _FIRST_NPTH +
                            "pad.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.1), pcbnew.FromMM(0.1)))\n"
                            "pad.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.65), pcbnew.FromMM(0.65)))\n")
    widths = sorted(zone[6] for zone in zones)
    assert widths[0] == pytest.approx(1.15, abs=0.02)


def test_a_pad_without_a_drill_gets_no_keepout(workspace):
    answer, zones = _keepouts_on(workspace, "hole",
                                 _FIRST_NPTH + "pad.SetDrillSize(pcbnew.VECTOR2I(0, 0))\n")
    assert answer["keepouts"] == 1 and len(zones) == 1


def test_the_edge_margin_is_the_larger_of_hole_and_edge_clearance(workspace):
    answer, _ = _keepouts_on(workspace, "auto")
    assert answer["margin_mm"] == 0.5  # release2's edge 0.5 > hole 0.25


def _fake_router(workspace, monkeypatch, import_writes=None):
    """service.python and the java call faked: records which file each step
    touched. `import_writes` is text the SES import writes into the board."""
    calls = []

    def fake_python(script, *args):
        calls.append((script, args))
        if script is service._EXPORT_DSN_WITH_KEEPOUTS:
            pathlib.Path(args[1]).write_text("(pcb)")
            return 0, '{"keepouts": 2, "margin_mm": 0.25}'
        if script is service._EXPORT_DSN:
            pathlib.Path(args[1]).write_text("(pcb)")
            return 0, "dsn written"
        if import_writes:
            pathlib.Path(args[0]).write_text(import_writes)
        return 0, "ses imported"

    def fake_run(cmd, **kwargs):
        pathlib.Path(cmd[cmd.index("-do") + 1]).write_text("(session)")
        return subprocess.CompletedProcess(cmd, 0, "routed", "")

    monkeypatch.setattr(service, "python", fake_python)
    monkeypatch.setattr(service.subprocess, "run", fake_run)
    (workspace / "board.kicad_pcb").write_text("(kicad_pcb)")
    return calls


def test_route_exports_the_board_fenced_in_memory_and_imports_into_it(workspace, monkeypatch):
    calls = _fake_router(workspace, monkeypatch)

    answer = service.route("board.kicad_pcb", 1)

    export, imported = calls
    board = str(workspace / "board.kicad_pcb")
    assert export[0] is service._EXPORT_DSN_WITH_KEEPOUTS
    assert export[1][0] == board and export[1][2:] == ("hole", service.NPTH_KEEPOUT)
    assert imported[0] is service._IMPORT_SES and imported[1][0] == board
    assert answer["npth_keepouts"] == 2
    assert not [p for p in workspace.rglob("*") if ".route" in p.name]


def test_routing_keeps_the_boards_net_classes_and_rules(workspace, monkeypatch):
    # Net classes, their assignments and the hole clearance live in the
    # .kicad_pro beside the board. An export that loses it routes every net
    # at the default width and fences NPTH holes at the default clearance.
    shutil.copy(FIXTURES / "boards" / "release2.kicad_pcb", workspace / "board.kicad_pcb")
    code, out = service.python(
        "import sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "ds = b.GetDesignSettings()\n"
        "ds.m_HoleClearance = pcbnew.FromMM(0.4)\n"
        "nc = pcbnew.NETCLASS('Power')\n"
        "nc.SetTrackWidth(pcbnew.FromMM(0.8)); nc.SetClearance(pcbnew.FromMM(0.3))\n"
        "ds.m_NetSettings.SetNetclass('Power', nc)\n"
        "ds.m_NetSettings.SetNetclassPatternAssignment('GND', 'Power')\n"
        "pcbnew.SaveBoard(sys.argv[1], b)\n",
        str(workspace / "board.kicad_pcb"))
    assert code == 0, out
    assert (workspace / "board.kicad_pro").is_file()

    real_python, real_run, outputs = service.python, subprocess.run, []

    def export_for_real(script, *args):
        if script is service._IMPORT_SES:
            return 0, "ses imported"  # the routes are not what is under test
        code, out = real_python(script, *args)
        outputs.append(out)
        return code, out

    def stub_router(cmd, **kwargs):
        if cmd[0] != "java":
            return real_run(cmd, **kwargs)
        pathlib.Path(cmd[cmd.index("-do") + 1]).write_text("(session)")
        return subprocess.CompletedProcess(cmd, 0, "routed", "")

    monkeypatch.setattr(service, "python", export_for_real)
    monkeypatch.setattr(service.subprocess, "run", stub_router)

    answer = service.route("board.kicad_pcb", 1)

    dsn = (workspace / "board.dsn").read_text()
    assert "(class Power" in dsn, "the DSN lost the board's net classes"
    start = dsn.index("(class Power")
    end = dsn.find("(class ", start + 1)
    assert "(width 800)" in dsn[start:end if end >= 0 else None]
    reported = json.loads(next(line for line in reversed(outputs[-1].splitlines())
                               if line.startswith("{")))
    assert reported["margin_mm"] == 0.4
    assert answer["npth_keepouts"] == 2
    assert not [p for p in workspace.rglob("*") if ".route" in p.name]


def test_route_without_keepouts_exports_the_board_itself(workspace, monkeypatch):
    monkeypatch.setattr(service, "NPTH_MARGIN", None)
    calls = _fake_router(workspace, monkeypatch)

    answer = service.route("board.kicad_pcb", 1)

    assert [script for script, _ in calls] == [service._EXPORT_DSN, service._IMPORT_SES]
    assert calls[0][1][0] == str(workspace / "board.kicad_pcb")
    assert answer["npth_keepouts"] == 0


def test_a_keepout_that_reaches_the_saved_board_is_refused(workspace, monkeypatch):
    _fake_router(workspace, monkeypatch,
                 import_writes=f'(kicad_pcb (zone (name "{service.NPTH_KEEPOUT}")))')
    with pytest.raises(RuntimeError, match="reached the saved board"):
        service.route("board.kicad_pcb", 1)


# --- the default fab profile ----------------------------------------------------

from circuit_mcp import fab


def _project(workspace, name):
    """A real saved project (board + .kicad_pro) copied into the workspace."""
    for f in (FIXTURES / "projects" / name).iterdir():
        shutil.copy(f, workspace / f.name)
    return "board.kicad_pcb"


def _settings(workspace, board):
    code, out = service.python(
        "import json, sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "ds = b.GetDesignSettings()\n"
        "mm = pcbnew.ToMM\n"
        "print(json.dumps({'clearance': mm(ds.m_MinClearance), 'edge': mm(ds.m_CopperEdgeClearance),\n"
        "  'hole': mm(ds.m_HoleClearance), 'drill': mm(ds.m_MinThroughDrill),\n"
        "  'track': mm(ds.m_TrackMinWidth), 'via': mm(ds.m_ViasMinSize),\n"
        "  'classes': {str(n): mm(c.GetClearance()) for n, c in b.GetAllNetClasses().items()}}))\n",
        str(workspace / board))
    assert code == 0, out
    return json.loads(next(line for line in reversed(out.splitlines()) if line.startswith("{")))


def test_without_rules_the_profile_sets_floors_and_leaves_net_classes_alone(workspace):
    board = _project(workspace, "release2")
    before = _settings(workspace, board)

    answer = service.call("rules", {"board": board, "rules": {}})

    after = _settings(workspace, board)
    assert answer["profile"] == fab.PROFILE and answer["agent_rules"] == []
    assert (after["clearance"], after["edge"], after["hole"]) == (0.1, 0.2, 0.2)
    assert (after["drill"], after["track"], after["via"]) == (0.3, 0.1, 0.45)
    assert after["classes"] == before["classes"]


def test_the_rules_step_writes_the_custom_rules_beside_the_board(workspace):
    board = _project(workspace, "release2")
    service.call("rules", {"board": board, "rules": {}})
    assert (workspace / "board.kicad_dru").read_text() == fab.custom_rules()


def test_a_hand_written_kicad_dru_is_backed_up_before_the_rules_step_writes_its_own(workspace):
    """The rules step rewrites `<stem>.kicad_dru` on every run_layout; a file a
    human wrote there by hand (no rules-step header) must not be silently
    lost."""
    board = _project(workspace, "release2")
    dru = workspace / "board.kicad_dru"
    dru.write_text("(version 1)\n; hand-written by a human\n")

    answer = service.call("rules", {"board": board, "rules": {}})

    orig = workspace / "board.kicad_dru.orig"
    assert orig.read_text() == "(version 1)\n; hand-written by a human\n"
    assert dru.read_text() == fab.custom_rules()
    assert answer["kept_dru"] == "board.kicad_dru.orig"


def test_a_kicad_dru_the_tools_wrote_before_is_simply_rewritten(workspace):
    """A file that already carries the rules step's own header is ours from
    a previous run, not a human's, so it is rewritten with no backup."""
    board = _project(workspace, "release2")
    dru = workspace / "board.kicad_dru"
    dru.write_text(fab.custom_rules({"clearance_mm": 0.5}))

    answer = service.call("rules", {"board": board, "rules": {}})

    assert not (workspace / "board.kicad_dru.orig").exists()
    assert dru.read_text() == fab.custom_rules()
    assert answer["kept_dru"] is None


def test_the_latest_human_kicad_dru_backup_overwrites_an_older_one(workspace):
    board = _project(workspace, "release2")
    dru = workspace / "board.kicad_dru"
    (workspace / "board.kicad_dru.orig").write_text("stale backup")
    dru.write_text("(version 1)\n; second hand-written file\n")

    answer = service.call("rules", {"board": board, "rules": {}})

    assert (workspace / "board.kicad_dru.orig").read_text() == \
        "(version 1)\n; second hand-written file\n"
    assert answer["kept_dru"] == "board.kicad_dru.orig"


def test_an_agents_rules_still_win_over_the_profile(workspace):
    board = _project(workspace, "release2")

    service.call("rules", {"board": board,
                           "rules": {"clearance_mm": 0.15, "edge_clearance_mm": 0.4}})

    after = _settings(workspace, board)
    assert (after["clearance"], after["edge"]) == (0.15, 0.4)
    # An agent-given clearance is a fab's, so it reaches every class (release check 2).
    assert set(after["classes"].values()) == {0.15}


# --- subprocess hygiene --------------------------------------------------------

def test_pcbnew_snippets_do_not_import_modules_from_the_project(workspace, monkeypatch):
    """The snippets run with the project as the working directory, and
    `python3 -c` puts that directory first on sys.path: a `json.py` beside
    the board replaced the standard library in every pcbnew step."""
    (workspace / "json.py").write_text("raise SystemExit('shadowed by the project')\n")
    monkeypatch.chdir(workspace)
    assert service.python("import json; print(json.dumps(1))") == (0, "1")


def test_a_snippet_that_times_out_is_a_short_runtime_error(monkeypatch):
    """Not subprocess.TimeoutExpired, whose message is the whole script."""
    monkeypatch.setattr(service, "TIMEOUT", 1)
    with pytest.raises(RuntimeError) as raised:
        service.python("import time; time.sleep(5)")
    assert "timed out after 1s" in str(raised.value)
    assert "sleep" not in str(raised.value)


def test_a_step_that_times_out_is_a_runtime_error(workspace, monkeypatch):
    shutil.copy(FIXTURES / "boards" / "test.kicad_pcb", workspace / "b.kicad_pcb")
    monkeypatch.setattr(service, "TIMEOUT", 0.01)
    with pytest.raises(RuntimeError, match="the 'drc' step timed out"):
        service.call("drc", {"board": "b.kicad_pcb"})
