"""Finding a part by what it is, instead of guessing what it is called."""
import pytest

from circuit_mcp import parts, paths


SYM = '''(kicad_symbol_lib
	(symbol "D"
		(property "Reference" "D")
		(property "Description" "Diode")
		(property "ki_keywords" "diode")
		(symbol "D_0_1"
			(pin passive line
				(at -3.81 0 0)
				(length 2.54)
				(name "K"
					(effects (font (size 1.27 1.27)))
				)
				(number "2"
					(effects (font (size 1.27 1.27)))
				)
			)
			(pin passive line
				(at 3.81 0 180)
				(length 2.54)
				(name "A"
					(effects (font (size 1.27 1.27)))
				)
				(number "1"
					(effects (font (size 1.27 1.27)))
				)
			)
		)
	)
	(symbol "D_Zener"
		(property "Reference" "D")
		(property "Description" "Zener diode")
		(property "ki_keywords" "zener")
	)
	(symbol "R"
		(property "Reference" "R")
		(property "Description" "Resistor")
	)
)
'''


@pytest.fixture
def project(workspace, monkeypatch):
    monkeypatch.setattr(paths, "SYMBOL_ROOT", workspace / "symbols")
    monkeypatch.setattr(paths, "FP_ROOT", workspace / "fp")
    (workspace / "symbols").mkdir()
    (workspace / "symbols" / "Device.kicad_sym").write_text(SYM)
    pretty = workspace / "fp" / "Diode_THT.pretty"
    pretty.mkdir(parents=True)
    for name in ("D_DO-41_SOD81_P10.16mm_Horizontal", "D_DO-35_SOD27_P7.62mm"):
        (pretty / f"{name}.kicad_mod").write_text("(footprint)")
    return workspace


def test_it_finds_a_symbol_by_what_the_part_is(project):
    """The failure this replaces: the agent wanted a diode, guessed
    `Device:Diode`, and there is no such symbol -- the generic diode is
    `Device:D`. It then grepped a 151,000-line file and ran out of steps."""
    out = parts.find_part("diode")
    assert "Device:D" in out
    assert "Device:D_Zener" in out
    assert "Device:R" not in out


def test_it_gives_the_pin_names_so_they_are_not_guessed(project):
    """`led['A']` and `led['C']` are guesses until something says so. A part
    whose pins are named is a part that can be wired without a second round
    trip."""
    out = parts.find_part("diode")
    assert "A" in out and "K" in out
    assert "1" in out and "2" in out


def test_it_finds_footprints_too(project):
    """`Diode_THT:DO-41` was the other guess, and the real name is
    D_DO-41_SOD81_P10.16mm_Horizontal."""
    out = parts.find_part("DO-41")
    assert "Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal" in out


def test_nothing_matching_says_so_and_says_where_it_looked(project):
    out = parts.find_part("flux capacitor")
    assert "flux capacitor" in out
    assert "symbols" in out or "fp" in out


def test_a_broad_query_is_capped_rather_than_flooding_the_window(project, monkeypatch):
    """MAX_TOOL_OUTPUT is 8192 characters. A search for "r" against KiCad's
    real libraries matches thousands, and a tool that answers with all of them
    has spent the window to say nothing."""
    monkeypatch.setattr(parts, "MAX_HITS", 1)
    out = parts.find_part("d")
    assert out.count("Device:") <= 1
    assert "more" in out.lower()


# --- matching, when the user's word is not KiCad's word ---------------------

CONN = '''(kicad_symbol_lib
	(symbol "Conn_01x02_Pin"
		(property "Description" "Generic connector, single row, 01x02, script generated")
		(property "ki_keywords" "connector")
	)
	(symbol "Screw_Terminal_01x02"
		(property "Description" "Generic screw terminal, single row, 01x02")
		(property "ki_keywords" "screw terminal connector")
	)
)
'''


@pytest.fixture
def bigger(project):
    (project / "symbols" / "Connector.kicad_sym").write_text(CONN)
    pretty = project / "fp" / "Connector_PinHeader_2.54mm.pretty"
    pretty.mkdir(parents=True)
    (pretty / "PinHeader_1x02_P2.54mm_Vertical.kicad_mod").write_text("(footprint)")
    return project


def test_a_phrase_finds_the_part_even_when_kicad_uses_another_word(bigger):
    """The measured miss: find_part('pin header') returned nothing, because
    KiCad's description says "connector" and never "header". The agent
    concluded no terminal existed and substituted something else -- while
    Connector:Screw_Terminal_01x02 was sitting in the library."""
    out = parts.find_part("pin header")
    assert "Conn_01x02_Pin" in out


def test_the_closest_match_is_listed_first(bigger):
    """Loosening to any-word would bury the right answer under everything
    containing "connector", so hits are ranked by how much of the query they
    actually match."""
    out = parts.find_part("screw terminal")
    lines = [line for line in out.splitlines() if line.startswith("  ") and ":" in line]
    assert "Screw_Terminal_01x02" in lines[0]


def test_footprints_without_a_symbol_are_called_out(project):
    """The exact shape of the failure: 278 footprints, no symbol, and no hint
    that the symbol library was simply absent."""
    out = parts.find_part("DO-41")
    assert "no symbol" in out.lower()


def test_all_the_words_beat_any_of_them(bigger):
    """`screw terminal` should not return every connector that merely says
    "connector". Requiring all the words first is what keeps a loose fallback
    from flooding the answer -- 616 hits led by a 20-pin JTAG header is not an
    answer to "pin header"."""
    out = parts.find_part("screw terminal")
    assert "Screw_Terminal_01x02" in out
    assert "Conn_01x02_Pin" not in out, "the any-word fallback should not fire"


def test_the_fallback_still_fires_when_all_the_words_find_nothing(bigger):
    out = parts.find_part("header 01x02")
    assert "Conn_01x02_Pin" in out


# --- a symbol whose footprint is not here -------------------------------------
#
# Measured on 2026-09-11. Asked for a battery, find_part answered
# "2 symbols matching 'battery' -- Device:Battery_Cell" and said NOTHING about
# footprints, because no battery footprint is vendored. The model wrote
# footprint="Device:Battery_Cell" -- the SYMBOL id reused as a footprint --
# and kinet2pcb died with `KeyError: 'Device'` after the netlist was already
# on disk. find_part already warns in the other direction ("a footprint alone
# cannot be placed"); this is the same warning, the other way round.

def test_a_symbol_with_no_footprint_says_so(project):
    """Device.kicad_sym has a battery symbol; fp/ has only Diode_THT."""
    (project / "symbols" / "Batt.kicad_sym").write_text(
        '(kicad_symbol_lib\n\t(symbol "Battery_Cell"\n'
        '\t\t(property "Description" "Single-cell battery")\n\t)\n)\n')
    out = parts.find_part("battery")
    assert "Batt:Battery_Cell" in out
    # The half-part has to be named as a half-part.
    assert "no footprint" in out.lower()
    # And it must not read as "pick one anyway": the symbol id is exactly what
    # the model reached for last time.
    assert "cannot be used as a footprint" in out.lower()


def test_the_footprint_library_listing_is_capped(project):
    """Against the real image this branch used to list all 155 footprint
    libraries (about 2681 characters) on every "no footprint matched"
    answer. Capped at MAX_HITS names, with the remainder counted rather than
    silently dropped."""
    (project / "symbols" / "Batt.kicad_sym").write_text(
        '(kicad_symbol_lib\n\t(symbol "Battery_Cell"\n'
        '\t\t(property "Description" "Single-cell battery")\n\t)\n)\n')
    # The project fixture already has one .pretty (Diode_THT); add enough
    # more that the total clears MAX_HITS regardless.
    for i in range(parts.MAX_HITS + 10):
        (project / "fp" / f"Other{i:02d}.pretty").mkdir()
    total_libraries = len(list((project / "fp").glob("*.pretty")))

    out = parts.find_part("battery")

    assert "no footprint matched" in out.lower()
    line = next(l for l in out.splitlines() if "Footprint libraries here:" in l)
    body = line.split("Footprint libraries here:", 1)[1].strip()
    assert body.endswith(".)")
    body = body[:-2]
    names_part, _, remainder_part = body.rpartition(", and ")
    assert len(names_part.split(", ")) == parts.MAX_HITS
    assert remainder_part == (f"{total_libraries - parts.MAX_HITS} more "
                              f"(use a more specific query)")


def test_the_two_halves_of_a_part_warn_independently(project):
    """Both warnings exist, and neither fires when the part is whole."""
    whole = parts.find_part("diode")
    assert "no footprint" not in whole.lower()
    assert "no symbol matched" not in whole.lower()


# --- the two questions that used to skip the read jail ----------------------
#
# describe_part and describe_footprint don't go through paths.inside; they
# take Library:Part rather than a workspace path, and are confined by
# _not_a_name (a name may not look like a path at all) plus _inside (a glob
# match must resolve under a library root or the workspace). Both are
# regression coverage for the harness's measured escape, ported onto the
# monkeypatched SYMBOL_ROOT/FP_ROOT fixture rather than the old sandbox
# module.


@pytest.mark.parametrize("call, arg", [
    (parts.describe_part, "../../etc/Secret:S"),
    (parts.describe_part, "/etc/Secret:S"),
    (parts.describe_part, "Device:../../etc/passwd"),
    (parts.describe_footprint, "../../etc/Sneak:P"),
    (parts.describe_footprint, "/etc/Sneak:P"),
    (parts.describe_footprint, "Diode_THT:../../etc/passwd"),
])
def test_a_path_is_refused_before_any_library_is_opened(call, arg):
    """describe_part("../../elsewhere/Secret:S") used to read a .kicad_sym
    outside the workspace and print its pins. `_not_a_name` refuses anything
    that looks like a path -- a leading slash, a `..` component anywhere, a
    leading dot -- before a glob is ever built, so no filesystem lookup
    happens at all."""
    out = call(arg)
    assert "is a path, not a name" in out


def test_a_symlink_out_of_every_root_is_not_followed(project, tmp_path_factory):
    """A symlink planted under the (fixture) symbol root that resolves
    outside SYMBOL_ROOT, FP_ROOT and WORKSPACE alike is dropped by `_inside`,
    so describe_part reports the library as not found rather than reading
    through the symlink."""
    outside = tmp_path_factory.mktemp("outside")
    (outside / "Out.kicad_sym").write_text(SYM)
    (project / "symbols" / "Out.kicad_sym").symlink_to(outside / "Out.kicad_sym")
    out = parts.describe_part("Out:D")
    assert "no Out.kicad_sym" in out
    assert "pins" not in out


# --- footprints a netlist asks for and the project cannot resolve --------------

NETLIST = '''(export (version "E")
  (components
    (comp (ref "BT1") (value "3.7V") (footprint "Device:Battery_Cell"))
    (comp (ref "U1") (value "LP3982") (footprint "Regulator_Linear:LP3982ILD-3.3"))
    (comp (ref "D1") (value "D") (footprint "Diode_THT:D_DO-35_SOD27_P7.62mm"))
    (comp (ref "D2") (value "D") (footprint "Diode_THT:D_NOT_HERE"))))
'''


def test_a_netlist_naming_libraries_that_are_not_here_is_reported_before_the_build(project):
    """kinet2pcb reports this as `KeyError: 'Device'` -- a Python traceback
    from inside a dependency, behind a pcbnew assert about register_action().
    A live model read that twice and called the tool again unchanged."""
    unresolved = parts.unresolved_footprints(NETLIST, project / "fp")
    joined = "\n".join(unresolved)
    # The library is absent entirely.
    assert "Device:Battery_Cell" in joined
    assert "Regulator_Linear:LP3982ILD-3.3" in joined
    # The library is here but the footprint inside it is not -- a different
    # fault with the same consequence, and it must not be missed.
    assert "Diode_THT:D_NOT_HERE" in joined
    # The one that resolves is not reported.
    assert "D_DO-35" not in joined


def test_a_netlist_whose_footprints_all_resolve_reports_nothing(project):
    good = '(export (comp (ref "D1") (footprint "Diode_THT:D_DO-35_SOD27_P7.62mm")))'
    assert parts.unresolved_footprints(good, project / "fp") == []


def test_an_unresolved_footprint_names_the_libraries_that_do_exist(project):
    """"'Device' is not a footprint library" is half an answer; the other half
    is what IS here, because the model cannot list the directory it is being
    corrected about without another round trip."""
    unresolved = parts.unresolved_footprints(NETLIST, project / "fp")
    assert any("Diode_THT" in line for line in unresolved)


def test_a_part_in_the_project_is_still_described(project):
    out = parts.describe_part("Device:D")
    assert "2 pins" in out and "K" in out and "A" in out


def test_real_libraries_answer_a_common_part():
    """No fixture: this runs against the real KiCad libraries baked into the
    image, at the default paths.SYMBOL_ROOT and paths.FP_ROOT."""
    text = parts.describe_part("Device:R")
    assert "1" in text and "2" in text, text
    text = parts.describe_footprint("Resistor_SMD:R_0805_2012Metric")
    assert "mm" in text, text
