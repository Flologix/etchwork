import pytest

from contract.checkers import REGISTRY
from contract.checkers.topology import diode_across, net_connects, no_short, series_part_between
from contract.match import NetSpec, PartSpec
from contract.tests.boards import fp, make, pad

PARTS = {
    "ic": PartSpec.from_toml("ic", {"ref": "^U[0-9]+$"}),
    "sw": PartSpec.from_toml("sw", {"ref": "^SW[0-9]+$"}),
    "led": PartSpec.from_toml("led", {"ref": "^(D|LED)[0-9]+$"}),
    "bt": PartSpec.from_toml("bt", {"ref": "^BT[0-9]+$"}),
}
N = lambda name, table: NetSpec.from_toml(name, table, PARTS)
SUPPLY = N("supply", {"words": ["VCC", "VDD"]})
GROUND = N("ground", {"words": ["GND", "VSS"]})
OSC = N("enc_osc", {"words": ["OSC"], "part": "ic"})
DATA = N("enc_data", {"words": ["K0", "K1", "K2", "K3"], "part": "ic"})
LED_NETS = N("led_nets", {"part": "led"})
BATT = N("batt_pos", {"words": ["+"], "part": "bt"})
ENC_VDD = N("enc_vdd", {"words": ["VDD"], "part": "ic"})


def keyfob(r1="330k", r2="1k", r3=None, sw2_net="N_SW2", k2_net="", bt_plus="VCC"):
    footprints = [fp("U1", "EV1527", pad_count=8), fp("R1", r1), fp("R2", r2), fp("D1", "RED"),
                  fp("SW1", pad_count=4), fp("SW2", pad_count=4), fp("BT1", "CR2032", pad_count=3)]
    if r3:
        footprints.append(fp("R3", r3))
    pads = [pad("U1", 1, "GND", names=["VSS"]), pad("U1", 2, "NET3", names=["OSC"]),
            pad("U1", 3, "N_SW1", names=["K0"]), pad("U1", 4, "N_SW2", names=["K1"]),
            pad("U1", 5, k2_net, names=["K2"]), pad("U1", 7, "DOUT", names=["DOUT"]),
            pad("U1", 8, "VCC", names=["VDD"]),
            pad("R1", 1, "NET3"), pad("R1", 2, "VCC"),
            pad("R2", 1, "DOUT"), pad("R2", 2, "LED_A"),
            pad("D1", 1, "GND", names=["K"]), pad("D1", 2, "LED_A", names=["A"]),
            pad("SW1", 1, "VCC"), pad("SW1", 1, "VCC"), pad("SW1", 2, "N_SW1"), pad("SW1", 2, "N_SW1"),
            pad("SW2", 1, "VCC"), pad("SW2", 2, sw2_net),
            pad("BT1", 1, bt_plus, names=["+"]), pad("BT1", 2, "GND", names=["-"])]
    if r3:
        pads.append(pad("R3", 1, "GND"))
        pads.append(pad("R3", 2, "LED_A"))
    return make(footprints=footprints, pads=pads)


def test_registered():
    for name, fn in [("series_part_between", series_part_between),
                     ("net_connects", net_connects), ("no_short", no_short)]:
        assert REGISTRY[name] is fn


# --- series_part_between ---------------------------------------------------

def test_series_between_two_nets_in_range():
    r = series_part_between(keyfob(), net_a=OSC, net_b=SUPPLY, kind="R", min=300e3, max=430e3)
    assert r.passed and r.measured == {"parts": ["R1 330k"]}


def test_series_out_of_range_names_the_value():
    r = series_part_between(keyfob(r1="100k"), net_a=OSC, net_b=SUPPLY, kind="R", min=300e3, max=430e3)
    assert not r.passed and r.measured == {"parts": ["R1 100k"]} and "100k" in r.detail


def test_series_wrong_kind_is_not_counted():
    r = series_part_between(keyfob(), net_a=OSC, net_b=SUPPLY, kind="C", min=0, max=1)
    assert not r.passed and r.measured == {"parts": []}


def test_series_open_ended_with_exclusions():
    # LED nets are {GND, LED_A}; excluding ground leaves LED_A, and R2 runs
    # from LED_A to DOUT. A resistor to ground would not count.
    r = series_part_between(keyfob(), net_a=LED_NETS, exclude=(SUPPLY, GROUND),
                            kind="R", min=470.0, max=4700.0)
    assert r.passed and r.measured == {"parts": ["R2 1k"]}
    assert not series_part_between(keyfob(r2="100"), net_a=LED_NETS, exclude=(SUPPLY, GROUND),
                                   kind="R", min=470.0, max=4700.0).passed


def test_series_excluded_net_should_not_pass():
    # A resistor (R3) between an excluded net (GND) and a non-excluded part of net_a (LED_A)
    # should NOT pass. The excluded net must not be treated as "outside" the reduced set.
    # R2 is out of range to ensure only R3 is considered.
    r = series_part_between(keyfob(r2="100", r3="1k"), net_a=LED_NETS, exclude=(SUPPLY, GROUND),
                            kind="R", min=470.0, max=4700.0)
    # R3 should NOT be in measured parts; only R2 is (which is out of range)
    assert not r.passed and "R3" not in str(r.measured)


def _led_on_dout(r3_to="TX_DATA"):
    # D1 driven straight from DOUT with no resistor; a 1k R3 hangs off DOUT
    # towards something else (the transmitter's data pin). R3 touches the
    # LED's net but is not in series with the LED.
    return make(
        footprints=[fp("U1", "EV1527", pad_count=8), fp("U2", "SYN115", pad_count=6),
                    fp("D1", "RED"), fp("R3", "1k")],
        pads=[pad("U1", 7, "DOUT", names=["DOUT"]),
              pad("D1", 1, "GND", names=["K"]), pad("D1", 2, "DOUT", names=["A"]),
              pad("R3", 1, "DOUT"), pad("R3", 2, r3_to),
              pad("U2", 6, r3_to, names=["DATA"])])


def test_series_open_ended_needs_a_series_node():
    r = series_part_between(_led_on_dout(), net_a=LED_NETS, exclude=(SUPPLY, GROUND),
                            kind="R", min=470.0, max=4700.0)
    assert not r.passed, r.detail
    assert "R3" in r.detail and "U1" in r.detail
    assert r.measured["parts"] == [] and r.measured["not_in_series"] == ["R3 1k"]


def test_series_node_may_hold_other_net_a_parts():
    # Two LEDs chained through one node, fed by R2: every pad on LED_A
    # belongs to R2 or to an LED, so R2 is in series.
    b = make(footprints=[fp("R2", "1k"), fp("D1", "RED"), fp("D2", "GRN")],
             pads=[pad("R2", 1, "DOUT"), pad("R2", 2, "LED_A"),
                   pad("D1", 2, "LED_A"), pad("D1", 1, "MID"),
                   pad("D2", 2, "MID"), pad("D2", 1, "GND")])
    r = series_part_between(b, net_a=LED_NETS, exclude=(SUPPLY, GROUND),
                            kind="R", min=470.0, max=4700.0)
    assert r.passed and r.measured["parts"] == ["R2 1k"]


def test_series_node_rule_skipped_without_a_part():
    # net_a found by words alone names no part to be in series with, so any
    # other pads on the shared net are allowed.
    dout = N("dout", {"words": ["DOUT"]})
    r = series_part_between(_led_on_dout(), net_a=dout, kind="R", min=470.0, max=4700.0)
    assert r.passed and r.measured["parts"] == ["R3 1k"]


def test_series_unresolvable_net_is_a_failure():
    nowhere = N("nowhere", {"words": ["ZZZ"]})
    r = series_part_between(keyfob(), net_a=nowhere, net_b=SUPPLY, kind="R", min=0, max=1e9)
    assert not r.passed and "nowhere" in r.detail


# --- net_connects ----------------------------------------------------------

def test_net_connects_pair():
    assert net_connects(keyfob(), net_a=BATT, net_b=ENC_VDD).passed
    r = net_connects(keyfob(bt_plus="VBATX"), net_a=BATT, net_b=ENC_VDD)
    assert not r.passed and "VBATX" in r.detail


def test_net_connects_each_part_distinct():
    r = net_connects(keyfob(), each_part=PARTS["sw"], net_b=DATA, distinct=True)
    assert r.passed
    assert r.measured == {"reach": {"SW1": ["N_SW1"], "SW2": ["N_SW2"]}}


def test_net_connects_two_switches_on_one_input_fail_distinct():
    b = keyfob(sw2_net="N_SW1")
    assert net_connects(b, each_part=PARTS["sw"], net_b=DATA).passed          # both reach one
    assert not net_connects(b, each_part=PARTS["sw"], net_b=DATA, distinct=True).passed


def test_net_connects_stranded_part_fails():
    r = net_connects(keyfob(sw2_net="NOWHERE"), each_part=PARTS["sw"], net_b=DATA, distinct=True)
    assert not r.passed and "SW2" in r.detail


def test_net_connects_needs_exactly_one_side():
    with pytest.raises(ValueError):
        net_connects(keyfob(), net_b=DATA)
    with pytest.raises(ValueError):
        net_connects(keyfob(), net_b=DATA, net_a=BATT, each_part=PARTS["sw"])


# --- no_short --------------------------------------------------------------

def test_no_short_distinct_nets_pass():
    assert no_short(keyfob(), net_a=DATA, net_b=SUPPLY).passed


def test_no_short_data_pin_tied_to_supply_fails():
    r = no_short(keyfob(k2_net="VCC"), net_a=DATA, net_b=SUPPLY)
    assert not r.passed and r.measured == {"shared": ["VCC"]}


def test_no_short_unresolvable_is_a_failure():
    assert not no_short(keyfob(), net_a=N("x", {"words": ["ZZZ"]}), net_b=SUPPLY).passed


# --- diode_across ------------------------------------------------------------------

QPARTS = {"q": PartSpec.from_toml("q", {"ref": "^Q[0-9]+$"})}
V12 = NetSpec.from_toml("in12", {"words": ["+12V"]}, QPARTS)
DRAIN = NetSpec.from_toml("drain", {"words": ["D"], "part": "q"}, QPARTS)


def _coil(k_net="+12V", a_net="N_DRAIN", names=("K", "A"), diode=True):
    """Q1's drain and a +12V terminal, with D1 across them."""
    pads = [pad("Q1", 3, "N_DRAIN", names=["D"]), pad("J1", 1, "+12V")]
    footprints = [fp("Q1", "AO3400A", pad_count=3), fp("J1", pad_count=2)]
    if diode:
        footprints.append(fp("D1", "1N4148W"))
        pads += [pad("D1", 1, k_net, names=[names[0]] if names[0] else []),
                 pad("D1", 2, a_net, names=[names[1]] if names[1] else [])]
    return make(footprints=footprints, pads=pads)


def test_diode_across_registered():
    assert REGISTRY["diode_across"] is diode_across


def test_diode_across_passes_cathode_on_supply():
    r = diode_across(_coil(), cathode=V12, anode=DRAIN)
    assert r.passed, r.detail
    assert r.measured == {"across": ["D1"], "reversed": [], "unnamed": []}


def test_diode_across_accepts_spelled_out_pin_names():
    assert diode_across(_coil(names=("CATHODE", "ANODE")), cathode=V12, anode=DRAIN).passed


def test_diode_across_fails_reversed_and_says_so():
    r = diode_across(_coil(k_net="N_DRAIN", a_net="+12V"), cathode=V12, anode=DRAIN)
    assert not r.passed
    assert "reversed" in r.detail
    assert r.measured["reversed"] == ["D1"]


def test_diode_across_fails_when_orientation_cannot_be_told():
    r = diode_across(_coil(names=("", "")), cathode=V12, anode=DRAIN)
    assert not r.passed
    assert "cannot be told" in r.detail
    assert r.measured["unnamed"] == ["D1"]


def test_diode_across_fails_with_no_diode():
    r = diode_across(_coil(diode=False), cathode=V12, anode=DRAIN)
    assert not r.passed and "no diode" in r.detail


def test_diode_across_fails_when_a_net_is_missing():
    board = make(footprints=[fp("J1", pad_count=2)], pads=[pad("J1", 1, "+12V")])
    r = diode_across(board, cathode=V12, anode=DRAIN)
    assert not r.passed and "no net matches drain" in r.detail
