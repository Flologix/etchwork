"""The part spec: the one file the agent's model writes for a part.

The datasheet-parts design (§4). The model reads a datasheet and writes
`parts/<MPN>.part.toml`; the engine computes the footprint and symbol from it
(make_part, a later phase) and never takes geometry from the model directly.
This module is the spec's format and every check that needs no geometry:

- every value cites its `source` (a page, a figure, a quote), so a human can
  check it against the datasheet;
- whatever the datasheet does not state is `unknown = true` with a `note`
  saying what was assumed -- never a plausible guess left unmarked; the ODU
  run of 2026-09-28 guessed a pin numbering the drawing did not show;
- the drawing's `view` is stated, because top and bottom views are mirror
  images and confusing them is the classic footprint error;
- pins and pads agree, number for number.

`load` never raises on a bad spec: it answers with every problem, the way
set_net_classes does, so one round trip fixes them all.
"""
import re
import tomllib

from circuit_mcp import paths

FAMILIES = ("circular", "pads", "dual_row", "quad_flat")
# In the design (§6.1) but not built yet: none, since phase 4.
LATER = ()
VIEWS = ("top", "bottom")
DIRECTIONS = ("cw", "ccw")
SHAPES = ("circle", "rect", "oval", "roundrect")
# KiCad's electrical pin types, as its symbol files spell them.
PIN_TYPES = ("input", "output", "bidirectional", "tri_state", "passive", "free",
             "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
             "no_connect")
_LIBRARY = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
# The symbol's reference prefix: J for a connector, U for an IC, and so on.
_REFERENCE = re.compile(r"^[A-Z]{1,4}$")


def footprint_name(data: dict) -> str:
    """The footprint's and symbol's name: `name`, or the MPN made file-safe."""
    return data.get("name") or re.sub(r"[^A-Za-z0-9_.-]", "_", str(data.get("mpn", "")))


def footprint_id(data: dict) -> str:
    """`<library>:<name>`, as a netlist names the footprint."""
    return f"{data.get('library')}:{footprint_name(data)}"


def load(spec: str) -> tuple[dict | None, list[str]]:
    """`(the spec, its problems)`; the spec is None when it cannot be read."""
    try:
        path = paths.inside(spec)
    except ValueError as error:
        return None, [f"{spec}: {error}"]
    if not path.is_file():
        return None, [f"no such file in the workspace: {spec}"]
    try:
        data = tomllib.loads(path.read_text(errors="replace"))
    except tomllib.TOMLDecodeError as error:
        return None, [f"{spec} is not valid TOML: {error}"]
    return data, problems(data)


def _number(value, above=0.0) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > above


def _whole(value, least=1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= least


def _pair(value, positive=False) -> bool:
    return (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
            and (not positive or all(v > 0 for v in value)))


def _pin(value) -> str | None:
    """A pin or pad number as text; TOML lets it be written as an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return value if isinstance(value, str) and value.strip() else None


def _sourced(table: dict, name: str, out: list[str], fallback: bool = False) -> None:
    if not fallback and not (isinstance(table.get("source"), str) and table["source"].strip()):
        out.append(f"{name}: no source (the page and figure it came from)")


def _tables(data, name=""):
    """Every table in the spec, with its dotted name: `package.rings[1]`."""
    if isinstance(data, dict):
        yield name, data
        for key, value in data.items():
            yield from _tables(value, f"{name}.{key}" if name else key)
    elif isinstance(data, list):
        for i, item in enumerate(data, start=1):
            if isinstance(item, dict):
                yield from _tables(item, f"{name}[{i}]")


def _ring(ring: dict, name: str, out: list[str]) -> None:
    if not _number(ring.get("diameter_mm")):
        out.append(f"{name}: diameter_mm must be a number above 0 (got {ring.get('diameter_mm')!r})")
    if not _whole(ring.get("count")):
        out.append(f"{name}: count must be a whole number of at least 1 (got {ring.get('count')!r})")
    if not isinstance(ring.get("start_deg"), (int, float)) or isinstance(ring.get("start_deg"), bool):
        out.append(f"{name}: start_deg must be a number: pin's angle, 0 = +x, 90 = up")
    if ring.get("direction") not in DIRECTIONS:
        out.append(f"{name}: direction must be 'cw' or 'ccw' (got {ring.get('direction')!r})")
    if not _whole(ring.get("first_pin")):
        out.append(f"{name}: first_pin must be a whole number of at least 1")
    if "drill_mm" in ring and not _number(ring["drill_mm"]):
        out.append(f"{name}: drill_mm must be a number above 0 (got {ring['drill_mm']!r})")
    if "pad_mm" in ring and not _number(ring["pad_mm"]):
        out.append(f"{name}: pad_mm must be a number above 0 (got {ring['pad_mm']!r})")
    if "drill_mm" not in ring and "pad_mm" not in ring:
        out.append(f"{name}: give drill_mm (through-hole) or pad_mm (surface-mount)")
    _sourced(ring, name, out)


def _pad(pad: dict, name: str, out: list[str]) -> None:
    if _pin(pad.get("number")) is None:
        out.append(f"{name}: number is required")
    if not _pair(pad.get("at_mm")):
        out.append(f"{name}: at_mm must be [x, y] in mm")
    if not _pair(pad.get("size_mm"), positive=True):
        out.append(f"{name}: size_mm must be [width, height] in mm, both above 0")
    if "drill_mm" in pad and not _number(pad["drill_mm"]):
        out.append(f"{name}: drill_mm must be a number above 0 (got {pad['drill_mm']!r})")
    if "shape" in pad and pad["shape"] not in SHAPES:
        out.append(f"{name}: shape must be one of {', '.join(SHAPES)} (got {pad['shape']!r})")
    _sourced(pad, name, out)


def _rows(package: dict, family: str, out: list[str]) -> None:
    """dual_row (SOIC, TSSOP, DIP, headers) and quad_flat (QFN, QFP), from
    the datasheet's recommended land pattern."""
    count = package.get("count")
    if not _whole(count, 2):
        out.append(f"package.count must be the number of terminals, a whole number of at least 2 "
                   f"(got {count!r})")
    elif family == "dual_row" and count % 2:
        out.append(f"package.count must be even for dual_row: two rows of the same length (got {count})")
    elif family == "quad_flat":
        lr = package.get("pins_lr")
        if lr is None and count % 4:
            out.append(f"package.count must be a multiple of 4 for quad_flat, unless pins_lr says "
                       f"how many are on the left and right sides (got {count})")
        elif lr is not None and not (_whole(lr) and 2 * lr < count and (count - 2 * lr) % 2 == 0):
            out.append(f"package.pins_lr must be a whole number of pins per left/right side that "
                       f"leaves an even number, above 0, for top and bottom (got {lr!r} of {count})")
    if not _number(package.get("pitch_mm")):
        out.append(f"package.pitch_mm must be a number above 0 (got {package.get('pitch_mm')!r})")
    if "leads" in package:
        _leads(package, out)
        return
    if not _number(package.get("row_spacing_mm")):
        out.append(f"package.row_spacing_mm must be a number above 0 "
                   f"(got {package.get('row_spacing_mm')!r})")
    if "column_spacing_mm" in package and not _number(package["column_spacing_mm"]):
        out.append(f"package.column_spacing_mm must be a number above 0 "
                   f"(got {package['column_spacing_mm']!r})")
    if "drill_mm" in package and not _number(package["drill_mm"]):
        out.append(f"package.drill_mm must be a number above 0 (got {package['drill_mm']!r})")
    pad = package.get("pad_mm")
    if pad is None:
        if "drill_mm" not in package:
            out.append("package.pad_mm must be [length, width] in mm: the land's size, from the "
                       "recommended land pattern")
    elif not (_pair(pad, positive=True) or ("drill_mm" in package and _number(pad))):
        out.append(f"package.pad_mm must be [length, width] in mm, both above 0 (got {pad!r})")
    if "thermal_pad_mm" in package and not _pair(package["thermal_pad_mm"], positive=True):
        out.append("package.thermal_pad_mm must be [width, height] in mm, both above 0")
    if "thermal_pin" in package and _pin(package["thermal_pin"]) is None:
        out.append("package.thermal_pin must be the exposed pad's pin number")


LEADS = ("gull_wing", "no_lead")


def span(value) -> tuple[float, float] | None:
    """A toleranced dimension as `(min, max)`: one number, or [min, max]."""
    if _number(value):
        return (float(value), float(value))
    if _pair(value, positive=True) and value[0] <= value[1]:
        return (float(value[0]), float(value[1]))
    return None


def _leads(package: dict, out: list[str]) -> None:
    """Lands computed from the package drawing (phase 4b): the lead type and
    its dimensions, each one number or [min, max] as the drawing gives it."""
    if "row_spacing_mm" in package or "pad_mm" in package:
        out.append("package: give the recommended land pattern (row_spacing_mm, pad_mm) or the "
                   "leads (leads, span_mm, lead_length_mm, lead_width_mm), not both")
    if package["leads"] not in LEADS:
        out.append(f"package.leads must be one of {', '.join(LEADS)} (got {package['leads']!r})")
    fields = ["span_mm", "lead_length_mm", "lead_width_mm"] + (
        ["span_y_mm"] if "span_y_mm" in package else [])
    for field in fields:
        if span(package.get(field)) is None:
            out.append(f"package.{field} must be a number or [min, max] in mm, min <= max "
                       f"(got {package.get(field)!r})")
    length = span(package.get("lead_length_mm"))
    for field in ("span_mm", "span_y_mm"):
        outer = span(package.get(field))
        if outer and length and outer[0] - 2 * length[1] <= 0:
            out.append(f"package.lead_length_mm {list(length)} leaves nothing between the rows of "
                       f"a {field} of {list(outer)}: a lead is at most half the span")


def _raw_pads(data: dict) -> list[str]:
    """Every pad number the package makes, duplicates included."""
    package = data.get("package") if isinstance(data.get("package"), dict) else {}
    numbers = []
    if package.get("family") == "circular":
        for ring in package.get("rings") or []:
            if isinstance(ring, dict) and _whole(ring.get("first_pin")) and _whole(ring.get("count")):
                numbers += [str(n) for n in range(ring["first_pin"], ring["first_pin"] + ring["count"])]
        for slot in package.get("slots") or []:
            if isinstance(slot, dict) and _pin(slot.get("pin")):
                numbers.append(_pin(slot["pin"]))
    elif package.get("family") in ("dual_row", "quad_flat") and _whole(package.get("count"), 1):
        numbers += [str(n) for n in range(1, package["count"] + 1)]
        if "thermal_pad_mm" in package:
            numbers.append(_pin(package.get("thermal_pin", package["count"] + 1)) or "?")
    elif package.get("family") == "pads":
        for pad in data.get("pads") or []:
            if isinstance(pad, dict) and _pin(pad.get("number")):
                numbers.append(_pin(pad["number"]))
    return numbers


def _order(number: str):
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


def pad_numbers(data: dict) -> list[str]:
    """The pads the package makes, in pin order."""
    return sorted(set(_raw_pads(data)), key=_order)


def unknowns(data: dict) -> list[str]:
    """What the datasheet did not state, as `<table>: <note>`."""
    return [f"{name}: {table.get('note', '').strip()}"
            for name, table in _tables(data) if name and table.get("unknown") is True]


def problems(data: dict) -> list[str]:
    """Every problem with a spec, or [] when it can be built from."""
    out = []
    for field in ("mpn", "manufacturer", "datasheet", "library"):
        if not (isinstance(data.get(field), str) and data[field].strip()):
            out.append(f"missing {field} (text)")
    if isinstance(data.get("library"), str) and data["library"].strip() \
            and not _LIBRARY.match(data["library"]):
        out.append(f"library {data['library']!r} must be a plain name (letters, digits, _ . -); "
                   f"it becomes fp/<library>.pretty/")
    if "name" in data and not (isinstance(data["name"], str) and _NAME.match(data["name"])):
        out.append(f"name {data['name']!r} must be a plain name (letters, digits, _ . -)")
    if "reference" in data and not (isinstance(data["reference"], str)
                                    and _REFERENCE.match(data["reference"])):
        out.append(f"reference {data['reference']!r} must be 1-4 capital letters, like J or U")
    if "verified" in data and not (isinstance(data["verified"], str) and data["verified"].strip()):
        out.append("verified must say who checked the part against the datasheet, and when")
    if isinstance(data.get("datasheet"), str) and data["datasheet"].strip():
        try:
            if not paths.inside(data["datasheet"]).is_file():
                out.append(f"datasheet: no such file in the project: {data['datasheet']}")
        except ValueError as error:
            out.append(f"datasheet: {error}")

    for name, table in _tables(data):
        if name and table.get("unknown") is True and not str(table.get("note", "")).strip():
            out.append(f"{name}: unknown = true needs a note saying what was assumed")

    package = data.get("package")
    if not isinstance(package, dict):
        out.append("missing package (a table: family, view, source)")
        package = {}
    else:
        family = package.get("family")
        if family in LATER:
            out.append(f"package.family {family!r} is not supported yet "
                       f"({' and '.join(LATER)} come in a later phase); use 'pads' for now")
        elif family not in FAMILIES:
            out.append(f"package.family {family!r} is not one of {', '.join(FAMILIES)}"
                       + (f" ({', '.join(LATER)} later)" if LATER else ""))
        if package.get("view") not in VIEWS:
            out.append("package.view must be 'top' or 'bottom': the view the drawing is in, "
                       "since top and bottom views are mirror images")
        _sourced(package, "package", out)
        if family == "circular":
            rings = package.get("rings")
            if not (isinstance(rings, list) and rings):
                out.append("package.rings: a circular package needs at least one ring")
            for i, ring in enumerate(rings or [], start=1):
                _ring(ring if isinstance(ring, dict) else {}, f"package.rings[{i}]", out)
            for i, slot in enumerate(package.get("slots") or [], start=1):
                name, slot = f"package.slots[{i}]", slot if isinstance(slot, dict) else {}
                if _pin(slot.get("pin")) is None:
                    out.append(f"{name}: pin is required")
                if not _pair(slot.get("at_mm")):
                    out.append(f"{name}: at_mm must be [x, y] in mm")
                if not _pair(slot.get("size_mm"), positive=True):
                    out.append(f"{name}: size_mm must be [width, height] in mm, both above 0")
                _sourced(slot, name, out)
        elif family in ("dual_row", "quad_flat"):
            _rows(package, family, out)
        elif family == "pads":
            pads = data.get("pads")
            if not (isinstance(pads, list) and pads):
                out.append("pads: an explicit pad list needs at least one pad")
            for i, pad in enumerate(pads or [], start=1):
                _pad(pad if isinstance(pad, dict) else {}, f"pads[{i}]", out)

    pins = data.get("pins")
    shared = isinstance(data.get("pins_source"), str) and data["pins_source"].strip()
    if not (isinstance(pins, list) and pins):
        out.append("missing pins (one [[pins]] per pin: number, name, type)")
        pins = []
    numbers = []
    for i, pin in enumerate(pins, start=1):
        name, pin = f"pins[{i}]", pin if isinstance(pin, dict) else {}
        number = _pin(pin.get("number"))
        if number is None:
            out.append(f"{name}: number is required")
        else:
            numbers.append(number)
        if not (isinstance(pin.get("name"), str) and pin["name"].strip()):
            out.append(f"{name}: name is required")
        if pin.get("type") not in PIN_TYPES:
            out.append(f"{name}: type {pin.get('type')!r} is not one of {', '.join(PIN_TYPES)}")
        if not shared and not (isinstance(pin.get("source"), str) and pin["source"].strip()):
            out.append(f"{name}: no source, and no pins_source for the whole table")

    if isinstance(data.get("fab"), dict):
        fab = data["fab"]
        if "min_lead_mm" in fab and not _number(fab["min_lead_mm"]):
            out.append(f"fab: min_lead_mm must be a number above 0 (got {fab['min_lead_mm']!r})")
        _sourced(fab, "fab", out)

    raw = _raw_pads(data)
    for number in sorted({n for n in raw if raw.count(n) > 1}, key=_order):
        out.append(f"pad {number} is numbered twice")
    for number in sorted({n for n in numbers if numbers.count(n) > 1}, key=_order):
        out.append(f"pin {number} is listed twice")
    pads, pinset = set(raw), set(numbers)
    if raw and numbers:
        for number in sorted(pads - pinset, key=_order):
            out.append(f"pad {number} has no pin")
        for number in sorted(pinset - pads, key=_order):
            out.append(f"pin {number} has no pad")
    return out


def awaiting(netlist_text: str) -> list[str]:
    """The parts on this board that make_part built and no human has verified,
    one Markdown line each, with what the datasheet did not state.

    Datasheet-parts design §5.4. Listed by check_done and the README next to
    the nets awaiting a decision; they do not block DONE (§10.3), and a human
    clears one by setting `verified = "<name>, <date>"` in its spec.
    """
    try:
        folder = paths.inside("parts")
    except ValueError:
        return []
    out = []
    for path in sorted(folder.glob("*.part.toml")) if folder.is_dir() else []:
        spec, problems = load(paths.rel(path)) if path.is_file() else (None, ["?"])
        if spec is None or problems or spec.get("verified"):
            continue
        used = footprint_id(spec)
        if f'(footprint "{used}")' not in netlist_text:
            continue
        doubts = unknowns(spec) or ["not yet checked against the datasheet by a person"]
        out.append(f"- **{used}** ({paths.rel(path)}): " + "; ".join(doubts))
    return out
