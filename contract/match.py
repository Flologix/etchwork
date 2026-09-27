"""How a clause finds things on a board it has never seen (spec §5.1, §5.2).

A part is matched by a predicate over reference, library id, value and pad
count: an allowlist from contract.toml, never an exact library id. A net is
matched by function: the words of its pins' names, or of its own name,
against a synonym list. Nothing here knows about any one task.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from contract.board import Board, Footprint, Pad

# Split on anything but letters, digits and '+': `~{RESET}/PB5` -> RESET, PB5;
# a battery's `+` pin stays `+`; `+3V3` stays one word.
_SPLIT = re.compile(r"[^A-Za-z0-9+]+")


def words(name: str) -> frozenset[str]:
    return frozenset(w.upper() for w in _SPLIT.split(name) if w)


_SCALE = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
          "": 1.0, "r": 1.0, "R": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
# A number, an optional SI prefix (or R for ohms), and optional digits after
# the prefix, which is where 4k7 and 2R2 keep their decimal point. Anything
# after that (F, Ω, /16V) is ignored. Anchored at the start, so RED and
# CR2032 are not values.
_VALUE = re.compile(r"(\d+(?:\.\d+)?)\s*([pnuµμmrRkKMG]?)(\d*)")


def parse_value(text: str) -> float | None:
    """A component value as a number in base units, or None."""
    m = _VALUE.match(text.strip().replace(",", "."))
    if m is None:
        return None
    whole, prefix, tail = m.groups()
    if tail and "." in whole:
        return None  # 4.7k7 is not a spelling of anything
    return float(whole + ("." + tail if tail else "")) * _SCALE[prefix]


def is_kind(fp: Footprint, kind: str) -> bool:
    """`R` matches R1 and R12, not RV1 or RN1: the designator's letters, exactly."""
    return re.fullmatch(rf"{re.escape(kind)}\d+", fp.ref) is not None


def pads_by_ref(board: Board) -> dict[str, list[Pad]]:
    out: dict[str, list[Pad]] = {}
    for p in board.pads():
        out.setdefault(p.ref, []).append(p)
    return out


def _strings(value) -> tuple[str, ...]:
    """TOML gives a bare string or a list; a bare string is one pattern,
    not a tuple of its characters."""
    return (value,) if isinstance(value, str) else tuple(str(v) for v in value)


_PART_KEYS = {"ref", "lib_id", "value", "pads"}


@dataclass(frozen=True)
class PartSpec:
    """An allowlist. Every field given must match; a field matches when any
    of its patterns does. Patterns are regexes, searched, not full-matched."""
    name: str
    ref: tuple[str, ...] = ()
    lib_id: tuple[str, ...] = ()
    value: tuple[str, ...] = ()
    pads: tuple[int, ...] = ()

    @classmethod
    def from_toml(cls, name: str, table: dict) -> PartSpec:
        unknown = set(table) - _PART_KEYS
        if unknown:
            raise ValueError(f"[parts.{name}]: unknown key(s) {sorted(unknown)}")
        spec = cls(name, ref=_strings(table.get("ref", ())),
                   lib_id=_strings(table.get("lib_id", ())),
                   value=_strings(table.get("value", ())),
                   pads=tuple(int(n) for n in table.get("pads", ())))
        if not (spec.ref or spec.lib_id or spec.value or spec.pads):
            raise ValueError(f"[parts.{name}] would match every part; "
                             f"give at least one of {sorted(_PART_KEYS)}")
        for pattern in (*spec.ref, *spec.lib_id, *spec.value):
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"[parts.{name}]: bad regex {pattern!r}: {e}") from e
        return spec

    def matches(self, fp: Footprint) -> bool:
        def any_of(patterns, text):
            return not patterns or any(re.search(p, text) for p in patterns)
        return (any_of(self.ref, fp.ref) and any_of(self.lib_id, fp.lib_id)
                and any_of(self.value, fp.value)
                and (not self.pads or fp.pad_count in self.pads))


def matching(board: Board, part: PartSpec) -> list[Footprint]:
    return [f for f in board.footprints() if part.matches(f)]


@dataclass(frozen=True)
class NetSpec:
    """A net found by function. `words` are matched against the words of a
    pin's names and of the net's own name; `part`, when given, limits the
    search to that part's pins. A `part` with no words means every net on it."""
    name: str
    words: frozenset[str] = frozenset()
    part: PartSpec | None = None

    @classmethod
    def from_toml(cls, name: str, table: dict, parts: dict[str, PartSpec]) -> NetSpec:
        unknown = set(table) - {"words", "part"}
        if unknown:
            raise ValueError(f"[nets.{name}]: unknown key(s) {sorted(unknown)}")
        synonyms = _strings(table.get("words", ()))
        for s in synonyms:
            # A synonym that splits into several words could never match one.
            if words(s) != {s.upper()}:
                raise ValueError(f"[nets.{name}]: {s!r} is not one word "
                                 "(words split on anything but letters, digits and '+')")
        part = None
        if "part" in table:
            if table["part"] not in parts:
                raise ValueError(f"[nets.{name}]: part {table['part']!r} is not a [parts] alias")
            part = parts[table["part"]]
        if not synonyms and part is None:
            raise ValueError(f"[nets.{name}] would match every net; give words, a part, or both")
        return cls(name, frozenset(s.upper() for s in synonyms), part)


def resolve_pads(board: Board, spec: NetSpec) -> list[Pad]:
    """The connected pads a net spec picks out: a pad matches when its pin
    names or its net's name share a word with the spec."""
    refs = None if spec.part is None else {f.ref for f in matching(board, spec.part)}
    found = []
    for p in board.pads():
        if not p.net or (refs is not None and p.ref not in refs):
            continue
        said = words(p.net).union(*(words(n) for n in p.names))
        if not spec.words or said & spec.words:
            found.append(p)
    return found


def resolve_net(board: Board, spec: NetSpec) -> frozenset[str]:
    """The nets a spec picks out; empty when nothing on the board matches,
    which the calling checker reports as a clause failure (spec §5.2)."""
    return frozenset(p.net for p in resolve_pads(board, spec))
