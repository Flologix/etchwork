"""Datasheet-parts design, phase 4b: lands computed from the package's lead
dimensions alone, by IPC-7351B at density level B (nominal), for a datasheet
that gives the package drawing but no recommended land pattern.

    Z = Lmin + 2*toe + sqrt(CL^2 + F^2 + P^2)           outer edge to outer edge
    G = Smax' - 2*heel - sqrt(CS^2 + F^2 + P^2)         inner edge to inner edge
    X = Wmin + 2*side + sqrt(CW^2 + F^2 + P^2)          land width

with S = L - 2T, CS = sqrt(CL^2 + 2*CT^2), Smax' = Smax - (Smax - Smin - CS)/2,
each rounded to 0.05 mm, and G widened to keep 0.2 mm from an exposed pad.

The goldens are KiCad's own footprints, which its generator built the same
way from the same lead dimensions (its size_definitions): reproducing them
pad for pad from the leads alone is the proof the formulas are right.
"""
import re

import pytest

from circuit_mcp import makepart, partspec
from test_families import _compare, _spec, kicad_pads

SOIC8 = {"family": "dual_row", "count": 8, "pitch_mm": 1.27, "leads": "gull_wing",
         "span_mm": [5.8, 6.2], "lead_length_mm": [0.4, 1.27], "lead_width_mm": [0.31, 0.51]}
# TSSOP-16: JEDEC MO-153 AB, as KiCad's size definition gives it -- one span, no tolerance.
TSSOP16 = {"family": "dual_row", "count": 16, "pitch_mm": 0.65, "leads": "gull_wing",
           "span_mm": 6.4, "lead_length_mm": [0.45, 0.75], "lead_width_mm": [0.17, 0.30]}
# LQFP-48 7x7: fine pitch (<= 0.625 mm), where the side goal turns negative.
LQFP48 = {"family": "quad_flat", "count": 48, "pitch_mm": 0.5, "leads": "gull_wing",
          "span_mm": 9.0, "lead_length_mm": [0.45, 0.75], "lead_width_mm": [0.17, 0.27]}
# QFN-16 3x3: no leads; the span is the body, the lead is the terminal.
QFN16 = {"family": "quad_flat", "count": 16, "pitch_mm": 0.5, "leads": "no_lead",
         "span_mm": [2.9, 3.1], "lead_length_mm": [0.3, 0.5], "lead_width_mm": [0.18, 0.30],
         "thermal_pad_mm": [1.7, 1.7]}


def test_soic8_from_its_leads_is_kicads_soic8(workspace):
    _compare(makepart.pads(_spec(workspace, SOIC8)),
             kicad_pads("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"))


def test_tssop16_from_its_leads_is_kicads_tssop16(workspace):
    _compare(makepart.pads(_spec(workspace, TSSOP16)),
             kicad_pads("Package_SO", "TSSOP-16_4.4x5mm_P0.65mm"))


def test_fine_pitch_lqfp48_from_its_leads_is_kicads_lqfp48(workspace):
    _compare(makepart.pads(_spec(workspace, LQFP48)),
             kicad_pads("Package_QFP", "LQFP-48_7x7mm_P0.5mm"))


def test_qfn16_from_its_terminals_is_kicads_qfn16(workspace):
    """G is widened to keep the pads 0.2 mm from the 1.7 mm exposed pad."""
    _compare(makepart.pads(_spec(workspace, QFN16)),
             kicad_pads("Package_DFN_QFN", "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm"))


def test_the_answer_says_the_lands_were_computed_and_how(workspace):
    _spec(workspace, SOIC8)
    out = makepart.make_part("parts/X.part.toml")
    assert out.startswith("MADE"), out
    assert "computed from the leads" in out and "IPC-7351B" in out, out
    assert re.search(r"Z 6\.9.*G 3.*X 0\.6", out), out


def test_a_rectangular_package_gets_each_axis_from_its_own_span(workspace):
    made = makepart.pads(_spec(workspace, {**LQFP48, "count": 40, "pins_lr": 12,
                                           "span_y_mm": 7.0}))
    side = next(p for p in made if p["number"] == "1")
    bottom = next(p for p in made if p["number"] == "13")
    assert side["x"] != pytest.approx(bottom["y"])      # different spans, different rows


@pytest.mark.parametrize("change, problem", [
    ({"row_spacing_mm": 5.4, "pad_mm": [1.5, 0.6]}, "not both"),
    ({"leads": "j_lead"}, "leads"),
    ({"span_mm": [6.2, 5.8]}, "span_mm"),
    ({"lead_width_mm": None}, "lead_width_mm"),
    ({"lead_length_mm": [3.0, 3.2]}, "lead_length_mm"),
])
def test_bad_lead_dimensions_are_named(workspace, change, problem):
    package = {**SOIC8, **change}
    package = {k: v for k, v in package.items() if v is not None}
    try:
        _spec(workspace, package)
    except AssertionError as error:
        assert problem in str(error), error
    else:
        pytest.fail(f"no problem named for {change}")
