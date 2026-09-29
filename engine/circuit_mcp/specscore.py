"""Score a model-written part spec against a golden one.

The datasheet-parts design, §9: the model evaluation. The engine checks a
spec's consistency (partspec) and computes and checks its footprint
(makepart), but nothing there can tell a value read wrong from the datasheet
from a value read right. Only a spec a human wrote and checked -- the golden
-- can, so this compares the two, fact by fact:

- geometry: every pad's position, size and drill, as make_part computes them,
  ignoring pad numbers (a land pattern and the leads it comes from are the
  same answer);
- numbering: which pad is which pin;
- view: top or bottom, since the two are mirror images;
- each pin: its name and electrical type.

Each fact gets one verdict:

    right         the candidate matches the golden
    needless      right, but the candidate marked it unknown
    flagged       wrong, and the candidate marked it unknown
    both_unknown  the datasheet does not state it, and the candidate says so
    wrong         wrong, and NOT marked unknown                      <- silent
    guessed       the datasheet does not state it, and the candidate filled
                  it in without marking it unknown                   <- silent

A silent error is the metric that matters: a flagged doubt reaches a human
(check_done lists it), a silent one reaches the fab. A spec passes with none.

    python3 -m circuit_mcp.specscore <candidate.part.toml> <golden.part.toml> [--json]

exits 0 on a pass, 1 on silent errors, 2 when the candidate is not a valid
spec at all.
"""
import json
import sys
import tomllib

from circuit_mcp import makepart, partspec

POSITION_MM = 0.05
SIZE_MM = 0.05
DRILL_MM = 0.02
SILENT = ("wrong", "guessed")
VERDICTS = ("right", "needless", "flagged", "both_unknown", "wrong", "guessed")


def _unknown(spec: dict) -> set[str]:
    return {name for name, table in partspec._tables(spec) if name and table.get("unknown") is True}


def _geometry_doubt(marks: set[str]) -> bool:
    return any(m == "package" or m.startswith(("package.", "pads[")) or m == "pads" for m in marks)


def _pin_doubt(marks: set[str], index: int | None) -> bool:
    return "pins" in marks or (index is not None and f"pins[{index}]" in marks)


def _pad_key(pad: dict):
    drill = pad["drill"]
    return (pad["size"], drill if isinstance(drill, tuple) or drill is None else (drill, drill))


def _close(a, b, tolerance) -> bool:
    if a is None or b is None:
        return a is b
    return all(abs(x - y) <= tolerance + 1e-9 for x, y in zip(a, b))


def _same_pad(a: dict, b: dict, numbered: bool) -> bool:
    (sa, da), (sb, db) = _pad_key(a), _pad_key(b)
    return (_close((a["x"], a["y"]), (b["x"], b["y"]), POSITION_MM) and _close(sa, sb, SIZE_MM)
            and _close(da, db, DRILL_MM) and (not numbered or a["number"] == b["number"]))


def _match(candidate: list[dict], golden: list[dict], numbered: bool) -> list[str]:
    """What does not match, pad for pad; [] when every golden pad has its twin."""
    left, misses = list(candidate), []
    for pad in golden:
        twin = next((c for c in left if _same_pad(c, pad, numbered)), None)
        if twin is None:
            misses.append(f"pad {pad['number']} at ({pad['x']:g}, {pad['y']:g}) {pad['size']}")
        else:
            left.remove(twin)
    misses += [f"extra pad {c['number']} at ({c['x']:g}, {c['y']:g})" for c in left]
    return misses


def _verdict(known: bool, right: bool, doubted: bool) -> str:
    if not known:
        return "both_unknown" if doubted else "guessed"
    if right:
        return "needless" if doubted else "right"
    return "flagged" if doubted else "wrong"


def score(candidate: dict, golden: dict) -> dict:
    """The fact-by-fact comparison, with counts, silent errors and the verdict."""
    # A datasheet path is the candidate project's business; everything else
    # the engine would refuse makes the spec invalid.
    problems = [p for p in partspec.problems(candidate) if not p.startswith("datasheet:")]
    if problems:
        return {"valid": False, "problems": problems, "facts": [], "counts": {},
                "silent": None, "pass": False}
    mine, theirs = _unknown(candidate), _unknown(golden)
    ours, gold = makepart.pads(candidate), makepart.pads(golden)
    facts = []

    misses = _match(ours, gold, numbered=False)
    facts.append({"fact": "geometry", "detail": "; ".join(misses[:5]) or "every pad matches",
                  "verdict": _verdict(not _geometry_doubt(theirs), not misses, _geometry_doubt(mine))})

    numbering_known = "numbering" not in theirs and not _geometry_doubt(theirs)
    wrong_numbers = _match(ours, gold, numbered=True) if not misses else ["the geometry differs"]
    facts.append({"fact": "numbering", "detail": "; ".join(wrong_numbers[:5]) or "every pad numbered alike",
                  "verdict": _verdict(numbering_known, not wrong_numbers,
                                      "numbering" in mine or _geometry_doubt(mine))})

    view_right = candidate["package"].get("view") == golden["package"].get("view")
    facts.append({"fact": "view",
                  "detail": f"{candidate['package'].get('view')} (golden: {golden['package'].get('view')})",
                  "verdict": _verdict("package" not in theirs, view_right, "package" in mine)})

    by_number = {partspec._pin(p["number"]): (i, p) for i, p in enumerate(candidate.get("pins", []), 1)}
    for index, pin in enumerate(golden.get("pins", []), 1):
        number = partspec._pin(pin["number"])
        found = by_number.pop(number, None)
        mine_index, theirs_pin = (found[0], found[1]) if found else (None, None)
        right = bool(theirs_pin) and (theirs_pin["name"].strip().upper() == pin["name"].strip().upper()
                                      and theirs_pin["type"] == pin["type"])
        detail = (f"{theirs_pin['name']} {theirs_pin['type']}" if theirs_pin else "missing") + \
                 f" (golden: {pin['name']} {pin['type']})"
        facts.append({"fact": f"pin {number}", "detail": detail,
                      "verdict": _verdict(not _pin_doubt(theirs, index), right,
                                          _pin_doubt(mine, mine_index))})
    for number, (index, pin) in by_number.items():
        facts.append({"fact": f"pin {number}", "detail": f"{pin['name']} is not a pin of the golden",
                      "verdict": "flagged" if _pin_doubt(mine, index) else "wrong"})

    counts = {v: sum(f["verdict"] == v for f in facts) for v in VERDICTS}
    silent = sum(counts[v] for v in SILENT)
    return {"valid": True, "problems": [], "facts": facts, "counts": counts,
            "silent": silent, "pass": silent == 0}


def report_text(report: dict) -> str:
    if not report["valid"]:
        return "INVALID: the engine would refuse this spec:\n" + "\n".join(
            f"  - {p}" for p in report["problems"])
    head = "PASS" if report["pass"] else f"FAIL: {report['silent']} silent error(s)"
    counts = ", ".join(f"{n} {v}" for v, n in report["counts"].items() if n)
    lines = [f"{head} ({counts})"]
    for fact in report["facts"]:
        if fact["verdict"] != "right":
            marker = "!!" if fact["verdict"] in SILENT else "  "
            lines.append(f"{marker} {fact['fact']}: {fact['verdict']} -- {fact['detail']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--json"]
    if len(args) != 2:
        print("usage: python3 -m circuit_mcp.specscore <candidate.part.toml> <golden.part.toml> [--json]",
              file=sys.stderr)
        return 2
    candidate, golden = (tomllib.loads(open(path).read()) for path in args)
    report = score(candidate, golden)
    print(json.dumps(report, indent=2) if "--json" in argv else report_text(report))
    return 2 if not report["valid"] else 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
