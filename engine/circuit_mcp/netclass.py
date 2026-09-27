"""Net classification's deterministic parts, ported from the Rust classifier
(flai's circuit crate, src/{netlist,sexpr,rules,evidence,classes,netclasses,physical,eval}.rs) so the engine can run the whole flow without flai. Parity
against the Rust is checked byte-for-byte against goldens exported before the
Rust is removed (engine-portability spec §4.1, §6.1).

The menu and the physical width/clearance table are **loaded** from
`netclass_data.json` (exported from the Rust's own `NetClass`/`CurrentTier`/
`physical()`), never copied into this file, so the two cannot drift silently.

There is deliberately no power *rule* (rules.rs): a name says a net is power,
not how much current it carries. What there is instead is a power *floor*
(spec §4.2): `set_net_classes`'s caller must refuse a pick of `signal` for a
net that carries a `power_in`/`power_out` pin, forcing a deliberate
`review: true` instead of a silent under-rating.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# --- the menu and physical table: loaded, never copied (spec §4.1) --------

_DATA = json.loads(Path(__file__).with_name("netclass_data.json").read_text())

MENU = {
    "classes": {c["id"]: c["criterion"] for c in _DATA["classes"]},
    "tiers": {t["id"]: t["criterion"] for t in _DATA["tiers"]},
}
_PHYSICAL = _DATA["physical"]
# Classes that are current-carrying return/supply nets (classes::is_power).
POWER_CLASSES = {c["id"] for c in _DATA["classes"] if c["power"]}
# Ascending current order, taken from the data file's own order
# (classes.rs declares CurrentTier::ALL in ascending order).
TIER_ORDER = list(MENU["tiers"])
_TIER_INDEX = {tier: i for i, tier in enumerate(TIER_ORDER)}


def _physical(class_id: str, tier_id: str | None) -> dict:
    return _PHYSICAL[f"{class_id}|{tier_id or '-'}"]


# --- the netlist view (netlist.rs) -----------------------------------------


@dataclass
class Component:
    ref: str
    value: str
    description: str | None
    footprint: str | None


@dataclass
class Node:
    ref: str
    pin: str
    function: str | None
    pin_type: str | None


@dataclass
class Net:
    name: str
    nodes: list[Node]


@dataclass
class Netlist:
    components: dict[str, Component]
    nets: list[Net]

    def component(self, reference: str) -> Component | None:
        return self.components.get(reference)


# A quoted string's content, escape-aware like sexpr.rs's own reader: a
# backslash protects the character after it (so a quote or another backslash
# can appear inside), which is why `[^"]*` alone is wrong -- it closes the
# string at the first `\"` instead of treating it as a literal quote.
_QSTR = r'"((?:[^"\\]|\\.)*)"'
# `\(` + `char\)` doesn't capture; a group is inlined per field below instead.
_ESCAPE = re.compile(r"\\(.)", re.S)


def _unescape(text: str) -> str:
    """`\\n` -> a newline; any other `\\X` -> `X` -- exactly sexpr.rs's own
    string reader, which treats every other escape as "the next character,
    literally" rather than a fixed table of escapes."""
    return _ESCAPE.sub(lambda m: "\n" if m.group(1) == "n" else m.group(1), text)


# KiCad pretty-prints one atom per line, so nothing here may assume a whole
# `(comp ...)` or `(net ...)` sits on one line -- same constraint kicad_lib
# was written against (see its own comment on this).  Components carry an
# optional `(fields ...)` tree between the atoms that matter, so a component
# is split into its own block first rather than matched with one regex that
# would also catch a `(field (name "Footprint") "...")` entry.
_COMP_SPLIT = re.compile(r"\(comp\b")
_REF = re.compile(r"\(ref " + _QSTR + r"\)")
_VALUE = re.compile(r"\(value " + _QSTR + r"\)")
_DESCRIPTION = re.compile(r"\(description " + _QSTR + r"\)")
_FOOTPRINT = re.compile(r"\(footprint " + _QSTR + r"\)")

# `(net (code 1) (name "+5V") ...)`; code may be bare or quoted. Lazily
# captures up to the next net (or the end), so one net's body never bleeds
# into the next.
_NET = re.compile(
    r"\(net\s+\(code\s*\"?\d+\"?\)\s*\(name " + _QSTR + r"\)(.*?)(?=\(net\s+\(code|\Z)",
    re.S,
)
# Each node's own stretch, isolated the same way: split on `(node`, so
# `ref`/`pin`/`pinfunction`/`pintype` are read from exactly one node's text,
# order-independent (KiCad writes `pinfunction` before `pintype`; SKiDL
# writes no `pinfunction` at all -- see netlist.rs's module comment).
_NODE_SPLIT = re.compile(r"\(node\b")
_NODE_PIN = re.compile(r"\(pin " + _QSTR + r"\)")
_NODE_PINFUNCTION = re.compile(r"\(pinfunction " + _QSTR + r"\)")
_NODE_PINTYPE = re.compile(r"\(pintype " + _QSTR + r"\)")


def _components(text: str) -> dict[str, Component]:
    # Only the components section: libparts carries its own `(footprint ...)`
    # entries, which a split over the whole file would read too.
    body = text.split("(libparts", 1)[0]
    out: dict[str, Component] = {}
    for piece in _COMP_SPLIT.split(body)[1:]:
        ref = _REF.search(piece)
        if ref is None:
            continue
        value = _VALUE.search(piece)
        description = _DESCRIPTION.search(piece)
        footprint = _FOOTPRINT.search(piece)
        out[_unescape(ref.group(1))] = Component(
            ref=_unescape(ref.group(1)),
            value=_unescape(value.group(1)) if value else "",
            description=_unescape(description.group(1)) if description else None,
            footprint=_unescape(footprint.group(1)) if footprint else None,
        )
    return out


def _nets(text: str) -> list[Net]:
    nets: list[Net] = []
    for name, block in _NET.findall(text):
        nodes: list[Node] = []
        for piece in _NODE_SPLIT.split(block)[1:]:
            ref = _REF.search(piece)
            pin = _NODE_PIN.search(piece)
            if ref is None or pin is None:
                continue
            function = _NODE_PINFUNCTION.search(piece)
            pin_type = _NODE_PINTYPE.search(piece)
            nodes.append(
                Node(
                    ref=_unescape(ref.group(1)),
                    pin=_unescape(pin.group(1)),
                    function=_unescape(function.group(1)) if function else None,
                    pin_type=_unescape(pin_type.group(1)) if pin_type else None,
                )
            )
        nets.append(Net(name=_unescape(name), nodes=nodes))
    return nets


def read(text: str) -> Netlist:
    """Parse a KiCad/SKiDL netlist: components and nets, nets in file order."""
    return Netlist(components=_components(text), nets=_nets(text))


# --- rules (rules.rs) -------------------------------------------------------

_GROUND_PREFIXES = ("SIG", "A", "D", "P")


def _is_ground_name(name: str) -> bool:
    """`^(A|D|P|SIG)?GND[0-9]*$` or `^VSS$`, case-insensitive."""
    upper = name.upper()
    if upper == "VSS":
        return True
    rest = upper
    for prefix in _GROUND_PREFIXES:
        if upper.startswith(prefix):
            candidate = upper[len(prefix):]
            if candidate.startswith("GND"):
                rest = candidate
                break
    if not rest.startswith("GND"):
        return False
    digits = rest[3:]
    return all(ch.isdigit() for ch in digits)


def _is_power_pin_type(pin_type: str) -> bool:
    """Matches SKiDL's `POWER-IN`/`POWER-OUT` and KiCad's `power_in`/
    `power_out`, case-insensitively."""
    normalized = pin_type.upper().replace("-", "_")
    return normalized in ("POWER_IN", "POWER_OUT")


def rule(netlist: Netlist, net: Net) -> tuple[str, str] | None:
    """`(class_id, rule_name)` for a net a rule alone decides, or None."""
    if _is_ground_name(net.name):
        return ("ground", "ground-name")
    touches_crystal = any(
        (part := netlist.component(node.ref)) is not None
        and part.footprint is not None
        and part.footprint.startswith("Crystal:")
        for node in net.nodes
    )
    has_power_pin = any(
        node.pin_type is not None and _is_power_pin_type(node.pin_type)
        for node in net.nodes
    )
    if touches_crystal and len(net.nodes) <= 4 and not has_power_pin:
        return ("crystal", "crystal-footprint")
    return None


# --- the power floor (spec §4.2; no equivalent Rust rule) -------------------

# The floor's own minimum tier (I1): raising a review pick's class to
# power_logic without also raising a lt_0_5a tier leaves the copper at
# power_logic|lt_0_5a == signal|lt_0_5a (both 0.25 mm) -- the floor's "raise
# to power_logic" would then never widen anything.
_FLOOR_TIER = "0_5a_to_2a"


def power_floor(net: Net) -> str | None:
    """The first `REF:PIN (TYPE)` on `net` whose pin type is a power pin, or
    None. Callers refuse a non-power pick on a net this returns non-None
    for, unless the caller sets `review: true`."""
    for node in net.nodes:
        if node.pin_type is not None and _is_power_pin_type(node.pin_type):
            return f"{node.ref}:{node.pin} ({node.pin_type})"
    return None


# --- evidence (evidence.rs) -------------------------------------------------

# Beyond this many pins the rest are summarised by reference prefix, so a
# ground net with a hundred pins stays within the request budget.
MAX_NODE_LINES = 40


def _is_generated_name(name: str) -> bool:
    """KiCad and SKiDL invent names like `N$1` and `Net-(D1-Pad2)` for nets
    the designer did not name. Such a name is noise, not evidence."""
    return name.startswith("N$") or name.startswith("Net-(")


def describe_part(reference: str, part: Component | None) -> str:
    """`ref value (description) [footprint]`, omitting whatever the netlist
    lacks."""
    text = reference
    if part is not None:
        if part.value:
            text += f" {part.value}"
        if part.description:
            text += f" ({part.description})"
        if part.footprint:
            text += f" [{part.footprint}]"
    return text


def _other_side(netlist: Netlist, reference: str, pin: str) -> tuple[str, Net] | None:
    """For a pin on a two-terminal part, the part's other pin and the net it
    sits on. None when the part has any other number of pins in the
    netlist."""
    pins = (
        (node.pin, net)
        for net in netlist.nets
        for node in net.nodes
        if node.ref == reference
    )
    first = next(pins, None)
    if first is None:
        return None
    second = next(pins, None)
    if second is None:
        return None
    if next(pins, None) is not None:
        return None
    return second if first[0] == pin else first


def evidence(netlist: Netlist, net: Net) -> str:
    """The net's name and every pin on it, as `jev` sees it."""
    name = "(unnamed)" if _is_generated_name(net.name) else net.name
    lines = [f"Net: {name}", f"Pins ({len(net.nodes)}):"]
    for node in net.nodes[:MAX_NODE_LINES]:
        text = describe_part(node.ref, netlist.component(node.ref))
        text += f" pin {node.pin}"
        if node.function:
            text += f' "{node.function}"'
        if node.pin_type:
            text += f" {node.pin_type.lower()}"
        other = _other_side(netlist, node.ref, node.pin)
        if other is not None:
            other_pin, other_net = other
            if other_net.name != net.name:
                other_name = (
                    "an unnamed net"
                    if _is_generated_name(other_net.name)
                    else other_net.name
                )
                text += f" -> pin {other_pin} on {other_name}"
        lines.append(f"- {text}")
    rest = net.nodes[min(len(net.nodes), MAX_NODE_LINES):]
    if rest:
        by_prefix: dict[str, int] = {}
        for node in rest:
            prefix = ""
            for ch in node.ref:
                if ch.isascii() and ch.isalpha():
                    prefix += ch
                else:
                    break
            by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
        counts = sorted(by_prefix.items(), key=lambda item: (-item[1], item[0]))
        summary = ", ".join(f"{count} x {prefix}" for prefix, count in counts)
        lines.append(f"... and {len(rest)} more: {summary}")
    return "\n".join(lines)


# --- the menu's consistency rule (classes.rs::consistent) -------------------


def _consistent(class_id: str, tier_id: str) -> bool:
    """A class/tier pair that contradicts itself goes to review."""
    if class_id != "signal":
        return True
    return _TIER_INDEX[tier_id] < _TIER_INDEX["0_5a_to_2a"]


# --- validation (spec §4.2's `set_net_classes`) -----------------------------


# The number of problems validate() returns before it stops listing more and
# says how many were left out instead (m-d): an agent that returns picks in
# the wrong shape entirely (a list of strings, say) would otherwise get one
# problem line per pick, which for a large netlist buries the actionable
# ones (missing/duplicate/menu) in noise.
MAX_PROBLEMS = 100


def _malformed_pick(pick) -> bool:
    """True when `pick` cannot be read as a pick at all: not an object, or
    missing a string `net`/`class`/`tier`/`reason`, or a `review` that is
    present and not a bool. Checked before anything else touches the pick,
    so a model returning the wrong shape gets a problem line, never an
    exception."""
    if not isinstance(pick, dict):
        return True
    for key in ("net", "class", "tier", "reason"):
        if not isinstance(pick.get(key), str):
            return True
    review = pick.get("review")
    return review is not None and not isinstance(review, bool)


def validate(netlist: Netlist, open_nets: list[str], picks: list[dict]) -> list[str]:
    """Human-readable problems with `picks` against `open_nets`. An empty
    list means every pick may be written."""
    open_set = set(open_nets)
    net_by_name = {n.name: n for n in netlist.nets}

    problems: list[str] = []
    good_picks: list[dict] = []
    for index, pick in enumerate(picks):
        if _malformed_pick(pick):
            problems.append(f"{index}: pick must be an object with string net/class/tier/reason")
        else:
            good_picks.append(pick)

    picks_by_net: dict[str, dict] = {}
    duplicates: list[str] = []
    for pick in good_picks:
        name = pick["net"]
        if name in picks_by_net:
            if name not in duplicates:
                duplicates.append(name)
        else:
            picks_by_net[name] = pick

    for name in open_nets:
        if name not in picks_by_net:
            problems.append(f"{name}: missing")
    for pick in good_picks:
        name = pick["net"]
        if name not in open_set:
            problems.append(f"{name}: not an open net")
    for name in duplicates:
        problems.append(f"{name}: picked more than once")

    for name in open_nets:
        pick = picks_by_net.get(name)
        if pick is None:
            continue
        class_id = pick["class"]
        tier_id = pick["tier"]
        reason = pick["reason"]

        class_ok = class_id in MENU["classes"]
        tier_ok = tier_id in MENU["tiers"]
        if not class_ok:
            problems.append(f"{name}: class {class_id} is not in the menu")
        if not tier_ok:
            problems.append(f"{name}: tier {tier_id} is not in the menu")
        if not (1 <= len(reason) <= 300):
            problems.append(f"{name}: reason must be 1-300 characters")
        if class_ok and tier_ok and not _consistent(class_id, tier_id):
            problems.append(
                f"{name}: signal at {tier_id} is inconsistent "
                "(signal nets carry under 0.5 A)"
            )

        net = net_by_name.get(name)
        if net is not None and not pick.get("review"):
            pin = power_floor(net)
            if pin and class_id not in POWER_CLASSES:
                problems.append(
                    f"{name}: {pin} is a power pin, so pick ground, "
                    "power_logic or power_high_current, or set review: true"
                )

    if len(problems) > MAX_PROBLEMS:
        problems = problems[:MAX_PROBLEMS] + [f"… and {len(problems) - MAX_PROBLEMS} more"]
    return problems


# --- the writer (netclasses.rs, adapted for a picking model) ---------------


def _netclass_name(class_id: str, tier_id: str | None) -> str:
    return f"{class_id}_{tier_id}" if tier_id else class_id


def write_netclasses(netlist: Netlist, picks: list[dict]) -> dict:
    """The netclasses JSON: every rule-decided net, plus every picked net.

    A pick without `review` is accepted unless the physical entry for its
    class/tier carries a flag, in which case it goes to `needs_review` with
    the flag as its reason. A pick with `review: true` goes to
    `needs_review` with the pick's own reason; if the power floor triggers
    and the picked class is not already a power class, the class is raised
    to `power_logic` and the tier to at least `0_5a_to_2a` (a pick already at
    or above that tier keeps its tier), so the floor changes the copper, not
    just the label, and a power-floor clause naming the tier is appended.
    Any physical flag is appended too, joined with `"; "`.
    """
    picks_by_net = {pick["net"]: pick for pick in picks}
    nets_out: dict[str, dict] = {}
    review: list[dict] = []

    for net in netlist.nets:
        decided = rule(netlist, net)
        if decided is not None:
            class_id, _rule_name = decided
            tier_id = None
            source = "rule"
            reason = None
        else:
            pick = picks_by_net.get(net.name)
            if pick is None:
                continue
            class_id = pick.get("class")
            tier_id = pick.get("tier")
            source = "model"
            if pick.get("review"):
                reason = pick.get("reason", "")
                pin = power_floor(net)
                if pin and class_id not in POWER_CLASSES:
                    class_id = "power_logic"
                    if tier_id is None or _TIER_INDEX[tier_id] < _TIER_INDEX[_FLOOR_TIER]:
                        tier_id = _FLOOR_TIER
                    reason = (
                        f"{reason}; power floor: {pin} is a power pin, "
                        f"sized as power_logic {_FLOOR_TIER}"
                    )
            else:
                reason = None

        physical = _physical(class_id, tier_id)
        clauses = []
        if reason:
            clauses.append(reason)
        if physical["flag"]:
            clauses.append(physical["flag"])
        full_reason = "; ".join(clauses) if clauses else None
        status = "needs_review" if full_reason else "accepted"

        entry = {
            "netclass": _netclass_name(class_id, tier_id),
            "class": class_id,
            "tier": tier_id,
            "width_mm": physical["width_mm"],
            "clearance_mm": physical["clearance_mm"],
            "source": source,
            "status": status,
        }
        if physical.get("note"):
            entry["note"] = physical["note"]

        nets_out[net.name] = entry
        if full_reason:
            review.append({"net": net.name, "reason": full_reason})

    return {"version": 1, "nets": nets_out, "needs_review": review}


# --- scoring (eval.rs) -------------------------------------------------------


def load_labels(toml_text: str) -> dict[str, tuple[str, str]]:
    """`net -> (class, tier)` from a `[nets]` labels TOML file."""
    data = tomllib.loads(toml_text)
    labels: dict[str, tuple[str, str]] = {}
    for net, entry in data.get("nets", {}).items():
        class_id = entry["class"]
        tier_id = entry["tier"]
        if class_id not in MENU["classes"]:
            raise ValueError(f"{net}: unknown class {class_id}")
        if tier_id not in MENU["tiers"]:
            raise ValueError(f"{net}: unknown tier {tier_id}")
        labels[net] = (class_id, tier_id)
    return labels


def _is_power(class_id: str | None) -> bool:
    return class_id in POWER_CLASSES


def _under_rating(
    true_class: str,
    true_tier: str,
    predicted_class: str | None,
    predicted_tier: str | None,
) -> list[str]:
    """Ways `predicted` under-rates the true label. An empty list is safe."""
    why: list[str] = []
    if _is_power(true_class) and not _is_power(predicted_class):
        why.append(f"{true_class} net accepted as {predicted_class}")
    if true_class == "power_high_current" and predicted_class == "power_logic":
        why.append("high-current net accepted as power_logic")
    if true_class in ("rf_50ohm", "diff_pair") and predicted_class != true_class:
        why.append(f"{true_class} net accepted as {predicted_class}")
    if (
        predicted_tier is not None
        and true_tier is not None
        and _TIER_INDEX[predicted_tier] < _TIER_INDEX[true_tier]
    ):
        why.append(f"tier {predicted_tier} below true {true_tier}")
    return why


def score(netclasses: dict, labels: dict[str, tuple[str, str]]) -> dict:
    """Score a `netclasses.json`-shaped dict against `load_labels`'s output.

    Labelled nets only. Accepted means `status == "accepted"`. Dangerous
    errors are `_under_rating` with the true label as the reference:
    a power class accepted as non-power, `power_high_current` accepted as
    `power_logic`, `rf_50ohm`/`diff_pair` accepted as anything else, and a
    tier below the true tier.
    """
    nets = netclasses.get("nets", {})
    total = accepted = class_correct = tier_correct = 0
    dangerous: list[str] = []

    for net, (true_class, true_tier) in labels.items():
        entry = nets.get(net)
        if entry is None:
            continue
        total += 1
        if entry.get("status") != "accepted":
            continue
        accepted += 1
        predicted_class = entry.get("class")
        predicted_tier = entry.get("tier")
        if predicted_class == true_class:
            class_correct += 1
        if predicted_tier is not None and predicted_tier == true_tier:
            tier_correct += 1
        why = _under_rating(true_class, true_tier, predicted_class, predicted_tier)
        if why:
            dangerous.append(f"{net}: {'; '.join(why)}")

    return {
        "total": total,
        "accepted": accepted,
        "class_correct": class_correct,
        "tier_correct": tier_correct,
        "coverage": accepted / total if total else 0.0,
        "accuracy": class_correct / accepted if accepted else 0.0,
        "dangerous": dangerous,
    }
