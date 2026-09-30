"""How a clause finds things on a board it has never seen (spec §5.1, §5.2).

A part is matched by a predicate over reference, library id, value, pad
count and, optionally, what its pins do: an allowlist from contract.toml,
never an exact library id. A net is matched by function: the words of its
pins' names against a synonym list, falling back to the net's own name only
for a pad whose pin has no name. Nothing here knows about any one task.
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


# KiCad names a net nobody named after a pad on it: `Net-(D2-A)`,
# `unconnected-(U1-K2-Pad5)`, possibly under a sheet path. Such a name says
# nothing about function (D2 there is a designator, not data input 2).
_AUTO_NET = re.compile(r"(?:^|/)(?:Net|unconnected)-\(")


def pad_words(pad: Pad) -> frozenset[str]:
    """What a pad says about its function: the words of its pin names; only
    when it has none, the words of its net's name; never those of a KiCad
    auto-generated net name."""
    named = frozenset().union(*(words(n) for n in pad.names))
    if named or not pad.net or _AUTO_NET.search(pad.net):
        return named
    return words(pad.net)


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


def _ints(value) -> tuple[int, ...]:
    """TOML gives a bare int or a list; a bare int is one count."""
    if isinstance(value, int):
        return (value,)
    out = []
    for v in value:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            raise ValueError(f"non-integer pad count: {v!r}")
    return tuple(out)


def _one_words(where: str, value) -> frozenset[str]:
    """A synonym list: each entry a string that is exactly one word, since a
    synonym that splits into several could never match one."""
    out = []
    for s in ((value,) if isinstance(value, str) else value):
        if not isinstance(s, str):
            raise ValueError(f"{where}: {s!r} is not a string")
        if words(s) != {s.upper()}:
            raise ValueError(f"{where}: {s!r} is not one word "
                             "(words split on anything but letters, digits and '+')")
        out.append(s.upper())
    return frozenset(out)


_PART_KEYS = {"ref", "lib_id", "value", "pads", "with_pins"}


@dataclass(frozen=True)
class PartSpec:
    """An allowlist. Every field given must match; a field matches when any
    of its patterns does. Patterns are regexes, searched, not full-matched.
    `with_pins` is words, not patterns: the part must have at least one pad,
    connected or not, whose pin words (see pad_words) include one of them --
    so `ref = U*` plus `with_pins = [K0, D0]` is the encoder, not whichever
    IC sits beside it."""
    name: str
    ref: tuple[str, ...] = ()
    lib_id: tuple[str, ...] = ()
    value: tuple[str, ...] = ()
    pads: tuple[int, ...] = ()
    with_pins: frozenset[str] = frozenset()

    @classmethod
    def from_toml(cls, name: str, table: dict) -> PartSpec:
        unknown = set(table) - _PART_KEYS
        if unknown:
            raise ValueError(f"[parts.{name}]: unknown key(s) {sorted(unknown)}")
        try:
            pads = _ints(table.get("pads", ()))
        except ValueError as e:
            raise ValueError(f"[parts.{name}]: {e}") from e
        spec = cls(name, ref=_strings(table.get("ref", ())),
                   lib_id=_strings(table.get("lib_id", ())),
                   value=_strings(table.get("value", ())),
                   pads=pads,
                   with_pins=_one_words(f"[parts.{name}] with_pins",
                                        table.get("with_pins", ())))
        if not (spec.ref or spec.lib_id or spec.value or spec.pads or spec.with_pins):
            raise ValueError(f"[parts.{name}] would match every part; "
                             f"give at least one of {sorted(_PART_KEYS)}")
        for pattern in (*spec.ref, *spec.lib_id, *spec.value):
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"[parts.{name}]: bad regex {pattern!r}: {e}") from e
        return spec

    def matches(self, fp: Footprint, pads: list[Pad] | None = None) -> bool:
        """`pads` are the footprint's own pads; needed only for with_pins."""
        if self.with_pins and pads is None:
            raise ValueError(f"[parts.{self.name}] uses with_pins: pass the part's pads")

        def any_of(patterns, text):
            return not patterns or any(re.search(p, text) for p in patterns)
        return (any_of(self.ref, fp.ref) and any_of(self.lib_id, fp.lib_id)
                and any_of(self.value, fp.value)
                and (not self.pads or fp.pad_count in self.pads)
                and (not self.with_pins
                     or any(pad_words(p) & self.with_pins for p in pads)))


def matching(board: Board, part: PartSpec) -> list[Footprint]:
    by_ref = pads_by_ref(board)
    return [f for f in board.footprints() if part.matches(f, by_ref.get(f.ref, []))]


@dataclass(frozen=True)
class NetSpec:
    """A net found by function. `words` are matched against each pad's
    pad_words: its pin names, or its net's name when the pin has none;
    `part`, when given, limits the search to that part's pins. A `part` with
    no words means every net on it. `pins` instead picks that part's pads by
    number, for parts whose pin names say nothing (a header's `Pin_4`)."""
    name: str
    words: frozenset[str] = frozenset()
    part: PartSpec | None = None
    pins: frozenset[str] = frozenset()

    @classmethod
    def from_toml(cls, name: str, table: dict, parts: dict[str, PartSpec]) -> NetSpec:
        unknown = set(table) - {"words", "part", "pins"}
        if unknown:
            raise ValueError(f"[nets.{name}]: unknown key(s) {sorted(unknown)}")
        synonyms = _one_words(f"[nets.{name}]", table.get("words", ()))
        part = None
        if "part" in table:
            part_value = table["part"]
            if not isinstance(part_value, str):
                raise ValueError(f"[nets.{name}]: part must be a string, not {type(part_value).__name__}")
            if part_value not in parts:
                raise ValueError(f"[nets.{name}]: part {part_value!r} is not a [parts] alias")
            part = parts[part_value]
        pins = frozenset(_strings(table.get("pins", ())))
        if pins and part is None:
            raise ValueError(f"[nets.{name}]: pins needs a part: pad numbers mean nothing "
                             "without the part they are on")
        if pins and synonyms:
            raise ValueError(f"[nets.{name}]: give words or pins, not both")
        if not synonyms and part is None:
            raise ValueError(f"[nets.{name}] would match every net; give words, a part, or both")
        return cls(name, synonyms, part, pins)


def resolve_pads(board: Board, spec: NetSpec) -> list[Pad]:
    """The connected pads a net spec picks out: by pad number when the spec
    gives `pins`, else a pad matches when its pad_words share a word with
    the spec."""
    refs = None if spec.part is None else {f.ref for f in matching(board, spec.part)}
    found = []
    for p in board.pads():
        if not p.net or (refs is not None and p.ref not in refs):
            continue
        if spec.pins:
            if p.number in spec.pins:
                found.append(p)
        elif not spec.words or pad_words(p) & spec.words:
            found.append(p)
    return found


def resolve_net(board: Board, spec: NetSpec) -> frozenset[str]:
    """The nets a spec picks out; empty when nothing on the board matches,
    which the calling checker reports as a clause failure (spec §5.2)."""
    return frozenset(p.net for p in resolve_pads(board, spec))
