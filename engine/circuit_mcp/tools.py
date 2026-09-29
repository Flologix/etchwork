"""Run KiCad's own pipeline on a board, and summarise what comes back.

**What it returns is the summary, not the report.** A real DRC report is tens
of kilobytes against a MAX_TOOL_OUTPUT of 8192 -- measured at 33,458
characters for one demo board, summarising to 381. circuit_mcp.kicad does
that work; this hands it the file and prints the result.

**The verdict comes from the exit code, not from the summary.** kicad-cli's
--exit-code-violations is what says pass or fail; circuit_mcp.kicad
deliberately passes no verdict of its own, so that two things cannot disagree
about the same report. This joins them: the exit code decides PASS/FAIL, the
summariser says what was found, and the JSON stays on disk for a closer look.
"""
import hashlib
import json
import pathlib
import subprocess

from circuit_mcp import kicad, kicad_lib, netclass, parts, paths, service
from circuit_mcp import rules as rules_mod
from circuit_mcp.parts import describe_board

# What kicad-cli's --exit-code-violations means. Anything else is the tool
# itself failing rather than the board.
_CLEAN, _VIOLATIONS = 0, 5


def _verdict(report: dict) -> tuple[str, str | None]:
    """`(PASS|FAIL, both counts)` read from the REPORT, not the exit code.

    kicad-cli's --exit-code-violations answers 5 for any violation at all,
    warnings included, and `lib_footprint_issues` is unavoidable whenever the
    footprint library table is not configured -- which it is not, in a
    container. Treating exit 5 as failure therefore fails every correct board.
    Measured: one ladder rep read that FAIL, believed it, and spent the rest of
    its turn fixing a board that was already right.

    So severity decides, and unconnected items decide separately: a board that
    was placed and never routed carries no error-severity violation, because
    KiCad reports unrouted nets in their own array.

    **Both report shapes, because they are not the same shape.** A DRC report
    (a board) carries a top-level `violations`. An ERC report (a schematic)
    carries none at all: they sit under `sheets[].violations`, one array per
    sheet. This function was written against the board and read `violations`
    only, so EVERY ERC check returned PASS whatever the schematic said.

    Measured 2026-09-11 on a live session: `PASS (ERC, kicad-cli exit 5)`
    printed directly above two errors, one of them "Both RESET and GND are
    attached to the same items". A verdict that contradicts the report
    underneath it is worse than no verdict -- the model had no way to know it
    was finished, so it did not stop, and spent forty more steps writing a
    post-processor to delete power symbols out of generated KiCad files.
    """
    # Both counts, always, and NOT DONE in the verdict itself: release check 1
    # ended with a model reporting success over 21 DRC errors.
    errors, unrouted = _counts(report)
    if errors or unrouted:
        kinds = sorted({v.get("type") or "?" for v in errors})
        listed = f" ({', '.join(kinds)})" if kinds else ""
        return "FAIL", (f"NOT DONE: {len(errors)} DRC error(s){listed}, "
                        f"{len(unrouted)} unconnected")
    return "PASS", "DRC clean, 0 unconnected"


def _counts(report: dict) -> tuple[list, list]:
    """`(error-severity violations, unconnected items)` in a report.

    Through circuit_mcp.kicad, which is where the summary printed under the
    verdict comes from. Not a second reader of the same file.
    """
    errors = [v for v in kicad.violations(report)
              if v.get("severity") == "error" and v.get("_group") == "violations"]
    return errors, report.get("unconnected_items") or []


def drc_status(board: pathlib.Path) -> str | None:
    """None when the board's DRC report is current and clean, else why not.

    The report is `<stem>.drc.json` in the workspace, where run_drc and
    run_layout write it. One older than the board (a fill, a relayout, an
    edit in KiCad since) says nothing about the board as it now is.
    """
    drc = paths.WORKSPACE / f"{board.stem}.drc.json"
    if not drc.is_file():
        return "DRC not run"
    if board.is_file() and drc.stat().st_mtime < board.stat().st_mtime:
        return "DRC not run on the current board"
    try:
        errors, unrouted = _counts(json.loads(drc.read_text()))
    except (OSError, ValueError):
        return f"{paths.rel(drc)} could not be read"
    if errors or unrouted:
        return f"{len(errors)} DRC error(s), {len(unrouted)} unconnected"
    return None


def run_drc(board: str) -> str:
    """Run KiCad's own DRC checker on a board in the workspace."""
    workspace = paths.WORKSPACE
    try:
        source = paths.inside(board)
    except ValueError as error:
        return f"{board}: {error}"
    if not source.is_file():
        # Before the network, so a typo reads as a typo rather than as a
        # pipeline problem.
        return f"no such file in the workspace: {board}"

    try:
        answer = service.call("drc", {"board": board})
    except (ValueError, RuntimeError) as exc:
        return f"DRC could not run: {exc}"

    code = answer.get("exit")
    if code not in (_CLEAN, _VIOLATIONS):
        detail = (answer.get("stderr") or answer.get("error")
                  or answer.get("stdout") or "").strip()
        return f"kicad-cli failed (exit {code}): {detail[:400]}"

    report_path = workspace / answer["report"]
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError) as exc:
        return f"kicad-cli wrote {answer['report']}, which could not be read: {exc}"

    verdict, why = _verdict(report)
    headline = f"{verdict} — {why} (DRC, kicad-cli exit {code})"
    dru = source.with_suffix(".kicad_dru")
    rules_note = (f"custom rules: {paths.rel(dru)}" if dru.is_file()
                  else "custom rules: none (KiCad's project settings only)")
    return (f"{headline}\n{rules_note}\n{kicad.summarise(report)}\n"
            f"Full report: {answer['report']}")


# The layout pipeline, in the order the steps depend on each other. Each is a
# circuit_mcp.service step; see service.py for why each one has to be there
# rather than in kicad-cli.
_STEPS = ("pcb", "outline", "rules", "place", "route", "drc")

# The board's own minimums, and what each is called where a person can look it
# up. Keys are the DRC error classes the model actually sees, so the handle it
# reaches for has the same name as the thing it is trying to fix.
#
# KiCad's defaults are conservative -- 0.2 mm track, 0.2 mm clearance, 0.3 mm
# drill -- and a fine-pitch footprint violates them by existing: a USB-C
# receptacle's neighbouring pads sit 0.15 mm apart and a QFN's thermal vias
# drill 0.2 mm. Measured on a real board 2026-09-16, 107 of 111 DRC errors
# were track_width, drill_out_of_range and clearance. Those are manufacturing
# settings, not design mistakes, and no amount of moving parts fixes one.
RULES = (
    "clearance_mm",           # copper-to-copper, the `clearance` violations
    "track_width_mm",         # the `track_width` violations
    "via_diameter_mm",
    "via_drill_mm",
    "through_hole_drill_mm",  # the `drill_out_of_range` violations
    "annular_ring_mm",
    "hole_to_hole_mm",
    "hole_clearance_mm",      # hole-to-copper, the `hole_clearance` violations
    "edge_clearance_mm",     # the `copper_edge_clearance` violations
)


def check_rules(rules: dict) -> str | None:
    """None when `rules` is usable, or the sentence saying why it is not.

    Refused rather than passed through, for the reason an unknown reference is
    refused: a typo that silently sets nothing leaves a board measured against
    limits nobody chose, and it looks exactly like a board that was relaxed.
    """
    unknown = sorted(set(rules) - set(RULES))
    if unknown:
        return (f"unknown design rule(s): {', '.join(unknown)}. The rules are: "
                f"{', '.join(RULES)}")
    for name in sorted(rules):
        value = rules[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{name} must be a length in millimetres, got {value!r}"
        if value <= 0:
            return f"{name} must be a length in millimetres greater than zero, got {value!r}"
    return None


# What each arrange op needs beside `op` and `refs`, and one worked example of
# each: a refusal that shows the right shape costs one step, where a KeyError
# from inside the pcbnew subprocess costs a guess.
_ARRANGE_KEYS = {
    "rotate": ("degrees",),
    "align": ("axis", "at"),
    "space_evenly": ("axis", "from", "to"),
}
_ARRANGE_EXAMPLES = (
    '{"op": "rotate", "refs": ["R1", "R2"], "degrees": 90}',
    '{"op": "align", "refs": ["R1", "R2"], "axis": "y", "at": 12}',
    '{"op": "space_evenly", "refs": ["R1", "R2", "R3"], "axis": "x", "from": 2, "to": 58}',
)


def _arrange_problem(op) -> str | None:
    if not isinstance(op, dict):
        return "each op must be an object"
    kind = op.get("op")
    if kind not in _ARRANGE_KEYS:
        return f"op must be one of {', '.join(_ARRANGE_KEYS)}, got {kind!r}"
    refs = op.get("refs")
    if (not isinstance(refs, list) or not refs
            or not all(isinstance(r, str) for r in refs)):
        return "refs must be a non-empty list of reference strings"
    missing = [k for k in _ARRANGE_KEYS[kind] if k not in op]
    if missing:
        return f"{kind} needs {', '.join(missing)}"
    if "axis" in op and op["axis"] not in ("x", "y"):
        return f"axis must be \"x\" or \"y\", got {op['axis']!r}"
    return None


def check_arrange(ops) -> str | None:
    """None when every arrange op is well formed, or the refusal naming the
    first bad one with a worked example of each op."""
    examples = "Each op looks like:\n  " + "\n  ".join(_ARRANGE_EXAMPLES)
    if not isinstance(ops, list):
        return f"arrange must be a list of ops. {examples}"
    for number, op in enumerate(ops, 1):
        problem = _arrange_problem(op)
        if problem:
            return f"arrange op {number} ({json.dumps(op)}) is invalid: {problem}. {examples}"
    return None


# --- the overwrite guard -----------------------------------------------------
#
# run_layout must never silently replace a board edited outside the tools --
# in KiCad, say. The tools' own hash of the file they last wrote is kept
# beside it (dotfile, so it does not show up as an extra board), and a board
# whose bytes no longer match that hash is treated as someone else's edit.

def _hash_file(board_path: pathlib.Path) -> pathlib.Path:
    return board_path.with_name(f".{board_path.name}.layout-hash")


def record_board(board: str) -> None:
    """Remember the board as the tools last wrote it."""
    target = paths.inside(board)
    if not target.is_file():
        # Nothing was actually written -- there is nothing to remember. Only
        # reachable when a caller (or a test) fakes the pipeline steps rather
        # than really writing the board.
        return
    _hash_file(target).write_text(hashlib.sha256(target.read_bytes()).hexdigest())


def edited_outside(board: str) -> bool:
    """True when the board exists but is not what the tools last wrote."""
    target = paths.inside(board)
    if not target.is_file():
        return False
    recorded = _hash_file(target)
    return (not recorded.is_file()
            or recorded.read_text().strip() != hashlib.sha256(target.read_bytes()).hexdigest())


def run_layout(netlist: str, width_mm: float, height_mm: float,
               arrange: list | None = None, positions: dict | None = None,
               route: bool = True, rules: dict | None = None,
               overwrite: bool = False) -> str:
    """Turn a netlist into a routed board and report what DRC says.

    One call, because "lay this out" is one intention. Iterating means
    changing `positions` and calling again, not re-orchestrating the pipeline
    steps by hand -- and the thing worth iterating on is the placement, which
    is the only input here that takes judgement.

    Two ways to say where things go, and `arrange` is usually the better one.

    `positions` is `{reference: [x_mm, y_mm, rotation_degrees]}` -- exact, and
    it requires knowing how big each footprint is. (x, y) is the part's
    CENTRE, after rotation; the pipeline corrects for wherever the library put
    that footprint's anchor, which is pad 1 on some parts and a corner on
    others. It used to pass the coordinate straight through to the anchor
    while saying "centre" here, which cost one recorded session twelve
    identical layout failures -- see _PLACE in circuit_mcp/service.py.

    `arrange` is a list of operations applied in order, and the pipeline does
    the arithmetic against the real bounding boxes:

        {"op": "rotate", "refs": [...], "degrees": 90}
        {"op": "align", "refs": [...], "axis": "y", "at": 12}
        {"op": "space_evenly", "refs": [...], "axis": "x", "from": 2, "to": 58}

    `space_evenly` cannot produce an overlap, and refuses with the measurement
    when the parts do not fit. That is why it exists: given only a count of
    unrouted nets, this model reached one unrouted net and then oscillated,
    because millimetres are the wrong handle on a geometry it cannot see.

    An unknown reference is refused rather than ignored, because a typo that
    silently places nothing leaves a board that looks laid out and is not.

    `rules` sets the BOARD's own minimums, which is a different thing from
    placement and fixes a different set of DRC failures. Without `rules`, DRC
    checks the default fab profile (`fab.PROFILE`); a fine-pitch part can
    still violate a fab's numbers by existing -- a USB-C receptacle's
    neighbouring pads are 0.15 mm apart, a QFN's thermal vias drill 0.2 mm --
    and no amount of moving parts fixes that; the numbers have to move.

        rules={"clearance_mm": 0.15, "track_width_mm": 0.15,
               "through_hole_drill_mm": 0.2}

    Each key is named after the DRC violation it answers, and the full list is
    RULES. Measured on a real board 2026-09-16: 60 of its violations -- 28
    `clearance` and 32 `drill_out_of_range` -- became 0 with those three
    numbers, in one call. Set them to what the board will actually be
    fabricated at.

    Net classes, when `<netlist stem>.netclasses.json` exists beside the
    netlist, are applied right after the board gets its outline and before
    anything is placed -- the router reads the default net class out of the
    Specctra export, so setting classes after routing would measure a board
    against widths it was not routed at. The classes come from
    set_net_classes's width table, never from this tool. A net whose tier
    width is wider than its smallest copper pad is routed at the pad's width
    in a class of its own, and named in the result as pad-limited.

    When `route` is true, the tracks freerouting leaves behind are repaired
    after routing and before DRC: narrowed tracks are widened back to their
    net class (except into a pad smaller than that width, which is where a
    neck-down belongs), and SMD pads on the pour net are given a solid
    connection so a thermal relief does not starve them. Repairing before DRC,
    rather than after, is deliberate: DRC then reports any clearance problem
    the widening itself causes, instead of a clean verdict computed against a
    board that changed underneath it.

    Refuses to overwrite a board that was edited outside the tools --
    in KiCad, say -- since 512 kilobytes of manual routing is not something to
    discard on a relayout call. Pass `overwrite=True` only once a human has
    agreed to lose those edits.
    """
    workspace = paths.WORKSPACE
    board = f"{netlist.rsplit('.', 1)[0]}.kicad_pcb"

    try:
        edited = edited_outside(board)
    except ValueError as error:
        return f"{netlist}: {error}"
    if edited and not overwrite:
        return (f"{board} changed since the tools last wrote it (edited in "
                f"KiCad?). Ask the human before relayout; call run_layout "
                f"again with overwrite=true only if they agree to discard "
                f"those edits.")
    if not (workspace / netlist).is_file():
        return f"no such netlist in the workspace: {netlist}"
    if not positions and not arrange:
        return ("nothing to place: give either positions "
                "({ref: [x, y, rotation]}) or arrange (a list of rotate / "
                "align / space_evenly operations)")
    # Before anything is sent, so a malformed request costs one step and not a
    # half-built board: the pipeline mutates the board file in place.
    if rules:
        refusal = check_rules(rules)
        if refusal:
            return refusal
    if arrange:
        refusal = check_arrange(arrange)
        if refusal:
            return refusal
    # Before the network, because the alternative is what happened on
    # 2026-09-11: kinet2pcb raised `KeyError: 'Device'` from inside a
    # dependency, behind a pcbnew `assert "PgmOrNull()" failed in
    # register_action()` that is unrelated noise, and the model read the noise
    # and re-sent the same call twice. The netlist had already been written,
    # so the run looked like it was progressing.
    #
    # `Device`, `Regulator_Linear` and `Connector` are SYMBOL libraries. The
    # model took the symbol ids it had just been given and put them in the
    # footprint field, which is a guess that type-checks and cannot work.
    unresolved = parts.unresolved_footprints(
        (workspace / netlist).read_text(errors="replace"), paths.FP_ROOT)
    if unresolved:
        return ("these footprints cannot be resolved, so the board cannot be "
                "built:\n" + "\n".join(unresolved) +
                "\n\nA footprint is not a symbol: `Device:Battery_Cell` is a "
                "symbol id and there is no `Device` footprint library. Use "
                "find_part to get the footprint, which it prints as "
                "footprint='<library>:<name>'.")

    placing = "arrange" if arrange else "place"
    bodies = {
        "pcb": {"netlist": netlist, "out": board},
        "outline": {"board": board, "width_mm": width_mm, "height_mm": height_mm},
        "rules": {"board": board, "rules": rules},
        "place": {"board": board, "positions": positions},
        "arrange": {"board": board, "ops": arrange},
        "route": {"board": board},
        "drc": {"board": board},
    }

    # Beside the netlist, which is where set_net_classes writes it.
    classes = paths.inside(netlist).with_suffix(".netclasses.json")
    class_note = ("no net classes (run net_evidence and set_net_classes first "
                  "to size power traces); default net class used")
    repair_note = None
    fab_note = None

    # From here on, the pipeline can leave a board on disk that the tools
    # themselves wrote -- from the `pcb` step onward -- however this function
    # exits: a placement refusal, a routing error, a DRC hiccup, or a clean
    # finish. record_board has to run on every one of those paths, or a board
    # the tools just wrote is indistinguishable from one edited outside them,
    # and the NEXT run_layout call refuses it. `finally` is what guarantees
    # that regardless of which `return` below fires; record_board itself
    # no-ops when the pipeline failed before anything was written.
    answer = {}
    try:
        for step in _STEPS:
            if step == "place":
                step = placing
            if step == "route" and not route:
                continue
            # The rules step always runs: without the agent's `rules` it still
            # applies the default fab profile, so DRC checks a real fab's
            # numbers rather than KiCad's (edge 0.5, clearance 0.0).
            try:
                answer = service.call(step, bodies[step])
            except (ValueError, RuntimeError) as exc:
                # Named by step: a placement typo reported as a routing
                # problem sends the reader to the wrong half of the pipeline.
                return f"layout failed at the {step!r} step: {exc}"
            if isinstance(answer, dict) and answer.get("error"):
                return f"layout failed at the {step!r} step: {answer['error']}"

            if step == "rules" and isinstance(answer, dict) and answer.get("profile"):
                yours = answer.get("agent_rules") or []
                fab_note = (f"fab rules: {answer['profile']} (floors)"
                            + (f" + your rules: {', '.join(yours)}" if yours else ""))
                if answer.get("kept_dru"):
                    fab_note += f"; your .kicad_dru kept as {answer['kept_dru']}"
            if step == "outline" and classes.is_file():
                applied = rules_mod.apply_netclasses(board, paths.rel(classes))
                class_note = (
                    f"net classes applied: {len(applied['classes'])} classes on "
                    f"{applied['assigned']} nets"
                    + (f"; not on the board: {', '.join(applied['missing'])}"
                       if applied["missing"] else "")
                    + (f"; pad-limited: " + ", ".join(
                        f"{net} {tier:g}→{width:g} mm"
                        for net, tier, width in applied.get("capped") or [])
                       if applied.get("capped") else ""))
            if step == "route":
                repaired = rules_mod.finish(board)
                keepouts = answer.get("npth_keepouts") if isinstance(answer, dict) else None
                repair_note = (
                    f"widened {len(repaired['widened'])} narrowed track(s); "
                    f"solid pour connection on {len(repaired['solid'])} SMD pad(s)"
                    + (f"; router kept clear of {keepouts} NPTH hole(s)" if keepouts else ""))

        code = answer.get("exit")
        if code not in (_CLEAN, _VIOLATIONS):
            return f"DRC failed (exit {code}): {(answer.get('stderr') or '')[:300]}"
        try:
            report = json.loads((workspace / answer["report"]).read_text())
        except (OSError, KeyError, ValueError) as exc:
            return f"the DRC report could not be read: {exc}"

        verdict, why = _verdict(report)
        routed = "routed" if route else "not routed (placement only)"
        headline = f"{verdict} — {why} (DRC exit {code}, {routed})"

        lines = [headline, class_note] + ([fab_note] if fab_note else [])
        if repair_note:
            lines.append(repair_note)
        lines.append(kicad.summarise(report))
        lines.append(f"Board: {board}")
        return "\n".join(lines)
    finally:
        record_board(board)


def net_evidence(netlist: str, offset: int = 0, limit: int = 40) -> str:
    """The nets rules already decided, then a page of the others with their
    evidence and the closed menu. Pick a class and tier per open net, then
    call set_net_classes (spec §4.2). Read-only."""
    try:
        source = paths.inside(netlist)
    except ValueError as error:
        return f"{netlist}: {error}"
    if not source.is_file():
        return f"no such netlist in the project: {netlist}"
    nl = netclass.read(source.read_text(errors="replace"))
    decided, open_nets = [], []
    for net in nl.nets:
        r = netclass.rule(nl, net)
        if r:
            decided.append({"net": net.name, "class": r[0], "tier": None, "source": f"rule:{r[1]}"})
        else:
            open_nets.append(net)
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    page = open_nets[offset:offset + limit]
    nxt = offset + limit if offset + limit < len(open_nets) else None
    return json.dumps({
        "decided": decided,
        "open": [{"net": n.name, "evidence": netclass.evidence(nl, n)} for n in page],
        "menu": netclass.MENU, "total_open": len(open_nets), "next_offset": nxt,
    }, indent=1)


def set_net_classes(netlist: str, picks: list[dict]) -> str:
    """Validate the picks (every open net once, from the menu, a reason, no
    power-pin net picked as non-power unless review: true) and write
    <stem>.netclasses.json for run_layout. On any problem nothing is written."""
    try:
        source = paths.inside(netlist)
    except ValueError as error:
        return f"REFUSED:\n- {netlist}: {error}"
    if not source.is_file():
        return f"REFUSED:\n- no such netlist in the project: {netlist}"
    nl = netclass.read(source.read_text(errors="replace"))
    open_names = [n.name for n in nl.nets if not netclass.rule(nl, n)]
    problems = netclass.validate(nl, open_names, picks)
    if problems:
        return "REFUSED:\n" + "\n".join(f"- {p}" for p in problems)
    data = netclass.write_netclasses(nl, picks)
    target = source.with_suffix(".netclasses.json")
    target.write_text(json.dumps(data, indent=2) + "\n")
    review = [f"- {r['net']}: {r['reason']}" for r in data["needs_review"]]
    return (f"WROTE {paths.rel(target)}: {len(data['nets'])} nets, "
            f"{len(data['needs_review'])} awaiting the human's decision\n" + "\n".join(review))


# The copper layers a pour goes on by default. Both sides of a two-layer board,
# which is what this pipeline builds; a caller with more layers names them.
_POUR_LAYERS = ("F.Cu", "B.Cu")
# Making one pad solid changes the copper around it and can cut a
# neighbour's spokes (measured: release2's J1 B1, then J1 SH). So the repair
# repeats until DRC names nothing new. Each pass only adds pads DRC named and
# never removes one, so it cannot oscillate; the cap is a guard, not a budget.
THERMAL_PASSES = 4


def repair_thermals(board: str, net: str) -> dict:
    """Make solid exactly the pads on `net` that KiCad's DRC says have a
    starved thermal relief.
    `{"solid": [(ref, pad)], "still": [(ref, pad)], "uuids": [uuid, ...]}`,
    `uuids` in the same order as `solid`. A verifying DRC that could not be
    trusted after pads were made solid adds `"error": "repair not verified: …"`.

    Through-hole pads on the pour -- USB-C shield tabs, THT ground pins -- keep
    pcbnew's thermal relief, and beside a connector's crowded edge the tracks,
    pads and board edge cut its spokes below the two DRC requires. On
    2026-09-24 an agent spent ten minutes nudging a connector to escape one.
    A solid connection cannot starve, and only the pads DRC names get it.

    Only uuids `rules.solid_pads` actually reports in `changed` are recorded
    as solid; a `missing` uuid was never touched, and if DRC still names it,
    it belongs in `still`.

    The DRC here writes a temporary report and deletes it: never
    `<stem>.drc.json`, which is the agent's own run_drc and what check_done
    reads. No report on the FIRST check (a fake pipeline, a DRC that could
    not run) means nothing to repair. No report on a check made to VERIFY a
    `solid_pads` call is a different thing -- it means the repair cannot say
    whether it worked -- so that is reported as an error, not read as
    success. Either way, the temporary file is removed before every DRC, not
    only at the end, so a check that fails to produce a fresh report is
    never satisfied by reading the file the previous pass left behind.
    """
    temporary = f".{pathlib.Path(board).stem}.fill-drc.json"
    report_path = paths.WORKSPACE / temporary

    def check() -> dict | None:
        """The current DRC report, or None when there is nothing to trust:
        an exit code outside {0, 5}, or a report that was not written, or
        could not be read."""
        report_path.unlink(missing_ok=True)
        answer = service.call("drc", {"board": board, "out": temporary})
        if answer.get("exit") not in (_CLEAN, _VIOLATIONS):
            return None
        try:
            return json.loads(report_path.read_text())
        except (OSError, ValueError):
            return None

    solid: dict[str, tuple[str, str]] = {}
    try:
        report = check()
        if report is None:
            # No report on the first check: nothing to repair, exactly as
            # before -- the fake-pipeline tests rely on this shape.
            return {"solid": [], "still": []}
        for _ in range(THERMAL_PASSES):
            starved = [s for s in kicad.starved_pads(report, net) if s[0] not in solid]
            if not starved:
                return {"solid": list(solid.values()), "still": [], "uuids": list(solid)}
            changed = set(rules_mod.solid_pads(
                board, [uuid for uuid, _, _ in starved])["changed"])
            solid.update({uuid: (ref, pad) for uuid, ref, pad in starved if uuid in changed})
            report = check()
            if report is None:
                return {"solid": list(solid.values()), "still": [], "uuids": list(solid),
                        "error": "repair not verified: the DRC after making "
                                 "pads solid did not run"}
        still = [(ref, pad) for _, ref, pad in kicad.starved_pads(report, net)]
        return {"solid": list(solid.values()), "still": still, "uuids": list(solid)}
    finally:
        report_path.unlink(missing_ok=True)


def fill_zones(board: str, net: str = "GND", layers: list | None = None,
               inset_mm: float = 0.5) -> str:
    """Pour copper for a net over the board and fill it.

    Creates the zone as well as filling it, and that is not a liberty with the
    name: a board built by run_layout comes from kinet2pcb, which produces no
    zones at all, so there is never anything for a fill on its own to act on.
    Measured on a 33-part board: zones 0.

    Idempotent. Re-running replaces the pour it would have created rather than
    stacking a second one on the same copper -- so this is safe to call again
    after moving parts, which is exactly when a fill goes stale.

    Order matters and the reason is worth knowing. DRC reads the board on
    disk, so a zone that exists and is not filled makes DRC check copper that
    is not there. Fill first, then run_drc.

    After the pour fills, the SMD pads on `net` are given a solid zone
    connection -- the same repair run_layout makes after routing, run here too
    because a fill created after layout would otherwise leave those pads on a
    thermal relief nothing asked for. And makes solid any pad whose thermal
    relief DRC finds starved.
    """
    try:
        if not paths.inside(board).is_file():
            return f"no such board in the workspace: {board}"
    except ValueError as error:
        return f"{board}: {error}"

    # Decided BEFORE the pour: recording the hash afterwards regardless would
    # launder a human's KiCad edit into a board the tools "wrote", and the next
    # run_layout would then overwrite it without asking.
    was_ours = not edited_outside(board)
    body = {"board": board, "net": net,
            "layers": list(layers or _POUR_LAYERS), "inset_mm": inset_mm}
    try:
        answer = service.call("zone", body)
    except (ValueError, RuntimeError) as exc:
        return f"the pour failed: {exc}"
    if answer.get("error"):
        return f"the pour failed: {answer['error']}"

    repaired = rules_mod.finish(board, pour_net=net)
    try:
        thermal = repair_thermals(board, net)
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        thermal = {"solid": [], "still": [], "error": str(exc)}

    unfilled = answer.get("unfilled") or []
    replaced = answer.get("replaced") or 0
    lines = [f"{'REFILLED' if replaced else 'POURED'} {net} on "
             f"{', '.join(body['layers'])}",
             f"  zones on the board: {answer.get('zones')}",
             f"  filled area: {answer.get('filled_area_mm2')} mm2",
             f"  solid pour connection on {len(repaired['solid'])} SMD pad(s)"]
    lines += [f"  SOLID: {ref} {pad} (its thermal relief was starved; now a solid "
              f"connection -- no need to move parts for it; note in README.md "
              f"that it needs extra heat to hand-solder)" for ref, pad in thermal["solid"]]
    lines += [f"  STILL STARVED: {ref} {pad} -- run_drc shows why" for ref, pad in thermal["still"]]
    if thermal.get("error"):
        lines.append(f"  the thermal check could not run: {thermal['error']}")
    if unfilled:
        # Named, because a zone that would not fill is usually a pour with no
        # pad of that net inside it, and the board looks finished either way.
        lines.append(f"  WOULD NOT FILL: {', '.join(unfilled)} -- is there a "
                     f"pad on that net inside the outline?")
    lines.append(f"Board: {board}. Re-run DRC (run_drc) now: it reads "
                 f"the board on disk and the copper just changed.")
    if was_ours:
        record_board(board)
    return "\n".join(lines)


def export_gerbers(board: str, out: str | None = None) -> str:
    """The fabrication package: gerbers, drill files and a job file.

    This is the last artefact a board needs, and until it exists a DRC-clean
    board is still not something a fab can build.

    A warning rather than a refusal when the board carries an unfilled zone.
    The export itself is safe -- it passes --check-zones, which refills for the
    plot, measured as the difference between an 11,413-byte B.Cu with no pour
    and a 118,017-byte one with it. What is NOT safe is the DRC verdict you
    already have, which was computed against the unfilled board on disk. So the
    gerbers are right and the verdict behind them may not be.
    """
    try:
        source = paths.inside(board)
    except ValueError as error:
        return f"{board}: {error}"
    if not source.is_file():
        return f"no such board in the workspace: {board}"

    stale = kicad_lib.unfilled_zones(source.read_text(errors="replace"))
    body = {"board": board, "out": out or "gerbers"}
    try:
        answer = service.call("fab", body)
    except (ValueError, RuntimeError) as exc:
        return f"the fabrication export failed: {exc}"
    if answer.get("error"):
        return f"the fabrication export failed: {answer['error']}"

    files = answer.get("files") or []
    lines = [f"EXPORTED {len(files)} files to {answer.get('dir')}/",
             f"  gerbers: {len(answer.get('gerbers') or [])}",
             f"  drill:   {', '.join(answer.get('drill') or []) or 'none'}",
             f"  job:     {answer.get('job') or 'none'}"]
    not_ready = drc_status(source)
    if not_ready:
        # Still exported -- the files are what they are -- but a board that
        # fails DRC, or was never checked as it now is, is not something to
        # send to a fab, and that has to be the first thing read.
        lines.insert(0, f"NOT FABRICATION-READY: {not_ready}")
    if stale:
        lines.append(
            f"  WARNING: {stale} zone(s) on this board are not filled on disk. "
            f"The gerbers are correct -- the export refills them -- but any DRC "
            f"result you already have was computed without that copper. Run "
            f"fill_zones then run_drc before trusting the verdict.")
    return "\n".join(lines)


def run_skidl(script: str) -> str:
    """Run a SKiDL design script in the project folder and report what it produced."""
    try:
        target = paths.inside(script)
    except ValueError as error:
        return f"{script}: {error}"
    if not target.is_file():
        return f"no such script in the project: {script}"
    # "produced" below is read off mtime, not content, so it trusts the
    # filesystem's clock to tell an untouched .net apart from one the script
    # just rewrote -- fine on any filesystem with sub-second mtimes, which is
    # what this container's overlay filesystem provides.
    before = {p: p.stat().st_mtime for p in paths.WORKSPACE.glob("*.net")}
    try:
        done = subprocess.run(["python3", str(target)], cwd=paths.WORKSPACE,
                              capture_output=True, text=True, timeout=service.TIMEOUT,
                              env=service.script_env())
    except subprocess.TimeoutExpired:
        return f"{script} timed out after {service.TIMEOUT}s"
    output = (done.stdout + done.stderr).strip()
    if done.returncode != 0:
        return f"{script} failed (exit {done.returncode}):\n{output[-1500:]}"
    produced = sorted(p for p in paths.WORKSPACE.glob("*.net")
                      if before.get(p) != p.stat().st_mtime)
    erc = sorted(paths.WORKSPACE.glob(f"{target.stem}*.erc"))
    erc_text = erc[-1].read_text(errors="replace").strip() if erc else "no ERC file"
    if not produced:
        return (f"{script} ran but wrote no netlist; call generate_netlist(file_=...).\n"
                f"{output[-800:]}")
    nets = [paths.rel(p) for p in produced]
    return (f"netlist: {', '.join(nets)}\nERC: {erc_text[-800:] or 'clean'}\n"
            f"{output[-600:]}")
