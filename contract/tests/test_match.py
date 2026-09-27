import pytest

from contract.board import Footprint
from contract.match import (NetSpec, PartSpec, is_kind, matching, pads_by_ref,
                            parse_value, resolve_net, resolve_pads, words)
from contract.tests.boards import fp, make, pad


def _fp(ref, value="", lib_id="", pad_count=2):
    return Footprint(ref, value, lib_id, (0.0, 0.0), 0.0, (), pad_count)


# --- words -----------------------------------------------------------------

def test_words_split_and_uppercase():
    assert words("~{RESET}/PB5") == {"RESET", "PB5"}
    assert words("vcc_3v3") == {"VCC", "3V3"}


def test_words_keep_plus():
    assert words("+") == {"+"}
    assert words("BAT+") == {"BAT+"}
    assert words("+3V3") == {"+3V3"}
    assert words("-") == frozenset()


# --- parse_value -----------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("330k", 330e3), ("330K", 330e3), ("4k7", 4.7e3), ("4.7k", 4.7e3), ("4,7k", 4.7e3),
    ("470R", 470.0), ("470", 470.0), ("2R2", 2.2), ("1M", 1e6), ("1m", 1e-3),
    ("100n", 100e-9), ("100nF", 100e-9), ("0.1uF", 0.1e-6), ("0.1µF", 0.1e-6),
    ("100nF/16V", 100e-9), ("330 kΩ", 330e3), ("10p", 10e-12),
])
def test_parse_value_spellings(text, expected):
    assert parse_value(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["RED", "CR2032", "SW_Push", "", "4.7k7"])
def test_parse_value_non_values(text):
    assert parse_value(text) is None


# --- is_kind ---------------------------------------------------------------

def test_is_kind_matches_designator_letters_exactly():
    assert is_kind(_fp("R1"), "R") and is_kind(_fp("R12"), "R")
    assert not is_kind(_fp("RV1"), "R") and not is_kind(_fp("RN1"), "R")
    assert not is_kind(_fp("C1"), "R")


# --- PartSpec --------------------------------------------------------------

def test_partspec_every_given_field_must_match():
    spec = PartSpec.from_toml("holder", {"lib_id": ["(?i)2032"], "pads": [3]})
    assert spec.matches(_fp("BT1", lib_id="Battery:BatteryHolder_Keystone_3002_1x2032", pad_count=3))
    assert not spec.matches(_fp("BT1", lib_id="Battery:BatteryHolder_Keystone_3002_1x2032", pad_count=2))
    assert not spec.matches(_fp("BT1", lib_id="Battery:BatteryHolder_Keystone_3000_1x12mm", pad_count=3))


def test_partspec_any_pattern_in_a_field_matches():
    spec = PartSpec.from_toml("enc", {"value": ["(?i)1527", "(?i)2262"]})
    assert spec.matches(_fp("U1", value="EV1527")) and spec.matches(_fp("U1", value="pt2262"))
    assert not spec.matches(_fp("U1", value="LM358"))


def test_partspec_accepts_a_bare_string():
    spec = PartSpec.from_toml("u", {"ref": "^U[0-9]+$"})
    assert spec.ref == ("^U[0-9]+$",)
    assert spec.matches(_fp("U1")) and not spec.matches(_fp("R1"))


@pytest.mark.parametrize("table", [{}, {"colour": ["red"]}, {"value": ["("]}])
def test_partspec_rejects_bad_tables(table):
    with pytest.raises(ValueError):
        PartSpec.from_toml("p", table)


def test_matching_lists_footprints():
    b = make(footprints=[fp("U1", "EV1527"), fp("R1", "330k")])
    assert [f.ref for f in matching(b, PartSpec.from_toml("u", {"ref": "^U"}))] == ["U1"]


def test_partspec_pads_accepts_bare_int():
    spec = PartSpec.from_toml("p", {"pads": 3})
    assert spec.pads == (3,)


@pytest.mark.parametrize("table", [
    {"pads": ["x"]},
    {"pads": [None]},
])
def test_partspec_pads_rejects_non_ints(table):
    with pytest.raises(ValueError):
        PartSpec.from_toml("p", table)


# --- NetSpec ---------------------------------------------------------------

PARTS = {"ic": PartSpec.from_toml("ic", {"ref": "^U[0-9]+$"})}


def test_netspec_words_are_uppercased():
    assert NetSpec.from_toml("osc", {"words": ["osc"], "part": "ic"}, PARTS).words == {"OSC"}


@pytest.mark.parametrize("table", [
    {},                                   # matches every net
    {"words": ["VCC_3V3"]},               # not one word: could never match
    {"words": ["BAT-"]},                  # "-" is a separator: not one word
    {"words": ["VCC"], "part": "nope"},   # unknown part alias
    {"words": ["VCC"], "colour": "red"},  # unknown key
])
def test_netspec_rejects_bad_tables(table):
    with pytest.raises(ValueError):
        NetSpec.from_toml("n", table, PARTS)


def test_netspec_rejects_non_string_part():
    with pytest.raises(ValueError):
        NetSpec.from_toml("n", {"words": ["VCC"], "part": ["ic"]}, PARTS)


# --- resolution ------------------------------------------------------------

def _keyfob():
    return make(
        footprints=[fp("U1", "EV1527", pad_count=8), fp("R1", "330k"), fp("BT1", "CR2032", pad_count=3)],
        pads=[pad("U1", 2, "NET3", names=["OSC"]), pad("U1", 8, "VCC", names=["VDD"]),
              pad("U1", 1, "GND", names=["VSS"]), pad("U1", 5, "", names=["K2"]),
              pad("R1", 1, "NET3"), pad("R1", 2, "VCC"),
              pad("BT1", 1, "VCC", names=["+"]), pad("BT1", 2, "GND", names=["-"])])


def test_resolve_by_pin_name_on_a_part():
    osc = NetSpec.from_toml("osc", {"words": ["OSC"], "part": "ic"}, PARTS)
    assert resolve_net(_keyfob(), osc) == {"NET3"}
    assert [(p.ref, p.number) for p in resolve_pads(_keyfob(), osc)] == [("U1", "2")]


def test_resolve_by_net_name_anywhere():
    supply = NetSpec.from_toml("supply", {"words": ["VCC", "VDD"]}, PARTS)
    assert resolve_net(_keyfob(), supply) == {"VCC"}


def test_unconnected_pins_never_resolve():
    k2 = NetSpec.from_toml("k2", {"words": ["K2"], "part": "ic"}, PARTS)
    assert resolve_net(_keyfob(), k2) == frozenset()


def test_part_without_words_means_every_net_on_it():
    ic_nets = NetSpec.from_toml("all", {"part": "ic"}, PARTS)
    assert resolve_net(_keyfob(), ic_nets) == {"NET3", "VCC", "GND"}


def test_part_restriction_excludes_other_parts_pins():
    # BT1's "+" pin is on VCC, but a spec limited to U* must not see it.
    plus = NetSpec.from_toml("plus", {"words": ["+"], "part": "ic"}, PARTS)
    assert resolve_net(_keyfob(), plus) == frozenset()


def test_net_name_fallback_when_pin_names_are_empty():
    # A custom encoder symbol: no library pin names reach the board, but the
    # agent called the net OSC. Resolution must still find it...
    b = make(footprints=[fp("U1", "EV1527", pad_count=8)],
             pads=[pad("U1", 2, "OSC"), pad("U1", 3, "N$7")])
    osc = NetSpec.from_toml("osc", {"words": ["OSC"], "part": "ic"}, PARTS)
    assert resolve_net(b, osc) == {"OSC"}
    # ...and an anonymous net resolves to nothing, not to everything.
    k0 = NetSpec.from_toml("k0", {"words": ["K0"], "part": "ic"}, PARTS)
    assert resolve_net(b, k0) == frozenset()


def test_pads_by_ref():
    grouped = pads_by_ref(_keyfob())
    assert sorted(p.number for p in grouped["BT1"]) == ["1", "2"]
