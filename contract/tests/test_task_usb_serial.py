"""usb-serial: the task loads, withholds its numbers and pin names, and its
net specs find the header's pins by number (design 2026-09-29-contract-tasks
§4.2)."""
from pathlib import Path

import pytest

from contract import score
from contract.board import Footprint
from contract.checkers import REGISTRY
from contract.match import parse_value
from contract.tests.boards import fp, make, pad

TASK = Path(__file__).resolve().parents[1] / "tasks" / "usb-serial"
IDS = ["bridge", "cc1_rd", "cc2_rd", "cc_separate", "dp", "dm", "v3_cap", "vcc_cap",
       "tx_out", "rx_in", "dtr_reset"]


def _clause(clause_id):
    return {c.id: c for c in score.load_task(TASK).clauses}[clause_id]


def _run(clause_id, board):
    c = _clause(clause_id)
    return REGISTRY[c.checker](board, **c.args)


def test_loads_every_clause_with_no_errors():
    t = score.load_task(TASK)
    assert [c.id for c in t.clauses] == IDS
    assert [c.error for c in t.clauses] == [None] * len(IDS)


def test_prompt_withholds_the_numbers_and_pin_names():
    prompt = (TASK / "prompt.md").read_text()
    for secret in ["5.1", "5k1", "100n", "10n", "220n", "nF", "3 mm", "TXD", "RXD", "DTR",
                   "V3", "UD+", "CC1"]:
        assert secret not in prompt, secret


def test_header_pins_are_found_by_number():
    assert _clause("tx_out").args["net_b"].pins == {"4"}
    assert _clause("rx_in").args["net_b"].pins == {"5"}
    assert _clause("dtr_reset").args["net_b"].pins == {"6"}


@pytest.mark.parametrize("value, ok", [("CH340C", True), ("CH340K", True), ("CH340E", False),
                                       ("CH340N", False), ("CH340G", False), ("CP2102N", False),
                                       ("FT232RL", False)])
def test_bridge_allowlist(value, ok):
    part = Footprint("U1", value, "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm", (0.0, 0.0), 0.0, (), 16)
    assert _clause("bridge").args["part"].matches(part) is ok


@pytest.mark.parametrize("spelling", ["100n", "100nF", "0.1uF", "0.1u", "100n/50V", "47nF"])
def test_common_value_spellings_fall_in_range(spelling):
    args = _clause("dtr_reset").args
    assert args["min"] <= parse_value(spelling) <= args["max"]
    assert parse_value(spelling) >= _clause("v3_cap").args["min_farads"]


def test_series_resistor_on_tx_is_a_known_failure():
    # A 1k in series between TXD and header pin 4 is valid practice, but
    # tx_out asks for TXD to reach pin 4 directly: a documented limit
    # (provenance.md, Known limits), pinned so changing it is deliberate.
    board = make(
        footprints=[fp("U1", "CH340C", pad_count=16), fp("R5", "1k"), fp("J2", pad_count=6)],
        pads=[pad("U1", 2, "N_TXD", names=["TXD"]), pad("U1", 5, "D+", names=["UD+"]),
              pad("R5", 1, "N_TXD"), pad("R5", 2, "N_TX"),
              pad("J2", 4, "N_TX", names=["Pin_4"])])
    assert not _run("tx_out", board).passed
