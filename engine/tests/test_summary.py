import json
import shutil

from circuit_mcp import summary
from conftest import FIXTURES

BEGIN, END = summary.BEGIN, summary.END


def test_writes_computed_sections_and_keeps_the_agents_text(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    (workspace / "blinker.netclasses.json").write_text(json.dumps({
        "version": 1,
        "nets": {"VBUS": {"netclass": "power_high_current_0_5a_to_2a", "class": "power_high_current",
                          "tier": "0_5a_to_2a", "width_mm": 0.8, "clearance_mm": 0.3,
                          "source": "jev", "status": "accepted"}},
        "needs_review": [{"net": "+3V3", "reason": "resolver held: lowers jev's rating"}],
    }))
    (workspace / "README.md").write_text("# Blinker\n\nMy own notes.\n")
    text = summary.summarize_project("blinker.net")
    readme = (workspace / "README.md").read_text()
    assert "My own notes." in readme
    assert BEGIN in readme and END in readme
    assert "## Parts" in readme and "U2" in readme
    assert "| VBUS | power_high_current | 0_5a_to_2a | 0.8 |" in readme
    assert "## Nets awaiting your decision" in readme and "+3V3" in readme
    assert "README.md" in text


def test_pad_limited_power_nets_show_the_applied_width_and_await_a_decision(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    (workspace / "blinker.netclasses.json").write_text(json.dumps({
        "version": 1,
        "nets": {
            "VBUS": {"netclass": "power_high_current_2a_to_5a", "class": "power_high_current",
                     "tier": "2a_to_5a", "width_mm": 2.5, "clearance_mm": 0.2,
                     "source": "jev", "status": "accepted"},
            "LED": {"netclass": "signal_0_5a_to_2a", "class": "signal",
                    "tier": "0_5a_to_2a", "width_mm": 0.8, "clearance_mm": 0.2,
                    "source": "jev", "status": "accepted"},
        },
        "needs_review": [],
    }))
    (workspace / "blinker.netclasses.applied.json").write_text(json.dumps({"VBUS": 0.6, "LED": 0.3}))
    text = summary.summarize_project("blinker.net")
    readme = (workspace / "README.md").read_text()
    assert "| VBUS | power_high_current | 2a_to_5a | 0.6 |" in readme
    assert "- **VBUS**: routed at 0.6 mm, pad-limited, below the 2.5 mm tier" in readme
    # Only power and ground nets are a current decision; a capped signal is not.
    assert "**LED**" not in readme
    assert "1 review line(s)" in text


def test_netclasses_are_read_beside_a_netlist_in_a_subfolder(workspace):
    (workspace / "hw").mkdir()
    shutil.copy(FIXTURES / "blinker.net", workspace / "hw" / "blinker.net")
    (workspace / "hw" / "blinker.netclasses.json").write_text(json.dumps({
        "version": 1,
        "nets": {"VBUS": {"netclass": "power_high_current_2a_to_5a", "class": "power_high_current",
                          "tier": "2a_to_5a", "width_mm": 2.5, "clearance_mm": 0.2,
                          "source": "jev", "status": "accepted"}},
        "needs_review": [],
    }))
    (workspace / "hw" / "blinker.netclasses.applied.json").write_text(json.dumps({"VBUS": 0.6}))
    summary.summarize_project("hw/blinker.net")
    readme = (workspace / "README.md").read_text()
    assert "| VBUS | power_high_current | 2a_to_5a | 0.6 |" in readme
    assert "Not classified" not in readme


def test_rerunning_replaces_only_the_computed_block(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    summary.summarize_project("blinker.net")
    first = (workspace / "README.md").read_text()
    summary.summarize_project("blinker.net")
    assert (workspace / "README.md").read_text() == first
    assert first.count(BEGIN) == 1


def test_review_line_count_is_zero_when_no_nets_are_flagged(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    (workspace / "blinker.netclasses.json").write_text(json.dumps({
        "version": 1, "nets": {}, "needs_review": [],
    }))
    text = summary.summarize_project("blinker.net")
    readme = (workspace / "README.md").read_text()
    assert "None." in readme
    assert "0 review line(s)" in text


def test_review_line_count_matches_the_number_of_flagged_nets(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    (workspace / "blinker.netclasses.json").write_text(json.dumps({
        "version": 1, "nets": {},
        "needs_review": [{"net": "+3V3", "reason": "a"}, {"net": "VBUS", "reason": "b"}],
    }))
    text = summary.summarize_project("blinker.net")
    assert "2 review line(s)" in text


def test_review_line_count_is_zero_when_there_is_no_netclasses_file(workspace):
    # Bug 1: the normal state before set_net_classes has ever run.
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    text = summary.summarize_project("blinker.net")
    assert "0 review line(s)" in text


def test_begin_without_end_leaves_the_file_untouched(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    original = "# Blinker\n\n" + BEGIN + "\nstray leftover text\n"
    (workspace / "README.md").write_text(original)

    text = summary.summarize_project("blinker.net")
    assert (workspace / "README.md").read_text() == original
    assert text == ("README.md has malformed circuit summary markers "
                     "(1 begin, 0 end); nothing was changed. Remove the "
                     "stray markers, then run summarize_project again.")

    # A second call must not compound the damage -- same file, same message.
    text2 = summary.summarize_project("blinker.net")
    assert (workspace / "README.md").read_text() == original
    assert text2 == text


def test_end_before_begin_does_not_crash_and_leaves_the_file_untouched(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    original = "# Blinker\n\n" + END + "\nstray\n" + BEGIN + "\nmore\n"
    (workspace / "README.md").write_text(original)

    text = summary.summarize_project("blinker.net")
    assert (workspace / "README.md").read_text() == original
    assert text == ("README.md has malformed circuit summary markers "
                     "(1 begin, 1 end); nothing was changed. Remove the "
                     "stray markers, then run summarize_project again.")


def test_two_complete_marker_pairs_leaves_the_file_untouched(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    original = "# Blinker\n\n" + BEGIN + "\nA\n" + END + "\n\n" + BEGIN + "\nB\n" + END + "\n"
    (workspace / "README.md").write_text(original)

    text = summary.summarize_project("blinker.net")
    assert (workspace / "README.md").read_text() == original
    assert text == ("README.md has malformed circuit summary markers "
                     "(2 begin, 2 end); nothing was changed. Remove the "
                     "stray markers, then run summarize_project again.")


def test_pipe_in_a_net_name_is_escaped_in_the_table_cell(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    (workspace / "blinker.netclasses.json").write_text(json.dumps({
        "version": 1,
        "nets": {"A|B": {"class": "signal", "tier": None, "width_mm": 0.25,
                         "source": "rule", "status": "accepted"}},
        "needs_review": [],
    }))
    summary.summarize_project("blinker.net")
    readme = (workspace / "README.md").read_text()
    assert "A\\|B" in readme
    assert "A|B |" not in readme


def test_a_board_path_outside_the_workspace_is_not_read(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    text = summary.summarize_project("blinker.net", board="/etc/hostname")
    readme = (workspace / "README.md").read_text()
    assert "/etc/hostname" in readme and "absolute" in readme
    assert "- File:" not in readme
    assert "Status: NOT READY" in text


# --- Status: NOT READY must be impossible to miss ---------------------------

def _with_board(workspace, drc=None, drc_age=0):
    import os
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    (workspace / "blinker.kicad_pcb").write_text("(kicad_pcb)")
    if drc is not None:
        path = workspace / "blinker.drc.json"
        path.write_text(json.dumps(drc))
        if drc_age:
            st = path.stat()
            os.utime(path, (st.st_atime - drc_age, st.st_mtime - drc_age))


def test_status_says_not_ready_with_both_counts(workspace):
    _with_board(workspace, {"violations": [{"severity": "error", "type": "clearance"}] * 21,
                            "unconnected_items": [{}, {}, {}]})
    text = summary.summarize_project("blinker.net")
    readme = (workspace / "README.md").read_text()
    assert "Status: NOT READY: 21 DRC error(s), 3 unconnected" in readme
    assert "Status: NOT READY: 21 DRC error(s), 3 unconnected" in text


def test_status_treats_a_stale_drc_as_not_run(workspace):
    _with_board(workspace, {"violations": [], "unconnected_items": []}, drc_age=60)
    text = summary.summarize_project("blinker.net")
    assert "Status: NOT READY: DRC not run on the current board" in text
    assert "Status: NOT READY: DRC not run on the current board" in (workspace / "README.md").read_text()


def test_status_says_drc_clean_on_a_clean_current_board(workspace):
    _with_board(workspace, {"violations": [], "unconnected_items": []})
    text = summary.summarize_project("blinker.net")
    assert "Status: DRC clean" in text
    assert "Status: DRC clean" in (workspace / "README.md").read_text()


def test_readme_has_a_schematic_line(workspace):
    import json as _json
    from circuit_mcp import schematic
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    summary.summarize_project("blinker.net")
    assert "Schematic: not generated: run `export_schematic`" in (workspace / "README.md").read_text()
    (workspace / "blinker.kicad_sch").write_text("(kicad_sch)")
    (workspace / "blinker.sch-erc.json").write_text(_json.dumps({"sheets": [{"violations": [
        {"severity": "warning", "type": "pin_not_connected"}]}]}))
    summary.summarize_project("blinker.net")
    assert ("Schematic: blinker.kicad_sch (PDF: blinker-schematic.pdf); "
            "ERC: 1 warning (1 pin_not_connected)") in (workspace / "README.md").read_text()
