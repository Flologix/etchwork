"""Board checks, phase B: post-route extraction.

The board-checks design (§5). Every routed net is measured from the board
file -- length, vias, narrowest width -- and a power net's voltage drop is
worked out along its lowest-resistance path from its source (a power_out pin,
else the connector it comes in on) to its farthest load (a power_in pin).
Then each net is checked against its class: a crystal net's length, a
differential pair's mismatch, a rail's drop. DRC's clearance violations are
read for how far short they fell.

Every result warns and none blocks DONE (§3.4): these are rules of thumb
(board_checks.toml), not a signal-integrity sign-off, and the event log's
worst values are what the limits get tuned from.
"""
import heapq
import json
import math
import pathlib
import re
import tomllib
from dataclasses import dataclass

from circuit_mcp import boardgeom, netclass

_DATA = tomllib.loads(pathlib.Path(__file__).with_name("board_checks.toml").read_text())
RESISTIVITY = _DATA["copper"]["resistivity_ohm_m"]["value"]
THICKNESS_MM = _DATA["copper"]["thickness_mm"]["value"]
TIER_CURRENT = {k: v for k, v in _DATA["tier_current_a"].items() if k != "source"}
LIMITS = {k: v["value"] for k, v in _DATA["limits"].items()}
_SHORT = re.compile(r"clearance ([\d.]+) mm; actual ([\d.]+) mm")
_RAIL = re.compile(r"^\+?(\d+)V(\d*)$", re.I)
_PAIR = re.compile(r"^(.*?)(_?)(P|N|\+|-)$", re.I)
_SNAP = 3                          # mm decimals two track ends must share to meet


@dataclass
class NetFacts:
    net: str
    cls: str | None
    tier: str | None
    length: float = 0.0
    vias: int = 0
    narrowest: float | None = None
    path_mohm: float | None = None
    drop_mv: float | None = None
    rail_v: float | None = None
    current_a: float | None = None
    farthest: str | None = None
    note: str = ""


def resistance_mohm(length_mm: float, width_mm: float) -> float:
    """One track's resistance: rho * L / (w * t), in milliohms."""
    return RESISTIVITY * (length_mm / 1000) / ((width_mm / 1000) * (THICKNESS_MM / 1000)) * 1000


def rail_voltage(net: str) -> float | None:
    """`+3V3` 3.3, `+5V` 5.0, `VBUS` 5.0; None when the name does not say."""
    if net.upper() == "VBUS":
        return 5.0
    found = _RAIL.match(net)
    return float(f"{found.group(1)}.{found.group(2) or 0}") if found else None


def _graph(geometry: boardgeom.Geometry, net: str):
    """The net as a graph: track ends, vias and pads as nodes, tracks as
    edges weighted by resistance. A via or a through-hole pad joins every layer."""
    edges: dict[object, list[tuple[object, float]]] = {}

    def link(a, b, weight):
        edges.setdefault(a, []).append((b, weight))
        edges.setdefault(b, []).append((a, weight))

    ends = []
    for s in geometry.segments:
        if s.net != net:
            continue
        a = (round(s.x1, _SNAP), round(s.y1, _SNAP), s.layer)
        b = (round(s.x2, _SNAP), round(s.y2, _SNAP), s.layer)
        link(a, b, resistance_mohm(s.length, s.width) if s.width else math.inf)
        ends += [a, b]
    for i, v in enumerate(geometry.vias):
        if v.net != net:
            continue
        for end in ends:
            if math.hypot(end[0] - v.x, end[1] - v.y) <= v.size / 2 + 1e-6:
                link(("via", i), end, 0.0)
    for p in geometry.pads:
        if p.net != net:
            continue
        for end in ends:
            if ("*" in p.layers or end[2] in p.layers) and \
                    math.hypot(end[0] - p.x, end[1] - p.y) <= p.reach + 1e-6:
                link(("pad", p.ref, p.number), end, 0.0)
    return edges


def _cheapest(edges, start) -> dict:
    """Lowest resistance from `start` to every node (Dijkstra)."""
    best, queue = {start: 0.0}, [(0.0, 0, start)]
    count = 0
    while queue:
        cost, _, node = heapq.heappop(queue)
        if cost > best.get(node, math.inf):
            continue
        for other, weight in edges.get(node, []):
            total = cost + weight
            if total < best.get(other, math.inf):
                best[other] = total
                count += 1
                heapq.heappush(queue, (total, count, other))
    return best


def measure(geometry: boardgeom.Geometry, netlist_text: str, classes: dict) -> dict[str, NetFacts]:
    """Every routed net's facts. `classes` is netclasses.json's `nets`."""
    netlist = netclass.read(netlist_text)
    nodes = {n.name: n.nodes for n in netlist.nets}
    facts: dict[str, NetFacts] = {}
    for s in geometry.segments:
        f = facts.setdefault(s.net, NetFacts(s.net, (classes.get(s.net) or {}).get("class"),
                                             (classes.get(s.net) or {}).get("tier")))
        f.length += s.length
        f.narrowest = s.width if f.narrowest is None else min(f.narrowest, s.width)
    for v in geometry.vias:
        if v.net in facts:
            facts[v.net].vias += 1

    for name, f in facts.items():
        if not netclass._is_power(f.cls) or f.cls == "ground":
            continue
        pins = nodes.get(name, [])
        # SKiDL writes POWER-OUT / POWER-IN, KiCad power_out / power_in.
        kinds = [(n.ref, n.pin, (n.pin_type or "").lower().replace("-", "_")) for n in pins]
        sources = [(r, p) for r, p, t in kinds if t == "power_out"] \
            or [(r, p) for r, p, _ in kinds if r.upper().startswith("J")]
        loads = [(r, p) for r, p, t in kinds if t == "power_in"]
        if name in geometry.poured:
            f.note = "carried by the pour as well as the tracks; drop not computed"
            continue
        if not sources or not loads:
            f.note = "no source or no load pin on the net; drop not computed"
            continue
        edges = _graph(geometry, name)
        reached = [_cheapest(edges, ("pad", r, p)) for r, p in sources]
        worst, where = None, None
        for r, p in loads:
            cost = min((best.get(("pad", r, p), math.inf) for best in reached), default=math.inf)
            if worst is None or cost > worst:
                worst, where = cost, f"{r} pin {p}"
        if worst is None or math.isinf(worst):
            f.note = f"no routed path from the source to {where}; drop not computed"
            continue
        f.path_mohm, f.farthest = worst, where
        f.current_a = TIER_CURRENT.get(f.tier or "", None)
        if f.current_a is not None:
            f.drop_mv = worst * f.current_a
        f.rail_v = rail_voltage(name)
    return facts


def _pairs(facts: dict[str, NetFacts]):
    """Differential pairs among the diff_pair nets, by their P/N or +/- ending."""
    by_base: dict[tuple[str, str], dict[str, NetFacts]] = {}
    for f in facts.values():
        if f.cls != "diff_pair":
            continue
        found = _PAIR.match(f.net)
        if found:
            polarity = "+" if found.group(3).upper() in ("P", "+") else "-"
            by_base.setdefault((found.group(1).upper(), found.group(2)), {})[polarity] = f
    return [(p["+"], p["-"]) for p in by_base.values() if "+" in p and "-" in p]


def warnings(facts: dict[str, NetFacts], drc_report: dict | None) -> list[str]:
    out = []
    for f in sorted(facts.values(), key=lambda f: f.net):
        if f.cls == "crystal" and f.length > LIMITS["crystal_length_mm"]:
            out.append(f"crystal: {f.net} is {f.length:.1f} mm routed; the rule of thumb is "
                       f"{LIMITS['crystal_length_mm']:g} mm -- move the crystal closer to its pins")
        if f.drop_mv is not None and f.rail_v:
            share = f.drop_mv / 1000 / f.rail_v
            if share > LIMITS["power_drop_fraction"]:
                out.append(f"power: {f.net} drops {f.drop_mv:.0f} mV to {f.farthest} at {f.current_a:g} A "
                           f"(its {f.tier} tier's top): {share * 100:.1f} % of {f.rail_v:g} V, over the "
                           f"{LIMITS['power_drop_fraction'] * 100:g} % rule of thumb -- widen the track "
                           f"or shorten the path")
    for a, b in _pairs(facts):
        gap = abs(a.length - b.length)
        if gap > LIMITS["diff_pair_mismatch_mm"]:
            out.append(f"diff pair: {a.net} {a.length:.1f} mm vs {b.net} {b.length:.1f} mm, "
                       f"{gap:.2f} mm apart; the rule of thumb is {LIMITS['diff_pair_mismatch_mm']:g} mm "
                       f"-- route the shorter one longer")
    short = []
    for v in (drc_report or {}).get("violations", []):
        found = _SHORT.search(v.get("description", ""))
        if v.get("type") == "clearance" and found:
            items = " / ".join(i.get("description", "?") for i in v.get("items", []))
            short.append((float(found.group(1)) - float(found.group(2)), items))
    if short:
        worst, items = max(short)
        out.append(f"clearance: {len(short)} violation(s), worst {worst:.3f} mm short ({items})")
    return out


# --- phase A: placement rules (design §4) -------------------------------------------

def _distance(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _overlap(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def placement(geometry: boardgeom.Geometry, netlist_text: str) -> list[str]:
    """The placed board against the placement rules: decoupling and crystal
    proximity, connectors on an edge, nothing inside a keepout."""
    netlist = netclass.read(netlist_text)
    pads = {(p.ref, p.number): p for p in geometry.pads}
    counts: dict[str, int] = {}
    for p in geometry.pads:
        counts[p.ref] = counts.get(p.ref, 0) + 1
    out = []

    # Decoupling: a 2-pin capacitor between a supply and ground, and the
    # nearest pin that supply serves or comes from: an IC's power input, a
    # regulator's output, or the connector it enters on. Measured to power
    # inputs alone, a regulator's output cap beside its VO pin read 38 mm
    # from the nearest load (live board, 2026-09-29).
    nets_of: dict[str, dict[str, str]] = {}
    power_pins: dict[str, list[tuple[str, str]]] = {}
    for net in netlist.nets:
        for node in net.nodes:
            nets_of.setdefault(node.ref, {})[node.pin] = net.name
            kind = (node.pin_type or "").lower().replace("-", "_")
            chip = kind in ("power_in", "power_out") and counts.get(node.ref, 0) > 2
            if chip or node.ref.upper().startswith("J"):
                power_pins.setdefault(net.name, []).append((node.ref, node.pin))
    for ref, pins in sorted(nets_of.items()):
        if not ref.upper().startswith("C") or len(pins) != 2:
            continue
        names = list(pins.items())
        grounded = [netclass._is_ground_name(n) for _, n in names]
        if grounded.count(True) != 1:
            continue
        pin, supply = names[grounded.index(False)]
        cap_pad = pads.get((ref, pin))
        targets = [pads[t] for t in power_pins.get(supply, []) if t in pads]
        if cap_pad is None or not targets:
            continue
        nearest = min(targets, key=lambda t: _distance(cap_pad, t))
        gap = _distance(cap_pad, nearest)
        if gap > LIMITS["decoupling_mm"]:
            out.append(f"placement: {ref} (decoupling {nearest.ref} pin {nearest.number}, {supply}) is "
                       f"{gap:.1f} mm away; the rule is {LIMITS['decoupling_mm']:g} mm -- move it next to "
                       f"the pin")

    # Crystals: each crystal pad against the IC pin it drives.
    for net in netlist.nets:
        crystal = [n for n in net.nodes if n.ref.upper().startswith(("Y", "X"))]
        chips = [n for n in net.nodes if counts.get(n.ref, 0) > 2 and n not in crystal]
        for c in crystal:
            for chip in chips:
                a, b = pads.get((c.ref, c.pin)), pads.get((chip.ref, chip.pin))
                if a and b and _distance(a, b) > LIMITS["crystal_mm"]:
                    out.append(f"placement: {c.ref} (crystal, {net.name} to {chip.ref} pin {chip.pin}) is "
                               f"{_distance(a, b):.1f} mm away; the rule is {LIMITS['crystal_mm']:g} mm -- "
                               f"move it next to the pins")

    # Connectors: courtyard to the nearest board edge.
    edge = geometry.outline
    for f in geometry.footprints:
        if edge is None or f.courtyard is None:
            continue
        if not (f.library.startswith("Connector") or f.ref.upper().startswith("J")):
            continue
        left, top, right, bottom = f.courtyard
        gap = min(left - edge[0], top - edge[1], edge[2] - right, edge[3] - bottom)
        if gap > LIMITS["connector_edge_mm"]:
            out.append(f"placement: {f.ref} (connector) is {gap:.1f} mm from the nearest board edge; the "
                       f"rule is {LIMITS['connector_edge_mm']:g} mm -- move it to the edge its cable "
                       f"comes from")

    # Keepouts: no other part's courtyard inside one.
    for keepout in geometry.keepouts:
        for f in geometry.footprints:
            if f.courtyard and f.ref != keepout.owner and _overlap(f.courtyard, keepout.box):
                owner = f" ({keepout.owner}'s)" if keepout.owner else ""
                out.append(f"placement: {f.ref} is inside a keepout{owner} -- move it out, or it blocks "
                           f"what the keepout protects (an antenna's clear area, say)")
    return out


# --- phase C: EMC checks (design §6) ---------------------------------------------------

EMC = _DATA["emc"]
_OTHER_LAYER = {"F.Cu": "B.Cu", "B.Cu": "F.Cu"}


def _inside(points, x: float, y: float) -> bool:
    """Ray casting: is (x, y) inside the polygon? A fill's outline is
    fractured by KiCad into one simple polygon, holes included."""
    inside, j = False, len(points) - 1
    for i in range(len(points)):
        (xi, yi), (xj, yj) = points[i], points[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def on_fill(geometry: boardgeom.Geometry, net: str, layer: str, x: float, y: float) -> bool:
    return any(f.net == net and f.layer == layer and f.box[0] <= x <= f.box[2]
               and f.box[1] <= y <= f.box[3] and _inside(f.points, x, y) for f in geometry.fills)


def _fast(net: str, cls: str | None) -> tuple[str, float | None] | None:
    """`(kind, assumed edge time in ns)` for a fast net, or None. Crystals
    and RF are checked for their plane only: their edges are not digital."""
    if cls == "diff_pair":
        return "diff pair", EMC["edge_ns"]["diff_pair"]
    if cls in ("crystal", "rf_50ohm"):
        return cls.replace("_50ohm", ""), None
    words = set(re.split(r"[^A-Z0-9]+", net.upper()))
    for kind in ("clock", "switch_node", "gpio"):
        if words & set(EMC["name_words"][kind]):
            return kind.replace("_", " "), EMC["edge_ns"][kind]
    return None


def emc(geometry: boardgeom.Geometry, netlist_text: str, classes: dict,
        drc_report: dict | None) -> list[str]:
    """The routed board's EMC warnings: fast nets off or across their ground
    plane, or electrically long; stubs and islands from DRC."""
    netlist = netclass.read(netlist_text)
    names = {n.name for n in netlist.nets} | {s.net for s in geometry.segments}
    grounds = [n for n in names if (classes.get(n) or {}).get("class") == "ground"
               or netclass._is_ground_name(n)]
    step = EMC["sample_step_mm"]["value"]
    out = []
    for net in sorted(names):
        fast = _fast(net, (classes.get(net) or {}).get("class"))
        segments = [s for s in geometry.segments if s.net == net and s.layer in _OTHER_LAYER]
        if fast is None or not segments:
            continue
        kind, edge = fast
        covered = total = 0
        widest_gap = 0.0
        for s in segments:
            below = _OTHER_LAYER[s.layer]
            count = max(1, math.ceil(s.length / step))
            run, seen_ground = 0.0, False
            for k in range(count):
                t = (k + 0.5) / count
                x, y = s.x1 + (s.x2 - s.x1) * t, s.y1 + (s.y2 - s.y1) * t
                grounded = any(on_fill(geometry, g, below, x, y) for g in grounds)
                piece = s.length / count
                total += piece
                if grounded:
                    covered += piece
                    if seen_ground and run:
                        widest_gap = max(widest_gap, run)
                    run, seen_ground = 0.0, True
                elif seen_ground:
                    run += piece
        share = covered / total if total else 1.0
        if grounds and share < EMC["plane_coverage"]["value"]:
            out.append(f"emc: {net} runs over ground for {share * 100:.0f} % of its length (the rule of "
                       f"thumb is {EMC['plane_coverage']['value'] * 100:g} %): its return current has no "
                       f"plane to follow -- route it over the ground pour")
        if widest_gap >= EMC["gap_mm"]["value"]:
            out.append(f"emc: {net} crosses a {widest_gap:.1f} mm gap in the ground pour: its return "
                       f"current detours round it, and the loop radiates -- route it round the gap, or "
                       f"close the gap")
        length = sum(s.length for s in geometry.segments if s.net == net)
        if edge is not None:
            critical = EMC["signal_speed_mm_per_ns"]["value"] * edge * EMC["critical_fraction"]["value"]
            if length > critical:
                out.append(f"emc: {net} is {length:.1f} mm, electrically long for a ~{edge:g} ns edge "
                           f"(over {critical:.0f} mm) on a {kind} net: keep it over solid ground, shorten "
                           f"it, or add series termination")
    for v in (drc_report or {}).get("violations", []):
        items = v.get("items") or [{}]
        where = items[0].get("pos") or {}
        at = f" at ({where.get('x', 0):g}, {where.get('y', 0):g})" if where else ""
        what = items[0].get("description", "?")
        if v.get("type") in ("track_dangling", "via_dangling"):
            out.append(f"emc: a dangling track or via ({what}){at}: a stub radiates -- remove it")
        elif v.get("type") == "isolated_copper":
            out.append(f"emc: an isolated copper island ({what}){at}: unconnected copper -- remove it "
                       f"or connect it to its net")
    return out


def _read(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def board_checks(board: pathlib.Path, netlist: pathlib.Path) -> tuple[dict[str, NetFacts], list[str]]:
    """Measure and check a board from its files: the board, the netlist, and
    the <netlist stem>.netclasses.json and <board stem>.drc.json beside them."""
    geometry = boardgeom.read(board.read_text(errors="replace"))
    classes = (_read(netlist.with_suffix(".netclasses.json")) or {}).get("nets", {})
    text = netlist.read_text(errors="replace")
    facts = measure(geometry, text, classes)
    drc = _read(board.with_name(f"{board.stem}.drc.json"))
    return facts, placement(geometry, text) + warnings(facts, drc) + emc(geometry, text, classes, drc)


def placement_checks(board: pathlib.Path, netlist: pathlib.Path) -> list[str]:
    """Phase A alone, for a placed board that is not routed yet."""
    return placement(boardgeom.read(board.read_text(errors="replace")),
                     netlist.read_text(errors="replace"))


def summary_line(facts: dict[str, NetFacts], warned: list[str]) -> str:
    total = sum(f.length for f in facts.values())
    vias = sum(f.vias for f in facts.values())
    return (f"board checks: {len(facts)} routed nets, {total:.0f} mm of track, {vias} vias; "
            f"{len(warned)} warning(s)")


def readme_section(facts: dict[str, NetFacts], warned: list[str]) -> list[str]:
    rows = ["## Board checks", "",
            "Measured from the routed board. Every limit is a rule of thumb "
            "(engine/circuit_mcp/board_checks.toml): a check, not a signal-integrity sign-off.", "",
            "| Net | Class | Length (mm) | Vias | Narrowest (mm) | Path R (mΩ) | Drop |",
            "|---|---|---|---|---|---|---|"]
    order = sorted(facts.values(), key=lambda f: (f.cls or "~", f.net))
    for f in order:
        drop = (f"{f.drop_mv:.0f} mV at {f.current_a:g} A" if f.drop_mv is not None
                else ("—" if not f.note else f.note.split(";")[0]))
        rows.append(f"| {f.net.replace('|', '&#124;')} | {f.cls or '—'} | {f.length:.1f} | {f.vias} | "
                    f"{f.narrowest:g} | {'—' if f.path_mohm is None else f'{f.path_mohm:.0f}'} | {drop} |")
    rows += [""] + ([f"- {w}" for w in warned] or ["No warnings."]) + [""]
    return rows
