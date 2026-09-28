"""What connects to what. The workhorses every future task reuses."""
from __future__ import annotations

from contract.board import Board
from contract.checkers.base import Result
from contract.match import (NetSpec, PartSpec, is_kind, matching, pads_by_ref, parse_value,
                            resolve_net)


def _names(nets) -> str:
    return ", ".join(sorted(nets))


def series_part_between(board: Board, *, net_a: NetSpec, kind: str, min: float, max: float,
                        net_b: NetSpec | None = None,
                        exclude: tuple[NetSpec, ...] = ()) -> Result:
    """A two-net part of designator `kind`, valued within [min, max], with one
    pad on net_a (less the excluded nets) and the other on net_b -- or, when
    net_b is omitted, on any net outside net_a.

    Open-ended (no net_b), "in series" is checked, not assumed: the net the
    part shares with net_a must be a series node, every pad on it belonging
    to the part itself or to a part net_a.part matches. Otherwise a resistor
    that merely touches the LED's driven net (say, towards another IC's
    input) would count as the LED's current limit. With no net_a.part there
    is nothing to be in series with, and the rule is skipped."""
    lo, hi = min, max  # contract.toml spells them min/max; the builtins are unused below
    removed = frozenset().union(*(resolve_net(board, e) for e in exclude))
    full = resolve_net(board, net_a)
    a = full - removed
    if not a:
        return Result(False, f"no net matches {net_a.name}"
                             + (" once the excluded nets are removed" if removed else ""))
    b = None
    if net_b is not None:
        b = resolve_net(board, net_b)
        if not b:
            return Result(False, f"no net matches {net_b.name}")
    other = (lambda n: n in b) if b is not None else (lambda n: n not in full)
    by_ref = pads_by_ref(board)
    series_with = None
    if b is None and net_a.part is not None:
        series_with = {f.ref for f in matching(board, net_a.part)}
    on_net: dict[str, set[str]] = {}
    for p in board.pads():
        if p.net:
            on_net.setdefault(p.net, set()).add(p.ref)
    between, found, loose = [], [], []
    for f in board.footprints():
        if not is_kind(f, kind):
            continue
        nets = {p.net for p in by_ref.get(f.ref, []) if p.net}
        if len(nets) != 2:
            continue
        x, y = sorted(nets)
        if not ((x in a and other(y)) or (y in a and other(x))):
            continue
        if series_with is not None:
            node = x if x in a else y
            strangers = sorted(on_net[node] - series_with - {f.ref})
            if strangers:
                loose.append((f"{f.ref} {f.value}", node, strangers))
                continue
        between.append(f"{f.ref} {f.value}")
        value = parse_value(f.value)
        if value is not None and lo <= value <= hi:
            found.append(f.ref)
    where = (f"{net_a.name} ({_names(a)}) and "
             + (f"{net_b.name} ({_names(b)})" if b is not None else "another net"))
    measured = {"parts": between}
    if loose:
        measured["not_in_series"] = [part for part, _, _ in loose]
    if found:
        return Result(True, f"{', '.join(found)} between {where}, within {lo:g}..{hi:g}",
                      measured)
    if between:
        return Result(False, f"{kind} between {where}: {', '.join(between)}; "
                             f"none within {lo:g}..{hi:g}", measured)
    why = "".join(f"; {part} shares {node} with {', '.join(refs)}, so it is not in series"
                  for part, node, refs in loose)
    return Result(False, f"no {kind} in series between {where}{why}", measured)


def _assign(reach: dict[str, list[str]]) -> dict[str, str] | None:
    """A different net for every part, or None: a small bipartite matching."""
    refs = list(reach)

    def go(i: int, used: frozenset[str]):
        if i == len(refs):
            return {}
        for net in reach[refs[i]]:
            if net not in used:
                rest = go(i + 1, used | {net})
                if rest is not None:
                    return {refs[i]: net, **rest}
        return None

    return go(0, frozenset())


def net_connects(board: Board, *, net_b: NetSpec, net_a: NetSpec | None = None,
                 each_part: PartSpec | None = None, distinct: bool = False) -> Result:
    """net_a and net_b share a net; or every part matching each_part has a
    pad on a net_b net -- with `distinct`, each on a different one."""
    if (net_a is None) == (each_part is None):
        raise ValueError("net_connects takes exactly one of net_a or each_part")
    b = resolve_net(board, net_b)
    if not b:
        return Result(False, f"no net matches {net_b.name}")
    if net_a is not None:
        a = resolve_net(board, net_a)
        if not a:
            return Result(False, f"no net matches {net_a.name}")
        shared = sorted(a & b)
        if shared:
            return Result(True, f"{net_a.name} and {net_b.name} share {_names(shared)}",
                          {"shared": shared})
        return Result(False, f"{net_a.name} is on {_names(a)}; {net_b.name} is on {_names(b)}; "
                             "they share none", {net_a.name: sorted(a), net_b.name: sorted(b)})
    refs = sorted(f.ref for f in matching(board, each_part))
    if not refs:
        return Result(False, f"no part matches {each_part.name}")
    by_ref = pads_by_ref(board)
    reach = {ref: sorted({p.net for p in by_ref.get(ref, []) if p.net in b}) for ref in refs}
    stranded = [ref for ref, nets in reach.items() if not nets]
    if stranded:
        return Result(False, f"{', '.join(stranded)} reach no {net_b.name} net", {"reach": reach})
    if distinct and _assign(reach) is None:
        return Result(False, f"the {each_part.name} parts cannot each reach a different "
                             f"{net_b.name} net: {reach}", {"reach": reach})
    return Result(True, f"each {each_part.name} reaches {net_b.name}"
                        + (" on a distinct net" if distinct else "") + f": {reach}",
                  {"reach": reach})


def no_short(board: Board, *, net_a: NetSpec, net_b: NetSpec) -> Result:
    """Both resolve, and to disjoint sets of nets."""
    a, b = resolve_net(board, net_a), resolve_net(board, net_b)
    for spec, nets in ((net_a, a), (net_b, b)):
        if not nets:
            return Result(False, f"no net matches {spec.name}")
    shared = sorted(a & b)
    if shared:
        return Result(False, f"{net_a.name} and {net_b.name} are the same net: {_names(shared)}",
                      {"shared": shared})
    return Result(True, f"{net_a.name} ({_names(a)}) is distinct from {net_b.name} ({_names(b)})",
                  {net_a.name: sorted(a), net_b.name: sorted(b)})
