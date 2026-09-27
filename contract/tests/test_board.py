"""The adapter against the engine's real delivered boards: boards nobody
built for CONTRACT (design spec §6). Runs in the engine image only."""
import os
import pathlib

import pytest

pytest.importorskip("pcbnew")

from contract import dump  # noqa: E402
from contract.board import Board  # noqa: E402

ENGINE = pathlib.Path(os.environ.get("ETCHWORK_ENGINE_FIXTURES", "/opt/etchwork/tests/fixtures"))
BOARDS = sorted((ENGINE / "boards").glob("*.kicad_pcb"))


def test_fixture_boards_are_present():
    assert len(BOARDS) >= 6


@pytest.mark.parametrize("path", BOARDS, ids=lambda p: p.stem)
def test_every_engine_board_loads_consistently(path):
    b = Board(dump.dump(path))
    assert b.outline().bbox is not None
    assert b.footprints()
    refs = {f.ref for f in b.footprints()}
    for net, members in b.nets().items():
        assert net
        assert {ref for ref, _ in members} <= refs
    # Totals, not per ref: a real board may repeat a reference (logos, holes).
    assert sum(f.pad_count for f in b.footprints()) == len(b.pads())
    assert all(t.width > 0 for t in b.tracks())


def test_release_board_facts():
    b = Board(dump.dump(ENGINE / "projects/release/board.kicad_pcb",
                        ENGINE / "pinfn/release.net"))
    assert len(b.footprints()) == 17
    assert b.outline().width == pytest.approx(50.0)
    assert b.outline().height == pytest.approx(40.0)
    c3 = next(f for f in b.footprints() if f.ref == "C3")
    assert (c3.value, c3.lib_id) == ("100n", "Capacitor_SMD:C_0805_2012Metric")
    gnd = [z for z in b.zones() if z.net == "GND"]
    assert {layer for z in gnd for layer in z.layers} == {"F.Cu", "B.Cu"}
    assert all(z.filled for z in gnd)


def test_pin_names_come_from_the_netlist():
    b = Board(dump.dump(ENGINE / "projects/release/board.kicad_pcb",
                        ENGINE / "pinfn/release.net"))
    u1 = {p.number: p.names for p in b.pads() if p.ref == "U1"}
    assert "~{RESET}/PB5" in u1["1"]
    assert "SCK" in u1["7"]


def test_without_netlist_pin_names_are_empty_for_library_parts():
    b = Board(dump.dump(ENGINE / "projects/release/board.kicad_pcb"))
    assert all(not p.names for p in b.pads() if p.ref == "U1")


def test_pth_pads_list_only_enabled_copper():
    b = Board(dump.dump(ENGINE / "projects/release/board.kicad_pcb"))
    layer_sets = {p.layers for p in b.pads()}
    assert ("F.Cu", "B.Cu") in layer_sets
    assert all(not layer.startswith("In") for ls in layer_sets for layer in ls)


def test_unreadable_board_is_a_dump_error(tmp_path):
    bad = tmp_path / "bad.kicad_pcb"
    bad.write_text("(not a board")
    with pytest.raises(dump.DumpError):
        dump.dump(bad)
