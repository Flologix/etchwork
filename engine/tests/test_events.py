"""The local event log (flywheel design §5): one JSON line per tool call in
/workspace/.etchwork/events.jsonl, derived facts only, and never at the cost
of the tool's own answer."""
import inspect
import json

import pytest

from circuit_mcp import events


def _lines(workspace, name="events.jsonl"):
    path = workspace / ".etchwork" / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("CIRCUIT_EVENTS", raising=False)


def _tool(answer):
    @events.logged
    def run_drc(board: str) -> str:
        """Run DRC."""
        return answer
    return run_drc


def test_a_call_is_one_line_with_its_outcome_and_timing(workspace):
    answer = "PASS — DRC clean, 0 unconnected (DRC, kicad-cli exit 0)"
    assert _tool(answer)("board.kicad_pcb") == answer

    [event] = _lines(workspace)
    assert event["v"] == 1
    assert event["tool"] == "run_drc"
    assert event["outcome"] == "pass"
    assert event["args"] == {"board": "board.kicad_pcb"}
    assert event["chars"] == len(answer)
    assert isinstance(event["ms"], int) and event["ms"] >= 0
    assert event["session"] == events.SESSION
    assert event["ts"].endswith("Z")


def test_calls_are_numbered_within_a_session(workspace):
    tool = _tool("ok")
    tool("a.kicad_pcb")
    tool("b.kicad_pcb")
    first, second = _lines(workspace)
    assert second["seq"] == first["seq"] + 1
    assert first["session"] == second["session"]


@pytest.mark.parametrize("answer, outcome", [
    ("PASS — DRC clean, 0 unconnected", "pass"),
    ("FAIL — NOT DONE: 2 DRC error(s) (clearance), 0 unconnected", "fail"),
    ("REFUSED:\n- VIN: missing a reason", "refused"),
    ("NOT DONE:\n- DRC not run", "not_done"),
    ("DONE\nBoard: 34 x 22 mm", "done"),
    ("netlist: board.net\nERC: clean", "ok"),
])
def test_the_outcome_is_read_from_the_answer(answer, outcome):
    assert events.outcome(answer) == outcome


@pytest.mark.parametrize("answer, metrics", [
    ("FAIL — NOT DONE: 3 DRC error(s) (clearance, track_width), 1 unconnected (DRC exit 5, routed)",
     {"drc_errors": 3, "unconnected": 1, "violation_types": ["clearance", "track_width"]}),
    ("FAIL — NOT DONE: 0 DRC error(s), 4 unconnected (DRC exit 5, routed)",
     {"drc_errors": 0, "unconnected": 4, "violation_types": []}),
    ("PASS — DRC clean, 0 unconnected (DRC exit 0, routed)",
     {"drc_errors": 0, "unconnected": 0, "violation_types": []}),
    ("netlist: board.net", {}),
])
def test_drc_counts_are_read_from_the_headline(answer, metrics):
    assert events.metrics(answer) == metrics


def test_arguments_are_summarised_not_copied():
    summary = events.summarise("run_layout", {
        "netlist": "board.net", "width_mm": 34, "height_mm": 22, "route": True,
        "overwrite": False, "positions": {"R1": [1, 2, 0], "C1": [3, 4, 90]},
        "arrange": [{"op": "rotate", "refs": ["U1"], "degrees": 90},
                    {"op": "rotate", "refs": ["J1"], "degrees": 180},
                    {"op": "space_evenly", "refs": ["R1", "R2"], "axis": "x"}],
        "rules": {"clearance_mm": 0.15, "track_width_mm": 0.15}})
    assert summary == {"netlist": "board.net", "width_mm": 34, "height_mm": 22, "route": True,
                       "overwrite": False, "positions": 2,
                       "arrange": {"rotate": 2, "space_evenly": 1},
                       "rules": ["clearance_mm", "track_width_mm"]}


def test_net_class_picks_are_counted_and_their_reasons_left_out():
    summary = events.summarise("set_net_classes", {"netlist": "board.net", "picks": [
        {"net": "VIN", "class": "power_high_current", "tier": "2a_to_5a", "reason": "the 12 V input"},
        {"net": "LED", "class": "signal", "tier": "lt_0_5a", "reason": "LED drive", "review": True}]})
    assert summary == {"netlist": "board.net", "picks": 2, "review": 1}
    assert "12 V" not in json.dumps(summary)


def test_unset_arguments_are_left_out():
    assert events.summarise("check_done", {"netlist": "board.net", "board": None}) == {
        "netlist": "board.net"}


def test_a_tool_that_raises_still_raises_and_is_logged_without_its_message(workspace):
    @events.logged
    def fill_zones(board: str) -> str:
        raise RuntimeError("the pour failed: /secret/path detail")

    with pytest.raises(RuntimeError):
        fill_zones("board.kicad_pcb")
    [event] = _lines(workspace)
    assert event["outcome"] == "error"
    assert event["error"] == "RuntimeError"
    assert "secret" not in json.dumps(event)


def test_a_log_that_cannot_be_written_never_fails_the_tool(workspace):
    (workspace / ".etchwork").write_text("a file where the folder should be")
    assert _tool("PASS")("board.kicad_pcb") == "PASS"


def test_a_log_folder_that_points_outside_the_project_is_not_written(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (workspace / ".etchwork").symlink_to(outside, target_is_directory=True)
    assert _tool("PASS")("board.kicad_pcb") == "PASS"
    assert list(outside.iterdir()) == []


def test_a_log_file_that_is_a_symlink_is_not_followed(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "stolen"
    outside.write_text("")
    (workspace / ".etchwork").mkdir()
    (workspace / ".etchwork" / "events.jsonl").symlink_to(outside)
    assert _tool("PASS")("board.kicad_pcb") == "PASS"
    assert outside.read_text() == ""


def test_the_log_can_be_turned_off(workspace, monkeypatch):
    monkeypatch.setenv("CIRCUIT_EVENTS", "0")
    _tool("PASS")("board.kicad_pcb")
    assert not (workspace / ".etchwork").exists()


def test_the_log_rotates_once_when_it_grows_too_big(workspace, monkeypatch):
    monkeypatch.setattr(events, "MAX_BYTES", 200)
    tool = _tool("PASS")
    for _ in range(3):
        tool("board.kicad_pcb")
    assert _lines(workspace, "events.1.jsonl")
    assert len(_lines(workspace)) < 3


def test_the_wrapper_keeps_the_signature_fastmcp_builds_the_schema_from():
    def run_layout(netlist: str, width_mm: float, route: bool = True) -> str:
        """Lay out."""
        return ""
    wrapped = events.logged(run_layout)
    assert inspect.signature(wrapped) == inspect.signature(run_layout)
    assert (wrapped.__name__, wrapped.__doc__) == ("run_layout", "Lay out.")
