"""usbc-ldo: the task loads, withholds its numbers, and its allowlists and
net specs find what real boards carry (design 2026-09-29-contract-tasks §4.1)."""
from pathlib import Path

import pytest

from contract import score
from contract.board import Footprint
from contract.checkers import REGISTRY
from contract.match import parse_value
from contract.tests.boards import fp, make, pad

TASK = Path(__file__).resolve().parents[1] / "tasks" / "usbc-ldo"
IDS = ["usb_conn", "ldo_rating", "cc1_rd", "cc2_rd", "cc_separate", "vbus_to_ldo",
       "ldo_out", "cin", "cout", "rails_distinct"]


def _clause(clause_id):
    return {c.id: c for c in score.load_task(TASK).clauses}[clause_id]


def _run(clause_id, board):
    c = _clause(clause_id)
    return REGISTRY[c.checker](board, **c.args)


def test_loads_every_clause_with_no_errors():
    t = score.load_task(TASK)
    assert [c.id for c in t.clauses] == IDS
    assert [c.error for c in t.clauses] == [None] * len(IDS)


def test_prompt_withholds_the_numbers():
    prompt = (TASK / "prompt.md").read_text()
    for secret in ["5.1", "5k1", "4.1k", "6.1k", "1u", "uF", "µF", "3 mm", "CC1", "CC2", "Rd"]:
        assert secret not in prompt, secret


def _part(value, lib_id="Package_TO_SOT_SMD:SOT-23-5"):
    return Footprint("U1", value, lib_id, (0.0, 0.0), 0.0, (), 5)


@pytest.mark.parametrize("value", ["AP2112K-3.3", "AMS1117-3.3", "LD1117S33TR", "XC6220B331MR",
                                   "TLV75733PDBVR", "AP7361C-33E", "NCP1117ST33T3G",
                                   "LM1117MPX-3.3"])
def test_ldo_allowlist_accepts_regulators_rated_500ma_or_more(value):
    assert _clause("ldo_rating").args["part"].matches(_part(value))


@pytest.mark.parametrize("value", ["MCP1700-3302E", "HT7333-A", "XC6206P332MR", "LP2985-33",
                                   "MCP1702-3302E"])
def test_ldo_allowlist_rejects_regulators_rated_under_500ma(value):
    assert not _clause("ldo_rating").args["part"].matches(_part(value))


@pytest.mark.parametrize("lib_id, ok", [
    ("Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", True),
    ("Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", True),
    ("Connector_USB:USB_Micro-B_Molex-105017-0001", False),
    ("Connector_USB:USB_A_Molex_67643_Horizontal", False),
])
def test_usb_conn_allowlist(lib_id, ok):
    assert _clause("usb_conn").args["part"].matches(_part("USB_C", lib_id)) is ok


@pytest.mark.parametrize("spelling", ["5.1k", "5k1", "5K1", "5.1K", "5100"])
def test_common_value_spellings_fall_in_range(spelling):
    args = _clause("cc1_rd").args
    assert args["min"] <= parse_value(spelling) <= args["max"]
    for cap in ["1u", "1uF", "1µF", "1uF/16V", "10u", "2.2uF"]:
        assert parse_value(cap) >= _clause("cin").args["min_farads"], cap


def _ldo_board(vin="VIN", vout="VOUT"):
    """J1 USB-C VBUS -> U1 regulator (pin names vin/vout) -> J2, 1u caps by each pin."""
    return make(
        footprints=[fp("J1", "USB_C", pad_count=16), fp("U1", "LDO", pad_count=3),
                    fp("C1", "1u"), fp("C2", "1u"), fp("J2", pad_count=2)],
        pads=[pad("J1", "A4", "VBUS", names=["VBUS"]), pad("J1", "A1", "GND", names=["GND"]),
              pad("J1", "A5", "CC1", names=["CC1"]),
              pad("U1", 3, "VBUS", pos=(0.0, 0.0), names=[vin]),
              pad("U1", 1, "GND", names=["GND"]),
              pad("U1", 2, "+3V3", pos=(5.0, 0.0), names=[vout]),
              pad("C1", 1, "VBUS", pos=(-1.2, 0.0)), pad("C1", 2, "GND"),
              pad("C2", 1, "+3V3", pos=(6.2, 0.0)), pad("C2", 2, "GND"),
              pad("J2", 1, "+3V3", names=["Pin_1"]), pad("J2", 2, "GND", names=["Pin_2"])])


@pytest.mark.parametrize("vin, vout", [("VIN", "VOUT"), ("VI", "VO"), ("IN", "OUT")])
def test_regulator_pin_spellings_are_found(vin, vout):
    board = _ldo_board(vin, vout)
    for clause_id in ["vbus_to_ldo", "ldo_out", "cin", "cout", "rails_distinct"]:
        result = _run(clause_id, board)
        assert result.passed, (clause_id, result.detail)
