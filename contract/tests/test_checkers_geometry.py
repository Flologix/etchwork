import pytest

from contract.checkers import REGISTRY
from contract.checkers.geometry import decoupling_near, outline_within
from contract.match import NetSpec, PartSpec
from contract.tests.boards import fp, make, pad, rect


def test_registered():
    assert REGISTRY["outline_within"] is outline_within
    assert REGISTRY["decoupling_near"] is decoupling_near


# --- outline_within --------------------------------------------------------

def test_outline_within_passes_and_measures():
    r = outline_within(make(outline=rect(36.0, 22.0)), width_mm=37.0, height_mm=23.4, tolerance_mm=0.2)
    assert r.passed
    assert r.measured == {"width_mm": 36.0, "height_mm": 22.0}


def test_outline_within_tolerance_is_inclusive():
    assert outline_within(make(outline=rect(37.2, 23.6)), width_mm=37.0, height_mm=23.4,
                          tolerance_mm=0.2).passed
    assert not outline_within(make(outline=rect(37.3, 23.4)), width_mm=37.0, height_mm=23.4,
                              tolerance_mm=0.2).passed


def test_outline_within_accepts_either_orientation():
    r = outline_within(make(outline=rect(23.4, 37.0)), width_mm=37.0, height_mm=23.4, tolerance_mm=0.2)
    assert r.passed


def test_outline_within_too_big_reports_size():
    r = outline_within(make(outline=rect(40.0, 22.0)), width_mm=37.0, height_mm=23.4, tolerance_mm=0.2)
    assert not r.passed
    assert "40.00 x 22.00" in r.detail


def test_outline_within_no_outline_fails():
    r = outline_within(make(outline=[]), width_mm=37.0, height_mm=23.4, tolerance_mm=0.2)
    assert not r.passed and r.measured is None


# --- decoupling_near -------------------------------------------------------

PARTS = {"ic": PartSpec.from_toml("ic", {"ref": "^U[0-9]+$"})}
VDD = NetSpec.from_toml("enc_vdd", {"words": ["VDD", "VCC"], "part": "ic"}, PARTS)
GND = NetSpec.from_toml("ground", {"words": ["GND", "VSS"]}, PARTS)


def _board(cap_value="100n", cap_x=13.7, cap_nets=("VCC", "GND")):
    return make(
        footprints=[fp("U1", "EV1527", pad_count=8), fp("C1", cap_value)],
        pads=[pad("U1", 8, "VCC", pos=(12.5, 4.1), names=["VDD"]),
              pad("U1", 1, "GND", pos=(7.5, 7.9), names=["VSS"]),
              pad("C1", 1, cap_nets[0], pos=(cap_x, 4.1)),
              pad("C1", 2, cap_nets[1], pos=(cap_x + 1.55, 4.1))])


def test_decoupling_close_enough_passes():
    r = decoupling_near(_board(), pin=VDD, ground=GND, min_farads=10e-9, max_mm=3.0)
    assert r.passed
    assert r.measured["cap"] == "C1"
    assert r.measured["distance_mm"] == pytest.approx(1.2)
    assert r.measured["pin"] == "U1:8"


def test_decoupling_too_far_fails_with_distance():
    r = decoupling_near(_board(cap_x=20.0), pin=VDD, ground=GND, min_farads=10e-9, max_mm=3.0)
    assert not r.passed
    assert r.measured["distance_mm"] == pytest.approx(7.5)


def test_decoupling_too_small_fails():
    r = decoupling_near(_board(cap_value="1n"), pin=VDD, ground=GND, min_farads=10e-9, max_mm=3.0)
    assert not r.passed
    assert r.measured == {"candidates": ["C1 1n"]}


def test_decoupling_cap_not_across_supply_and_ground_fails():
    r = decoupling_near(_board(cap_nets=("VCC", "NET9")), pin=VDD, ground=GND,
                        min_farads=10e-9, max_mm=3.0)
    assert not r.passed


def test_decoupling_no_supply_pin_fails_not_raises():
    b = make(footprints=[fp("U1", "EV1527")], pads=[pad("U1", 2, "NET3")])
    r = decoupling_near(b, pin=VDD, ground=GND, min_farads=10e-9, max_mm=3.0)
    assert not r.passed and "enc_vdd" in r.detail


# --- decoupling_near each ------------------------------------------------------------

MCU_SUPPLY = NetSpec.from_toml("mcu_supply", {"words": ["VCC", "AVCC"], "part": "ic"}, PARTS)


def _mcu(caps, value="100n"):
    """U1 with VCC pins at (0, 0) and (0, 1.6) and AVCC at (8, 2); caps maps
    ref -> position of its supply pad, each across +3V3 and GND."""
    pads = [pad("U1", 4, "+3V3", pos=(0.0, 0.0), names=["VCC"]),
            pad("U1", 6, "+3V3", pos=(0.0, 1.6), names=["VCC"]),
            pad("U1", 18, "+3V3", pos=(8.0, 2.0), names=["AVCC"]),
            pad("U1", 3, "GND", names=["GND"])]
    for ref, (x, y) in caps.items():
        pads += [pad(ref, 1, "+3V3", pos=(x, y)), pad(ref, 2, "GND", pos=(x - 1.6, y))]
    return make(footprints=[fp("U1", "ATmega328P", pad_count=32),
                            *[fp(ref, value) for ref in caps]], pads=pads)


def test_each_fails_a_pin_left_without_a_cap():
    board = _mcu({"C1": (-1.2, 0.0)})
    assert decoupling_near(board, pin=MCU_SUPPLY, ground=GND, min_farads=100e-9, max_mm=3.0).passed
    r = decoupling_near(board, pin=MCU_SUPPLY, ground=GND, min_farads=100e-9, max_mm=3.0,
                        each=True)
    assert not r.passed
    assert "U1:18" in r.detail and "U1:4" not in r.detail
    assert r.measured["pins"]["U1:18"]["cap"] == "C1"
    assert r.measured["pins"]["U1:18"]["distance_mm"] > 3.0


def test_each_passes_when_every_pin_has_one_and_a_cap_may_serve_two():
    r = decoupling_near(_mcu({"C1": (-1.2, 0.0), "C2": (9.2, 2.0)}), pin=MCU_SUPPLY, ground=GND,
                        min_farads=100e-9, max_mm=3.0, each=True)
    assert r.passed, r.detail
    assert list(r.measured["pins"]) == ["U1:4", "U1:6", "U1:18"]
    assert r.measured["pins"]["U1:6"] == {"cap": "C1", "distance_mm": 2.0}


def test_each_ignores_caps_below_the_minimum():
    r = decoupling_near(_mcu({"C1": (-1.2, 0.0), "C2": (9.2, 2.0)}, value="10n"),
                        pin=MCU_SUPPLY, ground=GND, min_farads=100e-9, max_mm=3.0, each=True)
    assert not r.passed
    assert r.measured["pins"] == {"U1:4": None, "U1:6": None, "U1:18": None}
