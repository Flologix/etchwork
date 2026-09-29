"""Footprints the project brings itself: `fp/<Library>.pretty/<Name>.kicad_mod`.

Found on a live run (2026-09-28): asked for a board holding an ODU AMC
receptacle, which KiCad has no footprint for, the agent drew one from ODU's
datasheet into fp/ODU.pretty/ -- because the tools' own refusal said "this
project's symbols/ and fp/ are the only places looked". Nothing read fp/:
describe_footprint answered "no footprint" seven times, the agent asked the
human to restart the server twice, and the board was never built. The
fixture is that footprint.
"""
import shutil

import pytest

from circuit_mcp import parts, paths, service, tools
from conftest import FIXTURES
from test_tools import fake_pipeline

ODU = "ODU:ODU_AMC_GK1YAR-P16UC00-000L"
ODU_PRETTY = FIXTURES / "kicad" / "ODU.pretty"


@pytest.fixture
def kicad(tmp_path_factory, monkeypatch):
    """KiCad's own libraries, apart from the project: one Diode_THT footprint."""
    root = tmp_path_factory.mktemp("kicad-footprints")
    (root / "Diode_THT.pretty").mkdir()
    (root / "Diode_THT.pretty" / "D_DO-35.kicad_mod").write_text("(footprint)")
    monkeypatch.setattr(paths, "FP_ROOT", root)
    return root


@pytest.fixture
def odu(workspace, kicad):
    shutil.copytree(ODU_PRETTY, workspace / "fp" / "ODU.pretty")
    return workspace


def test_a_project_footprint_is_described(odu):
    out = parts.describe_footprint(ODU)
    assert out.startswith(ODU), out
    assert "17 electrical pads" in out, out


def test_kicads_own_footprints_are_still_described(odu):
    assert parts.describe_footprint("Diode_THT:D_DO-35").startswith("Diode_THT:D_DO-35")


def test_a_project_library_comes_before_kicads_of_the_same_name(workspace, kicad):
    mine = workspace / "fp" / "Diode_THT.pretty"
    mine.mkdir(parents=True)
    shutil.copy(ODU_PRETTY / "ODU_AMC_GK1YAR-P16UC00-000L.kicad_mod", mine / "D_DO-35.kicad_mod")
    assert "17 electrical pads" in parts.describe_footprint("Diode_THT:D_DO-35")


def test_find_part_finds_a_project_footprint(odu):
    assert ODU in parts.find_part("ODU AMC")


def test_the_run_layout_check_resolves_a_project_footprint(odu):
    netlist = f'(export (comp (ref "J1") (footprint "{ODU}")))'
    assert parts.unresolved_footprints(netlist, parts.fp_libraries()) == []


def test_an_unresolved_footprint_says_where_it_was_looked_for(odu):
    [line] = parts.unresolved_footprints(
        '(export (comp (ref "J1") (footprint "Nope:X")))', parts.fp_libraries())
    assert "fp/" in line and "ODU" in line and "Diode_THT" in line


def test_a_project_fp_folder_pointing_outside_the_project_is_ignored(workspace, kicad,
                                                                       tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    shutil.copytree(ODU_PRETTY, outside / "ODU.pretty")
    (workspace / "fp").symlink_to(outside, target_is_directory=True)
    assert parts.describe_footprint(ODU).startswith("no footprint")
    assert parts.project_fp_libraries() == []


def test_a_library_symlinked_out_of_the_project_is_ignored(workspace, kicad, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    shutil.copytree(ODU_PRETTY, outside / "ODU.pretty")
    (workspace / "fp").mkdir()
    (workspace / "fp" / "ODU.pretty").symlink_to(outside / "ODU.pretty", target_is_directory=True)
    assert parts.describe_footprint(ODU).startswith("no footprint")
    assert parts.project_fp_libraries() == []


def test_the_refusal_names_where_the_tools_really_look(odu):
    out = parts.describe_footprint("odu_breakout/ODU.pretty:X")
    assert "symbols/" not in out
    assert "fp/<Library>.pretty" in out and "KiCad" in out


def test_run_layout_hands_the_project_libraries_to_the_board_builder(odu, monkeypatch):
    (odu / "j.net").write_text(f'(export (comp (ref "J1") (footprint "{ODU}")))')
    seen = []
    monkeypatch.setattr(service, "call", fake_pipeline({"exit": 0, "report": "j.drc.json"}, seen))
    (odu / "j.drc.json").write_text('{"violations": [], "unconnected_items": []}')

    out = tools.run_layout("j.net", 30, 30, positions={"J1": [15, 15, 0]}, route=False)

    assert "cannot be resolved" not in out, out
    [pcb] = [body for step, body in seen if step == "pcb"]
    assert pcb["fp_libs"] == ["fp/ODU.pretty"]


def test_a_real_board_is_built_with_a_project_footprint(workspace):
    """End to end in the image: SKiDL writes the netlist, kinet2pcb builds the
    board from the project's library, and the board holds the receptacle."""
    odu = workspace                        # KiCad's real libraries, and the project's
    shutil.copytree(ODU_PRETTY, odu / "fp" / "ODU.pretty")
    (odu / "board.py").write_text(f'''from skidl import *
j = Part("Connector_Generic", "Conn_01x17", ref="J1", footprint="{ODU}")
h = Part("Connector_Generic", "Conn_02x09_Odd_Even", ref="J2",
         footprint="Connector_PinHeader_2.54mm:PinHeader_2x09_P2.54mm_Vertical")
for n in range(1, 18):
    net = Net(f"C{{n}}")
    net += j[n], h[n]
NC += h[18]
generate_netlist(file_="board.net")
''')
    ran = tools.run_skidl("board.py")
    assert ran.startswith("netlist: board.net"), ran

    out = tools.run_layout("board.net", 40, 30, route=False,
                           positions={"J1": [12, 15, 0], "J2": [30, 15, 0]})

    assert "Board: board.kicad_pcb" in out, out
    board = parts.describe_board("board.kicad_pcb")
    assert "2 footprints" in board, board
