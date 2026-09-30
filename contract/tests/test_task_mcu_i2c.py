"""mcu-i2c: the task loads, withholds its numbers and strap pins, and its
crystal clauses pass a 4-pad crystal (design 2026-09-29-contract-tasks §4.4)."""
from pathlib import Path

import pytest

from contract import score
from contract.board import Footprint
from contract.checkers import REGISTRY
from contract.match import parse_value
from contract.tests.boards import fp, make, pad

TASK = Path(__file__).resolve().parents[1] / "tasks" / "mcu-i2c"
IDS = ["mcu", "sensor", "crystal", "mcu_decoupling", "avcc", "reset_pullup", "crystal_xtal1",
       "crystal_xtal2", "xtal1_load", "xtal2_load", "sda_pullup", "scl_pullup", "sda_wired",
       "scl_wired", "csb_high", "sda_scl_distinct", "xtal_distinct", "sdo_tied",
       "sensor_decoupling"]


def _clause(clause_id):
    return {c.id: c for c in score.load_task(TASK).clauses}[clause_id]


def _run(clause_id, board):
    c = _clause(clause_id)
    return REGISTRY[c.checker](board, **c.args)


def test_loads_every_clause_with_no_errors():
    t = score.load_task(TASK)
    assert [c.id for c in t.clauses] == IDS
    assert [c.error for c in t.clauses] == [None] * len(IDS)


def test_decoupling_clauses_ask_for_every_pin():
    assert _clause("mcu_decoupling").args["each"] is True
    assert _clause("sensor_decoupling").args["each"] is True


def test_prompt_withholds_the_numbers_and_strap_pins():
    prompt = (TASK / "prompt.md").read_text()
    for secret in ["22p", "pF", "100n", "nF", "10k", "4.7k", "4k7", "3 mm", "CSB", "SDO",
                   "pull", "AVCC"]:
        assert secret not in prompt, secret


@pytest.mark.parametrize("value, ok", [("8MHz", True), ("8 MHz", True), ("8.000MHz", True),
                                       ("8M", True), ("16MHz", False), ("18MHz", False),
                                       ("80MHz", False), ("8kHz", False)])
def test_crystal_allowlist(value, ok):
    part = Footprint("Y1", value, "Crystal:Crystal_HC49-4H_Vertical", (0.0, 0.0), 0.0, (), 2)
    assert _clause("crystal").args["part"].matches(part) is ok


@pytest.mark.parametrize("value, ok", [("ATmega328P-AU", True), ("ATmega328P-MU", True),
                                       ("ATmega328-PU", True), ("ATmega32U4-AU", False),
                                       ("ATmega168-AU", False),
                                       ("ATmega328PB-AU", False)])
def test_mcu_allowlist(value, ok):
    part = Footprint("U1", value, "Package_QFP:TQFP-32_7x7mm_P0.8mm", (0.0, 0.0), 0.0, (), 32)
    assert _clause("mcu").args["part"].matches(part) is ok


@pytest.mark.parametrize("load, pullup", [("22p", "4.7k"), ("22pF", "4k7"), ("18pF", "10k"),
                                          ("12p", "2.2K"), ("27pF", "1k")])
def test_common_value_spellings_fall_in_range(load, pullup):
    xl, pu = _clause("xtal1_load").args, _clause("sda_pullup").args
    assert xl["min"] <= parse_value(load) <= xl["max"]
    assert pu["min"] <= parse_value(pullup) <= pu["max"]


def test_four_pad_crystal_passes():
    # Y1 with grounded case pads 2 and 4: three nets on one part.
    board = make(
        footprints=[fp("U1", "ATmega328P-AU", pad_count=32),
                    fp("Y1", "8MHz", pad_count=4), fp("C1", "22p"), fp("C2", "22p")],
        pads=[pad("U1", 7, "XT1", names=["XTAL1/PB6"]), pad("U1", 8, "XT2", names=["XTAL2/PB7"]),
              pad("U1", 18, "+3V3", names=["AVCC"]), pad("U1", 5, "GND", names=["GND"]),
              pad("Y1", 1, "XT1"), pad("Y1", 2, "GND"), pad("Y1", 3, "XT2"), pad("Y1", 4, "GND"),
              pad("C1", 1, "XT1"), pad("C1", 2, "GND"), pad("C2", 1, "XT2"), pad("C2", 2, "GND")])
    for clause_id in ["crystal", "crystal_xtal1", "crystal_xtal2", "xtal1_load", "xtal2_load"]:
        result = _run(clause_id, board)
        assert result.passed, (clause_id, result.detail)


def test_csb_on_a_ferrite_isolated_sensor_rail_passes():
    # CSB follows the sensor's own supply, which sits behind a ferrite.
    board = make(
        footprints=[fp("U1", "BME280", pad_count=8), fp("L1", "BLM18")],
        pads=[pad("U1", 2, "VDD_S", names=["CSB"]), pad("U1", 6, "VDD_S", names=["VDDIO"]),
              pad("U1", 8, "VDD_S", names=["VDD"]), pad("L1", 1, "+3V3"),
              pad("L1", 2, "VDD_S")])
    result = _run("csb_high", board)
    assert result.passed, result.detail


def test_qfn_pins_named_pc4_pc5_are_sda_and_scl():
    # ATmega328P-M carries only PC4 / PC5 on pads 27 / 28.
    board = make(
        footprints=[fp("U1", "ATmega328P-MU", pad_count=33), fp("U2", "BME280", pad_count=8),
                    fp("R1", "4.7k"), fp("R2", "4.7k")],
        pads=[pad("U1", 4, "+3V3", names=["VCC"]), pad("U1", 18, "+3V3", names=["AVCC"]),
              pad("U1", 27, "N_SDA", names=["PC4"]), pad("U1", 28, "N_SCL", names=["PC5"]),
              pad("U2", 2, "+3V3", names=["CSB"]), pad("U2", 3, "N_SDA", names=["SDI"]), pad("U2", 4, "N_SCL", names=["SCK"]),
              pad("R1", 1, "N_SDA"), pad("R1", 2, "+3V3"),
              pad("R2", 1, "N_SCL"), pad("R2", 2, "+3V3")])
    for clause_id in ["sda_pullup", "scl_pullup", "sda_wired", "scl_wired", "sda_scl_distinct"]:
        result = _run(clause_id, board)
        assert result.passed, (clause_id, result.detail)
