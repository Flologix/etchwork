"""The fab profile, pure: every number sourced, floors only, the custom rules.

Spec: docs/superpowers/specs/2026-09-24-circuit-fab-profile-design.md §3-4.
"""
from circuit_mcp import fab, tools

SOURCES = {"capabilities", "charges", "derived", "community"}


def test_every_profile_value_says_where_it_came_from():
    profile = fab.profiles()[fab.PROFILE]
    keys = [k for k, v in profile.items() if isinstance(v, dict)]
    assert len(keys) == 12
    for key in keys:
        entry = profile[key]
        assert entry["value"] > 0, key
        assert entry["source"] in SOURCES, key
        assert entry["quote"], key


def test_the_value_jlcpcb_does_not_publish_is_marked_community():
    assert fab.profiles()[fab.PROFILE]["npth_hole_clearance_mm"]["source"] == "community"


def test_floors_are_rules_step_knobs_and_never_a_net_class_setting():
    floors = fab.floors()
    assert set(floors) <= set(tools.RULES)
    # via_drill_mm sets the Default class's via drill; the fab's drill floor
    # is through_hole_drill_mm.
    assert "via_drill_mm" not in floors
    assert floors["through_hole_drill_mm"] == 0.30
    assert floors["edge_clearance_mm"] == 0.20


def test_custom_rules_carry_the_profiles_hole_rules():
    text = fab.custom_rules()
    assert text.startswith("(version 1)")
    assert '(rule "PTH hole to track"' in text and "(min 0.28mm)" in text
    assert '(rule "NPTH hole to track"' in text and "(min 0.254mm)" in text
    assert '(rule "NPTH hole as edge"' in text
    # KiCad issue #15867: custom rules evaluate && / || strictly left-to-right,
    # not with usual precedence; make condition explicit with parentheses.
    assert '(condition "(A.Type == \'Pad\' && !A.isPlated()) || (B.Type == \'Pad\' && !B.isPlated())"' in text
    assert "(constraint edge_clearance (min 0.254mm))" in text
    assert "(constraint hole_to_hole (min 0.45mm))" in text
    assert "(constraint hole_size (min 0.5mm))" in text


def test_an_agents_hole_clearance_is_its_fabs_hole_rule_everywhere():
    text = fab.custom_rules({"hole_clearance_mm": 0.3, "edge_clearance_mm": 0.4})
    assert "(constraint hole_clearance (min 0.3mm))" in text
    assert "(constraint edge_clearance (min 0.3mm))" in text  # NPTH as edge
    assert "0.254" not in text and "0.28mm" not in text


def test_an_agents_hole_to_hole_replaces_the_pth_pad_hole_to_hole_value():
    text = fab.custom_rules({"hole_to_hole_mm": 0.3})
    assert "(constraint hole_to_hole (min 0.3mm))" in text
    assert "0.45mm" not in text


def test_an_agents_through_hole_drill_replaces_the_npth_minimum_drill_value():
    text = fab.custom_rules({"through_hole_drill_mm": 0.25})
    assert "(constraint hole_size (min 0.25mm))" in text
    assert "0.5mm" not in text
