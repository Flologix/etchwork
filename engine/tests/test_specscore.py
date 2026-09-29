"""The part-spec evaluation (datasheet-parts design §9): a model-written spec
scored against a golden one.

The metric that matters is the silent error: a value that is wrong, or a
guess where the datasheet says nothing, and that the spec does NOT mark
unknown. A flagged doubt reaches a human (check_done lists it); a silent one
reaches the fab.
"""
import copy
import json
import subprocess
import sys
import tomllib

import pytest

from circuit_mcp import specscore
from conftest import FIXTURES

ODU = tomllib.loads((FIXTURES / "parts" / "GK1YAR-P16UC00-000L.part.toml").read_text())
SOIC_LAND = {
    "mpn": "X", "manufacturer": "Y", "datasheet": "d.pdf", "library": "L", "pins_source": "p.1",
    "package": {"family": "dual_row", "view": "top", "source": "p.9", "count": 8,
                "pitch_mm": 1.27, "row_spacing_mm": 4.95, "pad_mm": [1.95, 0.6]},
    "pins": [{"number": str(n), "name": f"P{n}", "type": "passive"} for n in range(1, 9)],
}


def _verdicts(report):
    return {f["fact"]: f["verdict"] for f in report["facts"]}


def test_a_spec_scored_against_itself_is_all_right():
    report = specscore.score(ODU, ODU)
    assert report["pass"] and report["silent"] == 0
    verdicts = _verdicts(report)
    assert verdicts["geometry"] == "right" and verdicts["view"] == "right"
    assert verdicts["numbering"] == "both_unknown"      # the golden does not know it either
    assert verdicts["pin 17"] == "right"


def test_the_same_footprint_described_by_its_leads_scores_right():
    """A land pattern and the leads it comes from are the same answer."""
    leads = copy.deepcopy(SOIC_LAND)
    for key in ("row_spacing_mm", "pad_mm"):
        leads["package"].pop(key)
    leads["package"].update(leads="gull_wing", span_mm=[5.8, 6.2], lead_length_mm=[0.4, 1.27],
                            lead_width_mm=[0.31, 0.51])
    report = specscore.score(leads, SOIC_LAND)
    assert report["pass"] and _verdicts(report)["geometry"] == "right", report


def test_a_dimension_off_by_less_than_the_tolerance_is_right():
    """0.02 mm of pitch moves an 8-pin SOIC's outer pads 0.03 mm: harmless."""
    close = copy.deepcopy(SOIC_LAND)
    close["package"]["pitch_mm"] = 1.25
    assert _verdicts(specscore.score(close, SOIC_LAND))["geometry"] == "right"


def test_a_wrong_dimension_nobody_flagged_is_a_silent_error():
    wrong = copy.deepcopy(SOIC_LAND)
    wrong["package"]["pitch_mm"] = 1.0     # misread: 1.27 mm
    report = specscore.score(wrong, SOIC_LAND)
    assert _verdicts(report)["geometry"] == "wrong"
    assert report["silent"] >= 1 and not report["pass"]


def test_the_same_wrong_dimension_flagged_is_not_silent():
    wrong = copy.deepcopy(SOIC_LAND)
    wrong["package"].update(pitch_mm=1.0, unknown=True, note="pitch unreadable on p.9")
    report = specscore.score(wrong, SOIC_LAND)
    assert _verdicts(report)["geometry"] == "flagged"
    assert report["silent"] == 0 and report["pass"]


def test_guessing_what_the_datasheet_does_not_state_is_a_silent_error():
    """The live ODU run's failure mode: a pin numbering the drawing did not
    show, filled in and left unmarked."""
    guess = copy.deepcopy(ODU)
    guess.pop("numbering")
    report = specscore.score(guess, ODU)
    assert _verdicts(report)["numbering"] == "guessed"
    assert not report["pass"]


def test_a_doubt_the_golden_does_not_share_is_counted_but_not_silent():
    cautious = copy.deepcopy(SOIC_LAND)
    cautious["numbering"] = {"unknown": True, "note": "not sure pin 1 is top left"}
    report = specscore.score(cautious, SOIC_LAND)
    assert _verdicts(report)["numbering"] == "needless"
    assert report["pass"]


def test_a_mirrored_view_is_caught():
    """Top and bottom views are mirror images: the classic footprint error."""
    mirrored = copy.deepcopy(ODU)
    mirrored["package"]["view"] = "top"
    report = specscore.score(mirrored, ODU)
    assert _verdicts(report)["view"] == "wrong"
    assert not report["pass"]


def test_each_pin_is_its_own_fact():
    wrong = copy.deepcopy(SOIC_LAND)
    wrong["pins"][3] = {"number": "4", "name": "GND", "type": "power_in"}
    report = specscore.score(wrong, SOIC_LAND)
    verdicts = _verdicts(report)
    assert verdicts["pin 4"] == "wrong" and verdicts["pin 3"] == "right"
    detail = next(f["detail"] for f in report["facts"] if f["fact"] == "pin 4")
    assert "GND" in detail and "P4" in detail


def test_a_spec_the_engine_would_refuse_is_invalid_not_scored():
    broken = copy.deepcopy(SOIC_LAND)
    broken["package"].pop("view")
    report = specscore.score(broken, SOIC_LAND)
    assert report["valid"] is False and not report["pass"]
    assert any("view" in p for p in report["problems"])


def test_the_counts_add_up():
    report = specscore.score(ODU, ODU)
    assert sum(report["counts"].values()) == len(report["facts"])


def test_the_command_line_prints_a_report_and_exits_by_the_verdict(tmp_path):
    good, bad = tmp_path / "good.toml", tmp_path / "bad.toml"
    golden = FIXTURES / "parts" / "GK1YAR-P16UC00-000L.part.toml"
    good.write_text(golden.read_text())
    bad.write_text(golden.read_text().replace('view = "bottom"', 'view = "top"'))
    ok = subprocess.run([sys.executable, "-m", "circuit_mcp.specscore", str(good), str(golden)],
                        capture_output=True, text=True)
    assert ok.returncode == 0 and "PASS" in ok.stdout, ok.stdout + ok.stderr
    failing = subprocess.run([sys.executable, "-m", "circuit_mcp.specscore", str(bad), str(golden),
                              "--json"], capture_output=True, text=True)
    assert failing.returncode == 1
    assert json.loads(failing.stdout)["pass"] is False
