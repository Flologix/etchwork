"""check_done: the finish line, read from the files alone.

Release check 3 (2026-09-24) finished a board at ~69 min and then spent 21
more minutes on a self-made "short" because nothing could say it was done.
Each test here is one state from the spec's §5.1 table.
"""
import json
import os
import shutil

import pytest

from circuit_mcp import done, schematic, summary
from conftest import FIXTURES

CLEAN = {"violations": [], "unconnected_items": []}
FILLED_BOARD = "(kicad_pcb (zone (net 1) (filled_polygon (pts))))"


@pytest.fixture(autouse=True)
def schematic_matches(monkeypatch):
    """These tests use blinker.net, which has no SKiDL script to draw from;
    the real schematic check is tested in test_done_schematic.py."""
    monkeypatch.setattr(schematic, "problems", lambda sch, netlist: [])


def _age(path, seconds):
    """Make `path` look `seconds` older than now."""
    st = path.stat()
    os.utime(path, (st.st_atime - seconds, st.st_mtime - seconds))


def _done_project(workspace, board_text=FILLED_BOARD, needs_review=None):
    """A project in the state check_done must call DONE.

    The board is written first and backdated, so everything produced from it
    is newer, as it is after a real run.
    """
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    (workspace / "blinker.netclasses.json").write_text(json.dumps({
        "version": 1, "nets": {},
        "needs_review": needs_review if needs_review is not None else [],
    }))
    board = workspace / "blinker.kicad_pcb"
    board.write_text(board_text)
    _age(board, 60)
    (workspace / "blinker.drc.json").write_text(json.dumps(CLEAN))
    gerbers = workspace / "gerbers"
    gerbers.mkdir()
    (gerbers / "blinker-F_Cu.gbr").write_text("G04*")
    (gerbers / "blinker.drl").write_text("M48")
    for name in schematic.files("blinker")[:2]:
        (workspace / name).write_text("(kicad_sch)")
    summary.summarize_project("blinker.net")
    readme = workspace / "README.md"
    readme.write_text(readme.read_text().replace(summary.PLACEHOLDER, "Blinks two LEDs."))
    return board


def test_a_finished_project_is_done_and_lists_the_flagged_nets(workspace):
    _done_project(workspace, needs_review=[{"net": "+3V3", "reason": "resolver held"}])
    out = done.check_done("blinker.net")
    assert out.startswith("DONE")
    assert "+3V3" in out and "resolver held" in out


def test_done_carries_the_board_facts_the_final_answer_needs(workspace):
    """Bar 4 of the 2026-09-24 live eval: after DONE the agent parsed the board
    and the DRC report itself, only to state the board's size and DRC counts.
    DONE hands it those facts so nothing is left to re-derive."""
    _done_project(workspace)
    (workspace / "blinker.drc.json").write_text(json.dumps(
        {"violations": [{"severity": "warning", "type": "lib_footprint_issues"}] * 3,
         "unconnected_items": []}))
    out = done.check_done("blinker.net")
    assert out.startswith("DONE")
    assert "DRC: 0 error(s), 0 unconnected, 3 warning(s)" in out
    assert "outline: none on Edge.Cuts" in out
    assert "footprints: 0" in out


def test_done_says_none_when_no_net_awaits_a_decision(workspace):
    _done_project(workspace)
    out = done.check_done("blinker.net")
    assert out.startswith("DONE")
    assert "None." in out


def test_check_done_writes_nothing(workspace):
    _done_project(workspace)
    before = {p: p.stat().st_mtime for p in workspace.rglob("*")}
    done.check_done("blinker.net")
    assert {p: p.stat().st_mtime for p in workspace.rglob("*")} == before


def test_no_board_is_not_done(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE:")
    assert "run_layout" in out


def test_a_missing_netlist_is_named(workspace):
    out = done.check_done("nope.net")
    assert out.startswith("NOT DONE:")
    assert "nope.net" in out


def test_missing_drc_is_not_done(workspace):
    _done_project(workspace)
    (workspace / "blinker.drc.json").unlink()
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE:")
    assert "DRC not run" in out and "run_drc" in out


def test_drc_older_than_the_board_is_not_done(workspace):
    _done_project(workspace)
    _age(workspace / "blinker.drc.json", 120)
    out = done.check_done("blinker.net")
    assert "DRC not run on the current board" in out and "run_drc" in out


def test_drc_errors_are_not_done(workspace):
    _done_project(workspace)
    (workspace / "blinker.drc.json").write_text(json.dumps(
        {"violations": [{"severity": "error", "type": "clearance"}], "unconnected_items": []}))
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE:")
    assert "1 DRC error(s), 0 unconnected" in out


def test_unconnected_items_are_not_done(workspace):
    _done_project(workspace)
    (workspace / "blinker.drc.json").write_text(json.dumps(
        {"violations": [], "unconnected_items": [{}, {}]}))
    out = done.check_done("blinker.net")
    assert "0 DRC error(s), 2 unconnected" in out


def test_an_unfilled_pour_is_not_done(workspace):
    _done_project(workspace, board_text="(kicad_pcb (zone (net 1)))")
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE:")
    assert "1 copper pour(s) not filled" in out and "fill_zones" in out


def test_missing_gerbers_are_not_done(workspace):
    _done_project(workspace)
    shutil.rmtree(workspace / "gerbers")
    out = done.check_done("blinker.net")
    assert "no gerbers" in out and "export_gerbers" in out


def test_an_empty_gerbers_folder_is_not_done(workspace):
    _done_project(workspace)
    for f in (workspace / "gerbers").iterdir():
        f.unlink()
    out = done.check_done("blinker.net")
    assert "no gerbers" in out and "export_gerbers" in out


def test_a_gerber_older_than_the_board_is_not_done(workspace):
    _done_project(workspace)
    _age(workspace / "gerbers" / "blinker.drl", 120)
    out = done.check_done("blinker.net")
    assert "gerbers/ has files older than the board" in out
    assert "blinker.drl" in out
    assert "export_gerbers" in out


def test_a_missing_readme_is_not_done(workspace):
    _done_project(workspace)
    (workspace / "README.md").unlink()
    out = done.check_done("blinker.net")
    assert "no README.md" in out and "summarize_project" in out


def test_a_readme_older_than_the_board_is_not_done(workspace):
    _done_project(workspace)
    _age(workspace / "README.md", 120)
    out = done.check_done("blinker.net")
    assert "README.md is older than the board" in out and "summarize_project" in out


def test_malformed_markers_are_not_done(workspace):
    _done_project(workspace)
    readme = workspace / "README.md"
    readme.write_text(readme.read_text() + "\n" + summary.BEGIN + "\n")
    out = done.check_done("blinker.net")
    assert "malformed summary markers" in out


def test_a_status_that_is_not_clean_is_not_done(workspace):
    _done_project(workspace)
    readme = workspace / "README.md"
    readme.write_text(readme.read_text().replace(
        "Status: DRC clean", "Status: NOT READY: DRC not run"))
    out = done.check_done("blinker.net")
    assert "Status: NOT READY: DRC not run" in out and "summarize_project" in out


def test_the_placeholder_is_not_done(workspace):
    _done_project(workspace)
    readme = workspace / "README.md"
    readme.write_text(summary.PLACEHOLDER + "\n" + readme.read_text())
    out = done.check_done("blinker.net")
    assert "replace it with what the board does" in out


def test_every_failure_is_listed_at_once(workspace):
    _done_project(workspace)
    _age(workspace / "blinker.drc.json", 120)
    shutil.rmtree(workspace / "gerbers")
    (workspace / "README.md").unlink()
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE:")
    for tool in ("run_drc", "export_gerbers", "summarize_project"):
        assert tool in out, tool


def test_a_deleted_netclasses_file_is_still_done_but_not_classified(workspace):
    _done_project(workspace)
    (workspace / "blinker.netclasses.json").unlink()
    out = done.check_done("blinker.net")
    assert out.startswith("DONE")
    assert "Not classified" in out


def test_an_explicit_board_is_used(workspace):
    _done_project(workspace)
    shutil.copy(workspace / "blinker.kicad_pcb", workspace / "other.kicad_pcb")
    out = done.check_done("blinker.net", board="other.kicad_pcb")
    # other.kicad_pcb has no other.drc.json of its own.
    assert "DRC not run" in out


def test_a_missing_schematic_is_not_done(workspace):
    _done_project(workspace)
    (workspace / "blinker.kicad_sch").unlink()
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE") and "blinker.kicad_sch" in out and "export_schematic" in out


def test_a_stale_schematic_is_not_done(workspace):
    _done_project(workspace)
    _age(workspace / "blinker-schematic.pdf", 3600)
    out = done.check_done("blinker.net")
    assert "blinker-schematic.pdf" in out and "older than blinker.net" in out


def test_done_names_the_schematic(workspace):
    _done_project(workspace)
    out = done.check_done("blinker.net")
    assert out.startswith("DONE") and "blinker.kicad_sch" in out


def _unavailable_project(workspace):
    """A finished project whose schematic SKiDL could not draw: the record
    stands in for the drawing (final review, Critical 1)."""
    _done_project(workspace)
    for name in schematic.files("blinker"):
        (workspace / name).unlink(missing_ok=True)
    record = workspace / "blinker.sch-unavailable.json"
    record.write_text(json.dumps({"reason": "SKiDL's generator drew it wrongly",
                                  "details": ["x"], "attempts": 5}))
    return record


def test_an_unavailable_record_in_place_of_the_schematic_is_done(workspace):
    _unavailable_project(workspace)
    out = done.check_done("blinker.net")
    assert out.startswith("DONE"), out
    assert ("Schematic: not available (SKiDL's generator drew it wrongly); "
            "say so in your final answer.") in out.splitlines()
    assert "blinker.kicad_sch" not in out


def test_a_stale_unavailable_record_is_not_done(workspace):
    record = _unavailable_project(workspace)
    _age(record, 3600)
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE") and "blinker.sch-unavailable.json older than blinker.net" in out


def test_an_unreadable_unavailable_record_is_not_done(workspace):
    record = _unavailable_project(workspace)
    record.write_text("not json")
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE") and "export_schematic" in out


def test_a_record_does_not_excuse_a_schematic_that_is_there(workspace):
    _unavailable_project(workspace)
    (workspace / "blinker.kicad_sch").write_text("(kicad_sch)")
    out = done.check_done("blinker.net")
    assert out.startswith("NOT DONE") and "blinker-schematic.pdf" in out


def test_a_keepout_in_a_footprint_does_not_stop_done(workspace):
    """A module's antenna keepout can never be filled; it is not a pour."""
    keepout = "(footprint \"RF_Module:ESP32-C6-MINI-1\" (zone (name \"Antenna\") (keepout (copperpour not_allowed)) (polygon (pts))))"
    _done_project(workspace, board_text=f"(kicad_pcb {keepout} (zone (net 1) (filled_polygon (pts))))")
    out = done.check_done("blinker.net")
    assert out.startswith("DONE"), out
