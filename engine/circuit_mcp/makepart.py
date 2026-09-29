"""make_part: a footprint computed from a part spec, and checked before it is
written.

The datasheet-parts design (§5.2, §6). The model has read the datasheet and
written the spec (circuit_mcp.partspec); this does the rest, and none of it
is the model's: pad positions, pad and hole sizes, courtyard, silkscreen and
fab outlines, and every check -- the fab's drills, annular rings and
clearances, and whether the pads can be routed out at all.

**Why routability is checked here.** On the live run of 2026-09-28 the ODU
receptacle's footprint was hand-drawn, placed and routed, and only then did
DRC report its five inner contacts unconnected. Between two outer drills
there is 0.631 mm; one track needs 0.10 + 2 x 0.28 mm there (JLCPCB's PTH
hole-to-copper clearance). That is arithmetic on the spec, so it is refused
here, with the numbers, before any layout.

**Sizes.** A datasheet's recommended land pattern wins wherever the spec
gives it (drill_mm, pad_mm, an explicit pad list). What it leaves out is
IPC nominal (density level B; land_patterns.toml), and a datasheet value
tighter than nominal is reported, not overridden.

It writes the part's symbol too (§5.2, step 3), into symbols/<library>.kicad_sym
beside the footprint: supply pins on top, ground at the bottom, the rest down
the sides in pin order.

Writes only on success, and only over files make_part wrote itself: a
hand-drawn footprint (the live run's) or a symbol library someone else made is
never replaced.
"""
import math
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import tomllib

from circuit_mcp import fab, partspec, paths, service

GENERATOR = "etchwork-make-part"
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_LAND = tomllib.loads(pathlib.Path(__file__).with_name("land_patterns.toml").read_text())
_EPS = 1e-9


def land(key: str) -> float:
    return _LAND["nominal"][key]["value"]


def pad_allowance() -> float:
    """Pad diameter over hole diameter at IPC nominal: two minimum annular
    rings plus the fabrication allowance (hole + 0.60 mm)."""
    return 2 * land("annular_ring_mm") + land("fabrication_allowance_mm")


# --- geometry --------------------------------------------------------------------

def pads(spec: dict) -> list[dict]:
    """Every pad, in footprint coordinates: mm, y down, seen from the top.

    A drawing in bottom view is mirrored (x -> -x): top and bottom views are
    mirror images, and a footprint is always drawn from the top.
    """
    package = spec["package"]
    out = []
    if package["family"] == "circular":
        for index, ring in enumerate(package.get("rings") or [], start=1):
            radius, count = ring["diameter_mm"] / 2, ring["count"]
            step = 360 / count * (-1 if ring["direction"] == "cw" else 1)
            drill = ring.get("drill_mm")
            size = ring.get("pad_mm") or (drill + pad_allowance())
            for k in range(count):
                angle = math.radians(ring["start_deg"] + k * step)
                out.append({"number": str(ring["first_pin"] + k),
                            "x": radius * math.cos(angle), "y": -radius * math.sin(angle),
                            "shape": "circle", "size": (size, size), "drill": drill,
                            "ring": index, "defaulted": "pad_mm" not in ring})
        for slot in package.get("slots") or []:
            w, h = slot["size_mm"]
            out.append({"number": str(slot["pin"]), "x": slot["at_mm"][0], "y": slot["at_mm"][1],
                        "shape": "oval", "drill": (w, h),
                        "size": (w + pad_allowance(), h + pad_allowance()),
                        "ring": None, "defaulted": True})
    elif package["family"] in ("dual_row", "quad_flat"):
        out += _row_pads(package)
    else:
        for pad in spec.get("pads") or []:
            w, h = pad["size_mm"]
            drill = pad.get("drill_mm")
            shape = pad.get("shape") or ("circle" if drill and w == h else "rect")
            out.append({"number": str(pad["number"]), "x": pad["at_mm"][0], "y": pad["at_mm"][1],
                        "shape": shape, "size": (w, h), "drill": drill,
                        "ring": None, "defaulted": False})
    for pad in out:
        if package["view"] == "bottom":
            pad["x"] = -pad["x"]
        pad["x"], pad["y"] = round(pad["x"], 4) + 0.0, round(pad["y"], 4) + 0.0
        if pad["number"] == "1" and pad["shape"] == "circle":
            pad["shape"] = "rect"           # KiCad's convention: pin 1 is the square pad
    return sorted(out, key=lambda p: partspec._order(p["number"]))


def _goals(package: dict) -> dict:
    kind = ("no_lead" if package["leads"] == "no_lead" else
            "gull_wing_large_pitch" if package["pitch_mm"] > 0.625 else "gull_wing_small_pitch")
    return {"kind": kind, **_LAND["nominal"][kind]}


def _round(value: float) -> float:
    step = land("round_mm")
    return round(round(value / step) * step, 4)


def lead_lands(package: dict, axis: str = "x") -> dict:
    """IPC-7351B's Z, G, X for one pair of opposite sides, from the leads.

    `axis` "x" uses span_mm (the left and right rows), "y" span_y_mm (top and
    bottom, defaulting to span_mm). G is widened to keep the exposed pad's
    clearance from the pads that face it.
    """
    goals = _goals(package)
    f, p = land("manufacturing_tolerance_mm"), land("placement_tolerance_mm")
    outer = partspec.span(package["span_y_mm"] if axis == "y" and "span_y_mm" in package
                          else package["span_mm"])
    length = partspec.span(package["lead_length_mm"])
    width = partspec.span(package["lead_width_mm"])
    cl, ct, cw = outer[1] - outer[0], length[1] - length[0], width[1] - width[0]
    z = outer[0] + 2 * goals["toe_mm"] + math.sqrt(cl ** 2 + f ** 2 + p ** 2)
    s_min, s_max = outer[0] - 2 * length[1], outer[1] - 2 * length[0]
    cs = math.sqrt(cl ** 2 + 2 * ct ** 2)
    s_max_rms = s_max - ((s_max - s_min) - cs) / 2
    g = s_max_rms - 2 * goals["heel_mm"] - math.sqrt(cs ** 2 + f ** 2 + p ** 2)
    x = width[0] + 2 * goals["side_mm"] + math.sqrt(cw ** 2 + f ** 2 + p ** 2)
    z, g, x = _round(z), _round(g), _round(x)
    widened = False
    if "thermal_pad_mm" in package:
        facing = package["thermal_pad_mm"][0 if axis == "x" else 1]
        least = facing + 2 * land("exposed_pad_clearance_mm")
        if g < least - _EPS:
            g, widened = round(least, 4), True
    return {"z": z, "g": g, "x": x, "kind": goals["kind"], "widened": widened,
            "length": round((z - g) / 2, 4), "spacing": round((z + g) / 2, 4)}


def _row_pads(package: dict) -> list[dict]:
    """dual_row and quad_flat, KiCad's convention: pin 1 at the top of the left
    side, numbered counter-clockwise; each land's length runs across its side."""
    if "leads" in package:
        across, down = lead_lands(package, "x"), lead_lands(package, "y")
        package = {**package, "row_spacing_mm": across["spacing"],
                   "column_spacing_mm": down["spacing"],
                   "pad_mm": [across["length"], across["x"]],
                   "pad_y_mm": [down["length"], down["x"]]}
    count, pitch = package["count"], package["pitch_mm"]
    drill = package.get("drill_mm")
    size = package.get("pad_mm")
    defaulted = size is None
    if defaulted:
        size = drill + pad_allowance()
    length, width = (size, size) if isinstance(size, (int, float)) else size
    shape = ("circle" if length == width else "oval") if drill else "roundrect"

    length_y, width_y = package.get("pad_y_mm", (length, width))

    def pad(number, x, y, across):
        w, h = (length, width) if across else (width_y, length_y)
        return {"number": str(number), "x": x, "y": y, "shape": shape, "size": (w, h),
                "drill": drill, "ring": None, "defaulted": defaulted}

    def along(n, i):
        return (i - (n - 1) / 2) * pitch

    cx = package["row_spacing_mm"] / 2
    out = []
    if package["family"] == "dual_row":
        half = count // 2
        out += [pad(1 + i, -cx, along(half, i), True) for i in range(half)]
        out += [pad(half + 1 + i, cx, -along(half, i), True) for i in range(half)]
    else:
        lr = package.get("pins_lr") or count // 4
        tb = (count - 2 * lr) // 2
        cy = package.get("column_spacing_mm", package["row_spacing_mm"]) / 2
        out += [pad(1 + i, -cx, along(lr, i), True) for i in range(lr)]
        out += [pad(lr + 1 + i, along(tb, i), cy, False) for i in range(tb)]
        out += [pad(lr + tb + 1 + i, cx, -along(lr, i), True) for i in range(lr)]
        out += [pad(2 * lr + tb + 1 + i, -along(tb, i), -cy, False) for i in range(tb)]
    if "thermal_pad_mm" in package:
        w, h = package["thermal_pad_mm"]
        out.append({"number": str(package.get("thermal_pin", count + 1)), "x": 0.0, "y": 0.0,
                    "shape": "rect", "size": (w, h), "drill": None, "ring": None,
                    "defaulted": False, "thermal": True})
    return out


# An exposed pad's paste, as KiCad's own QFN footprints have it: cells of at
# most PASTE_CELL mm each way, each opening PASTE_COVERAGE of its cell's area
# -- less solder under the pad, so the part does not float and voids stay
# small. Matched pad for pad against QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm.
PASTE_CELL = 1.2
PASTE_COVERAGE = 0.66


def paste(spec: dict) -> list[dict]:
    """The paste-only openings over each exposed pad."""
    out = []
    for pad in pads(spec):
        if not pad.get("thermal"):
            continue
        w, h = pad["size"]
        nx, ny = math.ceil(w / PASTE_CELL - _EPS), math.ceil(h / PASTE_CELL - _EPS)
        cw, ch = w / nx, h / ny
        side = math.sqrt(PASTE_COVERAGE)
        for i in range(nx):
            for j in range(ny):
                out.append({"x": round(pad["x"] - w / 2 + cw * (i + 0.5), 4) + 0.0,
                            "y": round(pad["y"] - h / 2 + ch * (j + 0.5), 4) + 0.0,
                            "size": (round(cw * side, 2), round(ch * side, 2))})
    return out


def _box(pad) -> tuple[float, float, float, float]:
    w, h = pad["size"]
    return pad["x"] - w / 2, pad["y"] - h / 2, pad["x"] + w / 2, pad["y"] + h / 2


def courtyard(spec: dict, pads_: list[dict]) -> tuple[float, float, float, float]:
    """The pads' extent plus IPC-7351's nominal courtyard excess."""
    boxes = [_box(p) for p in pads_]
    excess = land("courtyard_excess_mm")
    return (min(b[0] for b in boxes) - excess, min(b[1] for b in boxes) - excess,
            max(b[2] for b in boxes) + excess, max(b[3] for b in boxes) + excess)


def _gap(a, b) -> float:
    """Edge-to-edge distance between two pads' copper: circles as circles,
    everything else as its bounding rectangle (never smaller than the pad)."""
    if a["shape"] == "circle" and b["shape"] == "circle":
        return math.hypot(a["x"] - b["x"], a["y"] - b["y"]) - (a["size"][0] + b["size"][0]) / 2
    if a["shape"] == "circle" or b["shape"] == "circle":
        circle, other = (a, b) if a["shape"] == "circle" else (b, a)
        x1, y1, x2, y2 = _box(other)
        dx = max(x1 - circle["x"], 0, circle["x"] - x2)
        dy = max(y1 - circle["y"], 0, circle["y"] - y2)
        return math.hypot(dx, dy) - circle["size"][0] / 2
    ax1, ay1, ax2, ay2 = _box(a)
    bx1, by1, bx2, by2 = _box(b)
    dx, dy = max(bx1 - ax2, ax1 - bx2, 0), max(by1 - ay2, ay1 - by2, 0)
    return math.hypot(dx, dy) if dx or dy else -min(ax2 - bx1, bx2 - ax1, ay2 - by1, by2 - ay1)


def _drill_gap(a, b) -> float:
    def reach(pad):
        d = pad["drill"]
        return (max(d) if isinstance(d, tuple) else d) / 2
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"]) - reach(a) - reach(b)


# --- checks ----------------------------------------------------------------------

def _mm(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value - round(value, 2)) > 1e-6 \
        else f"{value:.2f}"


def check(spec: dict, pads_: list[dict], profile: str = fab.PROFILE) -> tuple[list[str], list[str]]:
    """`(problems, notes)`: a problem refuses the part; a note is reported."""
    problems, notes = [], []
    clearance, track = fab.value("clearance_mm", profile), fab.value("track_width_mm", profile)
    hole_clearance = fab.value("pth_hole_clearance_mm", profile)
    min_drill, ring_min = fab.value("via_drill_mm", profile), fab.value("annular_ring_mm", profile)
    hole_to_hole = fab.value("hole_to_hole_mm", profile)
    lead = (spec.get("fab") or {}).get("min_lead_mm")
    tht = [p for p in pads_ if p["drill"] is not None]

    for drill in sorted({p["drill"] for p in tht if not isinstance(p["drill"], tuple)}):
        numbers = [p["number"] for p in tht if p["drill"] == drill]
        which = f"pads {numbers[0]}–{numbers[-1]}" if len(numbers) > 1 else f"pad {numbers[0]}"
        if drill < min_drill - _EPS:
            problems.append(f"{which}: drill {_mm(drill)} mm is below {_mm(min_drill)} mm, the "
                            f"smallest {profile} drills at the standard price")
        if lead is not None:
            nominal = lead + land("hole_allowance_mm")
            if drill <= lead + _EPS:
                problems.append(f"{which}: drill {_mm(drill)} mm is no wider than the {_mm(lead)} mm "
                                f"lead; the pin will not go in")
            elif drill < nominal - _EPS:
                notes.append(f"{which}: drill {_mm(drill)} mm is tighter than IPC-7251 nominal for a "
                             f"{_mm(lead)} mm lead ({_mm(nominal)} mm); the datasheet's value is used")
    for pad in tht:
        d = pad["drill"] if isinstance(pad["drill"], tuple) else (pad["drill"], pad["drill"])
        ring = min(pad["size"][0] - d[0], pad["size"][1] - d[1]) / 2
        if ring < ring_min - _EPS:
            problems.append(f"pad {pad['number']}: annular ring {_mm(ring)} mm is below the fab's "
                            f"{_mm(ring_min)} mm")

    crowded, close = [], []
    for i, a in enumerate(pads_):
        for b in pads_[i + 1:]:
            gap = _gap(a, b)
            if gap < clearance - _EPS:
                crowded.append((a, b, gap))
            if a["drill"] is not None and b["drill"] is not None:
                holes = _drill_gap(a, b)
                if holes < hole_to_hole - _EPS:
                    close.append((a, b, holes))
    for a, b, gap in crowded[:5]:
        why = (f"; their size is IPC-7251 nominal (drill + {_mm(pad_allowance())}): give pad_mm "
               f"from the datasheet's recommended land pattern") if a["defaulted"] or b["defaulted"] else ""
        apart = "overlap" if gap <= _EPS else f"are {_mm(gap)} mm apart"
        problems.append(f"pads {a['number']} and {b['number']} {apart}; the fab needs "
                        f"{_mm(clearance)} between them{why}")
    if len(crowded) > 5:
        problems.append(f"... and {len(crowded) - 5} more pad pairs closer than {_mm(clearance)} mm")
    for a, b, holes in close[:5]:
        apart = "their drills overlap" if holes <= _EPS else f"{_mm(holes)} mm from drill to drill"
        problems.append(f"pads {a['number']} and {b['number']}: {apart}; the fab needs "
                        f"{_mm(hole_to_hole)} from drill to drill")

    problems += _routability(spec, pads_, track, clearance, hole_clearance)
    return problems, notes


def _routability(spec, pads_, track, clearance, hole_clearance) -> list[str]:
    """Can the pads inside a through-hole ring be routed out between its pads?

    A through-hole pad blocks every copper layer, so on a 2-layer board a pad
    inside a ring leaves only through the gaps between the ring's pads. Per
    gap, k tracks fit when both hold:

        copper:  k*track + (k+1)*clearance      <= pitch - pad
        holes:   k*track + (k-1)*clearance + 2*hole_clearance <= pitch - drill
    """
    package = spec["package"]
    if package["family"] != "circular":
        return []
    rings = sorted(enumerate(package.get("rings") or [], start=1),
                   key=lambda r: r[1]["diameter_mm"], reverse=True)
    out = []
    for index, ring in rings:
        if ring.get("drill_mm") is None or ring["count"] < 2:
            continue
        inside = sum(r["count"] for _, r in rings if r["diameter_mm"] < ring["diameter_mm"])
        if not inside:
            continue
        pad = next(p for p in pads_ if p["ring"] == index)
        pitch = ring["diameter_mm"] * math.sin(math.pi / ring["count"])
        drill, size = ring["drill_mm"], pad["size"][0]
        by_copper = math.floor((pitch - size - clearance) / (track + clearance) + _EPS)
        by_holes = math.floor((pitch - drill - 2 * hole_clearance + clearance) / (track + clearance) + _EPS)
        capacity = max(0, min(by_copper, by_holes)) * ring["count"]
        if capacity >= inside:
            continue
        if by_holes <= by_copper:
            limit = (f"{_mm(pitch - drill)} mm drill to drill; one {_mm(track)} mm track needs "
                     f"{_mm(track)} + 2 × {_mm(hole_clearance)} (the fab's hole-to-copper clearance) "
                     f"= {_mm(track + 2 * hole_clearance)} mm there")
        else:
            limit = (f"{_mm(pitch - size)} mm pad to pad; one {_mm(track)} mm track needs "
                     f"{_mm(track)} + 2 × {_mm(clearance)} (clearance) = {_mm(track + 2 * clearance)} mm")
        out.append(f"the {inside} pads inside ring {index} (Ø{_mm(ring['diameter_mm'])} mm) cannot be "
                   f"routed out: its neighbouring pads are {_mm(pitch)} mm apart centre to centre, "
                   f"{limit}. Room for {capacity} track(s), {inside} needed. The ways out are the "
                   f"human's call: smaller pads or holes if the datasheet allows them, via-in-pad, "
                   f"more layers, or a finer fab tier -- never loosened rules")
    return out


# --- the symbol --------------------------------------------------------------------

_GROUND = re.compile(r"^(A|D|P|S)?GND\d*$|^VSS\w*$|^GROUND$|^0V$|^VEE$", re.I)
_GRID = 2.54


def _pin_line(pin, x, y, angle) -> str:
    font = "(effects (font (size 1.27 1.27)))"
    return (f'\t\t\t(pin {pin["type"]} line (at {_f(x)} {_f(y)} {angle}) (length {_GRID}) '
            f'(name "{pin["name"]}" {font}) (number "{partspec._pin(pin["number"])}" {font}))')


def symbol_text(spec: dict, name: str) -> str:
    """The part's symbol as one top-level block of a `.kicad_sym`."""
    pins = sorted(spec["pins"], key=lambda p: partspec._order(partspec._pin(p["number"])))
    ground = [p for p in pins if _GROUND.match(p["name"])]
    supply = [p for p in pins if p["type"] in ("power_in", "power_out") and p not in ground]
    rest = [p for p in pins if p not in ground and p not in supply]
    half = math.ceil(len(rest) / 2)
    left, right = rest[:half], rest[half:]
    rows = max(len(left), len(right), 1)
    # Top and bottom pins' names are drawn inside the body, running down from
    # the edge: the side pins start below them, so the two never overlap.
    def room(row):
        return math.ceil((max(len(p["name"]) for p in row) * 1.0 + 1.5) / _GRID) if row else 0
    above, below = room(supply), room(ground)
    total = above + rows + below
    top_y = math.ceil((total + 1) / 2) * _GRID
    bottom_y = top_y - (total + 1) * _GRID
    longest = max([len(p["name"]) for p in left] + [0]) + max([len(p["name"]) for p in right] + [0])
    across = max(len(supply), len(ground), 1) * _GRID
    width = max(3 * _GRID, math.ceil((longest * 1.0 + 2 * _GRID) / (2 * _GRID)) * 2 * _GRID,
                math.ceil((across + _GRID) / (2 * _GRID)) * 2 * _GRID)
    edge = width / 2
    font = "(effects (font (size 1.27 1.27)))"
    hidden = "(effects (font (size 1.27 1.27)) (hide yes))"
    out = [f'\t(symbol "{name}"',
           '\t\t(pin_names (offset 1.016))',
           '\t\t(exclude_from_sim no) (in_bom yes) (on_board yes)',
           f'\t\t(property "Reference" "{spec.get("reference", "U")}" (at 0 {_f(top_y + 2 * _GRID)} 0) {font})',
           f'\t\t(property "Value" "{spec["mpn"]}" (at 0 {_f(bottom_y - 2 * _GRID)} 0) {font})',
           f'\t\t(property "Footprint" "{spec["library"]}:{name}" (at 0 0 0) {hidden})',
           f'\t\t(property "Datasheet" "{spec.get("datasheet", "")}" (at 0 0 0) {hidden})',
           f'\t\t(property "Description" "{spec.get("description", spec["mpn"])}" (at 0 0 0) {hidden})',
           f'\t\t(symbol "{name}_0_1"',
           f'\t\t\t(rectangle (start {_f(-edge)} {_f(top_y)}) (end {_f(edge)} {_f(bottom_y)}) '
           f'(stroke (width 0.254) (type default)) (fill (type background)))',
           '\t\t)',
           f'\t\t(symbol "{name}_1_1"']
    for i, pin in enumerate(left):
        out.append(_pin_line(pin, -edge - _GRID, top_y - (above + i + 1) * _GRID, 0))
    for i, pin in enumerate(right):
        out.append(_pin_line(pin, edge + _GRID, top_y - (above + i + 1) * _GRID, 180))
    for row, y, angle in ((supply, top_y + _GRID, 270), (ground, bottom_y - _GRID, 90)):
        for i, pin in enumerate(row):
            out.append(_pin_line(pin, (i - (len(row) - 1) / 2) * _GRID, y, angle))
    out += ['\t\t)', '\t)']
    return "\n".join(out)


def _top_level_symbols(text: str) -> list[tuple[str, str]]:
    """`(name, block)` for each top-level `(symbol ...)` of a library file,
    read to its closing parenthesis (quoted strings skipped)."""
    out = []
    for opener in re.finditer(r'^\t\(symbol "([^"]+)"', text, re.M):
        depth, i, quoted = 0, opener.start() + 1, False
        while i < len(text):
            c = text[i]
            if quoted:
                if c == "\\":
                    i += 1
                elif c == '"':
                    quoted = False
            elif c == '"':
                quoted = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append((opener.group(1), text[opener.start():i + 1]))
    return out


def library_text(existing: str | None, name: str, block: str) -> str:
    """The library with `block` in place of `name`'s symbol, or added."""
    symbols = _top_level_symbols(existing) if existing else []
    if any(n == name for n, _ in symbols):
        symbols = [(n, block if n == name else b) for n, b in symbols]
    else:
        symbols.append((name, block))
    return "\n".join(["(kicad_symbol_lib", "\t(version 20241209)", f'\t(generator "{GENERATOR}")',
                      '\t(generator_version "1")', *(b for _, b in symbols), ")"]) + "\n"


def _ours(path: pathlib.Path) -> str | None:
    """None when make_part may write `path`, else why not."""
    if path.is_symlink():
        return f"{paths.rel(path.parent)}/{path.name} is a symlink; make_part never writes through one"
    if path.exists() and f'generator "{GENERATOR}"' not in path.read_text(errors="replace")[:400]:
        return (f"{paths.rel(path)} exists and was not made by make_part; move it or give the "
                f"spec another library")
    return None


# --- the footprint file ------------------------------------------------------------

def _f(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def footprint_text(spec: dict, name: str, pads_: list[dict], source: str) -> str:
    x1, y1, x2, y2 = courtyard(spec, pads_)
    excess = land("courtyard_excess_mm")
    fx1, fy1, fx2, fy2 = x1 + excess, y1 + excess, x2 - excess, y2 - excess
    sx1, sy1, sx2, sy2 = fx1 - 0.12, fy1 - 0.12, fx2 + 0.12, fy2 + 0.12
    smd_only = all(p["drill"] is None for p in pads_)
    font = "(effects (font (size 1 1) (thickness 0.15)))"
    first = next((p for p in pads_ if p["number"] == "1"), pads_[0])
    mark_y = sy1 - 0.5 if first["y"] <= (fy1 + fy2) / 2 else sy2 + 0.5
    lines = [
        f'(footprint "{name}" (version 20240108) (generator "{GENERATOR}") (layer "F.Cu")',
        f'  (descr "{spec.get("description", spec["mpn"])}; {spec.get("manufacturer", "")} '
        f'{spec["mpn"]}; made by make_part from {source}")',
        f'  (attr {"smd" if smd_only else "through_hole"})',
        f'  (fp_text reference "REF**" (at 0 {_f(sy1 - 1.5)}) (layer "F.SilkS") {font})',
        f'  (fp_text value "{spec["mpn"]}" (at 0 {_f(sy2 + 1.5)}) (layer "F.Fab") {font})',
        f'  (fp_text user "${{REFERENCE}}" (at 0 0) (layer "F.Fab") {font})',
        f'  (fp_rect (start {_f(x1)} {_f(y1)}) (end {_f(x2)} {_f(y2)}) '
        f'(stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))',
        f'  (fp_rect (start {_f(fx1)} {_f(fy1)}) (end {_f(fx2)} {_f(fy2)}) '
        f'(stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab"))',
        f'  (fp_rect (start {_f(sx1)} {_f(sy1)}) (end {_f(sx2)} {_f(sy2)}) '
        f'(stroke (width 0.12) (type solid)) (fill none) (layer "F.SilkS"))',
        f'  (fp_circle (center {_f(first["x"])} {_f(mark_y)}) (end {_f(first["x"] + 0.25)} {_f(mark_y)}) '
        f'(stroke (width 0.12) (type solid)) (fill solid) (layer "F.SilkS"))',
    ]
    for p in pads_:
        at, size = f'(at {_f(p["x"])} {_f(p["y"])})', f'(size {_f(p["size"][0])} {_f(p["size"][1])})'
        if p.get("thermal"):
            # Copper and mask; its paste is the openings below.
            lines.append(f'  (pad "{p["number"]}" smd rect {at} {size} (layers "F.Cu" "F.Mask"))')
        elif p["drill"] is None:
            extra = " (roundrect_rratio 0.25)" if p["shape"] == "roundrect" else ""
            lines.append(f'  (pad "{p["number"]}" smd {p["shape"]} {at} {size} '
                         f'(layers "F.Cu" "F.Paste" "F.Mask"){extra})')
        else:
            drill = (f'(drill oval {_f(p["drill"][0])} {_f(p["drill"][1])})'
                     if isinstance(p["drill"], tuple) else f'(drill {_f(p["drill"])})')
            extra = " (roundrect_rratio 0.25)" if p["shape"] == "roundrect" else ""
            lines.append(f'  (pad "{p["number"]}" thru_hole {p["shape"]} {at} {size} {drill} '
                         f'(layers "*.Cu" "*.Mask"){extra})')
    for opening in paste(spec):
        lines.append(f'  (pad "" smd roundrect (at {_f(opening["x"])} {_f(opening["y"])}) '
                     f'(size {_f(opening["size"][0])} {_f(opening["size"][1])}) (layers "F.Paste") '
                     f'(roundrect_rratio 0.25))')
    return "\n".join(lines) + "\n)\n"


def _render(command: list[str], target: pathlib.Path) -> str | None:
    """Run a kicad-cli export into a temp folder and keep its SVG as `target`;
    why not, or None."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            done = subprocess.run([*command[:-1], "-o", tmp, command[-1]],
                                  capture_output=True, text=True, timeout=service.TIMEOUT)
        except subprocess.TimeoutExpired:
            return "the render timed out"
        made = next(iter(sorted(pathlib.Path(tmp).glob("*.svg"))), None)
        if done.returncode != 0 or made is None:
            return f"the render failed: {(done.stdout + done.stderr).strip()[-200:]}"
        if target.is_symlink():
            target.unlink()
        shutil.copyfile(made, target)
    return None


def make_part(spec_path: str) -> str:
    spec, problems = partspec.load(spec_path)
    if spec is None or problems:
        return "REFUSED:\n" + "\n".join(f"- {p}" for p in problems)
    name = partspec.footprint_name(spec)
    if not _NAME.match(name):
        return f"REFUSED:\n- name {name!r} must be a plain name (letters, digits, _ . -)"
    try:
        library = paths.inside(f"fp/{spec['library']}.pretty")
        target = library / f"{name}.kicad_mod"
        symbols = paths.inside(f"symbols/{spec['library']}.kicad_sym")
        spec_file = paths.inside(spec_path)
        beside = "" if spec_file.parent == paths.WORKSPACE else paths.rel(spec_file.parent) + "/"
        render = paths.inside(f"{beside}{name}.footprint.svg")
        symbol_render = paths.inside(f"{beside}{name}.symbol.svg")
    except ValueError as error:
        return f"REFUSED:\n- {error}"

    pads_ = pads(spec)
    problems, notes = check(spec, pads_)
    if target.is_symlink() or (target.exists() and GENERATOR not in target.read_text(errors="replace")):
        problems.append(f"{paths.rel(target)} exists and was not made by make_part; move it or "
                        f"give the spec another name")
    if (why := _ours(symbols)) is not None:
        problems.append(why)
    if problems:
        return "REFUSED:\n" + "\n".join(f"- {p}" for p in problems)

    library.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=library, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(footprint_text(spec, name, pads_, paths.rel(spec_file)))
    os.replace(tmp, target)
    symbols.parent.mkdir(parents=True, exist_ok=True)
    existing = symbols.read_text(errors="replace") if symbols.is_file() else None
    fd, tmp = tempfile.mkstemp(dir=symbols.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(library_text(existing, name, symbol_text(spec, name)))
    os.replace(tmp, symbols)
    rendered = _render(["kicad-cli", "fp", "export", "svg", "--footprint", name,
                        "--sketch-pads-on-fab-layers", "--layers", "F.Cu,F.SilkS,F.Fab,F.CrtYd",
                        str(library)], render)
    symbol_rendered = _render(["kicad-cli", "sym", "export", "svg", "--symbol", name,
                               str(symbols)], symbol_render)

    package = spec["package"]
    if "leads" in package:
        lands = lead_lands(package)
        notes.insert(0, f"lands computed from the leads, IPC-7351B nominal "
                        f"({lands['kind'].replace('_', ' ')}): Z {_mm(lands['z'])}, G {_mm(lands['g'])}, "
                        f"X {_mm(lands['x'])} mm"
                        + (" -- G widened to keep the exposed pad's clearance" if lands["widened"] else ""))
    tht = [p for p in pads_ if p["drill"] is not None]
    x1, y1, x2, y2 = courtyard(spec, pads_)
    lines = [f"MADE {paths.rel(target)} and its symbol in {paths.rel(symbols)}",
             f"  use it as Part(\"{spec['library']}\", \"{name}\", footprint=\"{spec['library']}:{name}\")",
             f"  {len(pads_)} pads: {len(tht)} through-hole, {len(pads_) - len(tht)} SMD",
             f"  courtyard {_mm(x2 - x1)} x {_mm(y2 - y1)} mm (IPC-7351 nominal excess "
             f"{_mm(land('courtyard_excess_mm'))} mm)",
             f"  checked against {fab.PROFILE}: drills, annular rings, pad and hole clearances"
             + (", routability" if spec["package"]["family"] == "circular" else "")]
    lines.append(f"  render: {paths.rel(render)} -- compare it with the datasheet page, pad numbers "
                 f"and pin 1 included" if rendered is None else f"  no render: {rendered}")
    lines.append(f"  symbol render: {paths.rel(symbol_render)}" if symbol_rendered is None
                 else f"  no symbol render: {symbol_rendered}")
    if notes:
        lines += ["Notes:"] + [f"  - {n}" for n in notes]
    unknown = partspec.unknowns(spec)
    if unknown or not spec.get("verified"):
        lines.append("Parts awaiting your verification (tell the human in the final answer):")
        lines += [f"  - {u}" for u in unknown] or ["  - not yet checked against the datasheet by a person"]
    return "\n".join(lines)
