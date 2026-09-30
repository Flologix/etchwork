"""A routed board's copper as geometry: track segments, vias, pads, and which
nets have a filled pour.

The board-checks design (§8). kicad_lib.read_board counts what a board holds;
the checks need where it is -- every segment's ends and width, every pad in
board coordinates -- so this reads the `.kicad_pcb` text itself: pure,
testable without KiCad, and checked against pcbnew's own positions and track
lengths on a real board with rotated footprints
(tests/test_board_extraction.py).

Straight segments only, which is all the tools' router writes; an arc is
counted by its chord and noted by the caller.
"""
import math
import re
from dataclasses import dataclass, field

_NET = re.compile(r'\(net (?:\d+ )?"((?:[^"\\]|\\.)*)"\)')
_AT = re.compile(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)")
_XY = r"\(({}) ([-\d.]+) ([-\d.]+)\)"


@dataclass(frozen=True)
class Segment:
    net: str
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    layer: str

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


@dataclass(frozen=True)
class Via:
    net: str
    x: float
    y: float
    size: float


@dataclass(frozen=True)
class Pad:
    ref: str
    number: str
    net: str
    x: float
    y: float
    reach: float                  # half the pad's larger side: what a track end may touch
    layers: frozenset[str]


Box = tuple[float, float, float, float]          # left, top, right, bottom


@dataclass(frozen=True)
class Footprint:
    ref: str
    library: str                  # the footprint's library: "Connector_USB", "RF_Module"
    x: float
    y: float
    courtyard: Box | None         # in board coordinates


@dataclass(frozen=True)
class Keepout:
    owner: str | None             # the footprint that carries it, if any
    box: Box


@dataclass
class Geometry:
    segments: list[Segment] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    pads: list[Pad] = field(default_factory=list)
    poured: set[str] = field(default_factory=set)
    arcs: int = 0
    footprints: list[Footprint] = field(default_factory=list)
    keepouts: list[Keepout] = field(default_factory=list)
    outline: Box | None = None


def _blocks(text: str, head: str, indent: str = "\t") -> list[str]:
    """Each `(head ...)` block at the given indent, to its closing parenthesis."""
    out = []
    for opener in re.finditer(rf"^{indent}\({head}\b", text, re.M):
        depth, i, quoted = 0, opener.start(), False
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
        out.append(text[opener.start():i + 1])
    return out


def _net(block: str) -> str:
    found = _NET.search(block)
    return found.group(1) if found else ""


def _point(block: str, key: str) -> tuple[float, float]:
    found = re.search(_XY.format(key), block)
    return float(found.group(2)), float(found.group(3))


def _number(block: str, key: str) -> float:
    found = re.search(rf"\({key} ([-\d.]+)", block)
    return float(found.group(1)) if found else 0.0


def _layers(block: str) -> frozenset[str]:
    found = re.search(r"\(layers ([^)]*)\)", block)
    names = re.findall(r'"([^"]+)"', found.group(1)) if found else []
    if any(n.startswith("*.Cu") for n in names):
        return frozenset({"*"})                    # through-hole: every copper layer
    return frozenset(n for n in names if n.endswith(".Cu"))


def _placement(footprint: str):
    """`(ref, x, y, to_board)`: where the footprint is, and the function that
    takes a point in its own coordinates to the board's."""
    at = _AT.search(footprint)
    fx, fy = float(at.group(1)), float(at.group(2))
    angle = math.radians(float(at.group(3) or 0))
    reference = re.search(r'\(property "Reference" "([^"]*)"', footprint) \
        or re.search(r'\(fp_text reference "([^"]*)"', footprint)

    def to_board(px, py):
        # KiCad: y points down, a positive angle turns counter-clockwise on screen.
        return (fx + px * math.cos(angle) + py * math.sin(angle),
                fy - px * math.sin(angle) + py * math.cos(angle))
    return (reference.group(1) if reference else "?"), fx, fy, to_board


_GRAPHICS = ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc")


def _courtyard(footprint: str, to_board) -> Box | None:
    """The bounding box of the footprint's F.CrtYd/B.CrtYd drawing, on the board."""
    points = []
    for kind in _GRAPHICS:
        for item in _blocks(footprint, kind, r"\t\t"):
            if not re.search(r'\(layer "[FB]\.CrtYd"\)', item):
                continue
            local = [(float(a), float(b)) for _, a, b in
                     re.findall(r"\((start|end|mid|center|xy) ([-\d.]+) ([-\d.]+)\)", item)]
            if kind == "fp_rect" and len(local) >= 2:
                (x1, y1), (x2, y2) = local[:2]
                local = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            if kind == "fp_circle":
                centre = re.search(r"\(center ([-\d.]+) ([-\d.]+)\)", item)
                end = re.search(r"\(end ([-\d.]+) ([-\d.]+)\)", item)
                cx, cy = float(centre.group(1)), float(centre.group(2))
                r = math.hypot(float(end.group(1)) - cx, float(end.group(2)) - cy)
                local = [(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r)]
            points += [to_board(x, y) for x, y in local]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4))


def _box(points) -> Box | None:
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _pads(footprint: str) -> list[Pad]:
    ref, _, _, to_board = _placement(footprint)
    out = []
    for pad in _blocks(footprint, "pad", r"\t\t"):
        head = re.match(r'\t\t\(pad "([^"]*)"', pad)
        local = _AT.search(pad)
        x, y = to_board(float(local.group(1)), float(local.group(2)))
        size = re.search(r"\(size ([\d.]+) ([\d.]+)\)", pad)
        reach = max(float(size.group(1)), float(size.group(2))) / 2 if size else 0.0
        out.append(Pad(ref=ref, number=head.group(1), net=_net(pad), x=round(x, 4), y=round(y, 4),
                       reach=reach, layers=_layers(pad)))
    return out


def read(text: str) -> Geometry:
    geometry = Geometry()
    for block in _blocks(text, "segment"):
        (x1, y1), (x2, y2) = _point(block, "start"), _point(block, "end")
        layer = re.search(r'\(layer "([^"]+)"\)', block)
        geometry.segments.append(Segment(net=_net(block), x1=x1, y1=y1, x2=x2, y2=y2,
                                         width=_number(block, "width"),
                                         layer=layer.group(1) if layer else ""))
    for block in _blocks(text, "arc"):
        (x1, y1), (x2, y2) = _point(block, "start"), _point(block, "end")
        layer = re.search(r'\(layer "([^"]+)"\)', block)
        geometry.segments.append(Segment(net=_net(block), x1=x1, y1=y1, x2=x2, y2=y2,
                                         width=_number(block, "width"),
                                         layer=layer.group(1) if layer else ""))
        geometry.arcs += 1
    for block in _blocks(text, "via"):
        x, y = _point(block, "at")
        geometry.vias.append(Via(net=_net(block), x=x, y=y, size=_number(block, "size")))
    for block in _blocks(text, "footprint"):
        geometry.pads += _pads(block)
        ref, x, y, to_board = _placement(block)
        library = re.match(r'\t\(footprint "([^":]*)', block)
        geometry.footprints.append(Footprint(ref=ref, library=library.group(1) if library else "",
                                             x=x, y=y, courtyard=_courtyard(block, to_board)))
        # A footprint's zones are written in board coordinates, not the footprint's.
        for zone in _blocks(block, "zone", r"\t\t"):
            if "(keepout" in zone:
                box = _box([(float(a), float(b)) for a, b in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", zone)])
                if box:
                    geometry.keepouts.append(Keepout(owner=ref, box=box))
    for block in _blocks(text, "zone"):
        if "(keepout" not in block and "filled_polygon" in block:
            geometry.poured.add(_net(block))
        elif "(keepout" in block:
            box = _box([(float(a), float(b)) for a, b in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", block)])
            if box:
                geometry.keepouts.append(Keepout(owner=None, box=box))
    edges = []
    for kind in ("gr_line", "gr_rect", "gr_arc", "gr_poly"):
        for block in _blocks(text, kind):
            if re.search(r'\(layer "Edge\.Cuts"\)', block):
                edges += [(float(a), float(b)) for _, a, b in
                          re.findall(r"\((start|end|mid|xy) ([-\d.]+) ([-\d.]+)\)", block)]
    geometry.outline = _box(edges)
    return geometry
