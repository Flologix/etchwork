"""Reading KiCad's own files, so the model does not have to grep them.

Measured 2026-09-11 across three recorded autonomous runs
(`~/.flai/trajectories/cli/`), every `run_shell` command classified by the
question it was asking: **56% of 181 commands were four questions** -- what
pins does this symbol have, what pads does this footprint have, what is
connected to what in this netlist, and did the generated board actually get
made. All four are answerable from files the harness already vendors, and all
four were being answered by hand-written parsers that work often enough to be
trusted and fail silently when they do not.

Two of those failures, both measured:

**`extends`.** `find_part("ATtiny85V-10P")` returned a description and no pins,
because that symbol's whole body is `(extends "ATtiny25V-10P")` -- KiCad's
derived-symbol mechanism, which most ICs use and no passive does. So the
catalogue answered pins for the two-pin parts where they are obvious and went
silent for the eight-pin part where they decide the design. One run spent a
grep, a hand-written probe script and two failed SKiDL API guesses to recover
one fact.

**Layer names.** A run grepped a routed board for `track` and `Edge_Cuts`, got
zero of each, and wrote "either the file uses a layout I'm not grepping
correctly, or DRC passed vacuously". KiCad 10 writes `segment` and
`Edge.Cuts`. It was one step from reporting a fabricated result. Format names
belong in one tested place, where a version rename fails a test instead of a
board report.

Pure: handed text, returns data. No I/O, no Docker, no model -- the rule
`CLAUDE.md` sets for `shell_jail_rules` and `app/kicad.py`, and what lets every
shape here be tested in milliseconds.
"""
import re
from dataclasses import dataclass, field

# How many `extends` hops to follow. Three is past anything KiCad ships and
# far short of a cycle burning the stack.
MAX_EXTENDS = 4


@dataclass(frozen=True)
class Pin:
    number: str
    name: str
    kind: str
    # KiCad's `(alternate "SPI1_MOSI" ...)` entries: the functions a pin can
    # take besides its primary name. STM32 symbols carry thousands; the AVR
    # libraries carry none, which is what pin_functions.toml fills in.
    alternates: tuple[str, ...] = ()


@dataclass
class Symbol:
    library: str
    name: str
    description: str
    pins: list[Pin] = field(default_factory=list)
    # The parent this symbol's pins came from, or None when it defines its
    # own. Stated rather than hidden: a pin list whose origin is invisible is
    # the kind of fact that gets doubted and re-grepped, which is the
    # behaviour this module replaces.
    inherited_from: str | None = None
    footprint: str = ""


# A top-level symbol: one tab, unlike the sub-units nested inside it.
_SYMBOL = re.compile(r'^\t\(symbol "([^"]+)"', re.MULTILINE)
_EXTENDS = re.compile(r'\(extends "([^"]+)"')
_PROPERTY = r'\(property "{}" "([^"]*)"'
# `(pin bidirectional line ... (name "PB2" ...) (number "7" ...))`. The type is
# the word right after `(pin`, and name and number are separated by an effects
# block with nested parens -- so they cannot be matched as adjacent, and the
# hop to the number has to be non-greedy or it runs into the NEXT pin's.
_PIN = re.compile(
    r'\(pin\s+(\S+)\s+\S+.*?\(name "([^"]*)".*?\(number "([^"]*)"', re.S)


# One pin's own stretch of a symbol block, from its `(pin <type> <style>` to
# the next. `\(pin\s` and not `\(pin`: the symbol header carries
# `(pin_names ...)` and `(pin_numbers ...)`, which are not pins.
_PIN_OPEN = re.compile(r'\(pin\s')
_PIN_NUMBER = re.compile(r'\(number "([^"]*)"')
_ALTERNATE = re.compile(r'\(alternate "([^"]+)"')


def _alternates(block: str) -> dict[str, tuple[str, ...]]:
    """`pin number -> its alternate function names`, for pins that have any.

    KiCad writes the alternates after the pin's name and number, so each pin
    is read from its own stretch rather than by one pattern over the block.
    """
    out: dict[str, tuple[str, ...]] = {}
    for piece in _PIN_OPEN.split(block)[1:]:
        number = _PIN_NUMBER.search(piece)
        names = _ALTERNATE.findall(piece)
        if number and names:
            out[number.group(1)] = tuple(names)
    return out


def _blocks(text: str) -> dict[str, str]:
    """`name -> block` for each top-level symbol, in file order."""
    starts = [(m.group(1), m.start()) for m in _SYMBOL.finditer(text)]
    out = {}
    for i, (name, at) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        out[name] = text[at:end]
    return out


def symbol_names(text: str) -> list[str]:
    """Every top-level symbol in a `.kicad_sym`, in file order."""
    return list(_blocks(text))


def read_symbol(text: str, name: str, library: str = "") -> "Symbol | str":
    """A symbol with its pins, or a sentence saying why not.

    A string rather than an exception or an empty result, because every way
    this fails is something the caller can act on and a model can read: the
    part is not here, or its parent is not here and naming the parent says
    which library to add.
    """
    blocks = _blocks(text)
    if name not in blocks:
        return (f"no symbol {name!r} in this library"
                + (f" ({library})" if library else ""))

    block = blocks[name]
    description = re.search(_PROPERTY.format("Description"), block)
    footprint = re.search(_PROPERTY.format("Footprint"), block)
    found = Symbol(library=library, name=name,
                   description=description.group(1) if description else "",
                   footprint=footprint.group(1) if footprint else "")

    # Follow `extends` to whichever ancestor actually carries pins. Each hop
    # is checked for presence, so "extends X, and X is not here" is reported
    # with the name -- an empty pin list is not an answer.
    seen, current, source = {name}, name, None
    for _ in range(MAX_EXTENDS):
        pins = _PIN.findall(blocks[current])
        if pins:
            alternates = _alternates(blocks[current])
            found.pins = [Pin(number=number, name=pin_name, kind=kind,
                              alternates=alternates.get(number, ()))
                          for kind, pin_name, number in pins]
            found.inherited_from = source
            return found
        parent = _EXTENDS.search(blocks[current])
        if parent is None:
            # No pins and nothing to inherit from. A real symbol shape (a
            # graphic, a power flag), so it is a Symbol with no pins rather
            # than a failure.
            return found
        source = parent.group(1)
        if source in seen:
            return (f"{name!r} extends {source!r}, which extends back into the "
                    f"chain -- this library's symbol inheritance has a cycle")
        if source not in blocks:
            return (f"{name!r} extends {source!r}, which is not in this "
                    f"library. Its pins live there; add the library that "
                    f"defines {source!r}.")
        seen.add(source)
        current = source
    return (f"{name!r} extends more than {MAX_EXTENDS} levels deep, which is "
            f"past anything KiCad ships; stopped at {current!r}")


# --- footprints ------------------------------------------------------------


@dataclass
class Footprint:
    name: str
    # Pad NUMBERS, one entry per physical pad, so a two-terminal part whose
    # footprint has four pads reads as ["1", "1", "2", "2"].
    electrical: list[str] = field(default_factory=list)
    # Unnumbered plated or unplated holes. Counted apart because a symbol
    # with two pins on a footprint with two pads and two mounting holes is
    # correct, and a tool reporting "4 pads" would confirm a mismatch that is
    # not there.
    mounting: int = 0
    extent: tuple[float, float] = (0.0, 0.0)

    @property
    def nets(self) -> set[str]:
        """The distinct pad numbers -- what a symbol's pins must cover."""
        return set(self.electrical)


_PAD = re.compile(r'\(pad "([^"]*)"\s+(\S+)')
_XY = re.compile(r'\((?:start|end|center|xy)\s+(-?[\d.]+)\s+(-?[\d.]+)')
_AT = re.compile(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)')
# The primitive's name is captured, because one of them is not a list of
# corners: a circle is (center) (end), two points a radius apart.
_DRAWING = re.compile(r'\((fp_line|fp_rect|fp_poly|fp_circle|fp_arc)\b')


def _extent(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2))


def read_footprint(text: str, name: str = "") -> Footprint:
    """A footprint's pads and how much room it needs.

    The extent comes from the courtyard when the footprint draws one, because
    that is the number placement has to respect -- `arrange` already refuses
    with "19.6mm of footprint in 14.0mm of room", and knowing it BEFORE the
    attempt is what removes the round-trips. Footprints that draw no
    courtyard fall back to their pads: a bound that is too small beats "no
    idea how big it is".
    """
    found = Footprint(name=name)
    for number, kind in _PAD.findall(text):
        if number:
            found.electrical.append(number)
        else:
            found.mounting += 1
    found.electrical.sort()

    # Split on drawing primitives so each one's layer and its coordinates
    # stay together; a global search for F.CrtYd and a global search for
    # coordinates would pair a layer with somebody else's points.
    courtyard: list[tuple[float, float]] = []
    pieces = _DRAWING.split(text)
    # With the group captured, split yields [head, kind, body, kind, body...].
    for kind, piece in zip(pieces[1::2], pieces[2::2]):
        if "F.CrtYd" not in piece and "B.CrtYd" not in piece:
            continue
        points = [(float(x), float(y)) for x, y in _XY.findall(piece)]
        if kind == "fp_circle" and len(points) >= 2:
            # (center cx cy) (end ex ey): the two points are a radius, not
            # an extent. Read as corners, a round courtyard measured half
            # its width and NO height -- Capacitor_THT's radial cans, a
            # vendored library, came back as "4.25 x 0.0 mm of board".
            (cx, cy), (ex, ey) = points[0], points[1]
            radius = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            points = [(cx - radius, cy - radius), (cx + radius, cy + radius)]
        courtyard.extend(points)
    if courtyard:
        found.extent = _extent(courtyard)
        return found

    pads = [(float(x), float(y)) for x, y in _AT.findall(text)]
    found.extent = _extent(pads)
    return found


# --- netlists --------------------------------------------------------------


@dataclass
class Component:
    ref: str
    value: str
    footprint: str


# KiCad pretty-prints its s-expressions one atom per line, so nothing here may
# assume `(net (code 1) (name "X")` is on one line -- and the code may be
# quoted or bare. A compact fixture is what let the first version of this
# module match nothing at all on a real netlist, which is the failure
# app/tools/parts.py's _PIN comment already records from the other direction.
# Split into blocks rather than matched in one pattern: a component carries
# an optional footprint and an arbitrary `(fields ...)` tree between the atoms
# that matter, and a single regex spanning that is the kind that matches on a
# fixture and silently not on a real file.
_COMP_SPLIT = re.compile(r'\(comp\b')
_REF = re.compile(r'\(ref "([^"]+)"\)')
_VALUE = re.compile(r'\(value "([^"]*)"\)')
_FOOTPRINT_OF = re.compile(r'\(footprint "([^"]*)"\)')
_NET = re.compile(
    r'\(net\s+\(code "?\d+"?\)\s*\(name "([^"]*)"\)(.*?)(?=\(net\s+\(code|\Z)',
    re.S)
_NODE = re.compile(
    r'\(node\s+\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)'
    r'(?:\s*\(pinfunction "([^"]*)"\))?', re.S)


def read_components(text: str) -> dict[str, Component]:
    """`ref -> Component` for every part in a netlist."""
    # Only the components section: a netlist's libparts carry `(footprint ...)`
    # of their own, and a split over the whole file would read those too.
    body = text.split("(libparts", 1)[0]
    out: dict[str, Component] = {}
    for piece in _COMP_SPLIT.split(body)[1:]:
        ref = _REF.search(piece)
        if ref is None:
            continue
        value = _VALUE.search(piece)
        footprint = _FOOTPRINT_OF.search(piece)
        out[ref.group(1)] = Component(
            ref=ref.group(1),
            value=value.group(1) if value else "",
            footprint=footprint.group(1) if footprint else "")
    return out


def read_netlist(text: str, by_function: bool = False) -> dict[str, list[str]]:
    """`net name -> ["REF:PIN", ...]`, sorted.

    `by_function` swaps the pin NUMBER for its function where the netlist
    records one: `U1:PB2` is how a person states an intention and `U1:7` is
    how the netlist records it, and an assertion is unusable unless both
    resolve to the same pin.

    Measured attempts to answer this by hand in one run: a regex that never
    matched because the closing paren sits on the last node's line, then a
    whitespace collapse that broke paren matching, then a paren counter --
    four rewrites of one throwaway parser, none of them kept.
    """
    return {name: sorted(pair[1 if by_function else 0] for pair in pairs)
            for name, pairs in read_nodes(text).items()}


def read_nodes(text: str) -> dict[str, list[tuple[str, str]]]:
    """`net name -> [("REF:NUMBER", "REF:FUNCTION"), ...]`, one pair per pin.

    Both spellings of the SAME pin, kept together. check_netlist used to take
    the two sorted lists read_netlist returns and zip them, and index i of
    one is not index i of the other once a net has three pins: `U1:1 U1:2
    U1:3` sorts one way and `PB3 PB4 PB5` another. A pin on a net nobody
    declared went unreported, and a pin declared by number was reported as
    undeclared by function. A pin with no recorded function is spelt by its
    number in both halves.
    """
    nets: dict[str, list[tuple[str, str]]] = {}
    for name, body in _NET.findall(text):
        pairs = [(f"{ref}:{number}", f"{ref}:{function or number}")
                 for ref, number, function in _NODE.findall(body)]
        nets[name] = sorted(pairs)
    return nets


# --- boards ----------------------------------------------------------------


@dataclass
class Board:
    footprints: int = 0
    # Copper layer -> track segment count. `segment`, which is what KiCad 10
    # writes; `track` was the KiCad 7 name and grepping for it on a routed
    # board returns zero, which reads exactly like an empty board.
    segments: dict[str, int] = field(default_factory=dict)
    has_outline: bool = False
    # Copper pours, and how many of them carry no copper yet. A zone that
    # exists and is not filled is the dangerous state: DRC reads the board on
    # disk and so checks copper that is not there, while a gerber export
    # refills for the plot and looks right. A keepout is not a pour and is
    # not counted. See unfilled_zones below.
    zones: int = 0
    unfilled: int = 0


_FOOTPRINT = re.compile(r'\(footprint "')
_SEGMENT = re.compile(r'\(segment\b(.*?)(?=\(segment\b|\Z)', re.S)
_LAYER = re.compile(r'\(layer "([^"]+)"')
# `Edge.Cuts` in KiCad 8 and later. `Edge_Cuts` was the older spelling and is
# the one a model reaches for from memory.
OUTLINE_LAYER = "Edge.Cuts"


def read_board(text: str) -> Board:
    """What is actually on a generated board.

    "DRC passed" and "the board has copper on it" are different facts, and a
    run that could not read the second nearly reported the first as a
    finished board.
    """
    found = Board(footprints=len(_FOOTPRINT.findall(text)))
    for body in _SEGMENT.findall(text):
        layer = _LAYER.search(body)
        if layer:
            found.segments[layer.group(1)] = found.segments.get(layer.group(1), 0) + 1
    found.has_outline = OUTLINE_LAYER in text
    found.zones = len(_pours(text))
    found.unfilled = unfilled_zones(text)
    return found


# One straight board-graphic line: its two ends, then (lazily) its own layer --
# the stroke between them carries no layer, so the first `(layer` is the line's.
_GR_LINE = re.compile(
    r'\(gr_line\s*\(start ([-\d.]+) ([-\d.]+)\)\s*\(end ([-\d.]+) ([-\d.]+)\).*?\(layer "([^"]+)"',
    re.S)


def outline_size(text: str) -> tuple[float, float] | None:
    """`(width, height)` in mm of the board edge, or None when there is none.

    Read from the Edge.Cuts lines, which is how the outline step draws it, so
    the agent can state the board's size without parsing the file itself.
    """
    points = []
    for m in _GR_LINE.finditer(text):
        if m.group(5) == OUTLINE_LAYER:
            x0, y0, x1, y1 = (float(m.group(i)) for i in range(1, 5))
            points += [(x0, y0), (x1, y1)]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (max(xs) - min(xs), max(ys) - min(ys))


# `\(zone\b` and not the literal "(zone ": KiCad 10 writes the opener followed
# by a NEWLINE, so a trailing space matches nothing -- measured, it read 0
# zones on a board that has two. The word boundary is what keeps it off
# `(zone_connect`, which appears inside every pad that sets one.
_ZONE_OPEN = re.compile(r"\(zone\b")
_FILLED = "filled_polygon"


def _zone_blocks(text: str) -> list[str]:
    """Each zone's own `(zone ...)` block, read to its closing parenthesis.

    Not simply split on the opener, as this once was: a zone's block is where
    its `(keepout ...)` lives, and the rest of the file -- a footprint's pads,
    the next footprint -- must not be read as part of it. Quoted strings are
    skipped, so a parenthesis in a zone's name does not end it early.
    """
    blocks = []
    for opener in _ZONE_OPEN.finditer(text):
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
        blocks.append(text[opener.start():i + 1])
    return blocks


def _pours(text: str) -> list[str]:
    """The zones that are copper pours: every zone but a keepout (a rule
    area), which forbids copper and so can never be filled -- such as the
    antenna keepout inside RF_Module:ESP32-C6-MINI-1, which on 2026-09-28
    made check_done answer NOT DONE on a finished board."""
    return [block for block in _zone_blocks(text) if "(keepout" not in block]


def unfilled_zones(text: str) -> int:
    """How many copper pours on this board carry no copper yet.

    A zone's fill lives inside it as one or more `filled_polygon` blocks, so a
    pour with none has never been filled -- measured on KiCad 10.0.5, where a
    freshly poured board had 2 zones and 7 filled_polygon blocks and the same
    board before filling had 1 and 0. It is enough for the question being
    asked -- "is any pour empty" -- which is a warning rather than a verdict.
    """
    return sum(1 for block in _pours(text) if _FILLED not in block)


# --- the gate ---------------------------------------------------------------


def check_netlist(text: str, expected: dict) -> tuple[str, list[str]]:
    """`(PASS|FAIL, differences)` for a netlist against a declaration.

    The circuit equivalent of a failing test, and the reason the readers above
    are worth building rather than merely faster. Three recorded runs verified
    connectivity by writing a throwaway parser each time -- one rewrote the
    same script four times -- because there was no way to state what the
    netlist was supposed to say and have something check it.

    A declaration states what must be TRUE, not everything that is: nets it
    does not mention are left alone, so a power rail or an auto-named net does
    not have to be enumerated to assert one channel. Within a net it mentions,
    the check is exact in both directions -- a missing pin is a channel that
    was not wired, and an unexpected pin is how RESET ends up sharing a net
    with GND, which the netlist is the last place to catch before the board.

    A pin may be declared by function (`U1:PB2`) or by number (`U1:7`). A
    declaration that has to be written in pin numbers is one nobody keeps up
    to date, and the netlist records both.
    """
    declared = (expected or {}).get("nets") if isinstance(expected, dict) else None
    # The shape is checked before anything is compared. TOML hands out
    # whatever was typed: `LED_A = "U1:PB2"` is a string, and iterating it
    # declared the pins `U`, `1`, `:`... and reported seven differences that
    # named no pin; `LED_A = 3` raised out of the tool as a TypeError.
    shape = ("expected.toml's [nets] must map each net name to a list of "
             "pins, e.g. LED_A = [\"U1:PB2\", \"R1:2\"]")
    if declared is not None and not isinstance(declared, dict):
        return "FAIL", [shape]
    declared = declared or {}
    for name, wanted in declared.items():
        if not isinstance(wanted, list) or not all(isinstance(p, str) for p in wanted):
            return "FAIL", [f"{name}: {shape}"]
    if not declared:
        return "FAIL", ["the declaration names nothing to check, so this "
                        "would pass over an empty assertion"]

    nodes = read_nodes(text)
    differences: list[str] = []

    for name, wanted in sorted(declared.items()):
        if name not in nodes:
            near = ", ".join(sorted(nodes)) or "none"
            differences.append(
                f"{name}: declared, and the netlist has no such net "
                f"(it has: {near})")
            continue
        pairs = nodes[name]
        # Either spelling satisfies a declared pin, so the two forms are
        # unioned rather than chosen between.
        actual = {spelling for pair in pairs for spelling in pair}
        numbers = " ".join(number for number, _ in pairs)
        for pin in wanted:
            if pin not in actual:
                differences.append(
                    f"{name}: {pin} was declared and is not on it "
                    f"(on it: {numbers})")
        # The other direction, in the netlist's own spelling: a pin that is
        # there and was not asked for. Pairs, not two lists zipped -- see
        # read_nodes for what the zip got wrong.
        for number, alias in pairs:
            if number not in wanted and alias not in wanted:
                differences.append(
                    f"{name}: {alias} is on it and was not declared")

    return ("FAIL" if differences else "PASS"), differences


# --- pin functions -----------------------------------------------------------
#
# The 2026-09-24 stop-condition eval produced a board that passed every check
# and could not be programmed: its net ISP_SCK sat on the ATtiny85's PB3 (SCK
# is PB2). describe_part had shown PB3's name and nothing else, and the
# declaration check_netlist compares against was written with the same
# mistake. What a net's name SAYS it carries is a fact that can be checked
# against what its pins CAN carry.

# Bus words name the same signal at both ends -- a sensor's SDA goes to the
# MCU's SDA -- so they must land on an MCU pin that has the function. End
# words name one end's pin -- a GPS module's TX goes to the MCU's RX, a
# driver's RESET to any GPIO -- so any pin on the net carrying the class
# satisfies them. SS/CS are not words at all: a chip select is any GPIO.
BUS_WORDS = {"MOSI": "MOSI", "MISO": "MISO", "SCK": "SCK", "SCLK": "SCK",
             "SDA": "SDA", "SCL": "SCL"}
# The UART class is spelt with a character no split can produce, so a net
# named UART_TO_PC does not demand it.
UART = "TX/RX"
END_WORDS = {"RESET": "RESET", "RST": "RESET", "NRST": "RESET",
             "TX": UART, "TXD": UART, "RX": UART, "RXD": UART}
_BUS = set(BUS_WORDS.values())
_END = set(END_WORDS.values())
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9+]+")


def words(name: str) -> set[str]:
    """The separator-delimited words of a name, upper-cased, aliases resolved.

    `~{RESET}/PB5` -> {RESET, PB5}; `SPI1_MOSI` -> {SPI1, MOSI}; `TXD` ->
    {TX/RX}. Separators only, so `TXLED` stays one word and states nothing.
    """
    out = set()
    for word in _WORD_SPLIT.split(name):
        if word:
            upper = word.upper()
            out.add(BUS_WORDS.get(upper) or END_WORDS.get(upper) or upper)
    return out


@dataclass(frozen=True)
class PartPins:
    """What the check knows about one symbol's pins."""
    # Pin number -> primary name, as the symbol draws it (`AREF/PB0`).
    names: dict[str, str]
    # Pin number -> functions beyond the name: KiCad alternates plus
    # pin_functions.toml. Empty for a part nobody has described.
    functions: dict[str, tuple[str, ...]]
    # A connector passes signals through; it neither originates nor ends
    # one. So its pin names are demands on the MCU (a programming header's
    # ~{RST} must meet the MCU's RESET) and never satisfy a demand themselves.
    connector: bool = False

    @property
    def provider(self) -> bool:
        """True when its functions are known, so its pins can satisfy a bus
        word. A resistor, a header or an undescribed IC is not one."""
        return any(self.functions.values())

    def words_of(self, number: str) -> set[str]:
        found = words(self.names.get(number, ""))
        for function in self.functions.get(number, ()):
            found |= words(function)
        return found


_LIBSOURCE = re.compile(r'\(libsource\s+\(lib "([^"]+)"\)\s*\(part "([^"]+)"\)')


def read_libsources(text: str) -> dict[str, tuple[str, str]]:
    """`ref -> (library, symbol)` for every part in a netlist."""
    body = text.split("(libparts", 1)[0]
    out: dict[str, tuple[str, str]] = {}
    for piece in _COMP_SPLIT.split(body)[1:]:
        ref = _REF.search(piece)
        source = _LIBSOURCE.search(piece)
        if ref and source:
            out[ref.group(1)] = (source.group(1), source.group(2))
    return out


def _pin_order(number: str) -> tuple[int, str]:
    return (int(number), "") if number.isdigit() else (10**6, number)


def _label(word: str) -> str:
    return "TX or RX" if word == UART else word


def _pin_repr(ref: str, number: str, part: "PartPins") -> str:
    """`U1:28 PC5 [SCL ADC5 PCINT13]` -- a provider pin's name, and its
    functions when it has any, so a false alarm can be checked at a glance
    without a second `describe_part` round trip."""
    functions = part.functions.get(number, ())
    tail = f" [{' '.join(functions)}]" if functions else ""
    return f"{ref}:{number} {part.names.get(number, '?')}{tail}"


def function_mismatches(text: str, lookup) -> list[str]:
    """One line per function a net says it carries and none of its pins can.

    `lookup(library, symbol) -> PartPins | None` is injected: the pure part is
    here, the library files are parts.pin_lookup's business. A net with no
    provider pin (no part whose functions are known) gets no verdict, so an
    undescribed part can only ever be silent, never wrong.
    """
    parts = {}
    for ref, (library, symbol) in read_libsources(text).items():
        found = lookup(library, symbol)
        if found is not None:
            parts[ref] = found

    findings = []
    for net, pairs in sorted(read_nodes(text).items()):
        pins = []
        for spelling, _ in pairs:
            ref, _, number = spelling.rpartition(":")
            if ref in parts:
                pins.append((ref, number, parts[ref]))
        providers = [(ref, number, part) for ref, number, part in pins if part.provider]
        if not providers:
            continue

        # word -> (who asks for it, strict). Strict demands are met only by a
        # provider pin. The net's name asks for bus words strictly and for
        # end words loosely: a driver IC's own ~{RESET} pin meets DRV_RST. A
        # connector passes a signal through and never originates or ends one,
        # so its pin names ask strictly for bus words and RESET too -- a
        # programming header's ~{RST} must reach the MCU's RESET. Any other
        # part's pin names ask for nothing: KiCad names a multi-role pin by
        # one role only (Sensor_Pressure:BMP280's pin 4 is "SCK", its SPI
        # name, and is correctly wired to an MCU's SCL in I2C mode), so the
        # name says nothing about what the net's other end must be.
        asks: dict[str, tuple[str, bool]] = {}
        for word in sorted(words(net) & (_BUS | _END)):
            asks[word] = ("the net's name says", word in _BUS)
        for ref, number, part in pins:
            if part.provider or not part.connector:
                continue
            for word in sorted(words(part.names.get(number, "")) & (_BUS | {"RESET"})):
                if not asks.get(word, ("", False))[1]:
                    asks[word] = (f"{ref}:{number} is named", True)

        for word, (who, strict) in asks.items():
            candidates = providers if strict else [
                (ref, number, part) for ref, number, part in pins if not part.connector]
            if any(word in part.words_of(number) for _, number, part in candidates):
                continue
            owners = {ref: part for ref, _, part in providers}
            where = [f"{ref}:{number} ({part.names[number]})"
                     for ref, part in sorted(owners.items())
                     for number in sorted(part.names, key=_pin_order)
                     if word in part.words_of(number)]
            if not where:
                # None of the net's providers carries this function on ANY
                # pin, so no wiring could satisfy the name -- it describes a
                # software function (an ATtiny85's SoftwareSerial TX/RX, on a
                # part with no hardware UART at all), not a mistake.
                continue
            label = _label(word)
            here = ", ".join(_pin_repr(ref, number, part) for ref, number, part in providers)
            findings.append(
                f"{net}: {who} {label}, and no pin on it can carry {label} "
                f"(on it: {here}); {label} is " + ", ".join(where))
    return findings
