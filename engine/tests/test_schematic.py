"""Generated schematics (schematic spec): the connectivity check, the ERC
summary, and the kicad-cli wrappers."""
import pytest

from circuit_mcp import schematic


def comp(ref):
    return f'(comp (ref "{ref}") (value "x") (footprint "L:F"))\n'


def net(code, name, *pins):
    nodes = "".join(f'(node (ref "{p.split(":")[0]}") (pin "{p.split(":")[1]}"))' for p in pins)
    return f'(net (code "{code}") (name "{name}") {nodes})\n'


def netlist(refs, nets):
    body = "".join(comp(r) for r in refs)
    wires = "".join(net(i + 1, name, *pins) for i, (name, pins) in enumerate(nets))
    return f"(export (components\n{body}) (nets\n{wires}))"


SKIDL = netlist(["R1", "D1", "U1"], [
    ("VCC", ["U1:8", "R1:1"]),
    ("N$1", ["R1:2", "D1:2"]),
    ("GND", ["U1:4", "D1:1"]),
])


def test_files_are_named_after_the_stem():
    assert schematic.files("board") == ("board.kicad_sch", "board-schematic.pdf", "board.sch-erc.json")


def test_compare_identical_netlists_match():
    assert schematic.compare(SKIDL, SKIDL) == []


def test_compare_ignores_net_names():
    renamed = SKIDL.replace('"N$1"', '"Net-(D1-A)"')
    assert schematic.compare(SKIDL, renamed) == []


def test_compare_ignores_power_symbols_and_single_pin_nets():
    kicad = netlist(["R1", "D1", "U1", "#PWR01", "#FLG01"], [
        ("VCC", ["U1:8", "R1:1", "#PWR01:1", "#FLG01:1"]),
        ("Net-(D1-A)", ["R1:2", "D1:2"]),
        ("GND", ["U1:4", "D1:1"]),
        ("unconnected-(U1-PB4-Pad3)", ["U1:3"]),
    ])
    assert schematic.compare(SKIDL, kicad) == []


def test_compare_names_a_missing_part():
    kicad = netlist(["R1", "U1"], [("VCC", ["U1:8", "R1:1"])])
    problems = schematic.compare(SKIDL, kicad)
    assert any("missing from the schematic" in p and "D1" in p for p in problems)


def test_compare_names_an_extra_part():
    kicad = netlist(["R1", "D1", "U1", "R9"], [
        ("VCC", ["U1:8", "R1:1"]), ("N$1", ["R1:2", "D1:2"]), ("GND", ["U1:4", "D1:1"])])
    assert any("only in the schematic" in p and "R9" in p for p in schematic.compare(SKIDL, kicad))


def test_compare_names_a_moved_pin():
    kicad = netlist(["R1", "D1", "U1"], [
        ("VCC", ["U1:8", "R1:1", "D1:2"]),
        ("GND", ["U1:4", "D1:1"]),
        ("N$1", ["R1:2"]),
    ])
    problems = schematic.compare(SKIDL, kicad)
    assert any("R1:2" in p and "D1:2" in p for p in problems)
    assert any("D1:2" in p and "U1:8" in p for p in problems)


def test_erc_summary_clean_after_config_noise():
    report = {"sheets": [{"violations": [
        {"severity": "warning", "type": "lib_symbol_issues"},
        {"severity": "warning", "type": "footprint_link_issues"}]}]}
    assert schematic.erc_summary(report) == "ERC: clean"


def test_erc_summary_counts_by_severity_and_type():
    report = {"sheets": [{"violations": [
        {"severity": "warning", "type": "pin_not_connected"},
        {"severity": "warning", "type": "pin_not_connected"},
        {"severity": "error", "type": "power_pin_not_driven"},
        {"severity": "warning", "type": "lib_symbol_issues"}]}]}
    assert schematic.erc_summary(report) == \
        "ERC: 1 error, 2 warnings (1 power_pin_not_driven, 2 pin_not_connected)"


def test_problems_on_an_unreadable_schematic(workspace):
    sch = workspace / "board.kicad_sch"
    sch.write_text("(this is not a schematic")
    (workspace / "board.net").write_text(SKIDL)
    out = schematic.problems(sch, workspace / "board.net")
    assert len(out) == 1 and "schematic netlist export failed" in out[0]


def test_kicad_cli_timeouts_become_runtime_errors(workspace, monkeypatch):
    import subprocess

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("kicad-cli", 1)
    monkeypatch.setattr(schematic.subprocess, "run", slow)
    (workspace / "board.kicad_sch").write_text("(kicad_sch)")
    with pytest.raises(RuntimeError, match="timed out"):
        schematic.schematic_netlist(workspace / "board.kicad_sch")


def test_schematic_netlist_refuses_a_non_regular_path(workspace, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("kicad-cli must not run on a non-regular path")
    monkeypatch.setattr(schematic.subprocess, "run", never)
    (workspace / "real.kicad_sch").write_text("(kicad_sch)")
    (workspace / "board.kicad_sch").symlink_to(workspace / "real.kicad_sch")
    (workspace / "folder.kicad_sch").mkdir()
    for name in ("board.kicad_sch", "folder.kicad_sch", "missing.kicad_sch"):
        with pytest.raises(RuntimeError, match="is not a regular file"):
            schematic.schematic_netlist(workspace / name)


import json
import pathlib
import shutil

from circuit_mcp import tools
from conftest import FIXTURES

GROUPED = FIXTURES / "projects" / "grouped"
BACKOFF1 = FIXTURES / "projects" / "backoff1"
MULTIUNIT = FIXTURES / "projects" / "multiunit"
# The DRIVER line right after the script's circuit has been written out for
# the netlist check: a crash injected after it is a generator crash.
CHECKED = 'print("netlist written " + nonce, flush=True)\n'


def _grouped(workspace):
    for name in ("board.py", "board_parts.py"):
        shutil.copy(GROUPED / name, workspace / name)
    ran = tools.run_skidl("board.py")
    assert ran.startswith("netlist: board.net"), ran


def test_export_schematic_draws_checks_and_reports(workspace):
    _grouped(workspace)
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("EXPORTED"), out
    for name in schematic.files("board"):
        assert (workspace / name).is_file(), name
    assert "schematic matches the netlist" in out
    assert "power flags:" in out and "GND" in out
    assert "ERC: clean" in out
    assert schematic.problems(workspace / "board.kicad_sch", workspace / "board.net") == []


def test_export_schematic_script_importing_a_sibling_module(workspace):
    _grouped(workspace)          # board.py imports board_parts.py
    assert schematic.export_schematic("board.py", "board.net").startswith("EXPORTED")


def test_export_schematic_leaves_the_netlist_and_folder_alone(workspace):
    _grouped(workspace)
    # A script that also draws its own schematic and writes a second netlist:
    # while export_schematic runs it, both calls must do nothing.
    with (workspace / "board.py").open("a") as f:
        f.write('generate_netlist(file_="other.net")\n'
                'generate_schematic(filepath=".", top_name="not_this_one")\n'
                'default_circuit.generate_netlist(file_="board.net")\n'
                'default_circuit.generate_schematic(filepath=".", top_name="nor_this_one")\n')
    net = workspace / "board.net"
    before_bytes, before_mtime = net.read_bytes(), net.stat().st_mtime_ns
    before = {p.name for p in workspace.iterdir()}
    schematic.export_schematic("board.py", "board.net")
    assert net.read_bytes() == before_bytes and net.stat().st_mtime_ns == before_mtime
    added = {p.name for p in workspace.iterdir()} - before
    assert added <= set(schematic.files("board")) | {"board.kicad_prl"}, added
    assert not (workspace / "other.net").exists()
    assert not (workspace / "not_this_one.kicad_sch").exists()
    assert not (workspace / "nor_this_one.kicad_sch").exists()


def test_export_schematic_on_a_real_project(workspace):
    shutil.copytree(BACKOFF1, workspace, dirs_exist_ok=True)
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("EXPORTED") and "schematic matches the netlist" in out, out


def test_export_schematic_reports_a_failing_script(workspace, monkeypatch):
    _grouped(workspace)
    (workspace / "board.py").write_text("raise RuntimeError('boom from the script')\n")
    before = {p.name for p in workspace.iterdir()}
    calls = []
    real_run = schematic.subprocess.run

    def counting_run(*args, **kwargs):
        calls.append((args, kwargs))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(schematic.subprocess, "run", counting_run)
    out = schematic.export_schematic("board.py", "board.net")
    assert "could not be drawn" in out and "boom from the script" in out
    assert "attempt" not in out
    assert len(calls) == 1, "a failing script must not be retried with another seed"
    assert {p.name for p in workspace.iterdir()} == before


def test_export_schematic_passes_pythonhashseed_to_the_driver(workspace, monkeypatch):
    _grouped(workspace)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return schematic.subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom from the script")

    monkeypatch.setattr(schematic.subprocess, "run", fake_run)
    schematic.export_schematic("board.py", "board.net")
    assert captured["env"] is not None and captured["env"]["PYTHONHASHSEED"] == "0"


def test_export_schematic_retries_after_a_generator_crash(workspace, monkeypatch):
    _grouped(workspace)
    # The least invasive way to force SKiDL's real generator to crash on a
    # specific seed, without adding test-only branches to the production
    # DRIVER: monkeypatch the DRIVER text itself (export_schematic writes it
    # to a temp file and runs it unmodified) so it raises right after the
    # check-netlist marker, only for one seed. The real script and the
    # real drawing on every other seed still run for real.
    crashy_driver = schematic.DRIVER.replace(
        CHECKED,
        CHECKED +
        'if seed == 42:\n'
        '    raise RuntimeError("boom from the generator")\n')
    assert crashy_driver != schematic.DRIVER
    monkeypatch.setattr(schematic, "DRIVER", crashy_driver)
    out = schematic.export_schematic("board.py", "board.net")
    assert "schematic matches the netlist" in out
    assert "attempt 2" in out


def _no_outputs(workspace, stem="board"):
    return not any(schematic.present(workspace / n) for n in schematic.files(stem))


def test_export_schematic_mismatch_on_every_seed_is_recorded_not_kept(workspace, monkeypatch):
    _grouped(workspace)
    monkeypatch.setattr(schematic, "problems", lambda sch, net: ["parts missing from the schematic: R9"])
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("SCHEMATIC NOT AVAILABLE:"), out
    assert "the board can still be finished" in out and "R9" in out
    assert _no_outputs(workspace)
    record = json.loads((workspace / "board.sch-unavailable.json").read_text())
    assert record["attempts"] == 5
    assert "does not match the netlist" in record["reason"]
    assert record["details"] == ["parts missing from the schematic: R9"]
    assert schematic.unavailable_reason(workspace / "board.sch-unavailable.json") == record["reason"]


def test_export_schematic_crash_on_every_seed_is_recorded(workspace, monkeypatch):
    _grouped(workspace)
    monkeypatch.setattr(schematic, "DRIVER", schematic.DRIVER.replace(
        CHECKED, CHECKED + 'raise RuntimeError("boom from the generator")\n'))
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("SCHEMATIC NOT AVAILABLE:") and "crashed" in out, out
    assert _no_outputs(workspace)
    record = json.loads((workspace / "board.sch-unavailable.json").read_text())
    assert "crashed" in record["reason"] and record["attempts"] == 5
    assert any("boom from the generator" in line for line in record["details"])


def test_export_schematic_on_a_multi_unit_part_is_not_available(workspace):
    # SKiDL's KiCad 10 writer leaves the pins of units 2 and up unconnected,
    # so no seed draws LM358's B and power units right (final review,
    # Critical 1). The board is correct; the drawing is what can't be had.
    shutil.copy(MULTIUNIT / "board.py", workspace / "board.py")
    ran = tools.run_skidl("board.py")
    assert ran.startswith("netlist: board.net"), ran
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("SCHEMATIC NOT AVAILABLE:"), out
    assert _no_outputs(workspace)
    record = json.loads((workspace / "board.sch-unavailable.json").read_text())
    assert record["attempts"] == 5 and record["details"]
    assert not list(workspace.glob("_etchwork_schematic*"))
    assert not (workspace / "board-erc.rpt").exists()


def test_a_successful_export_removes_an_old_unavailable_record(workspace):
    _grouped(workspace)
    record = workspace / "board.sch-unavailable.json"
    record.write_text('{"reason": "old", "details": [], "attempts": 5}')
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("EXPORTED"), out
    assert not record.exists()


def test_a_failing_script_leaves_an_unavailable_record_alone(workspace):
    _grouped(workspace)
    record = workspace / "board.sch-unavailable.json"
    record.write_text('{"reason": "old", "details": [], "attempts": 5}')
    (workspace / "board.py").write_text("raise RuntimeError('boom from the script')\n")
    out = schematic.export_schematic("board.py", "board.net")
    assert "could not be drawn" in out
    assert record.read_text() == '{"reason": "old", "details": [], "attempts": 5}'


def test_a_re_export_never_presents_the_old_drawing(workspace):
    _grouped(workspace)
    assert schematic.export_schematic("board.py", "board.net").startswith("EXPORTED")
    # The script now ends in sys.exit(0), so the driver never gets past it
    # to draw: the old drawing must not be reported (or left) as this run's.
    with (workspace / "board.py").open("a") as f:
        f.write("import sys\nsys.exit(0)\n")
    out = schematic.export_schematic("board.py", "board.net")
    assert not out.startswith("EXPORTED"), out
    assert "could not be drawn" in out and "before building the circuit" in out
    assert _no_outputs(workspace)


def test_export_schematic_leaves_files_the_run_did_not_make(workspace):
    _grouped(workspace)
    # Stands in for a user (or another tool) adding files to the workspace
    # while the drawing runs: only the run's own leftovers may be removed.
    with (workspace / "board.py").open("a") as f:
        f.write("import os\n"
                "open('notes_while_drawing.txt', 'w').write('mine')\n"
                "os.makedirs('folder_while_drawing', exist_ok=True)\n"
                "open('folder_while_drawing/x.txt', 'w').write('mine')\n")
    assert schematic.export_schematic("board.py", "board.net").startswith("EXPORTED")
    assert (workspace / "notes_while_drawing.txt").read_text() == "mine"
    assert (workspace / "folder_while_drawing" / "x.txt").read_text() == "mine"
    assert not list(workspace.glob("_etchwork_schematic*"))
    assert not (workspace / "board-erc.rpt").exists()


def test_export_schematic_script_in_a_subfolder_imports_its_sibling(workspace):
    src = workspace / "src"
    src.mkdir()
    for name in ("board.py", "board_parts.py"):
        shutil.copy(GROUPED / name, src / name)
    ran = tools.run_skidl("src/board.py")
    assert "board.net" in ran, ran
    out = schematic.export_schematic("src/board.py", "board.net")
    assert out.startswith("EXPORTED"), out


def test_export_schematic_retries_seeds_until_one_matches(workspace, monkeypatch):
    _grouped(workspace)
    calls = {"n": 0}

    def flaky_problems(sch, net):
        calls["n"] += 1
        return ["parts missing from the schematic: R9"] if calls["n"] == 1 else []

    monkeypatch.setattr(schematic, "problems", flaky_problems)
    out = schematic.export_schematic("board.py", "board.net")
    assert "schematic matches the netlist" in out
    assert "attempt 2" in out


def test_export_schematic_refuses_paths_outside_the_workspace(workspace):
    assert not schematic.export_schematic("../x.py", "board.net").startswith("EXPORTED")


def test_export_schematic_missing_files(workspace):
    assert "no such script" in schematic.export_schematic("nope.py", "nope.net")


def _counting(monkeypatch):
    calls = []
    real_run = schematic.subprocess.run

    def counting_run(*args, **kwargs):
        calls.append((args, kwargs))
        return real_run(*args, **kwargs)
    monkeypatch.setattr(schematic.subprocess, "run", counting_run)
    return calls


def test_driver_crash_injection_anchor_is_in_the_driver():
    assert schematic.DRIVER.count(CHECKED) == 1


def test_a_script_edited_after_run_skidl_is_refused_not_recorded(workspace, monkeypatch):
    # board.py changed (the LED turned round) but run_skidl was not re-run:
    # board.net is the old circuit. Every drawing would mismatch it, and that
    # must never read as SKiDL's generator failing (residual fix, Important).
    _grouped(workspace)
    board = workspace / "board.py"
    text = board.read_text()
    assert 'd["A,K"]' in text
    board.write_text(text.replace('d["A,K"]', 'd["K,A"]'))
    calls = _counting(monkeypatch)
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("the script's circuit differs from board.net"), out
    assert "run `run_skidl` first" in out
    assert "D1" in out and "not in the script's circuit" in out
    assert len(calls) == 1, "a script/netlist mismatch must not be retried with another seed"
    assert not schematic.present(workspace / "board.sch-unavailable.json")
    assert _no_outputs(workspace)
    assert not list(workspace.glob("_etchwork_schematic*"))
    assert not (workspace / "board-erc.rpt").exists()


def test_the_wrong_script_is_refused_not_recorded(workspace):
    _grouped(workspace)
    shutil.copy(MULTIUNIT / "board.py", workspace / "other.py")
    out = schematic.export_schematic("other.py", "board.net")
    assert out.startswith("the script's circuit differs from board.net"), out
    assert not schematic.present(workspace / "board.sch-unavailable.json")
    assert _no_outputs(workspace)


def test_the_check_netlist_is_written_outside_the_workspace(workspace, monkeypatch):
    _grouped(workspace)
    seen = {}
    real_run = schematic.subprocess.run

    def spy(cmd, **kwargs):
        seen.setdefault("cmd", cmd)      # the first run is the driver's
        return real_run(cmd, **kwargs)
    monkeypatch.setattr(schematic.subprocess, "run", spy)
    net = workspace / "board.net"
    before = net.read_bytes()
    assert schematic.export_schematic("board.py", "board.net").startswith("EXPORTED")
    check = seen["cmd"][-1]
    assert check.endswith(".net") and workspace not in pathlib.Path(check).parents
    assert net.read_bytes() == before


def test_a_script_printing_the_old_marker_then_failing_is_a_script_failure(workspace, monkeypatch):
    _grouped(workspace)
    (workspace / "board.py").write_text("print('circuit built')\n"
                                        "raise RuntimeError('boom from the script')\n")
    calls = _counting(monkeypatch)
    out = schematic.export_schematic("board.py", "board.net")
    assert "could not be drawn" in out and "before building the circuit" in out, out
    assert len(calls) == 1
    assert not schematic.present(workspace / "board.sch-unavailable.json")


def test_the_unavailable_record_never_writes_through_a_symlink(workspace, monkeypatch, tmp_path_factory):
    _grouped(workspace)
    outside = tmp_path_factory.mktemp("outside") / "target.json"
    outside.write_text("not yours")
    record = workspace / "board.sch-unavailable.json"
    record.symlink_to(outside)
    monkeypatch.setattr(schematic, "problems", lambda sch, net: ["parts missing from the schematic: R9"])
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("SCHEMATIC NOT AVAILABLE:"), out
    assert outside.read_text() == "not yours"
    assert not record.is_symlink() and record.is_file()
    assert schematic.unavailable_reason(record)


@pytest.mark.parametrize("name", ["board.kicad_sch", "board-schematic.pdf",
                                  "board.sch-erc.json", "board.sch-unavailable.json"])
def test_a_folder_in_place_of_an_output_is_an_error_not_a_crash(workspace, monkeypatch, name):
    _grouped(workspace)
    folder = workspace / name
    folder.mkdir()
    (folder / "keep.txt").write_text("mine")
    calls = _counting(monkeypatch)
    out = schematic.export_schematic("board.py", "board.net")
    assert name in out and "is a folder" in out, out
    assert (folder / "keep.txt").read_text() == "mine"
    assert calls == [], "nothing is drawn while an output path is a folder"


def test_a_folder_made_by_the_script_is_an_error_not_a_crash(workspace):
    _grouped(workspace)
    with (workspace / "board.py").open("a") as f:
        f.write("import os\nos.makedirs('board-schematic.pdf', exist_ok=True)\n")
    out = schematic.export_schematic("board.py", "board.net")
    assert "board-schematic.pdf" in out and "is a folder" in out, out
    assert (workspace / "board-schematic.pdf").is_dir()
