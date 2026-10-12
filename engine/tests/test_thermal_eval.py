"""The thermal-repair evaluation covers five real boards,
each poured twice -- once with the repair switched off (the baseline, measured
now rather than remembered) and once with it -- then checked by KiCad's DRC.

Runs inside the image (pcbnew, kicad-cli). Slow: two pours and two DRCs a board.
"""
import collections
import json
import shutil

import pytest

from circuit_mcp import kicad, service, tools
from conftest import FIXTURES

BOARDS = FIXTURES / "boards"
# Measured on 2026-09-24 by pouring each board with today's fill_zones.
STARVING = {"release2": {("J1", "B1")}, "release3": {("J1", "SH")}}
CLEAN = ["stop1", "test", "mycircuit7"]


def _pour(workspace, name, repair: bool, monkeypatch):
    """Pour a fresh copy of a fixture board; return (error tally, starved,
    pad connections, fill_zones output, the uuids repair_thermals reported
    solid).

    The uuids come from a recording shim around `tools.repair_thermals`
    (monkeypatch, calling through to the real function) rather than from
    parsing the pad-connection dict below by name: J1's footprints carry
    4 pads named SH, so a wrong SH changing would still pass a name-only
    check. The uuid is the only thing that tells them apart.
    """
    folder = workspace / ("with" if repair else "without")
    folder.mkdir()
    shutil.copy(BOARDS / f"{name}.kicad_pcb", folder / "board.kicad_pcb")
    board = f"{folder.name}/board.kicad_pcb"
    captured_uuids = []
    with monkeypatch.context() as m:
        if repair:
            real_repair = tools.repair_thermals

            def recording(board, net):
                result = real_repair(board, net)
                captured_uuids.extend(result.get("uuids") or [])
                return result
            m.setattr(tools, "repair_thermals", recording)
        else:
            m.setattr(tools, "repair_thermals", lambda board, net: {"solid": [], "still": []})
        out = tools.fill_zones(board)
    service.call("drc", {"board": board, "out": f"{folder.name}/check.json"})
    report = json.loads((folder / "check.json").read_text())
    tally = collections.Counter(v.get("type") for v in kicad.violations(report)
                                if v.get("severity") == "error")
    starved = {(ref, pad) for _, ref, pad in kicad.starved_pads(report, "GND")}
    code, text = service.python(
        "import json, sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "print(json.dumps({'%s %s %s' % (fp.GetReference(), p.GetNumber(), p.m_Uuid.AsString()):\n"
        "                  int(p.GetLocalZoneConnection())\n"
        "                  for fp in b.GetFootprints() for p in fp.Pads()}))\n",
        str(folder / "board.kicad_pcb"))
    assert code == 0, text
    pads = json.loads(next(line for line in text.splitlines() if line.startswith("{")))
    return tally, starved, pads, out, captured_uuids


@pytest.mark.parametrize("name", sorted(STARVING))
def test_a_starving_board_is_repaired_on_exactly_the_starved_pads(workspace, name, monkeypatch):
    base_tally, base_starved, base_pads, _, _ = _pour(workspace, name, False, monkeypatch)
    tally, starved, pads, out, uuids = _pour(workspace, name, True, monkeypatch)

    # The baseline reproduces the starve the spec pre-registered.
    assert base_starved == STARVING[name]
    # Repaired: none left.
    assert starved == set() and tally["starved_thermal"] == 0
    # Dangerous error 1: no violation type grows.
    grown = {t: (base_tally[t], tally[t]) for t in tally if tally[t] > base_tally[t]}
    assert grown == {}
    # Dangerous error 2: only pads DRC named changed. Compared by uuid, not by
    # "ref pad" name -- J1's footprints carry 4 pads named SH, so a wrong SH
    # changing would still pass a name-only check -- and must equal exactly
    # the uuids repair_thermals itself reported making solid.
    changed_uuids = {key.split()[2] for key in pads if pads[key] != base_pads[key]}
    assert changed_uuids == set(uuids)
    # Name-level: every baseline-starved (ref, pad) is among the SOLID pads,
    # and what the tool output reports as SOLID matches what actually changed.
    # (release2 needs two passes: B1 solid starves the neighbouring SH.)
    changed_names = {" ".join(key.split()[:2]) for key in pads if pads[key] != base_pads[key]}
    reported = {" ".join(line.split()[1:3]) for line in out.splitlines()
                if line.strip().startswith("SOLID:")}
    assert changed_names == reported
    assert {f"{ref} {pad}" for ref, pad in STARVING[name]} <= changed_names
    assert "STILL STARVED" not in out


@pytest.mark.parametrize("name", CLEAN)
def test_a_board_that_does_not_starve_is_left_alone(workspace, name, monkeypatch):
    base_tally, base_starved, base_pads, _, _ = _pour(workspace, name, False, monkeypatch)
    tally, starved, pads, out, uuids = _pour(workspace, name, True, monkeypatch)

    assert base_starved == set()
    assert pads == base_pads
    assert tally == base_tally
    assert "SOLID:" not in out
    assert uuids == []


def test_the_repair_never_writes_the_report_check_done_reads(workspace, monkeypatch):
    # Dangerous error 3.
    _pour(workspace, "release2", True, monkeypatch)
    assert not (workspace / "with" / "board.drc.json").exists()
    assert not (workspace / "board.drc.json").exists()
    assert not list(workspace.rglob(".*fill-drc.json"))
