"""Reading KiCad's own files, so the model does not have to grep them.

Measured 2026-09-11 across three recorded autonomous runs: 56% of every shell
command was one of four questions -- what pins does this symbol have, what
pads does this footprint have, what is connected to what in this netlist, and
did the generated board actually get made. All four are answerable from files
the harness already vendors.

The fixtures are cut out of the real libraries on purpose. app/tools/parts.py
carries the scar: "written against a real Device.kicad_sym after a
hand-simplified fixture passed and the real file produced no pins at all."
"""
import pathlib

import pytest

from circuit_mcp import kicad_lib

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "kicad"


@pytest.fixture
def derived():
    return (FIXTURES / "derived.kicad_sym").read_text()


# --- symbols, and the extends mechanism that hid every IC's pins -----------


def test_a_symbol_that_defines_its_own_pins_reports_them(derived):
    found = kicad_lib.read_symbol(derived, "ATtiny25V-10P")
    assert isinstance(found, kicad_lib.Symbol)
    assert len(found.pins) == 8
    assert found.inherited_from is None
    by_number = {p.number: p.name for p in found.pins}
    assert by_number["4"] == "GND"
    assert by_number["8"] == "VCC"


def test_a_derived_symbol_reports_the_pins_it_inherits(derived):
    """THE defect this module exists for. find_part("ATtiny85V-10P") returned
    a description and no pins at all, because the symbol's whole body is
    `(extends "ATtiny25V-10P")` -- KiCad's derived-symbol mechanism, which
    most ICs use and no passive does. So the catalogue answered pins for the
    two-pin parts where they are obvious and went silent for the eight-pin
    part where they decide the design.

    Measured cost of that silence in one run: a grep that returned
    `(at -12.7 13.97 0)`, then a hand-written probe.py, then two failed SKiDL
    API guesses, then the answer. Three tools and five steps for one fact."""
    found = kicad_lib.read_symbol(derived, "ATtiny85V-10P")
    assert isinstance(found, kicad_lib.Symbol)
    assert len(found.pins) == 8
    assert {p.number: p.name for p in found.pins}["1"] == "~{RESET}/PB5"


def test_an_inherited_pin_list_says_where_it_came_from(derived):
    """A pin list whose origin is invisible is the kind of fact that gets
    doubted and re-grepped, which is the behaviour this is replacing."""
    found = kicad_lib.read_symbol(derived, "ATtiny85V-10P")
    assert found.inherited_from == "ATtiny25V-10P"


def test_the_electrical_type_travels_with_the_pin(derived):
    """`power_in` is what PWR_FLAG exists for, and two recorded runs spent
    steps discovering that a power pin was undriven only after ERC said so."""
    found = kicad_lib.read_symbol(derived, "ATtiny25V-10P")
    kinds = {p.number: p.kind for p in found.pins}
    assert kinds["4"] == "power_in"
    assert kinds["8"] == "power_in"
    assert kinds["6"] == "bidirectional"


def test_a_symbol_that_is_not_there_says_so(derived):
    assert isinstance(kicad_lib.read_symbol(derived, "NoSuchPart"), str)
    assert "NoSuchPart" in kicad_lib.read_symbol(derived, "NoSuchPart")


def test_a_parent_that_is_not_in_this_library_is_reported_not_hidden():
    """An empty pin list is not an answer. "It extends X and X is not here"
    is one, and it names the library that needs adding."""
    text = '(kicad_symbol_lib\n\t(symbol "Orphan"\n\t\t(extends "Elsewhere")\n\t)\n)\n'
    found = kicad_lib.read_symbol(text, "Orphan")
    assert isinstance(found, str)
    assert "Elsewhere" in found


def test_a_cycle_of_extends_is_refused_rather_than_followed_forever():
    text = ('(kicad_symbol_lib\n'
            '\t(symbol "A"\n\t\t(extends "B")\n\t)\n'
            '\t(symbol "B"\n\t\t(extends "A")\n\t)\n)\n')
    found = kicad_lib.read_symbol(text, "A")
    assert isinstance(found, str)
    assert "extends" in found.lower()


def test_every_symbol_in_a_library_can_be_listed(derived):
    assert set(kicad_lib.symbol_names(derived)) == {"ATtiny25V-10P", "ATtiny85V-10P"}


# --- footprints: the pads, and how much room they need ---------------------


@pytest.fixture
def button():
    return (FIXTURES / "SW_SPST_Omron_B3F-40xx.kicad_mod").read_text()


def test_a_footprints_electrical_pads_are_counted_apart_from_its_mounting_holes(button):
    """Run 2 found by hand that SW_Omron_B3FS is a 2-pin symbol against a
    6-pad footprint -- and it is only a mismatch if the mounting holes are
    counted, which they should not be. A tool that reported "6 pads" would
    have confirmed the wrong conclusion."""
    found = kicad_lib.read_footprint(button)
    assert found.electrical == ["1", "1", "2", "2"]
    assert found.mounting == 2
    assert found.nets == {"1", "2"}


def test_a_footprint_reports_how_much_room_it_needs(button):
    """The arrange endpoint already answers "19.6mm of footprint in 14.0mm of
    room" -- after the attempt. Knowing it before is what removes the
    guessing round-trips: run 2 called run_kicad_layout eight times."""
    found = kicad_lib.read_footprint(button)
    # The courtyard rect, `(start -1.5 -4.75) (end 14 9.75)`: 15.5 x 14.5 mm
    # for one tactile button. Two of them is 31 mm of row before anything
    # else is placed, which is precisely why run 2's 40 mm board kept being
    # refused and why it took eight run_kicad_layout calls to find a size.
    assert found.extent == (15.5, 14.5)


def test_a_footprint_with_no_courtyard_falls_back_to_its_pads():
    """Not every footprint draws one, and "no idea how big it is" is a worse
    answer than a pad-derived bound."""
    text = ('(footprint "X"\n'
            '  (pad "1" thru_hole oval (at 0 0) (size 2 2) (layers "*.Cu"))\n'
            '  (pad "2" thru_hole oval (at 5 0) (size 2 2) (layers "*.Cu"))\n)\n')
    found = kicad_lib.read_footprint(text)
    assert found.extent[0] >= 5.0


# --- netlists: what is actually connected to what --------------------------


# Cut from a netlist SKiDL actually wrote (testcli2/toggles.net, 2026-09-11),
# not hand-written. KiCad pretty-prints its s-expressions one atom per line,
# and a compact fixture is exactly what let this module ship a regex that
# matched nothing on a real file -- the same way app/tools/parts.py's _PIN
# once did. The module docstring warns about this; the first version of these
# tests did it anyway.
NETLIST = """(export (version "E")
  (components
    (comp
      (ref "R1")
      (value "220")
      (footprint "Resistor_THT:R_Axial_DIN0207"))
    (comp
      (ref "U1")
      (value "ATtiny85V-10P")
      (footprint "Package_DIP:DIP-8_W7.62mm")))
  (nets
    (net
      (code 1)
      (name "LED_A")
      (class "Default")
      (node
        (ref "R1")
        (pin "2")
        (pintype "PASSIVE"))
      (node
        (ref "U1")
        (pin "7")
        (pinfunction "PB2")
        (pintype "BIDIRECTIONAL")))
    (net
      (code 2)
      (name "GND")
      (node
        (ref "U1")
        (pin "4")
        (pinfunction "GND")
        (pintype "POWER_IN")))))
"""


def test_a_netlist_reads_as_nets_to_pins():
    """The 12%. Recorded attempts to answer this by hand included a regex
    that never matched because the closing paren sits on the last node's
    line, and a whitespace collapse that broke paren matching -- four
    rewrites of one throwaway parser in a single run."""
    nets = kicad_lib.read_netlist(NETLIST)
    assert nets["LED_A"] == ["R1:2", "U1:7"]
    assert nets["GND"] == ["U1:4"]


def test_the_pin_function_is_kept_so_a_declaration_can_use_names():
    """`U1:PB2` is how a person states the intent; `U1:7` is how the netlist
    records it. Both have to resolve to the same pin or the assertion is
    unusable."""
    nets = kicad_lib.read_netlist(NETLIST, by_function=True)
    assert nets["LED_A"] == ["R1:2", "U1:PB2"]


def test_the_parts_in_a_netlist_are_read_with_their_footprints():
    parts = kicad_lib.read_components(NETLIST)
    assert parts["U1"].value == "ATtiny85V-10P"
    assert parts["U1"].footprint == "Package_DIP:DIP-8_W7.62mm"
    assert parts["R1"].value == "220"


def test_an_empty_netlist_is_empty_rather_than_an_error():
    assert kicad_lib.read_netlist("(export (version \"E\"))") == {}


# --- boards: the format names that nearly produced a fabricated result -----


BOARD = '''(kicad_pcb (version 20241229)
  (footprint "Package_DIP:DIP-8_W7.62mm" (at 10 10))
  (footprint "LED_THT:LED_D5.0mm" (at 20 10))
  (gr_line (start 0 0) (end 30 0) (layer "Edge.Cuts"))
  (gr_line (start 30 0) (end 30 20) (layer "Edge.Cuts"))
  (segment (start 1 1) (end 2 2) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 2 2) (end 3 3) (width 0.25) (layer "B.Cu") (net 1))
)
'''


def test_a_board_reports_its_footprints_tracks_and_outline():
    """Run 3 grepped a routed board for `track` and `Edge_Cuts`, got zero of
    each, and wrote "Zero tracks, zero Edge_Cuts, yet DRC passed -- either
    the file uses a layout I'm not grepping correctly, or DRC passed
    vacuously." KiCad 10 writes `segment` and `Edge.Cuts`. It was one step
    from reporting a fabricated result because it was parsing a format by
    hand; the names belong in one tested place."""
    found = kicad_lib.read_board(BOARD)
    assert found.footprints == 2
    assert found.segments == {"F.Cu": 1, "B.Cu": 1}
    assert found.has_outline is True


def test_a_board_with_no_outline_says_so():
    found = kicad_lib.read_board('(kicad_pcb (footprint "X:Y" (at 0 0)))')
    assert found.has_outline is False
    assert found.segments == {}


# --- the gate: a netlist checked against what was intended -----------------


EXPECTED = {
    "nets": {"LED_A": ["U1:PB2", "R1:2"], "GND": ["U1:4"]},
}


def test_a_netlist_that_matches_the_declaration_passes():
    verdict, differences = kicad_lib.check_netlist(NETLIST, EXPECTED)
    assert verdict == "PASS"
    assert differences == []


def test_a_pin_may_be_declared_by_name_or_by_number():
    """`U1:PB2` is how a person states the intent and `U1:7` is how the
    netlist records it. A declaration that has to be written in pin numbers
    is one nobody will keep up to date."""
    for spelling in ("U1:PB2", "U1:7"):
        verdict, _ = kicad_lib.check_netlist(
            NETLIST, {"nets": {"LED_A": [spelling, "R1:2"]}})
        assert verdict == "PASS", spelling


def test_a_net_that_was_declared_and_never_generated_fails_by_name():
    verdict, differences = kicad_lib.check_netlist(
        NETLIST, {"nets": {"LED_B": ["U1:PB3", "R2:2"]}})
    assert verdict == "FAIL"
    assert any("LED_B" in d for d in differences)


def test_a_pin_missing_from_a_net_fails_and_says_which():
    verdict, differences = kicad_lib.check_netlist(
        NETLIST, {"nets": {"LED_A": ["U1:PB2", "R1:2", "C9:1"]}})
    assert verdict == "FAIL"
    assert any("C9:1" in d for d in differences)


def test_a_pin_on_a_net_that_was_not_declared_fails():
    """The direction that catches a real defect rather than a typo: a net
    quietly picking up a pin nobody intended is how RESET ends up shorted to
    GND, and the netlist is the only place it shows before the board."""
    verdict, differences = kicad_lib.check_netlist(
        NETLIST, {"nets": {"LED_A": ["U1:PB2"]}})
    assert verdict == "FAIL"
    assert any("R1:2" in d for d in differences)


def test_nets_the_declaration_does_not_mention_are_left_alone():
    """A declaration states what must be true, not everything that is. GND is
    not mentioned here and must not fail the check."""
    verdict, _ = kicad_lib.check_netlist(
        NETLIST, {"nets": {"LED_A": ["U1:PB2", "R1:2"]}})
    assert verdict == "PASS"


def test_an_empty_declaration_is_refused_rather_than_passing_vacuously():
    """A gate that passes when it was given nothing to check is worse than no
    gate: it reports success over an empty assertion."""
    verdict, differences = kicad_lib.check_netlist(NETLIST, {"nets": {}})
    assert verdict == "FAIL"
    assert any("nothing" in d.lower() for d in differences)


# --- the 2026-09-13 audit: three defects in the readers above ----------------


THREE_PIN_NET = """(export (version "E")
  (nets
    (net
      (code 1)
      (name "BUS")
      (node
        (ref "U1")
        (pin "1")
        (pinfunction "PB5"))
      (node
        (ref "U1")
        (pin "2")
        (pinfunction "PB3"))
      (node
        (ref "U1")
        (pin "3")
        (pinfunction "PB4")))))
"""


def test_read_nodes_keeps_both_spellings_of_one_pin_together():
    pairs = kicad_lib.read_nodes(THREE_PIN_NET)["BUS"]
    assert pairs == [("U1:1", "U1:PB5"), ("U1:2", "U1:PB3"), ("U1:3", "U1:PB4")]


def test_an_undeclared_pin_is_caught_whatever_spelling_the_others_use():
    """The gate's own assertion, misaligned. check_netlist zipped the sorted
    number list against the sorted function list, and on a three-pin net
    `1 2 3` pairs with `PB3 PB4 PB5` -- pin 2 (PB3) was compared as if it
    were PB4. Declaring PB5 and PB4 and NOT PB3 came back PASS: the exact
    short the check exists to catch, on a net with mixed spellings."""
    verdict, differences = kicad_lib.check_netlist(
        THREE_PIN_NET, {"nets": {"BUS": ["U1:1", "U1:PB5", "U1:PB4"]}})
    assert verdict == "FAIL"
    assert any("U1:PB3" in d and "not declared" in d for d in differences)


def test_a_fully_declared_net_in_mixed_spellings_passes():
    """The other half of the same misalignment: every pin declared, some by
    number and some by function, was reported as an undeclared pin."""
    verdict, differences = kicad_lib.check_netlist(
        THREE_PIN_NET, {"nets": {"BUS": ["U1:PB5", "U1:2", "U1:3"]}})
    assert verdict == "PASS", differences


@pytest.mark.parametrize("expected", [
    {"nets": {"BUS": "U1:PB5"}},          # a string iterates per character
    {"nets": {"BUS": 3}},                 # raised TypeError out of the tool
    {"nets": 7},
    {"nets": ["BUS"]},
    {"nets": {"BUS": ["U1:PB5", 2]}},
])
def test_a_declaration_of_the_wrong_shape_fails_with_the_shape_named(expected):
    verdict, differences = kicad_lib.check_netlist(THREE_PIN_NET, expected)
    assert verdict == "FAIL"
    assert len(differences) == 1
    assert "list of pins" in differences[0]


def test_a_round_courtyard_measures_its_diameter_both_ways():
    """Capacitor_THT's radial cans draw the courtyard as one fp_circle,
    `(center cx cy) (end cx+r cy)`. Read as two corners that is `r x 0`:
    describe_footprint said CP_Radial_D8.0mm needs "4.25 x 0.0 mm of board"
    for a part 9.6 mm across."""
    text = ('(footprint "CP_Radial_D8.0mm_P3.50mm"\n'
            '  (fp_circle (center 1.75 0) (end 6 0)\n'
            '    (stroke (width 0.05) (type default)) (fill none)\n'
            '    (layer "F.CrtYd"))\n'
            '  (pad "1" thru_hole circle (at 0 0) (size 1.6 1.6))\n'
            '  (pad "2" thru_hole circle (at 3.5 0) (size 1.6 1.6))\n)\n')
    found = kicad_lib.read_footprint(text)
    assert found.extent == (8.5, 8.5)


# --- the outline's size -----------------------------------------------------

# How KiCad 10 writes a board edge: one gr_line per side, the layer after the
# stroke. Copied from a board run_layout produced (2026-09-24).
_EDGE = """
	(gr_line
		(start {x0} {y0})
		(end {x1} {y1})
		(stroke
			(width 0.1)
			(type default)
		)
		(layer "Edge.Cuts")
		(uuid "b2cc98f2-fd02-4155-9be8-75a4e500b7e1")
	)"""


def _outline(w, h):
    sides = [(0, 0, w, 0), (w, 0, w, h), (w, h, 0, h), (0, h, 0, 0)]
    return "(kicad_pcb" + "".join(_EDGE.format(x0=a, y0=b, x1=c, y1=d) for a, b, c, d in sides) + ")"


def test_the_outline_size_is_read_from_the_edge_lines():
    assert kicad_lib.outline_size(_outline(32, 22)) == (32.0, 22.0)


def test_lines_on_other_layers_are_not_the_outline():
    silk = _outline(80, 60).replace('"Edge.Cuts"', '"F.SilkS"')
    assert kicad_lib.outline_size(silk[:-1] + _outline(32, 22)[len("(kicad_pcb"):]) == (32.0, 22.0)


def test_a_board_with_no_edge_has_no_size():
    assert kicad_lib.outline_size("(kicad_pcb)") is None


# --- KiCad alternates ---------------------------------------------------------

SYMBOL = '''(kicad_symbol_lib
\t(symbol "CHIP"
\t\t(pin_names (offset 1.016))
\t\t(symbol "CHIP_1_1"
\t\t\t(pin bidirectional line (at 0 0 0) (length 2.54)
\t\t\t\t(name "PA7" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "17" (effects (font (size 1.27 1.27))))
\t\t\t\t(alternate "SPI1_MOSI" bidirectional line)
\t\t\t\t(alternate "TIM1_CH1N" bidirectional line)
\t\t\t)
\t\t\t(pin power_in line (at 0 2.54 0) (length 2.54)
\t\t\t\t(name "VDD" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "1" (effects (font (size 1.27 1.27))))
\t\t\t)
\t\t)
\t)
\t(symbol "CHIP_V2"
\t\t(extends "CHIP")
\t)
)'''


def test_alternates_are_read_per_pin():
    found = kicad_lib.read_symbol(SYMBOL, "CHIP")
    by_number = {p.number: p for p in found.pins}
    assert by_number["17"].alternates == ("SPI1_MOSI", "TIM1_CH1N")
    assert by_number["1"].alternates == ()


def test_alternates_follow_extends():
    found = kicad_lib.read_symbol(SYMBOL, "CHIP_V2")
    assert {p.number: p.alternates for p in found.pins}["17"] == ("SPI1_MOSI", "TIM1_CH1N")


# --- keepouts are not pours ---------------------------------------------------
#
# Cut from a real board (2026-09-28): RF_Module:ESP32-C6-MINI-1 carries an
# "Antenna" rule area that forbids copper. It is a zone that can never hold a
# fill, and it was counted as an unfilled pour: check_done answered NOT DONE
# on a finished board, and the README said "Copper pours: 3, 1 NOT filled".
ANTENNA_FOOTPRINT = """	(footprint "RF_Module:ESP32-C6-MINI-1"
		(layer "F.Cu")
		(zone
			(layers "F.Cu" "B.Cu")
			(uuid "a94efd2a-edb1-4fca-a8ec-97c835d6ddd8")
			(name "Antenna (keep clear)")
			(hatch full 0.508)
			(connect_pads
				(clearance 0)
			)
			(min_thickness 0.254)
			(keepout
				(tracks not_allowed)
				(vias not_allowed)
				(pads not_allowed)
				(copperpour not_allowed)
				(footprints not_allowed)
			)
			(fill
				(thermal_gap 0.508)
				(thermal_bridge_width 0.508)
			)
			(polygon
				(pts
					(xy 46.6 11.4) (xy 3.4 11.4) (xy 3.4 -9) (xy 46.6 -9)
				)
			)
		)
	)
"""


def _pour(filled: bool) -> str:
    fill = '\t\t(filled_polygon\n\t\t\t(layer "F.Cu")\n\t\t\t(pts (xy 0 0))\n\t\t)\n' if filled else ""
    return (f'\t(zone\n\t\t(net "GND")\n\t\t(layer "F.Cu")\n\t\t(hatch edge 0.5)\n'
            f'\t\t(fill yes\n\t\t\t(thermal_gap 0.5)\n\t\t)\n{fill}\t)\n')


def test_a_footprints_keepout_is_not_an_unfilled_pour():
    board = "(kicad_pcb\n" + ANTENNA_FOOTPRINT + _pour(True) + _pour(True) + ")\n"
    assert kicad_lib.unfilled_zones(board) == 0
    found = kicad_lib.read_board(board)
    assert (found.zones, found.unfilled) == (2, 0)


def test_an_unfilled_pour_beside_a_keepout_is_still_counted():
    board = "(kicad_pcb\n" + _pour(True) + ANTENNA_FOOTPRINT + _pour(False) + ")\n"
    assert kicad_lib.unfilled_zones(board) == 1
    found = kicad_lib.read_board(board)
    assert (found.zones, found.unfilled) == (2, 1)
