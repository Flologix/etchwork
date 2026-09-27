"""Summarise a `kicad-cli` JSON report so a 32k window can hold it.

**The problem this solves.** `kicad-cli pcb drc --format json --severity-all`
on a real board emits thousands of violations, each with a description, a
severity, a type and a list of affected items carrying UUIDs and coordinates.
`MAX_TOOL_OUTPUT` is 8192 characters, so what reaches the model is the first
few kilobytes of that, ending mid-object. A truncated JSON fragment is worse
than a summary and worse than nothing: it is unparseable, it looks like data,
and the violations it happens to contain are the ones that sorted first rather
than the ones that matter.

The aggregation is most of the value. Four hundred `silk_edge_clearance`
warnings are ONE fact, and a model that reads them one at a time will spend
the whole window learning it.

**Deliberately dependency-free and standalone.** Nothing here imports from
`app`, and it uses only the standard library, because of where it has to run:
the shell jail's read roots are the workspace and `/repo`, so `/srv/app` --
where the Dockerfile puts this package -- is NOT readable from a jailed
command. The invocation that works is `python3 /repo/app/kicad.py report.json`
(python3 comes from /usr, which the jail grants for execution; the script
comes from /repo, which is a read root). An `from app import config` here
would break that and would only be noticed inside a container.

**It reports; it does not judge.** No verdict, and the exit code says only
whether the report could be read. `kicad-cli pcb drc --exit-code-violations`
is what says pass or fail, and a second thing computing its own answer from
the same data is a second thing that can disagree with it.

**Verified where.** The two report shapes are taken from KiCad's own schemas,
`resources/schemas/drc.v1.json` and `erc.v1.json` on the 9.0 branch, and as of
**2026-09-03 both have been run against output from a real `kicad-cli`** --
9.0.9, from the `kicad/kicad:9.0` image, on the `interf_u` demo. Those two
reports are committed as `tests/fixtures/kicad/real-*.json` and the tests read
them.

The schema-built fixtures turned out to have the shape right: every real-output
test passed the first time it ran. Real reports carry one key the hand-built
ones did not, `included_severities`, which changes nothing because unknown keys
are ignored. Measured on the way through: a 33 KB DRC report from
`pic_programmer` summarises to 381 characters, against a `MAX_TOOL_OUTPUT` of
8192 -- which is the number this module was written for.

Still unverified: a board whose violations are mostly NOT
`lib_footprint_issues`. Every demo in that image reports a flood of them
because its footprint libraries are unconfigured, so the aggregation was
exercised on real data but the *variety* was thin -- `interf_u` offered two
types where a real design under review would offer a dozen.
"""
import json
import re
import sys
from collections import Counter

# How many individual violations to print in full. Small on purpose: the
# per-type counts above them are what a reader acts on, and the examples exist
# to make one type concrete, not to enumerate the board.
MAX_SHOWN = 8

# Errors first. A model reading a list top-down fixes what it sees first, and
# on a board that is warnings-only-until-you-scroll, that is the wrong thing.
_SEVERITY_ORDER = {"error": 0, "warning": 1}


def violations(report: dict) -> list[dict]:
    """Every violation in a DRC or ERC report, as one list. Public.

    Named and exported because app/tools/kicad_tool.py's `_verdict` needs the
    SAME answer this module's summary is built from, and for a while it had
    its own: it read the top-level `violations` only, which an ERC report does
    not have, so every schematic check returned PASS while the summary printed
    underneath it listed the errors. A verdict contradicting its own report is
    worse than no verdict -- measured 2026-09-11, a model read `PASS (ERC)`
    above two errors and spent forty more steps unable to tell whether it was
    finished.

    Two functions knowing the same thing is how that happened. There is one
    now.
    """
    return _flatten(report)


def _flatten(report: dict) -> list[dict]:
    """Every violation in a DRC or ERC report, as one list.

    The two shapes differ and neither is a superset: a DRC report carries
    `violations`, `unconnected_items` and `schematic_parity` as three separate
    top-level arrays, while an ERC report nests its violations one level down
    under `sheets`. Reading both here rather than in two functions is what
    lets the caller not care which checker produced the file -- which the
    agent frequently will not know, because it ran both.
    """
    found: list[dict] = []
    for key in ("violations", "unconnected_items", "schematic_parity"):
        for violation in report.get(key) or []:
            # The group is kept because "unconnected" and "clearance" are not
            # the same kind of problem even at the same severity, and the
            # top-level array is the only thing that says which is which.
            found.append({**violation, "_group": key})
    for sheet in report.get("sheets") or []:
        for violation in sheet.get("violations") or []:
            found.append({**violation, "_group": "violations",
                          "_sheet": sheet.get("path") or ""})
    return found


# `PTH pad B1 [GND] of J1`, `SMD pad 3 [GND] of U1`: how KiCad names a pad in
# a violation's items. The zone item beside it is `Zone [GND] on F.Cu, ...`.
#
# The pad name is lazy (and may contain a space, e.g. a grid-array `A 1`) so
# it stops at the first ` [`; the net is greedy so a net name that itself
# contains brackets (`D[0]`, a bus member) is matched whole, up to the LAST
# `] of`.
_PAD_ITEM = re.compile(r"\bpad (.*?) \[(.*)\] of (\S+)$")


def starved_pads(report: dict, net: str) -> list[tuple[str, str, str]]:
    """`(uuid, ref, pad)` for each pad on `net` that a DRC report says has a
    starved thermal relief, deduplicated, in report order.

    The uuid is what identifies the pad on the board -- the repair acts on it,
    never on a position or a guess from the text; ref and pad are for the
    message. Only `starved_thermal` violations count, and only their pad item,
    not the zone item that always accompanies it.
    """
    found: dict[str, tuple[str, str, str]] = {}
    for violation in report.get("violations") or []:
        if violation.get("type") != "starved_thermal":
            continue
        for item in violation.get("items") or []:
            match = _PAD_ITEM.search(item.get("description") or "")
            uuid = item.get("uuid")
            if match and uuid and match.group(2) == net:
                found.setdefault(uuid, (uuid, match.group(3), match.group(1)))
    return list(found.values())


def _location(item: dict, units: str) -> str:
    position = item.get("pos") or {}
    if "x" not in position or "y" not in position:
        return item.get("description") or "(no location)"
    return (f"{item.get('description') or 'item'} "
            f"@ ({position['x']}, {position['y']}) {units}")


def summarise(report: dict, shown: int = MAX_SHOWN) -> str:
    """A short, ordered report of what the checker found."""
    units = report.get("coordinate_units") or "mm"
    source = report.get("source") or "(unknown source)"
    version = report.get("kicad_version") or "?"

    every = _flatten(report)
    # Excluded violations are in the file when --severity-all is used and are
    # NOT problems: somebody dismissed them in the design. Counted separately
    # rather than dropped silently, because "0 violations" and "0 violations
    # and 40 exclusions" are different states of a board.
    excluded = [v for v in every if v.get("excluded")]
    live = [v for v in every if not v.get("excluded")]

    severities = Counter(v.get("severity") or "unknown" for v in live)
    headline = ", ".join(f"{n} {name}" for name, n in sorted(
        severities.items(), key=lambda kv: _SEVERITY_ORDER.get(kv[0], 9)))

    lines = [f"{source} (KiCad {version})"]
    if not live:
        lines.append(f"No violations{f' ({len(excluded)} excluded)' if excluded else ''}.")
        return "\n".join(lines)

    lines.append(f"{len(live)} violation(s): {headline}"
                 + (f" -- plus {len(excluded)} excluded" if excluded else ""))

    by_type = Counter(
        (v.get("severity") or "unknown", v.get("type") or v["_group"])
        for v in live)
    lines.append("")
    lines.append("By type:")
    for (severity, kind), count in sorted(
            by_type.items(),
            key=lambda kv: (_SEVERITY_ORDER.get(kv[0][0], 9), -kv[1], kv[0][1])):
        lines.append(f"  {count:>4}  {kind} ({severity})")

    # ONE example per type, not the first N violations.
    #
    # Printing the first N was the obvious version and it is measurably worse:
    # on a board with twelve silkscreen warnings and two errors it spent six
    # of its eight slots reprinting the same warning at the same coordinates,
    # which the "By type" block above had already counted. Every line here
    # should carry something the counts do not.
    seen: set[tuple[str, str]] = set()
    examples = []
    for violation in sorted(
            live, key=lambda v: _SEVERITY_ORDER.get(v.get("severity") or "", 9)):
        key = (violation.get("severity") or "", violation.get("type") or violation["_group"])
        if key in seen:
            continue
        seen.add(key)
        examples.append(violation)

    lines.append("")
    lines.append(f"One example of each of the {len(seen)} type(s):"
                 if len(examples) <= shown else
                 f"An example of {shown} of the {len(seen)} type(s):")
    for violation in examples[:shown]:
        sheet = violation.get("_sheet")
        where = f" [{sheet}]" if sheet else ""
        lines.append(f"  [{violation.get('severity') or '?'}] "
                     f"{violation.get('type') or violation['_group']}{where}: "
                     f"{violation.get('description') or ''}".rstrip())
        for item in (violation.get("items") or [])[:3]:
            lines.append(f"      {_location(item, units)}")

    # Said on EVERY report with more than one violation, not only when the
    # example list was cut. The examples are one per type by construction, so
    # even a complete example list is not the complete violation list -- and a
    # summary that reads as the whole thing is the same mistake as a truncated
    # read with no notice.
    lines.append("")
    lines.append(f"[{len(live)} violation(s) in total; the full report is "
                 f"still in the JSON file.]")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """`python3 /repo/app/kicad.py <report.json>`. 0 read it, 2 could not.

    The exit code is about THIS program, not about the board. Whether the
    design passes is `kicad-cli`'s answer, and it already has a flag that says
    so.
    """
    if len(argv) != 2:
        print("usage: kicad.py <drc-or-erc-report.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as handle:
            report = json.load(handle)
    except OSError as exc:
        print(f"could not read {argv[1]}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        # Named as "not JSON" rather than passed through raw, because the
        # likeliest cause is the default --format report having been used
        # instead of --format json, and that is a one-flag correction.
        print(f"{argv[1]} is not JSON: {exc}. Re-run kicad-cli with "
              f"--format json.", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print(f"{argv[1]} is JSON but not a kicad-cli report "
              f"(expected an object at the top level).", file=sys.stderr)
        return 2
    print(summarise(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    sys.exit(main(sys.argv))
