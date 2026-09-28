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
    with pytest.raises(RuntimeError, match="timed out"):
        schematic.schematic_netlist(workspace / "board.kicad_sch")


import shutil

from circuit_mcp import tools
from conftest import FIXTURES

GROUPED = FIXTURES / "projects" / "grouped"
BACKOFF1 = FIXTURES / "projects" / "backoff1"


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
                'generate_schematic(filepath=".", top_name="not_this_one")\n')
    net = workspace / "board.net"
    before_bytes, before_mtime = net.read_bytes(), net.stat().st_mtime_ns
    before = {p.name for p in workspace.iterdir()}
    schematic.export_schematic("board.py", "board.net")
    assert net.read_bytes() == before_bytes and net.stat().st_mtime_ns == before_mtime
    added = {p.name for p in workspace.iterdir()} - before
    assert added <= set(schematic.files("board")) | {"board.kicad_prl"}, added
    assert not (workspace / "other.net").exists()
    assert not (workspace / "not_this_one.kicad_sch").exists()


def test_export_schematic_on_a_real_project(workspace):
    shutil.copytree(BACKOFF1, workspace, dirs_exist_ok=True)
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("EXPORTED") and "schematic matches the netlist" in out, out


def test_export_schematic_reports_a_failing_script(workspace):
    _grouped(workspace)
    (workspace / "board.py").write_text("raise RuntimeError('boom from the script')\n")
    before = {p.name for p in workspace.iterdir()}
    out = schematic.export_schematic("board.py", "board.net")
    assert "could not be drawn" in out and "boom from the script" in out
    assert {p.name for p in workspace.iterdir()} == before


def test_export_schematic_reports_a_mismatch(workspace, monkeypatch):
    _grouped(workspace)
    monkeypatch.setattr(schematic, "problems", lambda sch, net: ["parts missing from the schematic: R9"])
    out = schematic.export_schematic("board.py", "board.net")
    assert "DOES NOT MATCH" in out and "R9" in out


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


def test_export_schematic_reports_after_all_seeds_mismatch(workspace, monkeypatch):
    _grouped(workspace)
    monkeypatch.setattr(schematic, "problems", lambda sch, net: ["parts missing from the schematic: R9"])
    out = schematic.export_schematic("board.py", "board.net")
    assert "DOES NOT MATCH" in out and "5 of 5" in out


def test_export_schematic_refuses_paths_outside_the_workspace(workspace):
    assert not schematic.export_schematic("../x.py", "board.net").startswith("EXPORTED")


def test_export_schematic_missing_files(workspace):
    assert "no such script" in schematic.export_schematic("nope.py", "nope.net")
