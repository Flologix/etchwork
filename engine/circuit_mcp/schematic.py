"""Generated schematics: a KiCad schematic and PDF drawn from the SKiDL
script, checked against the netlist (schematic spec).

The drawing is a reference, never a source -- board.py is. So what matters
about it is that it shows the circuit that was built, and `problems` checks
exactly that: the parts, and which pins are joined, read back out of the
schematic by KiCad itself.
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

from circuit_mcp import kicad_lib, paths, service

# KiCad's library-configuration warnings: the container has no sym-lib-table
# on KiCad's search path, so every symbol and footprint draws one. Noise on
# every board, like lib_footprint_issues on the DRC side.
CONFIG_NOISE = {"lib_symbol_issues", "footprint_link_issues"}


def files(stem: str) -> tuple[str, str, str]:
    """(schematic, PDF, ERC report) names for a netlist `<stem>.net`."""
    return f"{stem}.kicad_sch", f"{stem}-schematic.pdf", f"{stem}.sch-erc.json"


def unavailable_file(stem: str) -> str:
    """The record export_schematic writes instead of a drawing when SKiDL's
    generator can't draw this circuit correctly (final review, Critical 1)."""
    return f"{stem}.sch-unavailable.json"


def unavailable_reason(path: pathlib.Path) -> str | None:
    """The one-line reason in an unavailable record; None if it is missing,
    not a regular file, or not a record export_schematic would write."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    reason = record.get("reason") if isinstance(record, dict) else None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return " ".join(reason.split())


def present(path: pathlib.Path) -> bool:
    """Anything at `path`, a dangling symlink included."""
    return path.is_symlink() or path.exists()


def connectivity(netlist_text: str) -> tuple[set[str], set[frozenset[str]]]:
    """(references, pin groups). Power symbols and flags (#PWR, #FLG) are
    drawing-only, and a one-pin net joins nothing, so neither counts."""
    refs = {r for r in kicad_lib.read_components(netlist_text) if not r.startswith("#")}
    groups = set()
    for pins in kicad_lib.read_nodes(netlist_text).values():
        group = frozenset(p for p, _ in pins if not p.startswith("#"))
        if len(group) > 1:
            groups.add(group)
    return refs, groups


def _pins(group) -> str:
    return " ".join(sorted(group))


def compare(netlist_text: str, schematic_netlist_text: str,
            other: str = "the schematic") -> list[str]:
    """What differs between SKiDL's netlist and the schematic's (or `other`
    circuit's, named so in the messages); empty when they match. Net names
    are not compared: an unnamed net is N$1 to SKiDL and Net-(D1-A) to
    KiCad."""
    want_refs, want = connectivity(netlist_text)
    got_refs, got = connectivity(schematic_netlist_text)
    out = []
    if want_refs - got_refs:
        out.append(f"parts missing from {other}: " + ", ".join(sorted(want_refs - got_refs)))
    if got_refs - want_refs:
        out.append(f"parts only in {other}: " + ", ".join(sorted(got_refs - want_refs)))
    for group in sorted(want - got, key=sorted):
        out.append(f"connected in the netlist, not in {other}: " + _pins(group))
    for group in sorted(got - want, key=sorted):
        out.append(f"connected in {other}, not in the netlist: " + _pins(group))
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def erc_summary(report: dict) -> str:
    """One line for the README and the tool's answer."""
    counts = Counter((v.get("severity"), v.get("type"))
                     for sheet in report.get("sheets", [])
                     for v in sheet.get("violations", [])
                     if v.get("type") not in CONFIG_NOISE)
    if not counts:
        return "ERC: clean"
    errors = sum(n for (severity, _), n in counts.items() if severity == "error")
    warnings = sum(n for (severity, _), n in counts.items() if severity == "warning")
    kinds = ", ".join(f"{n} {kind}" for (_, kind), n in sorted(counts.items(), key=lambda kv: (kv[0][0] != "error", kv[0][1])))
    head = ", ".join(x for x in (_plural(errors, "error") if errors else "",
                                 _plural(warnings, "warning") if warnings else "") if x)
    return f"ERC: {head} ({kinds})"


def _kicad(args: list[str], what: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["kicad-cli", *args], capture_output=True, text=True,
                              timeout=service.TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{what} timed out after {service.TIMEOUT}s")
    except OSError as error:
        raise RuntimeError(f"{what} could not run kicad-cli: {error}")


def _check(done: subprocess.CompletedProcess, out: pathlib.Path, what: str) -> None:
    if done.returncode != 0 or not out.is_file():
        raise RuntimeError(f"{what} failed: {(done.stdout + done.stderr)[-600:]}")


def schematic_netlist(sch: pathlib.Path) -> str:
    """The connectivity KiCad reads out of a schematic, as netlist text.

    Runs kicad-cli on a copy in a scratch folder, never on `sch` itself:
    kicad-cli writes a .kicad_prl beside whatever file it opens, and
    `problems` (so this) is what check_done calls to stay read-only. A
    path that is missing, a symlink or not a regular file is refused rather
    than opened."""
    if sch.is_symlink() or not sch.is_file():
        raise RuntimeError(f"schematic netlist export failed: {sch.name} is not a regular file")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        copy = tmp / sch.name
        shutil.copy(sch, copy)
        out = tmp / "schematic.net"
        done = _kicad(["sch", "export", "netlist", "--format", "kicadsexpr",
                       "-o", str(out), str(copy)], "schematic netlist export")
        _check(done, out, "schematic netlist export")
        return out.read_text(errors="replace")


def export_pdf(sch: pathlib.Path, pdf: pathlib.Path) -> None:
    done = _kicad(["sch", "export", "pdf", "-o", str(pdf), str(sch)], "schematic PDF export")
    _check(done, pdf, "schematic PDF export")


def run_erc(sch: pathlib.Path, report: pathlib.Path) -> dict:
    done = _kicad(["sch", "erc", "--format", "json", "--severity-all",
                   "-o", str(report), str(sch)], "schematic ERC")
    _check(done, report, "schematic ERC")
    try:
        return json.loads(report.read_text())
    except ValueError as error:
        raise RuntimeError(f"schematic ERC wrote an unreadable report: {error}")


def problems(sch: pathlib.Path, netlist: pathlib.Path) -> list[str]:
    """Why the schematic does not show the netlist's circuit; empty if it does."""
    try:
        text = schematic_netlist(sch)
    except RuntimeError as error:
        return [str(error)]
    return compare(netlist.read_text(errors="replace"), text)


# Runs in its own python3 process, in the workspace: argv is (script, stem,
# seed, nonce, check). The agent's script builds the circuit; everything it
# would write is switched off first -- SKiDL's module-level generate_*
# functions and the Circuit methods behind them, so
# `default_circuit.generate_netlist(...)` is a no-op too -- so its netlist is
# never touched and any drawing of its own never happens. The real netlist
# writer and generator are kept aside for the driver's own use. The script's
# own folder goes on sys.path, as when run_skidl runs it, so a sibling module
# it imports is found.
#
# Right after the script succeeds the driver prints "circuit built <nonce>"
# on a line of its own; `nonce` is fresh per run, so a script can't print it.
# A caller tells the agent's script failing or exiting early (no marker)
# apart from SKiDL's generator failing on an already-built circuit (marker,
# then a non-zero exit or no schematic file). Then, before anything is added
# to the circuit, SKiDL's real netlist writer writes the circuit's own
# netlist to `check` -- a temp path outside the workspace -- and the driver
# prints "netlist written <nonce>": export_schematic compares it with
# <stem>.net, so a script edited since run_skidl (or the wrong script) is
# refused instead of passing for a generator that draws wrongly (residual
# fix, Important). Then power flags, then SKiDL's generator with the fixed
# settings (schematic spec §4). SKiDL finds the default circuit in builtins.
#
# seed: SKiDL's placer seeds Python's global `random` from `options.get
# ("seed")`, which is None (i.e. system entropy) unless given. On a denser
# board that can't be routed automatically, SKiDL falls back to a
# labels-only drawing, and an unseeded run occasionally lands two unrelated
# labels on the same point, which KiCad then reads back as one net --
# verified against the backoff1 fixture: unseeded, 9 of 10 runs produced a
# spurious mismatch (e.g. GND merged with PWR_LED). `export_schematic` below
# tries a fixed list of seeds and keeps the first drawing that matches.
#
# A fixed seed alone is not enough, though: drawing backoff1 once crashed
# inside SKiDL's placer (a KeyError in its switchbox-coalescing code) with
# seed 42, and the crash did not reproduce over 6 reruns of the exact same
# script and seed. SKiDL's placer also iterates Python sets and dicts of
# parts and nets, and their iteration order -- randomized per process by
# CPython's default hash seed -- feeds into that placement and coalescing
# code alongside the `random` seed. `export_schematic` fixes PYTHONHASHSEED
# for the driver subprocess (below) so that order, and so the whole draw, is
# the same across runs of the same seed.
DRIVER = r"""
import builtins, os, runpy, sys
import skidl
from skidl import Part, Pin

script, stem, seed, nonce, check = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
draw = skidl.Circuit.generate_schematic
write_netlist = skidl.Circuit.generate_netlist
noop = lambda *args, **kwargs: None
for name in ("generate_netlist", "generate_schematic", "generate_pcb"):
    setattr(skidl, name, noop)
    setattr(skidl.Circuit, name, noop)
sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
runpy.run_path(script, run_name="__main__")
print("\ncircuit built " + nonce, flush=True)

netlist = write_netlist(builtins.default_circuit, file_=check, do_backup=False)
with open(check, "w") as f:
    f.write(str(netlist))
print("netlist written " + nonce, flush=True)

flagged = []
for net in list(builtins.default_circuit.nets):
    kinds = {pin.func for pin in net.pins}
    if Pin.types.PWRIN in kinds and Pin.types.PWROUT not in kinds:
        flag = Part("power", "PWR_FLAG")
        flag[1] += net
        flagged.append(net.name)
print("power flags: " + (", ".join(sorted(flagged)) or "none"))
draw(builtins.default_circuit, filepath=".", top_name=stem, title=stem, flatness=1.0,
     auto_stub=True, seed=seed)
"""

# Tried in order (see the note above DRIVER); any fixed values work, these
# are not otherwise significant.
SEEDS = (42, 7, 1234, 99, 2026)

# The driver's file name: SKiDL names its log and ERC files after it.
DRIVER_NAME = "_etchwork_schematic"


def _tidy(stem: str, before: set[str]) -> None:
    """Remove what a run leaves in the workspace besides its outputs, and
    only that: SKiDL's log and ERC files (named after the driver), the
    auto-stub loop's `<stem>-erc.rpt`, and a __pycache__ the run created.
    Anything else new in the workspace -- a user's file, say -- stays."""
    for entry in paths.WORKSPACE.iterdir():
        name = entry.name
        if name.startswith(DRIVER_NAME) or name == f"{stem}-erc.rpt":
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        elif name == "__pycache__" and name not in before and entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)


class _Folder(Exception):
    """An output path is a real folder: never removed, reported instead."""


def _clear(outputs) -> None:
    """Unlink the run's outputs, whatever is there (a symlink too, never
    what it points to). A real folder at one of them is not the run's to
    remove: raise _Folder instead."""
    for path in outputs:
        if path.is_dir() and not path.is_symlink():
            raise _Folder(path.name)
        if present(path):
            path.unlink()


def _not_available(stem: str, outputs, record: pathlib.Path, reason: str,
                   details: list[str]) -> str:
    """No drawing to keep: record why instead (final review, Critical 1).
    Whatever is at the record path goes first and the record is created
    afresh, never opened through a symlink."""
    _clear([*outputs, record])
    body = json.dumps({"reason": reason, "details": details,
                       "attempts": len(SEEDS)}, indent=2) + "\n"
    fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    return "\n".join([
        f"SCHEMATIC NOT AVAILABLE: {reason} — the board can still be finished; "
        "the README will say so",
        f"  wrote {record.name} (check_done accepts it in place of the schematic)",
        *(f"    - {d}" for d in details)])


def export_schematic(script: str, netlist: str) -> str:
    try:
        script_path, netlist_path = paths.inside(script), paths.inside(netlist)
    except ValueError as error:
        return f"{script} / {netlist}: {error}"
    if not script_path.is_file():
        return f"no such script in the project: {script}"
    if not netlist_path.is_file():
        return f"no such netlist in the project: {netlist} (run `run_skidl` first)"
    stem = netlist_path.stem
    before = {p.name for p in paths.WORKSPACE.iterdir()}
    try:
        return _export(script, script_path, netlist, netlist_path, before)
    except _Folder as folder:
        _tidy(stem, before)
        return (f"the schematic could not be written: {folder} is a folder, not a file; "
                "move or rename it, then run export_schematic again")


def _export(script: str, script_path: pathlib.Path, netlist: str,
            netlist_path: pathlib.Path, before: set[str]) -> str:
    stem = netlist_path.stem
    outputs = [paths.WORKSPACE / n for n in files(stem)]
    sch, pdf, erc = outputs
    record = paths.WORKSPACE / unavailable_file(stem)
    if record.is_dir() and not record.is_symlink():
        raise _Folder(record.name)
    # An earlier run's drawing must never pass for this run's: gone before
    # the first attempt. The unavailable record is only replaced once this
    # run has an outcome.
    _clear(outputs)
    flags = "power flags: none"
    mismatch: list[str] = []
    drawn_any = False
    crash_output = ""
    # See the note above DRIVER: fixing the hash seed makes the set/dict
    # iteration order SKiDL's placer depends on the same on every run.
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    nonce = secrets.token_hex(16)
    built, written = f"circuit built {nonce}", f"netlist written {nonce}"
    with tempfile.TemporaryDirectory() as tmp:
        driver = pathlib.Path(tmp) / f"{DRIVER_NAME}.py"
        driver.write_text(DRIVER)
        check = pathlib.Path(tmp) / "circuit.net"
        for attempt, seed in enumerate(SEEDS, start=1):
            if attempt > 1:
                _tidy(stem, before)
                _clear(outputs)
            if present(check):
                check.unlink()
            try:
                done = subprocess.run(
                    [sys.executable, str(driver), str(script_path), stem, str(seed), nonce,
                     str(check)],
                    cwd=paths.WORKSPACE, capture_output=True, text=True,
                    timeout=service.TIMEOUT, env=env)
            except subprocess.TimeoutExpired:
                _tidy(stem, before)
                _clear(outputs)
                return f"{script} timed out after {service.TIMEOUT}s while drawing the schematic"
            output = (done.stdout + done.stderr).strip()
            lines = done.stdout.splitlines()
            if built not in lines:
                # The agent's script failed (or exited) before the circuit
                # was built: nothing was drawn, and a different seed changes
                # nothing, so don't retry.
                _tidy(stem, before)
                _clear(outputs)
                return (f"the schematic could not be drawn: {script} failed or exited before "
                        f"building the circuit (exit {done.returncode}):\n{output[-1500:]}")
            if written not in lines or not check.is_file():
                # Without the circuit's own netlist there is no telling a
                # generator bug from a script that isn't <stem>.net's.
                _tidy(stem, before)
                _clear(outputs)
                return (f"the schematic could not be drawn: SKiDL could not write the netlist of "
                        f"{script}'s circuit to check it against {netlist_path.name} "
                        f"(exit {done.returncode}):\n{output[-1500:]}")
            differ = compare(netlist_path.read_text(errors="replace"),
                             check.read_text(errors="replace"), "the script's circuit")
            if differ:
                # Not the netlist's circuit: every drawing would mismatch,
                # and that is not the generator's fault. No record, no
                # retry (residual fix, Important).
                _tidy(stem, before)
                _clear(outputs)
                return "\n".join([
                    f"the script's circuit differs from {netlist_path.name}: run `run_skidl` "
                    f"first (or pass the script that wrote it)",
                    *(f"    - {d}" for d in differ[:5]),
                    *([f"    ... and {len(differ) - 5} more"] if len(differ) > 5 else [])])
            if done.returncode != 0 or not sch.is_file():
                # The circuit built and is the netlist's; SKiDL's generator
                # crashed drawing it. Retry with the next seed.
                crash_output = output
                continue
            drawn_any = True
            flags = next((line for line in output.splitlines() if line.startswith("power flags:")),
                         "power flags: none")
            mismatch = problems(sch, netlist_path)
            if not mismatch:
                break
        else:
            # No attempt drew a matching schematic of the netlist's own
            # circuit: never keep a wrong drawing, and never block a correct
            # board on the generator.
            _tidy(stem, before)
            if drawn_any:
                reason = (f"SKiDL's schematic generator drew this circuit wrongly with every one "
                          f"of {len(SEEDS)} seeds (its drawing does not match the netlist)")
                details = mismatch
            else:
                reason = f"SKiDL's schematic generator crashed with every one of {len(SEEDS)} seeds"
                details = crash_output[-1500:].splitlines()[-10:]
            return _not_available(stem, outputs, record, reason, details)
    _tidy(stem, before)
    # The script ran again with this drawing: it may have put something at
    # the PDF or ERC path.
    _clear([pdf, erc])
    try:
        export_pdf(sch, pdf)
        report = run_erc(sch, erc)
    except RuntimeError as error:
        return f"the schematic was drawn, but KiCad could not process it: {error}"
    _clear([record])
    return "\n".join([f"EXPORTED {sch.name}, {pdf.name} and {erc.name}",
                      f"  {flags}",
                      f"  drawn with seed {seed} (attempt {attempt} of {len(SEEDS)})",
                      f"  {erc_summary(report)}",
                      "  schematic matches the netlist"])
