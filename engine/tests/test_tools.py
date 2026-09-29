"""The tools that run KiCad's own pipeline, and summarise what comes back."""
import json
import subprocess

import pytest

from circuit_mcp import paths, rules, service, tools


@pytest.fixture
def workspace(workspace):
    """The shared workspace, pre-populated with a stub board file -- most of
    these tests act on a board that already exists and do not care what is
    in it, only what the (faked) pipeline says about it."""
    (workspace / "board.kicad_pcb").write_text("(kicad_pcb)")
    return workspace


def fake_pipeline(response, seen=None):
    """A stand-in for service.call. Same shape as the fake sidecar this
    replaced: (kind, body) instead of (url, body)."""
    def call(kind, body):
        if seen is not None:
            seen.append((kind, body))
        if callable(response):
            return response(kind, body)
        return response
    return call


DRC = {
    "$schema": "https://schemas.kicad.org/drc.v1.json",
    "source": "board.kicad_pcb", "kicad_version": "9.0.9",
    "coordinate_units": "mm",
    "violations": [{"type": "clearance", "severity": "error",
                    "description": "Clearance violation",
                    "items": [{"description": "Track", "pos": {"x": 1.0, "y": 2.0}}]}],
    "unconnected_items": [], "schematic_parity": [],
}


# --- run_drc -----------------------------------------------------------------

def test_a_board_with_violations_reports_them_and_says_it_failed(workspace, monkeypatch):
    (workspace / "board.drc.json").write_text(json.dumps(DRC))
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"exit": 5, "report": "board.drc.json", "stdout": "", "stderr": ""}))

    out = tools.run_drc("board.kicad_pcb")

    assert "1 violation(s)" in out
    assert "clearance" in out
    # The verdict comes from the exit code, never from the summariser --
    # circuit_mcp.kicad deliberately passes none.
    assert "FAIL" in out


def test_a_clean_board_says_so(workspace, monkeypatch):
    clean = {**DRC, "violations": []}
    (workspace / "board.drc.json").write_text(json.dumps(clean))
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"exit": 0, "report": "board.drc.json", "stdout": "", "stderr": ""}))

    out = tools.run_drc("board.kicad_pcb")

    assert "No violations" in out
    assert "PASS" in out


def test_a_failed_drc_call_is_reported(workspace, monkeypatch):
    """The likeliest failure now that there is no sidecar to be unreachable:
    the pipeline step itself raises. Reported rather than left to propagate,
    the same as every other tool here."""
    def refuse(kind, body):
        raise RuntimeError("kicad-cli: command not found")
    monkeypatch.setattr(service, "call", refuse)

    out = tools.run_drc("board.kicad_pcb")

    assert "could not run" in out and "command not found" in out


def test_run_drc_names_its_custom_rules_file_when_present(workspace, monkeypatch):
    (workspace / "board.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    (workspace / "board.kicad_dru").write_text("(version 1)\n")
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"exit": 0, "report": "board.drc.json", "stdout": "", "stderr": ""}))

    out = tools.run_drc("board.kicad_pcb")

    assert "custom rules: board.kicad_dru" in out


def test_run_drc_says_no_custom_rules_when_none_exist(workspace, monkeypatch):
    (workspace / "board.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"exit": 0, "report": "board.drc.json", "stdout": "", "stderr": ""}))

    out = tools.run_drc("board.kicad_pcb")

    assert "custom rules: none (KiCad's project settings only)" in out


def test_a_missing_board_is_caught_before_the_network(workspace, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline({"exit": 0}, seen))

    out = tools.run_drc("nope.kicad_pcb")

    assert "nope.kicad_pcb" in out
    assert seen == []


# --- the verdict must follow severity, not the exit code --------------------

WARNINGS_ONLY = {
    **DRC,
    "violations": [{"type": "lib_footprint_issues", "severity": "warning",
                    "description": "library not in the table",
                    "items": [{"description": "Footprint R1",
                               "pos": {"x": 1.0, "y": 2.0}}]}],
}


def test_a_board_with_only_warnings_is_not_reported_as_failed(workspace, monkeypatch):
    """kicad-cli's --exit-code-violations returns 5 for ANY violation, warnings
    included, and lib_footprint_issues is unavoidable in a container whose
    library table is not configured. Calling that FAIL sent one ladder rep off
    to fix a board that was already correct."""
    (workspace / "board.drc.json").write_text(json.dumps(WARNINGS_ONLY))
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"exit": 5, "report": "board.drc.json", "stdout": "", "stderr": ""}))

    out = tools.run_drc("board.kicad_pcb")

    assert "PASS" in out
    assert "1 warning" in out


def test_an_error_severity_violation_still_fails(workspace, monkeypatch):
    (workspace / "board.drc.json").write_text(json.dumps(DRC))
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"exit": 5, "report": "board.drc.json", "stdout": "", "stderr": ""}))

    assert "FAIL" in tools.run_drc("board.kicad_pcb")


# --- run_layout ----------------------------------------------------------

def test_layout_runs_the_whole_pipeline_and_reports_the_drc_verdict(workspace, monkeypatch):
    """One call, because "lay this out" is one intention. The steps -- board,
    outline, placement, routing, DRC -- are the pipeline's business, and an
    agent asked to iterate should change the positions and call again rather
    than re-orchestrate the steps by hand."""
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    seen = []
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})

    def call(kind, body):
        seen.append(kind)
        if kind == "pcb":
            return {"board": "c.kicad_pcb"}
        if kind == "route":
            return {"board": "c.kicad_pcb", "router": "0 unrouted"}
        if kind == "drc":
            return {"exit": 0, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb", "stdout": "ok"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]})

    assert seen == ["pcb", "outline", "rules", "place", "route", "drc"]
    assert "PASS" in out and "No violations" in out


def test_layout_stops_at_the_step_that_failed(workspace, monkeypatch):
    """A placement typo must not be reported as a routing problem."""
    (workspace / "c.net").write_text("(export)")

    def call(kind, body):
        if kind == "place":
            raise ValueError("no such footprint(s) on the board: R9")
        return {"board": "c.kicad_pcb"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout("c.net", 30, 20, positions={"R9": [8, 10, 0]})

    assert "place" in out and "R9" in out


def test_layout_can_skip_routing(workspace, monkeypatch):
    """Placement is the part under test when a board will not route; running
    the router anyway buys a slow answer to a question nobody asked."""
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps(DRC))
    seen = []

    def call(kind, body):
        seen.append(kind)
        if kind == "drc":
            return {"exit": 5, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb"}

    monkeypatch.setattr(service, "call", call)
    tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]}, route=False)

    assert "route" not in seen


# --- the verdict must follow severity, not the exit code (layout) -----------

def test_layout_fails_when_nets_are_left_unrouted(workspace, monkeypatch):
    """A placed board with no copper carries no ERRORS -- unconnected items are
    reported separately -- so severity alone would call it a pass."""
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps(
        {**WARNINGS_ONLY, "unconnected_items": [{"type": "unconnected_items",
                                                 "severity": "error",
                                                 "description": "GND",
                                                 "items": []}]}))
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})

    def call(kind, body):
        if kind == "drc":
            return {"exit": 5, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]})

    assert "FAIL — NOT DONE: 0 DRC error(s), 1 unconnected" in out


# --- relational placement -----------------------------------------------------

def test_layout_accepts_arrange_ops_instead_of_coordinates(workspace, monkeypatch):
    """The agent knows it wants the resistors in a row; it does not know that
    R_Axial_DIN0207 is 13mm long. `arrange` moves that arithmetic to the
    pipeline, which has the bounding boxes."""
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    seen = []
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})

    def call(kind, body):
        seen.append(kind)
        if kind == "drc":
            return {"exit": 0, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb", "stdout": "arranged"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout(
        "c.net", 60, 40,
        arrange=[{"op": "align", "refs": ["R1"], "axis": "y", "at": 12}])

    assert "arrange" in seen and "place" not in seen
    assert "PASS" in out


def test_a_layout_with_neither_positions_nor_arrange_is_refused(workspace, monkeypatch):
    (workspace / "c.net").write_text("(export)")
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline({"board": "c"}, seen))

    out = tools.run_layout("c.net", 60, 40)

    assert "positions" in out and "arrange" in out
    assert seen == [], "nothing to place must not reach the network"


def test_an_arrangement_that_cannot_fit_reports_the_measurement(workspace, monkeypatch):
    """The refusal is the feature. A count of unrouted nets gave the model
    nothing to steer by; 'they are 75.2mm of footprint in 56.0mm of room' does."""
    (workspace / "c.net").write_text("(export)")

    def call(kind, body):
        if kind == "arrange":
            raise ValueError(
                "space_evenly cannot fit R1, R2 between x=2.0 and x=58.0: they "
                "are 75.2mm of footprint in 56.0mm of room.")
        return {"board": "c.kicad_pcb"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout(
        "c.net", 60, 40, arrange=[{"op": "space_evenly", "refs": ["R1", "R2"],
                                   "axis": "x", "from": 2, "to": 58}])

    assert "75.2mm" in out and "56.0mm" in out


# --- footprints are checked before the netlist reaches kinet2pcb --------------

def test_unresolvable_footprints_are_caught_before_the_network(workspace, monkeypatch):
    """The live failure this removes, in full: the model wrote a netlist whose
    footprints named `Device`, `Regulator_Linear` and `Connector` -- SYMBOL
    library names, which are not footprint libraries -- and the only report
    was, three layers down,

        assert "PgmOrNull()" failed in register_action()
        KeyError: 'Device'

    a pcbnew assertion that is pure noise in front of a Python traceback from
    inside a dependency. The model called the tool again unchanged, twice,
    until the loop breaker stopped the turn.

    Caught here the answer names the pair, the fault and what IS available,
    and costs no container.
    """
    monkeypatch.setattr(paths, "FP_ROOT", workspace / "fp")
    (workspace / "n.net").write_text(
        '(export (comp (ref "BT1") (footprint "Device:Battery_Cell")))')
    (workspace / "fp" / "Diode_THT.pretty").mkdir(parents=True)
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline({"ok": True}, seen))

    out = tools.run_layout("n.net", 80, 60, arrange=[{"op": "rotate", "refs": ["BT1"], "degrees": 90}])

    assert "Device:Battery_Cell" in out
    assert "Diode_THT" in out          # what is actually here
    assert seen == []                  # the pipeline was never asked


def test_a_netlist_whose_footprints_resolve_still_reaches_the_pipeline(workspace, monkeypatch):
    """The guard must not become a wall: a good netlist goes through."""
    monkeypatch.setattr(paths, "FP_ROOT", workspace / "fp")
    pretty = workspace / "fp" / "Diode_THT.pretty"
    pretty.mkdir(parents=True)
    (pretty / "D_DO-35.kicad_mod").write_text("(footprint)")
    (workspace / "n.net").write_text(
        '(export (comp (ref "D1") (footprint "Diode_THT:D_DO-35")))')
    seen = []
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", fake_pipeline({"ok": True}, seen))

    tools.run_layout("n.net", 80, 60, arrange=[{"op": "rotate", "refs": ["BT1"], "degrees": 90}])

    assert seen != []


# --- the verdict has to be about the same report it prints -----------------
#
# Measured 2026-09-11 on a live 26-minute session: run_kicad_check(kind="erc")
# answered "PASS (ERC, kicad-cli exit 5)" and then printed two errors
# underneath it, including "Both RESET and GND are attached to the same
# items". The model could not tell whether it was finished, so it kept going
# for another forty steps -- writing a post-processor to delete power symbols
# out of generated KiCad files, which is the wrong layer and did real damage.
#
# The cause: kicad-cli's ERC report has NO top-level "violations". They live
# under sheets[].violations, one array per sheet. _verdict was written against
# the DRC report, which does have a top-level array, so every ERC check read
# zero errors and passed. Confirmed against the report that session produced.
#
# run_drc only ever feeds _verdict a DRC report -- there is no ERC tool here --
# but _verdict itself still reads both shapes generically (it shares the
# reader circuit_mcp.kicad.violations uses), so these are kept as direct tests
# of that function.


def erc_report(*severities, sheets=1):
    """The shape kicad-cli writes for a SCHEMATIC, which is not the board's."""
    per_sheet = [{"path": "/", "violations": [
        {"severity": s, "type": "power_pin_not_driven", "description": s}
        for s in severities]}] * sheets
    return {"$schema": "https://schemas.kicad.org/erc.v1.json",
            "source": "toggle-leds.kicad_sch", "sheets": per_sheet}


def test_an_erc_report_with_errors_does_not_pass():
    verdict, why = tools._verdict(erc_report("error", "warning"))
    assert verdict == "FAIL"
    assert why and "1 DRC error(s)" in why


def test_an_erc_report_with_only_warnings_passes():
    """The reason severity decides at all: lib_footprint_issues is
    unavoidable whenever the footprint library table is not configured, and
    failing every correct board on it was the defect this function already
    existed to prevent. That behaviour is unchanged -- it just has to read
    the right array first."""
    assert tools._verdict(erc_report("warning", "warning"))[0] == "PASS"


def test_errors_on_any_sheet_count():
    report = {"sheets": [{"violations": []},
                         {"violations": [{"severity": "error", "type": "x"}]}]}
    assert tools._verdict(report)[0] == "FAIL"


def test_a_board_report_is_still_read_the_way_it_always_was():
    board = {"violations": [{"severity": "error", "type": "shorting_items"}],
             "unconnected_items": []}
    assert tools._verdict(board)[0] == "FAIL"
    assert tools._verdict({"violations": [], "unconnected_items": []})[0] == "PASS"
    assert tools._verdict({"violations": [], "unconnected_items": [{"x": 1}]})[0] == "FAIL"


def test_a_failing_verdict_says_not_done_with_both_counts():
    """Release check 1 ended with a model reporting success over 21 DRC
    errors. The verdict now always names both counts and says NOT DONE."""
    report = {"violations": [{"severity": "error", "type": t}
                             for t in ["clearance"] * 19 + ["hole_clearance", "courtyards_overlap"]],
              "unconnected_items": [{"x": 1}, {"x": 2}, {"x": 3}]}
    assert tools._verdict(report) == (
        "FAIL", "NOT DONE: 21 DRC error(s) (clearance, courtyards_overlap, hole_clearance), 3 unconnected")


def test_a_clean_verdict_says_so_with_both_counts():
    assert tools._verdict({"violations": [], "unconnected_items": []}) == (
        "PASS", "DRC clean, 0 unconnected")


def test_run_drc_headline_leads_with_the_verdict(workspace, monkeypatch):
    (workspace / "board.drc.json").write_text(json.dumps(DRC))
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"exit": 5, "report": "board.drc.json", "stdout": "", "stderr": ""}))
    out = tools.run_drc("board.kicad_pcb")
    assert out.startswith("FAIL — NOT DONE: 1 DRC error(s) (clearance), 0 unconnected"), out


# --- board design rules -------------------------------------------------------

def _pipeline(seen):
    def call(kind, body):
        seen.append((kind, body))
        if kind == "drc":
            return {"exit": 0, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb", "stdout": "ok"}
    return call


def test_rules_are_sent_before_the_router_sees_the_board(workspace, monkeypatch):
    """The router reads the default net class out of the Specctra export, so
    rules set after routing would produce a board routed at one width and
    measured against another."""
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    seen = []
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", _pipeline(seen))

    tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]},
                     rules={"track_width_mm": 0.15})

    steps = [s for s, _ in seen]
    assert "rules" in steps
    assert steps.index("rules") < steps.index("route")
    body = next(p for s, p in seen if s == "rules")
    assert body["rules"] == {"track_width_mm": 0.15}


def test_without_rules_the_step_still_runs_for_the_fab_profile(workspace, monkeypatch):
    """Without the agent's rules, KiCad's defaults (edge 0.5, clearance 0.0)
    are no fab's numbers; the step runs anyway and applies the profile."""
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    seen = []
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", _pipeline(seen))

    tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]})

    (_, body), = [(s, b) for s, b in seen if s == "rules"]
    assert not body["rules"]


def test_layout_names_the_fab_profile_it_applied(workspace, monkeypatch):
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})

    def call(kind, body):
        if kind == "rules":
            return {"board": "c.kicad_pcb", "stdout": "ok",
                    "profile": "jlcpcb-2layer-standard", "agent_rules": ["clearance_mm"]}
        if kind == "drc":
            return {"exit": 0, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb", "stdout": "ok"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]},
                           rules={"clearance_mm": 0.15})

    assert "fab rules: jlcpcb-2layer-standard (floors) + your rules: clearance_mm" in out


def test_layout_notes_when_a_human_kicad_dru_was_kept(workspace, monkeypatch):
    """service.call('rules', ...) backs up a human-written .kicad_dru rather
    than silently overwriting it; run_layout has to say so in its note, or
    the agent never learns the file exists."""
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})

    def call(kind, body):
        if kind == "rules":
            return {"board": "c.kicad_pcb", "stdout": "ok",
                    "profile": "jlcpcb-2layer-standard", "agent_rules": [],
                    "kept_dru": "c.kicad_dru.orig"}
        if kind == "drc":
            return {"exit": 0, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb", "stdout": "ok"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]})

    assert "your .kicad_dru kept as c.kicad_dru.orig" in out


def test_an_unknown_rule_is_refused_with_the_ones_that_exist(workspace, monkeypatch):
    """A typo that silently sets nothing leaves a board measured against
    limits nobody chose -- the same reason an unknown reference is refused
    rather than ignored."""
    (workspace / "c.net").write_text("(export)")
    seen = []
    monkeypatch.setattr(service, "call", _pipeline(seen))

    out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]},
                           rules={"clearence_mm": 0.15})

    assert "clearence_mm" in out
    assert "clearance_mm" in out
    assert seen == [], "nothing is sent when the request is malformed"


def test_a_rule_that_is_not_a_positive_length_is_refused(workspace, monkeypatch):
    (workspace / "c.net").write_text("(export)")
    monkeypatch.setattr(service, "call", _pipeline([]))

    for bad in (0, -0.1, "thin", None):
        out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]},
                               rules={"track_width_mm": bad})
        assert "track_width_mm" in out and "millimetres" in out, bad


def test_the_rules_step_names_itself_when_it_fails(workspace, monkeypatch):
    """A rule the pipeline rejects reported as a placement problem sends the
    reader to the wrong half of the pipeline."""
    (workspace / "c.net").write_text("(export)")

    def call(kind, body):
        if kind == "rules":
            return {"error": "the board could not be loaded"}
        return {"board": "c.kicad_pcb"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]},
                           rules={"clearance_mm": 0.13})
    assert "'rules' step" in out


# --- net classes, applied before the router sees the board -------------------

def test_run_layout_applies_netclasses_when_present(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "call",
                        lambda kind, body: calls.append(kind) or
                        {"board": "b.kicad_pcb", "exit": 0, "report": "b.drc.json"})
    monkeypatch.setattr(rules, "apply_netclasses",
                        lambda board, f: {"classes": ["a"], "assigned": 2, "missing": []})
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    (workspace / "b.net").write_text("(export (components) (nets))")
    (workspace / "b.netclasses.json").write_text("{}")
    (workspace / "b.drc.json").write_text('{"violations": [], "unconnected_items": []}')
    text = tools.run_layout("b.net", 30, 20, arrange=[{"op": "rotate", "refs": ["R1"], "degrees": 0}])
    assert "net classes applied: 1 classes on 2 nets" in text, text


def test_run_layout_finds_netclasses_beside_a_netlist_in_a_subfolder(workspace, monkeypatch):
    """set_net_classes writes <netlist>.netclasses.json BESIDE the netlist; the
    layout used to look for it at the workspace root and silently ran with
    default widths."""
    seen = []
    monkeypatch.setattr(service, "call",
                        lambda kind, body: {"board": "hw/b.kicad_pcb", "exit": 0, "report": "b.drc.json"})
    monkeypatch.setattr(rules, "apply_netclasses",
                        lambda board, f: seen.append(f) or {"classes": ["a"], "assigned": 1, "missing": []})
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    (workspace / "hw").mkdir()
    (workspace / "hw" / "b.net").write_text("(export (components) (nets))")
    (workspace / "hw" / "b.netclasses.json").write_text("{}")
    (workspace / "b.drc.json").write_text('{"violations": [], "unconnected_items": []}')
    text = tools.run_layout("hw/b.net", 30, 20, positions={"R1": [1, 1, 0]})
    assert seen == ["hw/b.netclasses.json"], text
    assert "net classes applied" in text


def test_run_layout_names_the_pad_limited_nets(workspace, monkeypatch):
    monkeypatch.setattr(service, "call",
                        lambda kind, body: {"board": "b.kicad_pcb", "exit": 0, "report": "b.drc.json"})
    monkeypatch.setattr(rules, "apply_netclasses",
                        lambda board, f: {"classes": ["a", "a@0.6"], "assigned": 2, "missing": [],
                                          "capped": [["VBUS", 2.5, 0.6]]})
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    (workspace / "b.net").write_text("(export (components) (nets))")
    (workspace / "b.netclasses.json").write_text("{}")
    (workspace / "b.drc.json").write_text('{"violations": [], "unconnected_items": []}')
    text = tools.run_layout("b.net", 30, 20, arrange=[{"op": "rotate", "refs": ["R1"], "degrees": 0}])
    assert "pad-limited: VBUS 2.5→0.6 mm" in text, text


# --- repair runs after routing and before DRC, so DRC can still catch it -----

def test_finish_runs_after_route_and_before_drc(workspace, monkeypatch):
    """Task 5 review requirement: rules.finish widens narrowed tracks with no
    clearance check of its own, so it has to run where DRC can still report
    any clearance problem the widening causes -- after route, before drc."""
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    order = []

    def call(kind, body):
        order.append(kind)
        if kind == "drc":
            return {"exit": 0, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb"}

    def finish(board, pour_net="GND"):
        order.append("finish")
        return {"widened": [], "solid": []}

    monkeypatch.setattr(service, "call", call)
    monkeypatch.setattr(rules, "finish", finish)

    tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]})

    assert order.index("route") < order.index("finish") < order.index("drc"), order


# --- the overwrite guard -------------------------------------------------------

def test_run_layout_refuses_a_board_edited_outside_the_tools(workspace, monkeypatch):
    called = []
    monkeypatch.setattr(service, "call", lambda kind, body: called.append(kind) or {})
    (workspace / "b.net").write_text("(export (components) (nets))")
    (workspace / "b.kicad_pcb").write_text("(kicad_pcb)")
    tools.record_board("b.kicad_pcb")
    assert not tools.edited_outside("b.kicad_pcb")
    (workspace / "b.kicad_pcb").write_text("(kicad_pcb (edited))")
    text = tools.run_layout("b.net", 30, 20, arrange=[])
    assert "changed since the tools last wrote it" in text, text
    assert called == [], "nothing may run before the human agrees"


def test_fill_zones_does_not_launder_a_human_edit(workspace, monkeypatch):
    """fill_zones used to record the board's hash after pouring even when a
    human had edited it, after which run_layout would overwrite that edit
    without asking."""
    (workspace / "b.net").write_text("(export (components) (nets))")
    (workspace / "b.kicad_pcb").write_text("(kicad_pcb)")
    tools.record_board("b.kicad_pcb")
    (workspace / "b.kicad_pcb").write_text("(kicad_pcb (edited))")
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"zones": 2, "replaced": 0, "filled_area_mm2": 1.0, "unfilled": []}))
    tools.fill_zones("b.kicad_pcb")
    called = []
    monkeypatch.setattr(service, "call", lambda kind, body: called.append(kind) or {})
    text = tools.run_layout("b.net", 30, 20, positions={"R1": [1, 1, 0]})
    assert "changed since the tools last wrote it" in text, text
    assert called == []


def test_fill_zones_keeps_recording_a_board_the_tools_wrote(workspace, monkeypatch):
    (workspace / "b.kicad_pcb").write_text("(kicad_pcb)")
    tools.record_board("b.kicad_pcb")
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})

    def pour(kind, body):
        (workspace / "b.kicad_pcb").write_text("(kicad_pcb (zone))")
        return {"zones": 2, "replaced": 0, "filled_area_mm2": 1.0, "unfilled": []}

    monkeypatch.setattr(service, "call", pour)
    tools.fill_zones("b.kicad_pcb")
    assert not tools.edited_outside("b.kicad_pcb")


def test_a_board_with_no_record_counts_as_edited_outside(workspace):
    (workspace / "b.kicad_pcb").write_text("(kicad_pcb)")
    assert tools.edited_outside("b.kicad_pcb")
    assert not tools.edited_outside("missing.kicad_pcb")


def test_a_board_the_pipeline_wrote_is_recorded_even_if_a_later_step_fails(
        workspace, monkeypatch):
    """record_board used to run only after DRC succeeded, but the board is on
    disk from the `pcb` step onward -- outline, placement, route and finish
    all write to it too. A placement refusal, a routing error or a DRC hiccup
    left a board the tools had just written with no hash, so the NEXT
    run_layout call refused it as 'edited outside the tools' even though
    nobody had touched it."""
    (workspace / "c.net").write_text("(export)")
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})

    def call(kind, body):
        if kind == "pcb":
            (workspace / "c.kicad_pcb").write_text("(kicad_pcb)")
            return {"board": "c.kicad_pcb"}
        if kind == "route":
            raise RuntimeError("freerouting produced no session file")
        return {"board": "c.kicad_pcb"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]})

    assert "route" in out
    assert not tools.edited_outside("c.kicad_pcb"), \
        "the board the tools just wrote must not read as a human's edit"

    # A second call must reach the pipeline rather than being refused for a
    # board the tools themselves left behind.
    called_again = []
    monkeypatch.setattr(service, "call",
                        lambda kind, body: called_again.append(kind) or {})
    out2 = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]})
    assert "changed since the tools last wrote it" not in out2
    assert called_again, "the second call must have reached the pipeline"


# --- SKiDL ---------------------------------------------------------------

def test_run_skidl_reports_the_netlist_it_wrote(workspace):
    (workspace / "tiny.py").write_text(
        "from skidl import Part, Net, generate_netlist\n"
        "r = Part('Device', 'R', footprint='Resistor_SMD:R_0805_2012Metric')\n"
        "a, b = Net('A'), Net('B')\n"
        "a += r[1]; b += r[2]\n"
        "generate_netlist(file_='tiny.net')\n")
    text = tools.run_skidl("tiny.py")
    assert text.startswith("netlist: tiny.net"), text


def test_run_skidl_names_skidls_files_after_the_script_and_reports_its_erc(workspace):
    """Found on a live run (2026-09-29): run_skidl's launcher (python3 -c)
    imported SKiDL before the script ran, SKiDL found no __main__.__file__
    and named its files skidl_REPL.*, and run_skidl -- which reads
    <script>*.erc -- answered "no ERC file" for every board."""
    (workspace / "tiny.py").write_text(
        "from skidl import Part, Net, generate_netlist, ERC\n"
        "r = Part('Device', 'R', footprint='Resistor_SMD:R_0805_2012Metric')\n"
        "a, b = Net('A'), Net('B')\n"
        "a += r[1]; b += r[2]\n"
        "ERC()\n"
        "generate_netlist(file_='tiny.net')\n")
    text = tools.run_skidl("tiny.py")
    assert "no ERC file" not in text, text
    assert (workspace / "tiny.erc").is_file()
    assert not list(workspace.glob("skidl_REPL*")), sorted(p.name for p in workspace.iterdir())


def test_run_skidl_reports_a_failing_script(workspace):
    (workspace / "bad.py").write_text("raise SystemExit('boom')\n")
    assert "failed" in tools.run_skidl("bad.py")


def test_run_skidl_refuses_a_path_outside_the_workspace(workspace):
    assert "outside" in tools.run_skidl("../evil.py")
    assert "absolute" in tools.run_skidl("/etc/passwd")


def test_run_skidl_names_a_missing_script(workspace):
    assert "no such script" in tools.run_skidl("nope.py")


# --- the copper pour, and the package a fab is sent -------------------------
#
# Boards built by run_layout come from kinet2pcb, which produces no zones at
# all -- measured on a 33-part board: zones 0. So "fill the zones" with
# nothing to fill is a no-op, and fill_zones creates the pour too.

def test_filling_zones_asks_for_a_pour_on_both_sides_by_default(workspace, monkeypatch):
    seen = []
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": ["U1 pad 3"]})
    # The thermal repair's own DRC is covered below; this test is the pour call.
    monkeypatch.setattr(tools, "repair_thermals", lambda board, net: {"solid": [], "still": []})
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"zones": 2, "replaced": 0, "filled_area_mm2": 5241.4, "unfilled": []},
        seen))

    out = tools.fill_zones("board.kicad_pcb")

    (kind, payload), = seen
    assert kind == "zone"
    assert payload["net"] == "GND"
    assert payload["layers"] == ["F.Cu", "B.Cu"]
    assert "POURED" in out and "5241.4" in out
    # The order that matters: DRC reads the board on disk, so a fill that is
    # not followed by a re-check leaves a verdict computed without the copper.
    assert "run_drc" in out


def test_a_refilled_pour_says_it_replaced_rather_than_added(workspace, monkeypatch):
    """Idempotence is the property that makes this safe to call after moving a
    part, which is exactly when a fill goes stale. Verified against real KiCad
    10.0.5: two runs, 2 zones both times, replaced 0 then 2."""
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"zones": 2, "replaced": 2, "filled_area_mm2": 5241.4, "unfilled": []}))

    assert "REFILLED" in tools.fill_zones("board.kicad_pcb")


def test_a_pour_that_would_not_fill_is_named_not_swallowed(workspace, monkeypatch):
    """A zone with no pad of its net inside it fills to nothing, and the board
    looks finished either way."""
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"zones": 1, "replaced": 0, "filled_area_mm2": 0.0, "unfilled": ["GND"]}))

    out = tools.fill_zones("board.kicad_pcb")

    assert "WOULD NOT FILL" in out and "GND" in out


def test_filling_zones_reports_the_solid_pad_repair(workspace, monkeypatch):
    """fill_zones makes the same solid-pad repair run_layout makes after
    routing, because a fill created after layout would otherwise leave the
    pour net's SMD pads on a thermal relief nothing asked for."""
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [],
                                                       "solid": ["U1 pad 3", "U1 pad 4"]})
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"zones": 2, "replaced": 0, "filled_area_mm2": 100.0, "unfilled": []}))

    out = tools.fill_zones("board.kicad_pcb")

    assert "solid pour connection on 2 SMD pad(s)" in out


def test_exporting_gerbers_reports_the_package(workspace, monkeypatch):
    monkeypatch.setattr(service, "call", fake_pipeline({
        "dir": "board-gerbers",
        "files": ["board-F_Cu.gtl", "board-B_Cu.gbl", "board-Edge_Cuts.gm1",
                  "board-PTH.drl", "board-job.gbrjob"],
        "gerbers": ["board-F_Cu.gtl", "board-B_Cu.gbl", "board-Edge_Cuts.gm1"],
        "drill": ["board-PTH.drl"], "job": "board-job.gbrjob"}))

    out = tools.export_gerbers("board.kicad_pcb")

    assert "EXPORTED 5 files" in out
    assert "board-job.gbrjob" in out


def test_export_gerbers_defaults_the_output_directory_to_gerbers(workspace, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"dir": "gerbers", "files": [], "gerbers": [], "drill": [], "job": None}, seen))

    tools.export_gerbers("board.kicad_pcb")

    (kind, payload), = seen
    assert payload["out"] == "gerbers"


def test_an_unfilled_pour_warns_that_the_DRC_VERDICT_is_stale(workspace, monkeypatch):
    """The subtle half, and the reason this warns at all.

    Measured on KiCad 10.0.5, one board, B.Cu, three ways: unfilled with no
    --check-zones is 11,413 bytes and has no pour; unfilled WITH it is 118,017
    and does, because the export refills for the plot; a filled board is
    118,011. So the gerbers are right either way -- what is wrong is the DRC
    result already in hand, which was computed against the board on disk.
    """
    (workspace / "board.kicad_pcb").write_text(
        "(kicad_pcb\n  (zone\n    (net 2)\n  )\n)")
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"dir": "board-gerbers", "files": ["board-F_Cu.gtl"],
         "gerbers": ["board-F_Cu.gtl"], "drill": [], "job": None}))

    out = tools.export_gerbers("board.kicad_pcb")

    assert "WARNING" in out
    assert "not filled" in out
    assert "EXPORTED" in out, "the export still happened; only the verdict is suspect"


def test_a_filled_pour_exports_without_a_warning(workspace, monkeypatch):
    (workspace / "board.kicad_pcb").write_text(
        "(kicad_pcb\n  (zone\n    (net 2)\n    (filled_polygon (pts))\n  )\n)")
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"dir": "board-gerbers", "files": ["board-F_Cu.gtl"],
         "gerbers": ["board-F_Cu.gtl"], "drill": [], "job": None}))

    assert "WARNING" not in tools.export_gerbers("board.kicad_pcb")


def test_neither_tool_reaches_the_pipeline_for_a_board_that_is_not_there(workspace):
    """Before the network, like run_layout: a typo costs one step and
    not a half-built request."""
    assert "no such board" in tools.fill_zones("nope.kicad_pcb")
    assert "no such board" in tools.export_gerbers("nope.kicad_pcb")


_FAB = {"dir": "gerbers", "files": ["board-F_Cu.gtl"], "gerbers": ["board-F_Cu.gtl"],
        "drill": [], "job": None}


def _age(path, seconds):
    import os
    stat = path.stat()
    os.utime(path, (stat.st_atime - seconds, stat.st_mtime - seconds))


def test_export_gerbers_says_not_fabrication_ready_without_a_drc_report(workspace, monkeypatch):
    monkeypatch.setattr(service, "call", fake_pipeline(_FAB))
    out = tools.export_gerbers("board.kicad_pcb")
    assert out.startswith("NOT FABRICATION-READY: DRC not run"), out
    assert "EXPORTED" in out, "it still exports"


def test_export_gerbers_says_not_fabrication_ready_when_drc_failed(workspace, monkeypatch):
    (workspace / "board.drc.json").write_text(json.dumps(DRC))
    monkeypatch.setattr(service, "call", fake_pipeline(_FAB))
    out = tools.export_gerbers("board.kicad_pcb")
    assert out.startswith("NOT FABRICATION-READY: 1 DRC error(s), 0 unconnected"), out


def test_export_gerbers_says_not_fabrication_ready_when_drc_is_older_than_the_board(workspace, monkeypatch):
    (workspace / "board.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    _age(workspace / "board.drc.json", 60)
    monkeypatch.setattr(service, "call", fake_pipeline(_FAB))
    out = tools.export_gerbers("board.kicad_pcb")
    assert out.startswith("NOT FABRICATION-READY: DRC not run on the current board"), out


def test_export_gerbers_has_no_prefix_on_a_clean_current_board(workspace, monkeypatch):
    (workspace / "board.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    monkeypatch.setattr(service, "call", fake_pipeline(_FAB))
    out = tools.export_gerbers("board.kicad_pcb")
    assert out.startswith("EXPORTED"), out


# --- arrange ops are checked before anything is sent --------------------------

@pytest.mark.parametrize("op, why", [
    ({"op": "spin", "refs": ["R1"]}, "op"),
    ({"refs": ["R1"], "degrees": 90}, "op"),
    ({"op": "rotate", "refs": [], "degrees": 90}, "refs"),
    ({"op": "rotate", "refs": "R1", "degrees": 90}, "refs"),
    ({"op": "rotate", "refs": ["R1", 2], "degrees": 90}, "refs"),
    ({"op": "rotate", "refs": ["R1"]}, "degrees"),
    ({"op": "align", "refs": ["R1"], "axis": "y"}, "at"),
    ({"op": "align", "refs": ["R1"], "at": 3}, "axis"),
    ({"op": "space_evenly", "refs": ["R1"], "axis": "x", "from": 2}, "to"),
    ({"op": "space_evenly", "refs": ["R1"], "axis": "z", "from": 2, "to": 9}, "axis"),
    ("rotate", "object"),
])
def test_a_malformed_arrange_op_is_refused_with_examples(workspace, monkeypatch, op, why):
    (workspace / "c.net").write_text("(export)")
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline({"board": "c"}, seen))
    out = tools.run_layout("c.net", 30, 20,
                           arrange=[{"op": "rotate", "refs": ["R2"], "degrees": 0}, op])
    assert "arrange op 2" in out and why in out, out
    assert '{"op": "rotate", "refs": ["R1", "R2"], "degrees": 90}' in out
    assert '{"op": "align", "refs": ["R1", "R2"], "axis": "y", "at": 12}' in out
    assert '{"op": "space_evenly", "refs": ["R1", "R2", "R3"], "axis": "x", "from": 2, "to": 58}' in out
    assert seen == [], "nothing is sent when the request is malformed"


def test_arrange_must_be_a_list(workspace, monkeypatch):
    (workspace / "c.net").write_text("(export)")
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline({"board": "c"}, seen))
    out = tools.run_layout("c.net", 30, 20, arrange={"op": "rotate", "refs": ["R1"], "degrees": 0})
    assert "arrange must be a list" in out, out
    assert seen == []


# --- paths are confined before anything is read -------------------------------

@pytest.mark.parametrize("bad, why", [("../x.kicad_pcb", "outside"), ("/etc/hostname", "absolute")])
def test_board_tools_refuse_paths_outside_the_workspace(workspace, monkeypatch, bad, why):
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline({"exit": 0}, seen))
    for tool in (tools.run_drc, tools.fill_zones, tools.export_gerbers):
        out = tool(bad)
        assert why in out, (tool.__name__, out)
    assert seen == []


def test_run_layout_refuses_a_netlist_outside_the_workspace(workspace, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline({"exit": 0}, seen))
    out = tools.run_layout("../c.net", 30, 20, positions={"R1": [1, 1, 0]})
    assert "outside" in out, out
    assert seen == []


def test_run_skidl_reports_a_timeout(workspace, monkeypatch):
    monkeypatch.setattr(service, "TIMEOUT", 1)
    (workspace / "slow.py").write_text("import time\ntime.sleep(5)\n")
    assert tools.run_skidl("slow.py") == "slow.py timed out after 1s"


def test_run_skidl_does_not_hand_secrets_to_the_script(workspace, monkeypatch):
    """In a hosted job the server holds the model provider's key; the agent's
    script is arbitrary code with a proxy to the internet."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-very-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8888")
    monkeypatch.setenv("KICAD_SYMBOL_DIR", "/syms")
    (workspace / "peek.py").write_text(
        "import os\nprint([os.environ.get(n) for n in "
        "('OPENROUTER_API_KEY', 'HTTPS_PROXY', 'KICAD_SYMBOL_DIR')])\n")
    out = tools.run_skidl("peek.py")
    assert "[None, None, '/syms']" in out, out


def test_hole_clearance_is_an_accepted_rule():
    assert "hole_clearance_mm" in tools.RULES
    assert tools.check_rules({"hole_clearance_mm": 0.2}) is None


# --- the thermal repair in fill_zones -----------------------------------------

def _starved_report(uuid):
    return {"violations": [{"type": "starved_thermal", "severity": "error", "items": [
        {"description": "Zone [GND] on F.Cu, priority 0", "uuid": "zone"},
        {"description": "PTH pad SH [GND] of J1", "uuid": uuid}]}]}


def test_the_repair_makes_only_the_named_pads_solid_and_cleans_up(workspace, monkeypatch):
    reports = [_starved_report("sh-1"), {"violations": []}]
    calls, solid = [], []

    def drc(kind, body):
        calls.append((kind, body))
        (workspace / body["out"]).write_text(json.dumps(reports.pop(0)))
        return {"exit": 5, "report": body["out"]}

    monkeypatch.setattr(service, "call", drc)
    monkeypatch.setattr(rules, "solid_pads",
                        lambda board, uuids: solid.extend(uuids) or {"changed": uuids, "missing": []})

    result = tools.repair_thermals("board.kicad_pcb", "GND")

    assert result == {"solid": [("J1", "SH")], "still": [], "uuids": ["sh-1"]}
    assert solid == ["sh-1"]
    # A temporary report, never the one check_done reads, and gone afterwards.
    assert [body["out"] for _, body in calls] == [".board.fill-drc.json"] * 2
    assert not (workspace / ".board.fill-drc.json").exists()
    assert not (workspace / "board.drc.json").exists()


def test_the_repair_does_nothing_when_nothing_starves(workspace, monkeypatch):
    def drc(kind, body):
        (workspace / body["out"]).write_text(json.dumps({"violations": []}))
        return {"exit": 0}

    monkeypatch.setattr(service, "call", drc)
    monkeypatch.setattr(rules, "solid_pads",
                        lambda *a: pytest.fail("no pad should be touched"))
    assert tools.repair_thermals("board.kicad_pcb", "GND") == {
        "solid": [], "still": [], "uuids": []}


def test_the_repair_does_nothing_without_a_report(workspace, monkeypatch):
    """No report on the FIRST check -- a fake pipeline, or a DRC that could
    not run at all -- means nothing to repair, exactly as before this fix:
    the fake-pipeline tests rely on this exact shape."""
    monkeypatch.setattr(service, "call", lambda kind, body: {"exit": 2})
    monkeypatch.setattr(rules, "solid_pads",
                        lambda *a: pytest.fail("no pad should be touched"))
    assert tools.repair_thermals("board.kicad_pcb", "GND") == {"solid": [], "still": []}


# --- a verifying DRC (after solid_pads) that fails must not read as success --

def test_a_failed_verifying_drc_is_reported_as_unverified_not_success(workspace, monkeypatch):
    """The second DRC -- the one that checks whether making a pad solid
    actually worked -- returns exit 2 and writes nothing. The old code
    ignored the exit code and re-read whatever report happened to still be
    on disk, which was the FIRST pass's report; every pad already made solid
    is filtered out of `starved` by the `solid` dict, so that stale report
    was misread as a clean pass."""
    reports = [_starved_report("sh-1")]

    def drc(kind, body):
        if reports:
            (workspace / body["out"]).write_text(json.dumps(reports.pop(0)))
            return {"exit": 5, "report": body["out"]}
        return {"exit": 2}

    monkeypatch.setattr(service, "call", drc)
    monkeypatch.setattr(rules, "solid_pads",
                        lambda board, uuids: {"changed": uuids, "missing": []})

    result = tools.repair_thermals("board.kicad_pcb", "GND")

    assert result["solid"] == [("J1", "SH")]
    assert result.get("error") and "not verified" in result["error"]
    assert not (workspace / ".board.fill-drc.json").exists()


def test_a_stale_report_is_deleted_before_the_verifying_drc_runs(workspace, monkeypatch):
    """The old code deleted the temporary report only in a top-level
    `finally`, so it sat on disk between passes. Proves the fix deletes it
    BEFORE each DRC, not only at the very end: by the time the verifying
    call below runs, the first pass's file must already be gone."""
    reports = [_starved_report("sh-1")]
    seen_exists_on_second_call = []

    def drc(kind, body):
        path = workspace / body["out"]
        if reports:
            path.write_text(json.dumps(reports.pop(0)))
            return {"exit": 5, "report": body["out"]}
        seen_exists_on_second_call.append(path.exists())
        return {"exit": 2}

    monkeypatch.setattr(service, "call", drc)
    monkeypatch.setattr(rules, "solid_pads",
                        lambda board, uuids: {"changed": uuids, "missing": []})

    result = tools.repair_thermals("board.kicad_pcb", "GND")

    assert seen_exists_on_second_call == [False], (
        "the temporary report must be deleted before the verifying DRC "
        "runs, not only in a final cleanup")
    assert result != {"solid": [("J1", "SH")], "still": []}
    assert result.get("error") and "not verified" in result["error"]


def test_fill_zones_reports_each_repaired_and_still_starved_pad(workspace, monkeypatch):
    (workspace / "board.kicad_pcb").write_text("(kicad_pcb)")
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"zones": 2, "replaced": 0, "filled_area_mm2": 100.0, "unfilled": []}))
    monkeypatch.setattr(tools, "repair_thermals", lambda board, net: {
        "solid": [("J1", "B1"), ("J1", "SH")], "still": [("J2", "1")]})

    out = tools.fill_zones("board.kicad_pcb")

    assert "SOLID: J1 B1" in out and "SOLID: J1 SH" in out
    assert "no need to move parts" in out
    assert "STILL STARVED: J2 1" in out


def test_a_thermal_check_that_fails_does_not_fail_the_pour(workspace, monkeypatch):
    (workspace / "board.kicad_pcb").write_text("(kicad_pcb)")
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"zones": 2, "replaced": 0, "filled_area_mm2": 100.0, "unfilled": []}))

    def broken(board, net):
        raise RuntimeError("making pads solid failed: boom")

    monkeypatch.setattr(tools, "repair_thermals", broken)

    out = tools.fill_zones("board.kicad_pcb")

    assert out.startswith("POURED GND")
    assert "the thermal check could not run: making pads solid failed: boom" in out


def test_a_thermal_repair_timeout_does_not_fail_the_pour(workspace, monkeypatch):
    """subprocess.TimeoutExpired is a subprocess.SubprocessError, and the
    repair's own DRC or pcbnew steps can time out the same way any other
    pcbnew step can."""
    (workspace / "board.kicad_pcb").write_text("(kicad_pcb)")
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})
    monkeypatch.setattr(service, "call", fake_pipeline(
        {"zones": 2, "replaced": 0, "filled_area_mm2": 100.0, "unfilled": []}))

    def timed_out(board, net):
        raise subprocess.TimeoutExpired(cmd="kicad-cli", timeout=60)

    monkeypatch.setattr(tools, "repair_thermals", timed_out)

    out = tools.fill_zones("board.kicad_pcb")

    assert out.startswith("POURED GND")
    assert "the thermal check could not run" in out


def _starved_many(*pads):
    return {"violations": [{"type": "starved_thermal", "severity": "error", "items": [
        {"description": "Zone [GND] on F.Cu, priority 0", "uuid": "zone"},
        {"description": f"PTH pad {pad} [GND] of J1", "uuid": uuid}]} for uuid, pad in pads]}


def _scripted_drc(workspace, reports, calls=None):
    def drc(kind, body):
        if calls is not None:
            calls.append(body["out"])
        (workspace / body["out"]).write_text(json.dumps(reports.pop(0)))
        return {"exit": 5}
    return drc


def test_a_pad_starved_by_the_repair_itself_is_repaired_on_the_next_pass(workspace, monkeypatch):
    # release2's shape: B1 goes solid, which cuts the neighbouring SH's spokes.
    reports = [_starved_many(("b1", "B1")), _starved_many(("sh", "SH")), {"violations": []}]
    solid = []
    monkeypatch.setattr(service, "call", _scripted_drc(workspace, reports))
    monkeypatch.setattr(rules, "solid_pads",
                        lambda board, uuids: solid.append(list(uuids)) or
                        {"changed": uuids, "missing": []})

    result = tools.repair_thermals("board.kicad_pcb", "GND")

    assert solid == [["b1"], ["sh"]]
    assert result == {"solid": [("J1", "B1"), ("J1", "SH")], "still": [],
                      "uuids": ["b1", "sh"]}


def test_the_repair_stops_at_its_cap_and_names_what_is_still_starved(workspace, monkeypatch):
    # A new pad every pass never converges: stop at the cap, say what is left.
    passes = tools.THERMAL_PASSES
    reports = [_starved_many((f"u{i}", f"P{i}")) for i in range(passes + 1)]
    monkeypatch.setattr(service, "call", _scripted_drc(workspace, reports))
    monkeypatch.setattr(rules, "solid_pads",
                        lambda board, uuids: {"changed": uuids, "missing": []})

    result = tools.repair_thermals("board.kicad_pcb", "GND")

    assert len(result["solid"]) == passes
    assert result["still"] == [("J1", f"P{passes}")]
    assert result["uuids"] == [f"u{i}" for i in range(passes)]
    assert not (workspace / ".board.fill-drc.json").exists()


def test_a_pad_solid_pads_could_not_change_is_never_reported_solid(workspace, monkeypatch):
    """Only `changed` uuids count as repaired. A `missing` uuid -- one
    `solid_pads` could not find or touch -- must never show up as SOLID, and
    if DRC still names it afterwards, it belongs in `still`."""
    reports = [_starved_report("sh-1")] * (tools.THERMAL_PASSES + 1)

    def drc(kind, body):
        (workspace / body["out"]).write_text(json.dumps(reports.pop(0)))
        return {"exit": 5, "report": body["out"]}

    monkeypatch.setattr(service, "call", drc)
    monkeypatch.setattr(rules, "solid_pads",
                        lambda board, uuids: {"changed": [], "missing": uuids})

    result = tools.repair_thermals("board.kicad_pcb", "GND")

    assert result["solid"] == []
    assert result["uuids"] == []
    assert ("J1", "SH") in result["still"]


def test_layout_says_the_router_kept_clear_of_npth_holes(workspace, monkeypatch):
    (workspace / "c.net").write_text("(export)")
    (workspace / "c.drc.json").write_text(json.dumps({**DRC, "violations": []}))
    monkeypatch.setattr(rules, "finish",
                        lambda board, pour_net="GND": {"widened": [], "solid": []})

    def call(kind, body):
        if kind == "route":
            return {"board": "c.kicad_pcb", "router": "done", "npth_keepouts": 2}
        if kind == "drc":
            return {"exit": 0, "report": "c.drc.json", "stdout": "", "stderr": ""}
        return {"board": "c.kicad_pcb", "stdout": "ok"}

    monkeypatch.setattr(service, "call", call)
    out = tools.run_layout("c.net", 30, 20, positions={"R1": [8, 10, 0]})

    assert "router kept clear of 2 NPTH hole(s)" in out
