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
    facts = measure(geometry, netlist.read_text(errors="replace"), classes)
    return facts, warnings(facts, _read(board.with_name(f"{board.stem}.drc.json")))


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
