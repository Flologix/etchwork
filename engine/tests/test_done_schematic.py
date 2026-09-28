"""check_done's schematic rule on a real drawn schematic (schematic spec §6)."""
import os
import shutil

from circuit_mcp import done, schematic, tools
from conftest import FIXTURES

BACKOFF1 = FIXTURES / "projects" / "backoff1"
MULTIUNIT = FIXTURES / "projects" / "multiunit"


def _drawn(workspace):
    shutil.copytree(BACKOFF1, workspace, dirs_exist_ok=True)
    net = workspace / "board.net"
    t = net.stat().st_mtime - 100
    os.utime(net, (t, t))
    assert schematic.export_schematic("board.py", "board.net").startswith("EXPORTED")
    return net


def test_a_matching_schematic_passes(workspace):
    net = _drawn(workspace)
    assert done._schematic(net) == []


def test_a_rewired_netlist_no_longer_matches(workspace):
    net = _drawn(workspace)
    text = net.read_text()
    # Rename one pin in the netlist, as a changed circuit would, without
    # re-drawing: the schematic now shows the old circuit.
    import re
    moved = re.sub(r'(\(node\s+\(ref "[^"]+"\)\s*\(pin ")[^"]+(")', r"\g<1>999\2", text, count=1)
    assert moved != text
    net.write_text(moved)
    t = (workspace / "board.kicad_sch").stat().st_mtime - 100
    os.utime(net, (t, t))
    problems = done._schematic(net)
    assert problems and "does not match" in problems[0]


def test_missing_is_reported(workspace):
    net = _drawn(workspace)
    (workspace / "board-schematic.pdf").unlink()
    problems = done._schematic(net)
    assert "no board-schematic.pdf" in problems[0]


def test_stale_is_reported(workspace):
    net = _drawn(workspace)
    sch = workspace / "board.kicad_sch"
    t = net.stat().st_mtime - 100
    os.utime(sch, (t, t))
    problems = done._schematic(net)
    assert problems and "board.kicad_sch older than board.net" in problems[0]


def _not_drawable(workspace):
    shutil.copy(MULTIUNIT / "board.py", workspace / "board.py")
    assert tools.run_skidl("board.py").startswith("netlist: board.net")
    net = workspace / "board.net"
    t = net.stat().st_mtime - 100
    os.utime(net, (t, t))
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("SCHEMATIC NOT AVAILABLE"), out
    return net


def test_a_multi_unit_board_is_accepted_through_its_record(workspace):
    net = _not_drawable(workspace)
    assert done._schematic(net) == []


def test_a_stale_record_is_refused(workspace):
    net = _not_drawable(workspace)
    record = workspace / "board.sch-unavailable.json"
    t = net.stat().st_mtime - 100
    os.utime(record, (t, t))
    problems = done._schematic(net)
    assert problems and "board.sch-unavailable.json older than board.net" in problems[0]
