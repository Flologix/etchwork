"""The kicad-cli report summariser.

Fixtures are built to KiCad's own schemas -- `resources/schemas/drc.v1.json`
and `erc.v1.json` on the 9.0 branch -- not to a report this project has seen,
because no KiCad is installed in the image yet. That is stated in
app/kicad.py's docstring too, and it is the first thing to re-check when one
is: a schema is what a report is allowed to look like, not proof of what a
given version emits.
"""
import json
import pathlib

from circuit_mcp.kicad import main, summarise


def drc_report(violations, **extra) -> dict:
    return {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "source": "board.kicad_pcb",
        "date": "2026-09-02T10:00:00+0000",
        "kicad_version": "9.0.1",
        "coordinate_units": "mm",
        "violations": violations,
        "unconnected_items": [],
        "schematic_parity": [],
        **extra,
    }


def violation(kind="clearance", severity="error", n_items=1, **extra) -> dict:
    return {
        "type": kind,
        "description": f"{kind} problem",
        "severity": severity,
        "items": [
            {"uuid": f"0000-{i}", "description": f"Track {i}",
             "pos": {"x": 1.0 + i, "y": 2.0}}
            for i in range(n_items)
        ],
        "excluded": False,
        **extra,
    }


def test_a_clean_board_says_so_unambiguously(tmp_path):
    """The success case has to be readable as success. "No violations" is the
    sentence the agent repeats to the user, and anything hedged gets repeated
    hedged."""
    out = summarise(drc_report([]))
    assert "No violations" in out


def test_counts_come_before_examples(tmp_path):
    """The aggregation is most of the value: four hundred silk-clearance
    warnings are ONE fact, and a model reading them one at a time spends the
    whole window learning it."""
    out = summarise(drc_report(
        [violation("silk_edge_clearance", "warning") for _ in range(400)]
        + [violation("clearance", "error")]))
    assert "401 violation(s)" in out
    assert "1 error, 400 warning" in out
    assert "400  silk_edge_clearance (warning)" in out
    assert out.index("By type:") < out.index("example")


def test_errors_are_listed_before_warnings():
    """A model reading top-down fixes what it sees first. On a board that is
    warnings-until-you-scroll, that is the wrong thing."""
    out = summarise(drc_report(
        [violation("silk", "warning"), violation("clearance", "error")]))
    body = out[out.index("example"):]
    assert body.index("[error]") < body.index("[warning]")


def test_an_item_reports_where_it_is():
    """A violation with no location is one the agent cannot act on."""
    out = summarise(drc_report([violation("clearance", "error")]))
    assert "@ (1.0, 2.0) mm" in out


def test_the_units_come_from_the_report_not_a_guess():
    out = summarise(drc_report([violation()], coordinate_units="in"))
    assert "(1.0, 2.0) in" in out


def test_excluded_violations_are_counted_apart_not_dropped():
    """Somebody dismissed these in the design, so they are not problems -- but
    "0 violations" and "0 violations and 40 exclusions" are different states of
    a board, and only one of them is worth saying nothing about."""
    out = summarise(drc_report(
        [violation("clearance", "error", excluded=True)]))
    assert "No violations" in out
    assert "1 excluded" in out


def test_examples_are_one_per_type_not_the_first_n():
    """The version that printed the first N spent six of its eight slots
    reprinting the same silkscreen warning at the same coordinates -- which
    the "By type" block above it had already counted. Every example line has
    to carry something the counts do not."""
    out = summarise(drc_report(
        [violation("silk", "warning") for _ in range(12)]
        + [violation("clearance", "error")]))
    assert out.count("[warning] silk") == 1
    assert out.count("[error] clearance") == 1


def test_the_total_is_stated_even_when_every_type_was_shown():
    """One example per type means a COMPLETE example list is still not the
    complete violation list. Saying the total only when the list was cut is
    how a three-line summary of four hundred violations reads as four."""
    out = summarise(drc_report([violation("silk", "warning") for _ in range(400)]))
    assert "400 violation(s) in total" in out
    assert "still in the JSON file" in out


def test_unconnected_items_are_read_from_their_own_array():
    """A DRC report keeps these in a separate top-level array, so a summariser
    that only read `violations` would report a board with unrouted nets as
    clean -- the exact failure that would make this tool worth removing."""
    report = drc_report([])
    report["unconnected_items"] = [violation("unconnected", "error")]
    out = summarise(report)
    assert "1 violation(s)" in out
    assert "unconnected" in out


def test_an_erc_report_nests_its_violations_under_sheets():
    """The other shape. ERC has no top-level `violations` at all, so reading
    only the DRC shape reports every schematic as clean."""
    report = {
        "source": "sheet.kicad_sch",
        "date": "2026-09-02T10:00:00+0000",
        "kicad_version": "9.0.1",
        "coordinate_units": "mm",
        "sheets": [{"uuid_path": "/", "path": "/",
                    "violations": [violation("pin_not_connected", "error")]}],
    }
    out = summarise(report)
    assert "1 violation(s)" in out
    assert "pin_not_connected" in out


def test_main_reads_a_file_and_returns_zero(tmp_path, capsys):
    path = tmp_path / "drc.json"
    path.write_text(json.dumps(drc_report([violation()])))
    assert main(["kicad.py", str(path)]) == 0
    assert "clearance" in capsys.readouterr().out


def test_a_report_file_that_is_not_json_names_the_flag_that_fixes_it(
        tmp_path, capsys):
    """The likeliest cause is kicad-cli's default --format report having been
    used instead of --format json, and that is a one-flag correction. A raw
    JSONDecodeError does not suggest it."""
    path = tmp_path / "drc.rpt"
    path.write_text("** Drc report for board.kicad_pcb **\n")
    assert main(["kicad.py", str(path)]) == 2
    assert "--format json" in capsys.readouterr().err


def test_a_missing_file_exits_two_rather_than_raising(tmp_path, capsys):
    assert main(["kicad.py", str(tmp_path / "nope.json")]) == 2
    assert "could not read" in capsys.readouterr().err


def test_the_summariser_passes_no_verdict_of_its_own(tmp_path):
    """It reports; kicad-cli judges. A second thing computing pass/fail from
    the same data is a second thing that can disagree with the flag the loop
    actually checks -- so the exit code is about reading the file, and an
    error-laden report still exits 0."""
    path = tmp_path / "drc.json"
    path.write_text(json.dumps(drc_report(
        [violation("clearance", "error") for _ in range(5)])))
    assert main(["kicad.py", str(path)]) == 0


def test_it_imports_nothing_from_the_app_package():
    """Load-bearing, and invisible from inside a test run. The shell jail's
    read roots are the workspace and /repo, so /srv/app -- where the Dockerfile
    puts this package -- is NOT readable from a jailed command. The invocation
    that works is `python3 /repo/app/kicad.py`, and it only works while this
    module is standalone. An `from app import config` added later would break
    it in a way nothing but a container would notice."""
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "circuit_mcp" / "kicad.py").read_text()

    # The AST, not a substring search. The first version of this test grepped
    # for "from app" and failed on this module's own docstring, which explains
    # the rule -- a check that a comment about a rule can break is a check
    # that gets deleted the first time someone documents the rule properly.
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert not any(name == "app" or name.startswith("app.")
                   for name in imported), sorted(imported)


# --- against output from a real kicad-cli ------------------------------------
#
# Everything above builds its reports from KiCad's published schemas. These two
# are the output of `kicad-cli` 9.0.9 itself, captured 2026-09-03 from the
# `interf_u` demo in `kicad/kicad:9.0`:
#
#   kicad-cli pcb drc --format json --severity-all -o real-drc-interf_u.json \
#       /usr/share/kicad/demos/interf_u/interf_u.kicad_pcb
#   kicad-cli sch erc --format json --severity-all -o real-erc-interf_u.json \
#       /usr/share/kicad/demos/interf_u/interf_u.kicad_sch
#
# They are regression guards rather than a specification: they passed the first
# time they were run, which is the finding -- the schema-built fixtures had the
# shape right. What they pin is that a future KiCad's real output keeps it.

REAL = pathlib.Path(__file__).resolve().parent / "fixtures" / "kicad"


def _real(name):
    return json.loads((REAL / name).read_text())


def test_a_real_drc_report_summarises_within_the_tool_output_budget():
    """The whole reason this module exists: 15 KB of report, 8192 characters
    of MAX_TOOL_OUTPUT. Measured at 33 KB -> 381 characters on a bigger board."""
    text = summarise(_real("real-drc-interf_u.json"))
    assert len(text) < 8192
    assert "interf_u.kicad_pcb" in text
    assert "KiCad 9.0.9" in text


def test_a_real_drc_report_puts_its_errors_before_its_warnings():
    """interf_u carries 2 starved_thermal errors among 25 lib_footprint
    warnings -- the ordering that matters, from real output rather than a
    fixture written to want it."""
    text = summarise(_real("real-drc-interf_u.json"))
    assert "2 error" in text and "25 warning" in text
    assert text.index("starved_thermal") < text.index("lib_footprint_issues")


def test_a_real_drc_violation_reports_every_item_it_names():
    """starved_thermal names both the zone and the pad. A summary that showed
    one would be describing a different violation."""
    text = summarise(_real("real-drc-interf_u.json"))
    assert "Zone [GND]" in text and "pad 16 [GND] of U5" in text


def test_a_real_erc_report_is_read_through_its_sheets():
    """The ERC path nests violations under `sheets`, and until 2026-09-03 that
    was read from the schema and never from a real report. It is: real output
    carries sheets[].{path,uuid_path,violations}."""
    report = _real("real-erc-interf_u.json")
    assert "sheets" in report and "violations" not in report
    text = summarise(report)
    assert "3 violation(s)" in text
    assert "footprint_link_issues" in text


def test_a_real_report_carries_a_key_the_schema_fixtures_did_not():
    """`included_severities` is in real output and was not in the hand-built
    fixtures. It changes nothing here, and the point is that the summariser
    ignores keys it does not know rather than failing on them."""
    report = _real("real-drc-interf_u.json")
    assert "included_severities" in report
    assert summarise(report)


# --- starved thermal reliefs --------------------------------------------------
#
# fill_zones repairs these itself (tools.repair_thermals); starved_pads is the
# pure half that reads which pads a DRC report named.

from circuit_mcp.kicad import starved_pads


def _starved(pad_description, uuid="u-1", zone="Zone [GND] on F.Cu, priority 0"):
    return {"type": "starved_thermal", "severity": "error",
            "description": "Thermal relief connection to zone incomplete",
            "items": [{"description": zone, "uuid": "zone-uuid"},
                      {"description": pad_description, "uuid": uuid}]}


def test_starved_pads_names_the_pad_not_the_zone():
    report = {"violations": [_starved("PTH pad B1 [GND] of J1", "bf66")]}
    assert starved_pads(report, "GND") == [("bf66", "J1", "B1")]


def test_starved_pads_keeps_only_the_pour_net():
    report = {"violations": [_starved("PTH pad 2 [+5V] of J2", "a"),
                             _starved("PTH pad SH [GND] of J1", "b")]}
    assert starved_pads(report, "GND") == [("b", "J1", "SH")]


def test_starved_pads_ignores_other_violations_and_duplicates():
    clearance = {"type": "clearance", "severity": "error",
                 "items": [{"description": "PTH pad B1 [GND] of J1", "uuid": "x"}]}
    report = {"violations": [clearance, _starved("PTH pad B1 [GND] of J1", "u"),
                             _starved("PTH pad B1 [GND] of J1", "u")]}
    assert starved_pads(report, "GND") == [("u", "J1", "B1")]


def test_starved_pads_needs_a_uuid_to_act_on():
    report = {"violations": [_starved("PTH pad B1 [GND] of J1", None)]}
    assert starved_pads(report, "GND") == []


def test_starved_pads_matches_a_net_name_containing_brackets():
    """A bus member like `D[0]` puts a `]` inside the net's own brackets, so
    the net has to be matched greedily up to the LAST `] of`, not the first."""
    report = {"violations": [_starved("SMD pad 3 [D[0]] of U1", "u-3")]}
    assert starved_pads(report, "D[0]") == [("u-3", "U1", "3")]


def test_starved_pads_matches_a_pad_name_with_a_space():
    report = {"violations": [_starved("PTH pad A 1 [GND] of J1", "u-4")]}
    assert starved_pads(report, "GND") == [("u-4", "J1", "A 1")]


def test_starved_pads_on_a_real_report():
    # Both of the real report's starves: U5 pad 16 and U9 pad G2, on GND.
    found = starved_pads(_real("real-drc-interf_u.json"), "GND")
    assert [(ref, pad) for _, ref, pad in found] == [("U5", "16"), ("U9", "G2")]
