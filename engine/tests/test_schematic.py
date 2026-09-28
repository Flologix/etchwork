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
