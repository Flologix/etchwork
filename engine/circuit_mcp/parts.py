"""Find a KiCad part by what it is, rather than by guessing what it is called.

**The failure this exists to remove.** Asked for a 12V automotive indicator,
the agent designed one correctly -- reverse-polarity diode, a resistor sized
for 12V, a note about transient suppression -- and then could not build it,
because it guessed `Device:Diode` and the generic diode is `Device:D`. It fell
back to grepping a 151,000-line symbol file, read thirty lines of it, and ran
out of steps having produced no netlist. The design was right and the string
was wrong.

Two guesses, not one: the footprint was `Diode_THT:DO-41`, which is really
`Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal`. Both are answered here.

**Pin names are part of the answer.** `led['A']` and `led['C']` are guesses
until something states them, and a wrong pin name is another round trip. A
part is not usable from its name alone.

Reads KiCad's own libraries inside the image, plus any footprint libraries
the project brings itself in `fp/<Library>.pretty/` (see fp_libraries).
"""
import functools
import pathlib
import re
import tomllib

from circuit_mcp import kicad_lib, paths

# Past this many, a listing stops being an answer. MAX_TOOL_OUTPUT is 8192
# characters and a search for "r" against KiCad's real libraries matches
# thousands; a tool that returns all of them has spent the window to say
# nothing useful.
MAX_HITS = 12

# A top-level symbol: one tab, unlike the sub-units nested inside it.
_SYMBOL = re.compile(r'^\t\(symbol "([^"]+)"', re.MULTILINE)
_PROPERTY = r'\(property "{}" "([^"]*)"'
# `(name "K" (effects ...)) (number "2" (effects ...))` -- the effects block
# has nested parens, so the two cannot be matched as adjacent. Non-greedy to
# the NEXT number, which is the one belonging to this name. Written against a
# real Device.kicad_sym after a hand-simplified fixture passed and the real
# file produced no pins at all.
_PIN = re.compile(r'\(name "([^"]*)".*?\(number "([^"]*)"', re.S)


def _symbol_blocks(text: str):
    """`(name, block)` for each top-level symbol, in file order."""
    starts = [(m.group(1), m.start()) for m in _SYMBOL.finditer(text)]
    for i, (name, at) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        yield name, text[at:end]


def _describe(library: str, name: str, block: str) -> str:
    description = re.search(_PROPERTY.format("Description"), block)
    pins = _PIN.findall(block)
    line = f"  {library}:{name}"
    if description and description.group(1):
        line += f" -- {description.group(1)}"
    if pins:
        # Number first, because that is what indexing uses; the name is what
        # makes `led['A']` legible.
        shown = ", ".join(f"{number}={pin_name}" if pin_name else number
                          for pin_name, number in pins)
        line += f"\n      pins: {shown}"
    return line


def _score(query: str, words: list[str], haystack: str) -> int:
    """How well `haystack` answers the query. 0 means it does not.

    Any word rather than all of them, because the user's vocabulary is not
    KiCad's: "pin header" finds nothing under an all-words rule, since every
    description says "connector" and none says "header". Measured -- the agent
    concluded no terminal existed and substituted something else while
    Connector:Screw_Terminal_01x02 sat in the library.

    Loosening alone would bury the answer, so the whole phrase scores highest
    and each matched word adds one. Ranking is what makes any-word safe.
    """
    if query and query in haystack:
        return 10 + len(words)
    hits = sum(1 for word in words if word in haystack)
    if isinstance(words, _all_words) and hits < len(words):
        return 0
    return hits


class _all_words(list):
    """The same words, marked as "every one of these must appear"."""


def _terms(needle: str) -> tuple[str, list[str]]:
    # Words of one character match everything and rank nothing.
    return needle, [w for w in needle.split() if len(w) > 1]


# --- symbol libraries: KiCad's, and the project's own --------------------------
#
# The datasheet-parts design, section 5.3: a part make_part builds gets its own
# symbol in `symbols/<Library>.kicad_sym`, used as Part("<Library>", "<Name>").
# The same rules as fp/ for footprints: the project's library is found first
# and replaces KiCad's of the same name, and a symlink out of the project is
# not a library of this project.

PROJECT_SYMBOLS = "symbols"


def project_symbol_libraries() -> list[pathlib.Path]:
    """The project's own symbol libraries: `symbols/*.kicad_sym` inside it."""
    try:
        folder = paths.inside(PROJECT_SYMBOLS)
    except ValueError:
        return []
    if not folder.is_dir():
        return []
    libraries = []
    for library in sorted(folder.glob("*.kicad_sym")):
        try:
            resolved = paths.inside(f"{PROJECT_SYMBOLS}/{library.name}")
        except ValueError:
            continue
        if resolved.is_file():
            libraries.append(resolved)
    return libraries


def symbol_libraries() -> dict[str, pathlib.Path]:
    """Every symbol library by name: KiCad's, then the project's, which
    replaces one of KiCad's with the same name (SKiDL searches it first)."""
    libraries = {}
    root = paths.SYMBOL_ROOT
    for library in sorted(root.glob("*.kicad_sym")) if root.is_dir() else []:
        libraries[library.stem] = library
    for library in project_symbol_libraries():
        libraries[library.stem] = library
    return libraries


# Run before the agent's own script (run_skidl, export_schematic's driver):
# SKiDL searches ['.', KiCad's symbols], and the project's symbols/ goes
# right after '.', ahead of KiCad's -- the order symbol_libraries() answers in.
SKIDL_PRELUDE = r"""
import os as _os, skidl as _skidl
_symbols = _os.path.realpath("symbols")
if _os.path.isdir(_symbols) and _symbols.startswith(_os.path.realpath(".") + _os.sep):
    for _tool, _paths in _skidl.lib_search_paths.items():
        if _tool.startswith("kicad") and _symbols not in _paths:
            _paths.insert(1 if _paths[:1] == ["."] else 0, _symbols)
"""


def _search_symbols(needle: str, words: list[str] | None = None) -> list[str]:
    query, words = (needle, words) if words is not None else _terms(needle)
    scored = []
    for path in sorted(symbol_libraries().values(), key=lambda p: p.stem):
        text = path.read_text(errors="replace")
        for name, block in _symbol_blocks(text):
            keywords = re.search(_PROPERTY.format("ki_keywords"), block)
            description = re.search(_PROPERTY.format("Description"), block)
            haystack = " ".join([name, keywords.group(1) if keywords else "",
                                 description.group(1) if description else ""]).lower()
            score = _score(query, words, haystack)
            if score:
                scored.append((-score, f"{path.stem}:{name}",
                               _describe(path.stem, name, block)))
    return [line for _, _, line in sorted(scored)]


# --- footprint libraries: KiCad's, and the project's own ---------------------
#
# A part KiCad has no footprint for (an ODU receptacle, on the live run of
# 2026-09-28) needs one drawn from its datasheet, and the project is where it
# lives: `fp/<Library>.pretty/<Name>.kicad_mod`, used as `<Library>:<Name>`.
# Before this, the tools' refusal SAID "this project's symbols/ and fp/ are
# the only places looked" -- text left from the harness this was extracted
# from -- while nothing read fp/, and the agent spent two server restarts on
# it before stopping.

PROJECT_FP = "fp"


def project_fp_libraries() -> list[pathlib.Path]:
    """The project's own footprint libraries: every `fp/*.pretty` folder that
    resolves inside the project. A symlink out of it -- the folder or one
    library -- is not a library of this project, and is skipped."""
    try:
        folder = paths.inside(PROJECT_FP)
    except ValueError:
        return []
    if not folder.is_dir():
        return []
    libraries = []
    for library in sorted(folder.glob("*.pretty")):
        try:
            resolved = paths.inside(f"{PROJECT_FP}/{library.name}")
        except ValueError:
            continue
        if resolved.is_dir():
            libraries.append(resolved)
    return libraries


def fp_libraries() -> dict[str, pathlib.Path]:
    """Every footprint library by name: KiCad's, then the project's.

    A project library with the same name as one of KiCad's REPLACES it, as a
    whole: that is what kinet2pcb does when run_layout hands it the project's
    libraries, and describing a footprint the board builder cannot find would
    be worse than not finding it.
    """
    libraries = {}
    root = paths.FP_ROOT
    for library in sorted(root.glob("*.pretty")) if root.is_dir() else []:
        libraries[library.stem] = library
    for library in project_fp_libraries():
        libraries[library.stem] = library
    return libraries


def _search_footprints(needle: str, words: list[str] | None = None) -> list[str]:
    query, words = (needle, words) if words is not None else _terms(needle)
    scored = []
    for name, library in sorted(fp_libraries().items()):
        for path in sorted(library.glob("*.kicad_mod")):
            haystack = f"{name} {path.stem}".lower()
            score = _score(query, words, haystack)
            if score:
                scored.append((-score, f"{name}:{path.stem}",
                               f"  {name}:{path.stem}"))
    return [line for _, _, line in sorted(scored)]


def find_part(query: str) -> str:
    """Search KiCad's symbol and footprint libraries.

    `query` is what the part IS -- "diode", "electrolytic", "DO-41",
    "pin header" -- not what it might be called.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return "give something to search for, e.g. find_part('diode')"

    # All the words first, any of them only if that finds nothing. Requiring
    # every word is what keeps precision -- "pin header" under an any-word
    # rule returns 616 symbols led by a 20-pin JTAG connector, which is not an
    # answer. The loose pass exists for the vocabulary gap ("header" where
    # KiCad says "connector") and should not fire when the strict one works.
    # Per kind, not overall. "pin header" matches footprints strictly, via
    # "PinHeader", and no symbol -- so a global fallback never fires and the
    # answer is half a part, which is the failure this tool exists to remove.
    _, words = _terms(needle)
    symbols = (_search_symbols(needle, _all_words(words))
               or _search_symbols(needle, words))
    footprints = (_search_footprints(needle, _all_words(words))
                  or _search_footprints(needle, words))
    if not symbols and not footprints:
        return (f"nothing matching {query!r} anywhere in KiCad's symbols or "
                f"footprints. Check the spelling, or search for what the "
                f"part IS rather than its part number.")

    out = []
    for label, hits, note in (("symbols", symbols, "Part('<library>', '<name>')"),
                              ("footprints", footprints, "footprint='<library>:<name>'")):
        if not hits:
            continue
        out.append(f"{len(hits)} {label} matching {query!r} -- {note}:")
        out.extend(hits[:MAX_HITS])
        if len(hits) > MAX_HITS:
            out.append(f"  ... and {len(hits) - MAX_HITS} more; search for "
                       f"something narrower")
    if footprints and not symbols:
        # The shape of a real failure: 278 footprints, no symbol, and nothing
        # saying the symbol library was simply absent. A footprint without a
        # symbol cannot be used -- SKiDL needs both.
        out.append(f"  (no symbol matched {query!r} -- a footprint alone cannot "
                   f"be placed. Search for the part rather than the package.)")
    if symbols and not footprints:
        # The same half-part, the other way round, and the one that actually
        # bit. Asked for a battery, this returned Device:Battery_Cell and said
        # nothing about footprints; the model used the SYMBOL id as a
        # footprint and kinet2pcb died with KeyError: 'Device' -- after the
        # netlist was written, so the run looked like it had got somewhere.
        # Saying "cannot be used as a footprint" out loud is the point: the
        # silence is what reads as permission.
        libraries = sorted(fp_libraries())
        if not libraries:
            have = "none"
        elif len(libraries) > MAX_HITS:
            have = (", ".join(libraries[:MAX_HITS]) + ", and "
                    f"{len(libraries) - MAX_HITS} more (use a more specific query)")
        else:
            have = ", ".join(libraries)
        out.append(f"  (no footprint matched {query!r} -- a symbol alone cannot "
                   f"be placed, and a symbol id cannot be used as a footprint. "
                   f"Footprint libraries here: {have}.)")
    return "\n".join(out)


# A netlist's footprint field: `(footprint "LIB:NAME")`, LIB naming a footprint
# library (fp_libraries) and NAME a .kicad_mod inside it.
_FOOTPRINT_FIELD = re.compile(r'\(footprint "([^":]+):([^"]+)"')


def unresolved_footprints(netlist_text: str, libraries) -> list[str]:
    """Every `LIB:NAME` in a netlist that this project cannot resolve.

    Two distinct faults with one consequence: no library has that name, or
    one does and the footprint is not in it. Both end the same way --
    kinet2pcb raising `KeyError: '<LIB>'` from inside a dependency, behind a
    pcbnew assertion about register_action() that has nothing to do with it.
    A live model read that twice and called the tool again unchanged.

    `libraries` is fp_libraries()'s map, or a folder of `.pretty` libraries.
    Pure: takes the text and the libraries, so the check costs no Docker and
    is tested without one. Ordered and de-duplicated, because a netlist names
    the same footprint once per component and a list of forty identical lines
    is not a better answer than one.
    """
    if not isinstance(libraries, dict):
        root = pathlib.Path(libraries)
        libraries = {d.stem: d for d in root.glob("*.pretty")} if root.is_dir() else {}
    names = sorted(libraries)
    out, seen = [], set()
    for library, name in _FOOTPRINT_FIELD.findall(netlist_text):
        pair = f"{library}:{name}"
        if pair in seen:
            continue
        seen.add(pair)
        if library not in libraries:
            out.append(f"  {pair} -- no footprint library {library!r} in KiCad's libraries "
                       f"or the project's fp/<Library>.pretty/ "
                       f"(here: {', '.join(names) or 'none'})")
        elif not (libraries[library] / f"{name}.kicad_mod").is_file():
            out.append(f"  {pair} -- {library!r} is here but has no {name!r}")
    return out


# --- the four questions, answered in one call each -------------------------
#
# Measured 2026-09-11 across three recorded autonomous runs: 56% of 181 shell
# commands were four questions about files this project already vendors. See
# app/kicad_lib.py for the parsing and for what the hand-written versions got
# wrong.


def _not_a_name(wanted: str) -> str | None:
    """Why `wanted` cannot be a `Library:Part`, or None when it can.

    These two questions are answered by globbing under the library folders,
    and a glob takes `..` as a path component like any other: describe_part
    ("../../elsewhere/Secret:S") read a .kicad_sym outside the workspace and
    printed its pins. The agent process is not itself under Landlock -- the
    file tools ARE the read boundary -- so this refusal happens here, before
    any path is built. The library roots (paths.SYMBOL_ROOT, paths.FP_ROOT)
    and paths.WORKSPACE are that boundary; `_inside` is what checks a
    resolved match against them.
    """
    if "/" in wanted or "\\" in wanted or wanted.startswith("."):
        return (f"{wanted!r} is a path, not a name. Give Library:Name. Symbols come "
                f"from KiCad's libraries; footprints from the project's "
                f"fp/<Library>.pretty/ folders, then KiCad's.")
    return None


def _inside(found):
    """Only matches that resolve under a library root or the workspace."""
    roots = [paths.SYMBOL_ROOT.resolve(), paths.FP_ROOT.resolve(), paths.WORKSPACE]
    kept = []
    for path in found:
        resolved = pathlib.Path(path).resolve()
        if any(resolved == r or r in resolved.parents for r in roots):
            kept.append(path)
    return kept


def describe_part(symbol: str) -> str:
    """Every pin of a symbol, with its number, name and electrical type."""
    wanted = symbol.strip()
    if refused := _not_a_name(wanted):
        return refused
    library, _, name = wanted.rpartition(":")
    libraries = symbol_libraries()
    searched = [libraries[library]] if library in libraries else [] if library \
        else [libraries[n] for n in sorted(libraries)]
    matches = _inside(searched)
    if not matches:
        where = f"{library}.kicad_sym" if library else "symbol library"
        return (f"no {where} among KiCad's symbol libraries or the project's symbols/. "
                f"find_part searches them by what a part IS.")

    misses = []
    for path in matches:
        found = kicad_lib.read_symbol(path.read_text(errors="replace"), name,
                                      library=path.stem)
        if isinstance(found, str):
            # A "not in this library" from a library the caller did not name
            # is noise; anything else is the answer.
            if library or "no symbol" not in found:
                misses.append(f"  {path.stem}: {found}")
            continue
        return _render_symbol(found, _functions(path.stem, found))
    if misses:
        return "\n".join(misses)
    return (f"no symbol {name!r} in KiCad's libraries. find_part "
            f"searches by what a part IS.")


_TABLE_PATH = pathlib.Path(__file__).with_name("pin_functions.toml")


@functools.cache
def _table() -> dict:
    """pin_functions.toml: functions KiCad's AVR symbols do not carry."""
    return tomllib.loads(_TABLE_PATH.read_text())


def _functions(library: str, found) -> dict[str, tuple[str, ...]]:
    """`pin number -> functions beyond its name`: KiCad's alternates, then the
    table's row for the symbol that defines the pins (after `extends`)."""
    row = _table().get(f"{library}:{found.inherited_from or found.name}", {})
    listed = row.get("pins", {})
    out = {}
    for pin in found.pins:
        functions = tuple(dict.fromkeys([*pin.alternates, *listed.get(pin.number, [])]))
        if functions:
            out[pin.number] = functions
    return out


@functools.lru_cache(maxsize=256)
def _part_pins(where: str, stamp: int, library: str, symbol: str):
    """Cached per file and modification time: make_part rewrites the
    project's symbol libraries, and a stale answer would name old pins."""
    path = pathlib.Path(where)
    if not path.is_file() or not _inside([path]):
        return None
    found = kicad_lib.read_symbol(path.read_text(errors="replace"), symbol, library=library)
    if isinstance(found, str):
        return None
    return kicad_lib.PartPins(names={p.number: p.name for p in found.pins},
                              functions=_functions(library, found),
                              connector=library.startswith("Connector"))


def pin_lookup(library: str, symbol: str):
    """`PartPins` for a netlist's `(libsource (lib ..) (part ..))`, or None.

    The names come from the netlist the agent's script wrote, so they are
    refused as paths exactly as describe_part refuses them.
    """
    if _not_a_name(f"{library}:{symbol}"):
        return None
    path = symbol_libraries().get(library)
    if path is None:
        return None
    return _part_pins(str(path), path.stat().st_mtime_ns, library, symbol)


def _render_symbol(found, functions: dict[str, tuple[str, ...]] | None = None) -> str:
    origin = (f" (inherited from {found.inherited_from})"
              if found.inherited_from else "")
    lines = [f"{found.library}:{found.name}"
             + (f" -- {found.description}" if found.description else ""),
             f"  {len(found.pins)} pins{origin}:"]
    for pin in found.pins:
        line = f"    {pin.number:<4} {pin.name:<16} {pin.kind}"
        # What else the pin can be. Only what its name does not already say:
        # AREF/PB0 lists MOSI, not AREF again.
        named = {w.upper() for w in re.split(r"[^A-Za-z0-9+]+", pin.name) if w}
        extra = [f for f in (functions or {}).get(pin.number, ()) if f.upper() not in named]
        if extra:
            line += f"  functions: {' '.join(extra)}"
        lines.append(line)
    if found.footprint:
        lines.append(f"  footprint: {found.footprint}")
    if not found.pins:
        lines.append("  (this symbol defines no pins -- a graphic or a power "
                     "symbol, not a part to wire)")
    return "\n".join(lines)


def describe_footprint(footprint: str) -> str:
    """A footprint's pads and how much board room it needs."""
    wanted = footprint.strip()
    if refused := _not_a_name(wanted):
        return refused
    library, _, name = wanted.rpartition(":")
    libraries = fp_libraries()
    if not libraries:
        return "no footprint libraries found in the image or the project."
    searched = [libraries[library]] if library in libraries else [] if library \
        else list(libraries.values())
    matches = _inside([d / f"{name}.kicad_mod" for d in searched
                       if (d / f"{name}.kicad_mod").is_file()])
    if not matches:
        return (f"no footprint {wanted!r} here. Libraries: "
                f"{', '.join(sorted(libraries))}. find_part searches them by name; "
                f"a footprint KiCad lacks goes in the project's "
                f"fp/<Library>.pretty/<Name>.kicad_mod.")

    path = matches[0]
    found = kicad_lib.read_footprint(path.read_text(errors="replace"),
                                     name=f"{path.parent.stem}:{path.stem}")
    width, height = found.extent
    pads = ", ".join(found.electrical) or "none"
    lines = [found.name,
             f"  {len(found.electrical)} electrical pads: {pads}"
             + (f"  (+{found.mounting} mounting)" if found.mounting else ""),
             f"  needs {width} x {height} mm of board"]
    if len(matches) > 1:
        lines.append(f"  ({len(matches)} libraries have a {name!r}; this is "
                     f"{path.parent.stem})")
    return "\n".join(lines)


def netlist_nets(netlist: str, names: bool = True) -> str:
    """What is connected to what, from a generated netlist."""
    try:
        path = paths.inside(netlist)
    except ValueError as error:
        return f"{netlist}: {error}"
    if not path.is_file():
        return f"no such file in the workspace: {netlist}"
    text = path.read_text(errors="replace")
    nets = kicad_lib.read_netlist(text, by_function=names)
    if not nets:
        return (f"{netlist} names no nets. If the SKiDL script ran, it may "
                f"have written somewhere else -- generate_netlist() names the "
                f"file after the script unless told otherwise.")
    width = max(len(n) for n in nets)
    lines = [f"{len(nets)} nets in {netlist}:"]
    for name, pins in sorted(nets.items()):
        lines.append(f"  {name:<{width}}  {' '.join(pins)}  ({len(pins)} pins)")
    lonely = [n for n, pins in nets.items() if len(pins) < 2]
    if lonely:
        lines.append(f"  note: {', '.join(lonely)} reach only one pin, which "
                     f"is a net that connects nothing.")
    return "\n".join(lines)


def describe_board(board: str) -> str:
    """What is actually on a generated board: parts, copper and an outline."""
    try:
        path = paths.inside(board)
    except ValueError as error:
        return f"{board}: {error}"
    if not path.is_file():
        return f"no such file in the workspace: {board}"
    found = kicad_lib.read_board(path.read_text(errors="replace"))
    copper = ", ".join(f"{n} on {layer}" for layer, n in sorted(found.segments.items())) \
        or "none"
    lines = [f"{board}:",
             f"  {found.footprints} footprints",
             f"  track segments: {copper}",
             f"  board outline ({kicad_lib.OUTLINE_LAYER}): "
             f"{'yes' if found.has_outline else 'NO -- the board has no edge'}"]
    # Reported even when there are none, because zero is the answer people get
    # wrong: a board built from a netlist has no pour at all, and a ground
    # plane is something you add rather than something that arrives.
    if not found.zones:
        lines.append("  copper pours: none (fill_zones adds one)")
    elif found.unfilled:
        lines.append(f"  copper pours: {found.zones}, and {found.unfilled} "
                     f"NOT FILLED -- DRC is reading copper that is not there; "
                     f"run fill_zones, then run_kicad_check again")
    else:
        lines.append(f"  copper pours: {found.zones}, all filled")
    if not found.segments:
        lines.append("  note: no copper. A DRC that passes on this is passing "
                     "on an unrouted board.")
    return "\n".join(lines)


def check_netlist(netlist: str, expected: str) -> str:
    """Check a generated netlist against a declaration of what it should be.

    The circuit equivalent of running the tests. `expected` is a TOML file
    written BEFORE the SKiDL script, so it fails first and for the right
    reason:

        [nets]
        LED_A = ["U1:PB2", "R1:2"]
        BTN_A = ["U1:PB0", "SW1:1"]
    """
    import tomllib

    try:
        net_path = paths.inside(netlist)
    except ValueError as error:
        return f"{netlist}: {error}"
    try:
        spec_path = paths.inside(expected)
    except ValueError as error:
        return f"{expected}: {error}"
    for path, name in ((net_path, netlist), (spec_path, expected)):
        if not path.is_file():
            return f"no such file in the workspace: {name}"
    try:
        declaration = tomllib.loads(spec_path.read_text(errors="replace"))
    except tomllib.TOMLDecodeError as exc:
        return f"{expected} is not readable TOML: {exc}"

    text = net_path.read_text(errors="replace")
    verdict, differences = kicad_lib.check_netlist(text, declaration)
    # Checked on every call and independent of the declaration: the
    # declaration and the script can share one mistake, and on 2026-09-24
    # they did (ISP_SCK on the ATtiny85's PB3).
    mismatches = kicad_lib.function_mismatches(text, pin_lookup)
    if verdict == "PASS" and not mismatches:
        count = len((declaration.get("nets") or {}))
        return f"PASS -- {netlist} matches all {count} declared net(s)"
    lines = []
    if verdict != "PASS":
        lines.append(f"FAIL -- {len(differences)} difference(s) between {netlist} and "
                     f"{expected}:")
        lines += [f"  {d}" for d in differences]
    else:
        lines.append(f"FAIL -- {netlist} matches {expected}, but its pin functions do not:")
    if mismatches:
        lines.append("Pin functions (a net says a function no MCU pin on it can "
                     "carry). Fix the wiring. Only if the net is meant to be a "
                     "plain GPIO (a bit-banged bus, a button), drop the "
                     "function word from its name:")
        lines += [f"  {m}" for m in mismatches]
    return "\n".join(lines)
