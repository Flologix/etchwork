"""circuit_mcp.netclass: the Rust classifier's deterministic parts, ported
(engine-portability spec §4.1, §4.2), checked against goldens exported from
the Rust before it is removed (§6.1)."""
import json

import pytest

from circuit_mcp import netclass
from conftest import FIXTURES

CLS = FIXTURES / "classify"
NET = (CLS / "cnc_xy_controller.net").read_text()
LABELS = netclass.load_labels((CLS / "cnc_xy_controller.labels.toml").read_text())

# A real pipeline netlist with power_in/power_out pins (4 POWER-IN, 1
# POWER-OUT), used below where a power-pin net is needed -- see the note on
# test_the_power_floor_refuses_a_power_pin_net_picked_as_signal.
BACKOFF1 = (FIXTURES / "projects" / "backoff1" / "board.net").read_text()


def golden(name):
    return json.loads((CLS / name).read_text())


def test_the_fixture_has_53_nets():
    assert len(netclass.read(NET).nets) == 53


def test_escaped_quotes_in_the_netlist_are_unescaped():
    # sexpr.rs's own string reader is escape-aware (see its
    # keeps_spaces_and_escapes_inside_strings test): `\"` inside a quoted
    # string is a literal quote, not the string's end. A component value of
    # `1\"` (an inch mark) and a description containing `\"quoted\"` must
    # both come out unescaped, and evidence must show the unescaped text.
    text = (
        '(export\n'
        '  (components\n'
        '    (comp\n'
        '      (ref "C1")\n'
        '      (value "1\\"")\n'
        '      (description "a \\"quoted\\" value")\n'
        '      (footprint "Test:FP")))\n'
        '  (nets\n'
        '    (net\n'
        '      (code 1)\n'
        '      (name "N1")\n'
        '      (node\n'
        '        (ref "C1")\n'
        '        (pin "1")\n'
        '        (pintype "PASSIVE")))))\n'
    )
    nl = netclass.read(text)
    c1 = nl.components["C1"]
    assert c1.value == '1"'
    assert c1.description == 'a "quoted" value'
    ev = netclass.evidence(nl, nl.nets[0])
    assert '1"' in ev
    assert 'a "quoted" value' in ev


def test_evidence_matches_the_rust_byte_for_byte():
    nl = netclass.read(NET)
    assert {n.name: netclass.evidence(nl, n) for n in nl.nets} == golden("golden_evidence.json")


def test_rules_match_the_rust():
    nl = netclass.read(NET)
    got = {n.name: (list(r) if (r := netclass.rule(nl, n)) else None) for n in nl.nets}
    assert got == golden("golden_rules.json")


def test_the_writer_matches_the_rust_apart_from_source():
    nl = netclass.read(NET)
    decided = {n.name for n in nl.nets if netclass.rule(nl, n)}
    picks = [{"net": net, "class": c, "tier": t, "reason": "label"}
             for net, (c, t) in LABELS.items()
             if net in {n.name for n in nl.nets} and net not in decided]
    # The golden sized every labelled net from its label. The Python writer sizes rule
    # nets from the rule (no tier), so parity is checked on the picked nets only; `source`
    # differs by design ("model" vs the golden's "rule").
    got = netclass.write_netclasses(nl, picks)
    want = golden("golden_netclasses_from_labels.json")
    compared = 0
    for net, entry in want["nets"].items():
        if net in decided:
            continue
        mine, theirs = dict(got["nets"][net]), dict(entry)
        mine.pop("source"), theirs.pop("source")
        assert mine == theirs, net
        compared += 1
    assert compared == len(picks) and got["version"] == 1
    assert {r["net"] for r in got["needs_review"]} - decided == {r["net"] for r in want["needs_review"]} - decided


def test_rule_nets_are_written_with_their_rule_class_no_tier_and_source_rule():
    # test_the_writer_matches_the_rust_apart_from_source skips every rule-decided
    # net (`if net in decided: continue`), so it never actually checks this branch
    # of write_netclasses -- only the picked-net branch. Cover it directly.
    nl = netclass.read(NET)
    decided = {n.name: netclass.rule(nl, n) for n in nl.nets if netclass.rule(nl, n)}
    assert decided.keys() >= {"GND", "XTAL1", "XTAL2"}
    picks = [{"net": net, "class": c, "tier": t, "reason": "label"}
             for net, (c, t) in LABELS.items()
             if net in {n.name for n in nl.nets} and net not in decided]
    out = netclass.write_netclasses(nl, picks)
    for net, (class_id, _rule_name) in decided.items():
        entry = out["nets"][net]
        assert entry["class"] == class_id, net
        assert entry["tier"] is None, net
        assert entry["source"] == "rule", net
        assert entry["status"] == "accepted", net
        assert entry["netclass"] == class_id, net
    for pick in picks:
        assert out["nets"][pick["net"]]["source"] == "model", pick["net"]


def test_the_menu_is_the_closed_set():
    assert list(netclass.MENU["classes"]) == ["ground", "power_high_current", "power_logic", "crystal",
                                              "rf_50ohm", "diff_pair", "analog_sensitive", "signal"]
    assert list(netclass.MENU["tiers"]) == ["lt_0_5a", "0_5a_to_2a", "2a_to_5a", "gt_5a"]
    assert all(netclass.MENU["classes"].values()) and all(netclass.MENU["tiers"].values())


def _open(nl):
    return [n.name for n in nl.nets if not netclass.rule(nl, n)]


def _label_picks(nl):
    return [{"net": n, "class": LABELS[n][0], "tier": LABELS[n][1], "reason": "label"} for n in _open(nl)]


def test_label_picks_validate_clean():
    nl = netclass.read(NET)
    assert netclass.validate(nl, _open(nl), _label_picks(nl)) == []


def test_completeness_is_enforced():
    nl = netclass.read(NET)
    picks = _label_picks(nl)
    missing = picks[1:]
    extra = picks + [{"net": "NOPE", "class": "signal", "tier": "lt_0_5a", "reason": "x"}]
    dup = picks + [picks[0]]
    assert any("missing" in p for p in netclass.validate(nl, _open(nl), missing))
    assert any("unknown" in p or "not an open net" in p for p in netclass.validate(nl, _open(nl), extra))
    assert any("more than once" in p for p in netclass.validate(nl, _open(nl), dup))


def test_menu_and_reason_are_enforced():
    nl = netclass.read(NET)
    picks = _label_picks(nl)
    picks[0] = {**picks[0], "class": "bogus"}
    picks[1] = {**picks[1], "tier": "huge"}
    picks[2] = {**picks[2], "reason": ""}
    problems = netclass.validate(nl, _open(nl), picks)
    assert len(problems) >= 3


def test_signal_with_a_high_tier_is_inconsistent():
    nl = netclass.read(NET)
    picks = _label_picks(nl)
    signal_net = next(p for p in picks if p["class"] == "signal")
    signal_net.update(tier="0_5a_to_2a")
    assert any("inconsistent" in p for p in netclass.validate(nl, _open(nl), picks))


def _backoff1_picks(nl):
    """One pick per backoff1 open net: `ground`/`2a_to_5a` for any net the
    power floor flags, `signal`/`lt_0_5a` for the rest. backoff1 has no
    labels file, so this is a hand-built baseline rather than truth."""
    picks = []
    for n in nl.nets:
        if netclass.rule(nl, n):
            continue
        if netclass.power_floor(n):
            picks.append({"net": n.name, "class": "ground", "tier": "2a_to_5a", "reason": "power pin"})
        else:
            picks.append({"net": n.name, "class": "signal", "tier": "lt_0_5a", "reason": "signal"})
    return picks


def test_the_power_floor_refuses_a_power_pin_net_picked_as_signal():
    # cnc_xy_controller.net's own nets ("+5V", "VIN_PROT", "VMOT") already
    # carry power_in/power_out pins and are open (not rule-decided), so the
    # CNC fixture does not exercise a "no power pin nets here" gap -- see
    # test_the_cnc_fixture_carries_power_pin_nets below, and the task report
    # for the fixture-inspection evidence. backoff1 is used here instead
    # because it is a second, independent real pipeline netlist with power
    # pins (4 POWER-IN, 1 POWER-OUT) and no labels file to fight with.
    nl = netclass.read(BACKOFF1)
    open_nets = _open(nl)
    power_net = next(n for n in nl.nets if not netclass.rule(nl, n) and netclass.power_floor(n))
    picks = _backoff1_picks(nl)
    for p in picks:
        if p["net"] == power_net.name:
            p.update({"class": "signal", "tier": "lt_0_5a"})
    problems = netclass.validate(nl, open_nets, picks)
    assert any(power_net.name in p and "power" in p for p in problems)
    for p in picks:
        if p["net"] == power_net.name:
            p["review"] = True
    assert netclass.validate(nl, open_nets, picks) == []
    out = netclass.write_netclasses(nl, picks)
    entry = out["nets"][power_net.name]
    assert entry["class"] in ("power_logic", "power_high_current", "ground")
    assert entry["status"] == "needs_review"
    assert any(r["net"] == power_net.name for r in out["needs_review"])


def test_the_power_floor_raises_the_tier_not_just_the_class():
    # I1: the floor used to raise only the class (to power_logic), leaving the
    # tier -- and therefore the copper width -- exactly what the model picked
    # (lt_0_5a, 0.25 mm, identical to signal|lt_0_5a). A review pick that under-
    # rates a power-pin net as signal/lt_0_5a must come out sized as
    # power_logic at least 0_5a_to_2a, matching the data file's own width for
    # that class/tier -- not silently left at the signal-equivalent width.
    nl = netclass.read(BACKOFF1)
    open_nets = _open(nl)
    power_net = next(n for n in nl.nets if not netclass.rule(nl, n) and netclass.power_floor(n))
    picks = _backoff1_picks(nl)
    for p in picks:
        if p["net"] == power_net.name:
            p.update({"class": "signal", "tier": "lt_0_5a", "review": True})
    out = netclass.write_netclasses(nl, picks)
    entry = out["nets"][power_net.name]
    assert entry["class"] == "power_logic"
    assert entry["tier"] == "0_5a_to_2a"
    assert entry["width_mm"] == netclass._physical("power_logic", "0_5a_to_2a")["width_mm"]
    reason = next(r["reason"] for r in out["needs_review"] if r["net"] == power_net.name)
    assert "power floor: " in reason and "power_logic 0_5a_to_2a" in reason


def test_the_power_floor_leaves_an_already_power_pick_alone():
    # A pick that already names a power class (power_logic/lt_0_5a is a small
    # logic rail, a legitimate residual risk) is accepted unchanged when it
    # carries no review flag: the floor only refuses non-power picks.
    nl = netclass.read(NET)
    picks = _label_picks(nl)
    net_name = "+5V"  # open, carries a power_in/power_out pin (see
    # test_the_cnc_fixture_carries_power_pin_nets below).
    for p in picks:
        if p["net"] == net_name:
            p.update({"class": "power_logic", "tier": "lt_0_5a", "reason": "logic rail"})
            p.pop("review", None)
    assert netclass.validate(nl, _open(nl), picks) == []
    out = netclass.write_netclasses(nl, picks)
    entry = out["nets"][net_name]
    assert entry["class"] == "power_logic"
    assert entry["tier"] == "lt_0_5a"
    assert entry["status"] == "accepted"


def test_the_cnc_fixture_carries_power_pin_nets():
    # Ledgered finding, corrected: cnc_xy_controller.net was expected to have
    # no power_in/power_out pin types at all, which would have made the
    # brief's original power-floor test find no power net there. Inspecting
    # the fixture (and the Rust's own golden_evidence.json, which shows
    # "pin 3 power-out" / "pin N power-in" lines on "+5V", "GND", "VIN_PROT"
    # and "VMOT") shows the opposite: those nets carry POWER-IN/POWER-OUT
    # pins, and three of the four ("+5V", "VIN_PROT", "VMOT") are open (not
    # rule-decided) -- power_floor is not None for them. This test pins that
    # down instead, so the fact stays checked rather than silently assumed.
    nl = netclass.read(NET)
    with_power_pins = {n.name for n in nl.nets if netclass.power_floor(n) is not None}
    assert with_power_pins == {"+5V", "GND", "VIN_PROT", "VMOT"}
    open_with_power_pins = {n.name for n in nl.nets if not netclass.rule(nl, n) and netclass.power_floor(n)}
    # GND alone is rule-decided (ground-name); the other three are open.
    assert open_with_power_pins == {"+5V", "VIN_PROT", "VMOT"}


def test_validate_reports_malformed_picks_without_crashing():
    # m-d: validate used to call pick.get(...) unconditionally, which raises
    # AttributeError on a non-dict pick, and never checked that class/tier/
    # reason were even strings before using them -- a malformed pick from a
    # model that returns the wrong shape must produce a problem line, never
    # an exception.
    nl = netclass.read(NET)
    open_nets = _open(nl)
    picks = [
        "not a dict",
        {"net": open_nets[0], "class": 123, "tier": "lt_0_5a", "reason": "x"},
    ]
    problems = netclass.validate(nl, open_nets, picks)
    assert any(p.startswith("0:") and "pick must be an object" in p for p in problems)
    assert any(p.startswith("1:") and "pick must be an object" in p for p in problems)


def test_validate_caps_problems_at_100_with_a_tail_summary():
    nl = netclass.read(NET)
    open_nets = _open(nl)
    picks = ["bad"] * 150
    problems = netclass.validate(nl, open_nets, picks)
    assert len(problems) == 101
    assert problems[-1] == "… and 100 more"


def test_scoring_counts_under_rating_as_dangerous():
    nl = netclass.read(NET)
    picks = _label_picks(nl)
    assert netclass.score(netclass.write_netclasses(nl, picks), LABELS)["dangerous"] == []
    hi = next(p for p in picks if p["class"] == "power_high_current")
    hi.update({"class": "power_logic"})
    report = netclass.score(netclass.write_netclasses(nl, picks), LABELS)
    assert any(hi["net"] in d for d in report["dangerous"])
