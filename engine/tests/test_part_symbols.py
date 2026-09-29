"""Datasheet-parts design, phase 3: the part's own symbol, and the parts a
human still has to check.

- make_part writes the symbol too: symbols/<Library>.kicad_sym, next to the
  footprint, from the spec's pin table (§5.2, step 3).
- The project's symbols/ is searched before KiCad's own libraries -- by
  describe_part, find_part, check_netlist, and SKiDL when the agent's script
  runs (§5.3), exactly as fp/ is for footprints.
- A made part that no human has verified is listed by check_done and the
  README, next to the nets awaiting a decision (§5.4). It does not block DONE
  (§10.3, the design's proposal).
"""
import json
import re
import shutil

import pytest

from circuit_mcp import done, makepart, parts, paths, schematic, summary, tools
from conftest import FIXTURES
from test_done import _age, _done_project
from test_partspec import ODU, _edit

SPEC = "parts/M8-8P.part.toml"


def _ring_spec(d, name="M8-8P", pins=None):
    d.update(mpn=name, name=name, library="Circ", reference="J",
             description="8-pin circular PCB receptacle")
    d["package"]["view"] = "top"
    d["package"]["rings"] = [{"diameter_mm": 10.0, "count": 8, "start_deg": 90, "direction": "cw",
                              "first_pin": 1, "drill_mm": 1.0, "source": "p.2"}]
    d["package"].pop("slots", None)
    d["pins"] = pins or [
        {"number": "1", "name": "VCC", "type": "power_in"},
        {"number": "2", "name": "GND", "type": "power_in"},
        *({"number": str(n), "name": f"IO{n}", "type": "bidirectional"} for n in range(3, 9))]
    d["fab"] = {"min_lead_mm": 0.8, "source": "p.1"}


@pytest.fixture
def m8(workspace):
    (workspace / "parts").mkdir()
    (workspace / "datasheets").mkdir()
    (workspace / "datasheets" / "GK1YAR-P16UC00-000L.pdf").write_bytes(b"%PDF-1.4\n")
    spec = workspace / SPEC
    shutil.copy(ODU, spec)
    _edit(spec, _ring_spec)
    return spec


def _made(workspace):
    out = makepart.make_part(SPEC)
    assert out.startswith("MADE"), out
    return out


# --- make_part writes the symbol ----------------------------------------------

def test_make_part_writes_the_symbol_beside_the_footprint(m8, workspace):
    out = _made(workspace)
    library = workspace / "symbols" / "Circ.kicad_sym"
    assert library.is_file()
    assert "symbols/Circ.kicad_sym" in out and 'Part("Circ", "M8-8P"' in out, out
    text = library.read_text()
    assert 'generator "etchwork-make-part"' in text
    assert '(property "Footprint" "Circ:M8-8P"' in text
    assert '(property "Reference" "J"' in text


def test_the_symbol_has_every_pin_with_its_name_and_type(m8, workspace):
    _made(workspace)
    out = parts.describe_part("Circ:M8-8P")
    assert "8 pins" in out, out
    assert re.search(r"1\s+VCC\s+power_in", out) and re.search(r"5\s+IO5\s+bidirectional", out), out


def test_supply_pins_go_on_top_and_ground_at_the_bottom(m8, workspace):
    _made(workspace)
    text = (workspace / "symbols" / "Circ.kicad_sym").read_text()
    angle = {m[1]: int(m[0]) for m in re.findall(
        r'\(pin \S+ line \(at [-\d.]+ [-\d.]+ (\d+)\).*?\(number "(\w+)"', text, re.S)}
    assert angle["1"] == 270 and angle["2"] == 90       # pointing down from the top; up from below
    assert {angle[str(n)] for n in range(3, 9)} <= {0, 180}


def test_a_second_part_joins_the_same_library_and_a_rerun_replaces_only_its_own(m8, workspace):
    _made(workspace)
    shutil.copy(m8, workspace / "parts" / "M8-4P.part.toml")
    _edit(workspace / "parts" / "M8-4P.part.toml", lambda d: (_ring_spec(d, "M8-4P", pins=[
        {"number": str(n), "name": f"P{n}", "type": "passive"} for n in range(1, 5)]),
        d["package"]["rings"][0].update(count=4)))
    assert makepart.make_part("parts/M8-4P.part.toml").startswith("MADE")
    assert "4 pins" in parts.describe_part("Circ:M8-4P")
    assert "8 pins" in parts.describe_part("Circ:M8-8P")
    _made(workspace)
    text = (workspace / "symbols" / "Circ.kicad_sym").read_text()
    assert text.count('(symbol "M8-8P"') == 1 and text.count('(symbol "M8-4P"') == 1


def test_a_symbol_library_make_part_did_not_write_is_never_touched(m8, workspace):
    (workspace / "symbols").mkdir()
    mine = '(kicad_symbol_lib (version 20231120) (generator "kicad_symbol_editor"))\n'
    (workspace / "symbols" / "Circ.kicad_sym").write_text(mine)
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED") and "symbols/Circ.kicad_sym" in out, out
    assert (workspace / "symbols" / "Circ.kicad_sym").read_text() == mine
    assert not (workspace / "fp").exists()          # nothing half-written


def test_the_symbol_is_rendered_too(m8, workspace):
    out = _made(workspace)
    assert (workspace / "parts" / "M8-8P.symbol.svg").is_file(), out


# --- the project's symbols are found ------------------------------------------

def test_find_part_finds_a_project_symbol(m8, workspace):
    _made(workspace)
    assert "Circ:M8-8P" in parts.find_part("circular receptacle")


def test_a_project_symbol_library_replaces_kicads_of_the_same_name(workspace):
    (workspace / "symbols").mkdir()
    device = (paths.SYMBOL_ROOT / "Device.kicad_sym").read_text()
    start = device.index('(symbol "R"')
    (workspace / "symbols" / "Device.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20231120) (generator "test")\n'
        + "\t" + device[start:start + device[start:].index('\n\t(symbol "R_')] + ")\n")
    assert parts.describe_part("Device:R").startswith("Device:R")
    assert "no symbol" in parts.describe_part("Device:C")


def test_a_symbol_library_pointing_outside_the_project_is_ignored(m8, workspace, tmp_path_factory):
    _made(workspace)
    outside = tmp_path_factory.mktemp("outside")
    shutil.move(workspace / "symbols" / "Circ.kicad_sym", outside / "Circ.kicad_sym")
    (workspace / "symbols" / "Circ.kicad_sym").symlink_to(outside / "Circ.kicad_sym")
    assert parts.describe_part("Circ:M8-8P").startswith("no ")
    assert parts.project_symbol_libraries() == []


def test_the_agents_script_finds_the_project_symbol(m8, workspace):
    _made(workspace)
    (workspace / "board.py").write_text('''from skidl import *
j = Part("Circ", "M8-8P", ref="J1", footprint="Circ:M8-8P")
h = Part("Connector_Generic", "Conn_02x04_Odd_Even", ref="J2",
         footprint="Connector_PinHeader_2.54mm:PinHeader_2x04_P2.54mm_Vertical")
for n in range(1, 9):
    net = Net(f"S{n}")
    net += j[n], h[n]
generate_netlist(file_="board.net")
''')
    out = tools.run_skidl("board.py")
    assert out.startswith("netlist: board.net"), out
    netlist = (workspace / "board.net").read_text()
    assert '(lib "Circ")' in netlist and '(part "M8-8P")' in netlist


def test_the_schematic_is_drawn_with_the_project_symbol(m8, workspace):
    test_the_agents_script_finds_the_project_symbol(m8, workspace)
    out = schematic.export_schematic("board.py", "board.net")
    assert out.startswith("EXPORTED") and "schematic matches the netlist" in out, out


def test_check_netlist_reads_pin_names_from_a_project_symbol(m8, workspace):
    test_the_agents_script_finds_the_project_symbol(m8, workspace)
    assert parts.pin_lookup("Circ", "M8-8P").names["1"] == "VCC"


# --- parts awaiting your verification -------------------------------------------

@pytest.fixture
def schematic_matches(monkeypatch):
    """As in test_done: blinker.net has no script to draw a schematic from."""
    monkeypatch.setattr(schematic, "problems", lambda sch, netlist: [])


def _board_using(workspace, footprint="Circ:M8-8P"):
    """A DONE project whose netlist places the made part."""
    _done_project(workspace)
    net = workspace / "blinker.net"
    text = net.read_text()
    net.write_text(text.replace("(comp", f'(comp (ref "J9") (value "x") (footprint "{footprint}")) (comp', 1))
    _age(net, 60)                   # as old as the board: everything after it is current


def test_check_done_lists_a_made_part_nobody_has_verified(m8, workspace, schematic_matches):
    _made(workspace)
    _board_using(workspace)
    out = done.check_done("blinker.net")
    assert out.startswith("DONE"), out                   # listed, not blocking (§10.3)
    block = out.split("Parts awaiting your verification", 1)[1]
    assert "Circ:M8-8P" in block and "parts/M8-8P.part.toml" in block, out
    assert "numbering" in block, out                      # its unknowns


def test_a_verified_part_is_not_listed(m8, workspace, schematic_matches):
    _edit(m8, lambda d: (d.pop("numbering"), d.update(verified="F. K., 2026-09-29")))
    _made(workspace)
    _board_using(workspace)
    assert "Parts awaiting" not in done.check_done("blinker.net")


def test_a_made_part_the_board_does_not_use_is_not_listed(m8, workspace, schematic_matches):
    _made(workspace)
    _done_project(workspace)
    assert "Parts awaiting" not in done.check_done("blinker.net")


def test_the_readme_lists_the_parts_awaiting_verification(m8, workspace, schematic_matches):
    _made(workspace)
    _board_using(workspace)
    summary.summarize_project("blinker.net")
    readme = (workspace / "README.md").read_text()
    section = readme.split("## Parts awaiting your verification", 1)[1]
    assert "Circ:M8-8P" in section.split("##")[0], readme
