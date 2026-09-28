"""Generated schematics: a KiCad schematic and PDF drawn from the SKiDL
script, checked against the netlist (schematic spec).

The drawing is a reference, never a source -- board.py is. So what matters
about it is that it shows the circuit that was built, and `problems` checks
exactly that: the parts, and which pins are joined, read back out of the
schematic by KiCad itself.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
from collections import Counter

from circuit_mcp import kicad_lib, service

# KiCad's library-configuration warnings: the container has no sym-lib-table
# on KiCad's search path, so every symbol and footprint draws one. Noise on
# every board, like lib_footprint_issues on the DRC side.
CONFIG_NOISE = {"lib_symbol_issues", "footprint_link_issues"}


def files(stem: str) -> tuple[str, str, str]:
    """(schematic, PDF, ERC report) names for a netlist `<stem>.net`."""
    return f"{stem}.kicad_sch", f"{stem}-schematic.pdf", f"{stem}.sch-erc.json"


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


def compare(netlist_text: str, schematic_netlist_text: str) -> list[str]:
    """What differs between SKiDL's netlist and the schematic's; empty when
    they match. Net names are not compared: an unnamed net is N$1 to SKiDL
    and Net-(D1-A) to KiCad."""
    want_refs, want = connectivity(netlist_text)
    got_refs, got = connectivity(schematic_netlist_text)
    out = []
    if want_refs - got_refs:
        out.append("parts missing from the schematic: " + ", ".join(sorted(want_refs - got_refs)))
    if got_refs - want_refs:
        out.append("parts only in the schematic: " + ", ".join(sorted(got_refs - want_refs)))
    for group in sorted(want - got, key=sorted):
        out.append("connected in the netlist, not in the schematic: " + _pins(group))
    for group in sorted(got - want, key=sorted):
        out.append("connected in the schematic, not in the netlist: " + _pins(group))
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
    """The connectivity KiCad reads out of a schematic, as netlist text."""
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "schematic.net"
        done = _kicad(["sch", "export", "netlist", "--format", "kicadsexpr",
                       "-o", str(out), str(sch)], "schematic netlist export")
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
