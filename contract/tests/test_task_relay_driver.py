"""relay-driver: the task loads, withholds its numbers and the protection
it grades, and finds the terminal's and header's pins by number (design
2026-09-29-contract-tasks §4.3)."""
from pathlib import Path

import pytest

from contract import score
from contract.board import Footprint
from contract.match import parse_value

TASK = Path(__file__).resolve().parents[1] / "tasks" / "relay-driver"
IDS = ["mosfet", "relay", "coil_supply", "coil_drain", "source_gnd", "flyback",
       "gate_pulldown", "gate_series", "rails_distinct"]


def _clause(clause_id):
    return {c.id: c for c in score.load_task(TASK).clauses}[clause_id]


def test_loads_every_clause_with_no_errors():
    t = score.load_task(TASK)
    assert [c.id for c in t.clauses] == IDS
    assert [c.error for c in t.clauses] == [None] * len(IDS)


def test_prompt_withholds_the_numbers_and_the_protection():
    prompt = (TASK / "prompt.md").read_text().lower()
    for secret in ["diode", "flyback", "pull-down", "pulldown", "10k", "100k", "1m", "100r",
                   "1k", "1n4148", "resistor"]:
        assert secret not in prompt, secret


def test_connector_pins_are_found_by_number():
    assert _clause("flyback").args["cathode"].pins == {"1"}
    assert _clause("gate_pulldown").args["net_b"].pins == {"2"}
    assert _clause("gate_series").args["net_b"].pins == {"2"}


@pytest.mark.parametrize("value, ok", [
    ("AO3400A", True), ("IRLML2502TRPBF", True), ("IRLML6344TRPBF", True), ("SI2302CDS", True),
    ("2N7002", False), ("IRF540N", False), ("IRFZ44N", False), ("BS170", False),
    ("2N3904", False)])
def test_mosfet_allowlist(value, ok):
    part = Footprint("Q1", value, "Package_TO_SOT_SMD:SOT-23", (0.0, 0.0), 0.0, (), 3)
    assert _clause("mosfet").args["part"].matches(part) is ok


@pytest.mark.parametrize("pulldown, series", [("10k", "100"), ("100K", "100R"), ("1M", "220"),
                                              ("47k", "1k"), ("100k", "10R")])
def test_common_value_spellings_fall_in_range(pulldown, series):
    down, ser = _clause("gate_pulldown").args, _clause("gate_series").args
    assert down["min"] <= parse_value(pulldown) <= down["max"]
    assert ser["min"] <= parse_value(series) <= ser["max"]
