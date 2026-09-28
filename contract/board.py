"""The delivered board as plain data: what every checker reads.

Built from the JSON contract/dump.py writes inside the engine image, so
nothing here imports pcbnew and every checker is testable on the host.
Coordinates are millimetres in KiCad's frame (y grows downward).

Six accessors, deliberately few (design spec §4): adding one is cheap,
changing one breaks every checker.
"""
from __future__ import annotations

from dataclasses import dataclass

Point = tuple[float, float]


class BoardFormatError(ValueError):
    """The dump JSON is not the shape this module reads."""


@dataclass(frozen=True)
class Outline:
    polygon: tuple[Point, ...]

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        if not self.polygon:
            return None
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def width(self) -> float:
        box = self.bbox
        return 0.0 if box is None else box[2] - box[0]

    @property
    def height(self) -> float:
        box = self.bbox
        return 0.0 if box is None else box[3] - box[1]


@dataclass(frozen=True)
class Footprint:
    ref: str
    value: str
    lib_id: str
    pos: Point
    rot: float
    courtyard: tuple[Point, ...]
    pad_count: int


@dataclass(frozen=True)
class Pad:
    ref: str
    number: str
    pos: Point
    layers: tuple[str, ...]
    net: str
    # Every name the pin is known by: the pad's own pinfunction, the symbol's
    # pin name, and its alternate functions. Empty for an undescribed part.
    names: tuple[str, ...]


@dataclass(frozen=True)
class Track:
    layer: str
    width: float
    start: Point
    end: Point
    net: str


@dataclass(frozen=True)
class Zone:
    layers: tuple[str, ...]
    net: str
    outline: tuple[Point, ...]
    filled: bool
    rule_area: bool


def _point(value) -> Point:
    x, y = value
    return (float(x), float(y))


def _ring(values) -> tuple[Point, ...]:
    return tuple(_point(v) for v in values)


class Board:
    def __init__(self, data: dict):
        try:
            self._outline = Outline(_ring(data["outline"]))
            self._footprints = tuple(
                Footprint(ref=str(f["ref"]), value=str(f["value"]), lib_id=str(f["lib_id"]),
                          pos=_point(f["pos"]), rot=float(f["rot"]),
                          courtyard=_ring(f["courtyard"]), pad_count=int(f["pad_count"]))
                for f in data["footprints"])
            self._pads = tuple(
                Pad(ref=str(p["ref"]), number=str(p["number"]), pos=_point(p["pos"]),
                    layers=tuple(p["layers"]), net=str(p["net"]),
                    names=tuple(str(n) for n in p["names"]))
                for p in data["pads"])
            self._tracks = tuple(
                Track(layer=str(t["layer"]), width=float(t["width"]), start=_point(t["start"]),
                      end=_point(t["end"]), net=str(t["net"]))
                for t in data["tracks"])
            self._zones = tuple(
                Zone(layers=tuple(z["layers"]), net=str(z["net"]), outline=_ring(z["outline"]),
                     filled=bool(z["filled"]), rule_area=bool(z["rule_area"]))
                for z in data["zones"])
        except (KeyError, TypeError, ValueError) as e:
            raise BoardFormatError(f"board dump is malformed: {e!r}") from e
        nets: dict[str, set[tuple[str, str]]] = {}
        for p in self._pads:
            if p.net:
                nets.setdefault(p.net, set()).add((p.ref, p.number))
        self._nets = {name: frozenset(members) for name, members in nets.items()}

    def outline(self) -> Outline:
        return self._outline

    def footprints(self) -> tuple[Footprint, ...]:
        return self._footprints

    def pads(self) -> tuple[Pad, ...]:
        return self._pads

    def nets(self) -> dict[str, frozenset[tuple[str, str]]]:
        """Net name -> {(reference, pad number)}. Unconnected pads are in no net."""
        return dict(self._nets)

    def tracks(self) -> tuple[Track, ...]:
        return self._tracks

    def zones(self) -> tuple[Zone, ...]:
        return self._zones
