"""check_done's schematic rule on a real drawn schematic (schematic spec §6)."""
import os
import shutil

from circuit_mcp import done, schematic
from conftest import FIXTURES

BACKOFF1 = FIXTURES / "projects" / "backoff1"


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


def test_missing_and_stale_are_reported(workspace):
    net = _drawn(workspace)
    (workspace / "board-schematic.pdf").unlink()
    assert "board-schematic.pdf" in done._schematic(net)[0]
