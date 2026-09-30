import pytest

from contract.board import Footprint
from contract.match import (NetSpec, PartSpec, is_kind, matching, pads_by_ref,
                            pad_words, parse_value, resolve_net, resolve_pads, words)
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


# --- net-name words: a fallback only, never KiCad's auto-names ---------------

@pytest.mark.parametrize("net", ["Net-(D2-A)", "/sub/Net-(D2-A)", "unconnected-(U1-D2-Pad5)"])
def test_auto_named_nets_contribute_no_words(net):
    # KiCad names an unnamed net after a pad on it: `Net-(D2-A)` must not
    # read as the data input D2.
    b = make(footprints=[fp("U1", "EV1527", pad_count=8)], pads=[pad("U1", 3, net)])
    d2 = NetSpec.from_toml("d2", {"words": ["D2"], "part": "ic"}, PARTS)
    assert resolve_net(b, d2) == frozenset()


def test_pad_with_pin_names_ignores_its_net_name():
    # SW2 has named pins; that its net is called K0 says nothing about SW2's pins.
    b = make(footprints=[fp("U1", "EV1527", pad_count=8)],
             pads=[pad("U1", 7, "K0", names=["DOUT"])])
    k0 = NetSpec.from_toml("k0", {"words": ["K0"], "part": "ic"}, PARTS)
    assert resolve_net(b, k0) == frozenset()


def test_pad_without_pin_names_still_falls_back_to_net_name():
    b = make(footprints=[fp("U1", "EV1527", pad_count=8)], pads=[pad("U1", 3, "K0")])
    k0 = NetSpec.from_toml("k0", {"words": ["K0"], "part": "ic"}, PARTS)
    assert resolve_net(b, k0) == {"K0"}


def test_pad_words():
    from contract.board import Pad
    mk = lambda net, names=(): Pad("U1", "1", (0.0, 0.0), ("F.Cu",), net, tuple(names))
    assert pad_words(mk("VCC", ["VDD"])) == {"VDD"}
    assert pad_words(mk("VCC")) == {"VCC"}
    assert pad_words(mk("Net-(D2-A)")) == frozenset()
    assert pad_words(mk("VCC", ["~"])) == {"VCC"}  # a nameless '~' pin is no name


# --- PartSpec with_pins: a part picked out by what its pins do ----------------

def _two_ics():
    # U1 the encoder (K0 unconnected still counts), U2 a transmitter.
    return make(
        footprints=[fp("U1", "EV1527", pad_count=8), fp("U2", "SYN115", pad_count=6)],
        pads=[pad("U1", 8, "VCC", names=["VDD"]), pad("U1", 3, "", names=["K0"]),
              pad("U2", 5, "VCC", names=["VDD"]), pad("U2", 6, "DOUT", names=["DATA"])])


def test_with_pins_limits_matching_to_parts_with_those_pins():
    enc = PartSpec.from_toml("enc", {"ref": "^U[0-9]+$", "with_pins": ["K0", "D0"]})
    assert [f.ref for f in matching(_two_ics(), enc)] == ["U1"]


def test_with_pins_binds_a_net_spec_to_that_part():
    parts = {"enc": PartSpec.from_toml("enc", {"ref": "^U[0-9]+$", "with_pins": ["K0"]})}
    vdd = NetSpec.from_toml("vdd", {"words": ["VDD"], "part": "enc"}, parts)
    assert [(p.ref, p.number) for p in resolve_pads(_two_ics(), vdd)] == [("U1", "8")]


def test_with_pins_uses_the_net_name_fallback_for_unnamed_pads():
    b = make(footprints=[fp("U1", "EV1527", pad_count=8), fp("U2", "X", pad_count=6)],
             pads=[pad("U1", 3, "K0"), pad("U2", 1, "Net-(U2-K0)")])
    enc = PartSpec.from_toml("enc", {"ref": "^U", "with_pins": ["K0"]})
    assert [f.ref for f in matching(b, enc)] == ["U1"]


def test_with_pins_alone_is_enough_to_not_match_everything():
    assert PartSpec.from_toml("enc", {"with_pins": "K0"}).with_pins == frozenset({"K0"})


@pytest.mark.parametrize("table", [
    {"ref": "^U", "with_pins": ["K_0"]},   # not one word
    {"ref": "^U", "with_pins": [3]},       # not a string
])
def test_with_pins_rejects_bad_words(table):
    with pytest.raises(ValueError):
        PartSpec.from_toml("p", table)


def test_matches_without_board_refuses_a_with_pins_spec():
    # A with_pins spec cannot be judged from the footprint alone.
    spec = PartSpec.from_toml("enc", {"ref": "^U", "with_pins": ["K0"]})
    with pytest.raises(ValueError):
        spec.matches(_fp("U1"))


# --- NetSpec pins ----------------------------------------------------------------

HDR = {"hdr": PartSpec.from_toml("hdr", {"ref": "^J[0-9]+$", "pads": [6]})}


def _header_board():
    return make(
        footprints=[fp("J2", pad_count=6), fp("U1", pad_count=16)],
        pads=[pad("J2", 4, "N_TX", names=["Pin_4"]), pad("J2", 5, "N_RX", names=["Pin_5"]),
              pad("J2", 6, "", names=["Pin_6"]), pad("U1", 4, "N_U", names=["V3"])])


def test_netspec_pins_resolve_by_pad_number_on_the_part():
    spec = NetSpec.from_toml("tx", {"part": "hdr", "pins": ["4"]}, HDR)
    assert spec.pins == {"4"}
    assert resolve_net(_header_board(), spec) == {"N_TX"}


def test_netspec_pins_accept_integers_and_several_pins():
    spec = NetSpec.from_toml("data", {"part": "hdr", "pins": [4, 5]}, HDR)
    assert spec.pins == {"4", "5"}
    assert resolve_net(_header_board(), spec) == {"N_TX", "N_RX"}


def test_netspec_pins_skip_unconnected_pads():
    spec = NetSpec.from_toml("rst", {"part": "hdr", "pins": ["6"]}, HDR)
    assert resolve_net(_header_board(), spec) == frozenset()


@pytest.mark.parametrize("table, message", [
    ({"pins": ["4"]}, "pins needs a part"),
    ({"part": "hdr", "pins": ["4"], "words": ["TXD"]}, "words or pins, not both"),
])
def test_netspec_pins_misuse_is_refused(table, message):
    with pytest.raises(ValueError, match=message):
        NetSpec.from_toml("n", table, HDR)
