"""Package a finished workspace for the customer (board-service spec §4.3).

`python3 -m circuit_mcp.package` runs in its own container after the
agent's has exited: no network, the workspace read-only, a fresh output
folder at CIRCUIT_OUT. It checks the board with check_done and, only if it
is DONE, re-runs DRC itself and regenerates the Gerbers, on a copy of the
board in a temp folder: check_done reads reports and Gerbers the agent wrote,
and an agent can forge them. Only a board that is clean in that fresh DRC run
is packaged, into <out>/deliverable.zip, and the zip ships the fresh report
and Gerbers, and a schematic re-checked against the netlist, not the agent's
copies -- or, for a circuit SKiDL can't draw, export_schematic's record of
that as schematic-unavailable.json. It always writes <out>/result.json, and never writes into the
workspace.

The workspace is untrusted -- the agent controls every path in it -- so a
required file that is a symlink or not a regular file is refused, and
source files with unusual names are left out.

The value of every env var named in CIRCUIT_REDACT (default
OPENROUTER_API_KEY) is replaced by [redacted] in everything written,
including inside the gerbers zip. That catches accidents, not intent: an
encoded, split, or drawn-on-silkscreen key is not caught.
"""
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

from circuit_mcp import done, paths, schematic, service, tools

REDACTED = b"[redacted]"
SVG_LAYERS = "F.Cu,B.Cu,F.Silkscreen,Edge.Cuts"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
# result.json's reason carries check_done's text and kicad-cli's output, both
# shaped by the agent's files: bounded so a reader never gets megabytes.
MAX_REASON = 4000
TRUNCATED = "…[truncated]"


class _Refused(Exception):
    """A controlled reason not to package: str(exc) is used as the reason,
    unprefixed -- unlike an unexpected exception, which is wrapped."""


def secrets_from_env(env=os.environ) -> list[str]:
    """Values to redact: each named var that is set and 8+ characters."""
    names = [n.strip() for n in env.get("CIRCUIT_REDACT", "OPENROUTER_API_KEY").split(",")]
    return [env[n] for n in names if n and len(env.get(n, "")) >= 8]


def redact(data: bytes, secrets) -> tuple[bytes, int]:
    count = 0
    for secret in secrets:
        needle = secret.encode()
        count += data.count(needle)
        data = data.replace(needle, REDACTED)
    return data, count


def _regular(path: pathlib.Path) -> bool:
    """A real file, not a symlink (to anything) and not a device or folder."""
    return not path.is_symlink() and path.is_file()


def _board(workspace: pathlib.Path):
    found = sorted(workspace.glob("*.kicad_pcb"))
    if len(found) != 1:
        return None, f"expected exactly one .kicad_pcb at the project root, found {len(found)}"
    return found[0], ""


def _netlist(workspace: pathlib.Path, board: pathlib.Path):
    own = board.with_suffix(".net")
    if own.exists():
        return own, ""
    found = sorted(workspace.glob("*.net"))
    if len(found) != 1:
        return None, (f"no {own.name}, and expected exactly one .net at the project root, "
                      f"found {len(found)}")
    return found[0], ""


def _svg(copy: pathlib.Path) -> bytes | None:
    """A top-down picture of the board, or None if kicad-cli can't make one.
    From the temp copy: kicad-cli writes a .kicad_prl beside its input."""
    out = copy.parent / "board.svg"
    try:
        run = subprocess.run(
            ["kicad-cli", "pcb", "export", "svg", "--mode-single", "--page-size-mode", "2",
             "--exclude-drawing-sheet", "--layers", SVG_LAYERS, "-o", str(out), str(copy)],
            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.read_bytes() if run.returncode == 0 and out.is_file() else None


def _copy_board(board: pathlib.Path, tmp: pathlib.Path, budget: list) -> list[tuple[str, bytes]]:
    """Copy the board and its project and rules files into tmp, each only if
    it is a regular file with a safe name; the (kicad/<name>, bytes) entries."""
    entries = []
    for suffix in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
        f = board.with_suffix(suffix)
        if _regular(f) and SAFE_NAME.match(f.name):
            data = _sized_bytes(f, budget)
            (tmp / f.name).write_bytes(data)
            entries.append((f"kicad/{f.name}", data))
    return entries


def _fresh_drc(copy: pathlib.Path) -> tuple[dict, bytes]:
    """The engine's own DRC command, run on the temp copy: (report, raw bytes).
    Any exit but 0 (clean) or 5 (violations found) is a tool failure."""
    report = copy.parent / "drc.json"
    try:
        run = subprocess.run(service.drc_command(copy, report), capture_output=True,
                             text=True, timeout=service.TIMEOUT)
    except subprocess.TimeoutExpired:
        raise _Refused(f"the packaging step's DRC run timed out after {service.TIMEOUT} s")
    except OSError as exc:
        raise _Refused(f"the packaging step's DRC run could not start: {exc}")
    if run.returncode not in (0, 5):
        raise _Refused(f"the packaging step's DRC run failed (kicad-cli exit {run.returncode}): "
                       f"{(run.stdout + run.stderr)[-600:]}")
    try:
        raw = report.read_bytes()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("not a JSON object")
        tools._counts(parsed)
    except Exception as exc:
        raise _Refused(f"the packaging step's DRC run wrote an unreadable report: {exc}")
    return parsed, raw


def _fresh_gerbers(copy: pathlib.Path, secrets, budget: list) -> tuple[bytes, int]:
    """Gerbers and drill files regenerated from the temp copy, zipped."""
    folder = copy.parent / "gerbers"
    try:
        written = service.plot_fab(copy, folder)
    except subprocess.TimeoutExpired:
        raise _Refused(f"the packaging step's Gerber export timed out after {service.TIMEOUT} s")
    except RuntimeError as exc:
        raise _Refused(f"the packaging step's {exc}")
    buf, total = io.BytesIO(), 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as g:
        for name in written:
            data, n = redact(_sized_bytes(folder / name, budget), secrets)
            total += n
            g.writestr(name, data)
    return buf.getvalue(), total


def _sized_bytes(path: pathlib.Path, budget: list) -> bytes:
    """path.read_bytes(), refusing a file over MAX_FILE_BYTES or one that
    would push the running total (budget[0]) over MAX_TOTAL_BYTES."""
    size = path.lstat().st_size
    if size > MAX_FILE_BYTES:
        raise _Refused(f"{path.name} is {size} bytes, over the {MAX_FILE_BYTES}-byte size cap")
    budget[0] += size
    if budget[0] > MAX_TOTAL_BYTES:
        raise _Refused(f"packaged files total more than the {MAX_TOTAL_BYTES}-byte size cap")
    return path.read_bytes()


def _checked_schematic(source_sch: pathlib.Path, netlist: pathlib.Path, tmp: pathlib.Path,
                       budget: list) -> list[tuple[str, bytes]]:
    """The schematic, re-checked against the netlist, with a fresh PDF and
    ERC report made from the copy shipped."""
    sch_bytes = _sized_bytes(source_sch, budget)
    copied_sch = tmp / source_sch.name
    copied_sch.write_bytes(sch_bytes)
    mismatch = schematic.problems(copied_sch, netlist)
    if mismatch:
        raise _Refused("the schematic does not match the delivered netlist: "
                       + "; ".join(mismatch[:5]))
    try:
        schematic.export_pdf(copied_sch, tmp / "schematic.pdf")
        schematic.run_erc(copied_sch, tmp / "schematic-erc.json")
    except RuntimeError as error:
        raise _Refused(f"the packaging step could not process the schematic: {error}")
    return [
        (f"kicad/{source_sch.name}", sch_bytes),
        ("schematic.pdf", _sized_bytes(tmp / "schematic.pdf", budget)),
        ("schematic-erc.json", _sized_bytes(tmp / "schematic-erc.json", budget)),
    ]


def _unavailable_bytes(record: pathlib.Path, budget: list) -> bytes:
    """The unavailable record's bytes, refused unless they are the JSON
    object with a reason that export_schematic writes."""
    data = _sized_bytes(record, budget)
    try:
        parsed = json.loads(data)
        ok = isinstance(parsed, dict) and isinstance(parsed.get("reason"), str)
    except ValueError:
        ok = False
    if not ok:
        raise _Refused(f"{record.name} is not a schematic-unavailable record")
    return data


def _entries(workspace, board, netlist, secrets, result) -> tuple[list[tuple[str, bytes]], int]:
    """Everything the zip ships. DRC and Gerbers are re-made from a temp copy
    of the board; a board that is not clean in that run raises _Refused."""
    budget = [0]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        kicad_files = _copy_board(board, tmp, budget)
        copy = tmp / board.name
        report, raw = _fresh_drc(copy)
        errors, unconnected = tools._counts(report)
        result["drc_errors"], result["unconnected"] = len(errors), len(unconnected)
        if errors or unconnected:
            raise _Refused(
                "the packaging step re-ran DRC on the delivered board and it is not clean: "
                f"{len(errors)} error(s), {len(unconnected)} unconnected "
                f"(the workspace's {board.stem}.drc.json does not describe this board)")
        gerbers, total = _fresh_gerbers(copy, secrets, budget)
        svg = _svg(copy)
        # The schematic is copied, never regenerated: that would run the
        # agent's script, and this step runs no agent code. What it re-checks
        # instead is that the drawing shows the delivered netlist's circuit;
        # the PDF and ERC report are made fresh from the schematic shipped.
        sch_name = schematic.files(netlist.stem)[0]
        source_sch = workspace / sch_name
        record = workspace / schematic.unavailable_file(netlist.stem)
        if not schematic.present(source_sch) and _regular(record):
            # export_schematic found SKiDL can't draw this circuit: the
            # board ships without a schematic, and with the record of why.
            schematic_entries = [("schematic-unavailable.json", _unavailable_bytes(record, budget))]
        elif not (_regular(source_sch) and SAFE_NAME.match(sch_name)):
            raise _Refused(f"{sch_name} is missing, a symlink or not a regular file")
        else:
            schematic_entries = _checked_schematic(source_sch, netlist, tmp, budget)
    entries = [("fab/gerbers.zip", gerbers), *kicad_files]
    entries += schematic_entries
    entries += [(f"source/{f.name}", _sized_bytes(f, budget)) for f in sorted(workspace.glob("*.py"))
                if _regular(f) and SAFE_NAME.match(f.name)]
    entries.append((f"source/{netlist.name}", _sized_bytes(netlist, budget)))
    entries.append(("README.md", _sized_bytes(workspace / "README.md", budget)))
    entries.append(("drc.json", raw))
    if svg is not None:
        entries.append(("board.svg", svg))
    return entries, total


def _write(path: pathlib.Path, data: bytes) -> None:
    """Through a temp file and os.replace, so a reader never sees half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _remove_partial(out: pathlib.Path) -> None:
    target = out / "deliverable.zip"
    if target.exists():
        target.unlink()


def package(workspace: pathlib.Path, out: pathlib.Path, secrets: list[str]) -> dict:
    result = {"done": False, "reason": "", "board": None, "netlist": None,
              "drc_errors": None, "unconnected": None, "files": [], "redactions": 0}
    try:
        board, why = _board(workspace)
        netlist = None
        if board is not None:
            result["board"] = board.name
            netlist, why = _netlist(workspace, board)
        if netlist is not None:
            result["netlist"] = netlist.name
        # Both names end up as zip entry names (kicad/, source/): a name with
        # a separator or `..` in it would be a zip-slip for whoever unpacks.
        unsafe = [p.name for p in (board, netlist) if p is not None and not SAFE_NAME.match(p.name)]
        if unsafe:
            netlist = None
            why = "unsafe file name: " + ", ".join(repr(n) for n in unsafe)
        if netlist is not None:
            required = [board, netlist, workspace / "README.md", workspace / f"{board.stem}.drc.json"]
            # The schematic and its unavailable record ship too, when there:
            # refused here, before check_done reads them.
            required += [p for p in (workspace / schematic.files(netlist.stem)[0],
                                     workspace / schematic.unavailable_file(netlist.stem))
                         if schematic.present(p)]
            refused = [p.name for p in required if schematic.present(p) and not _regular(p)]
            oversized = [p.name for p in required if p.exists() and _regular(p)
                        and p.lstat().st_size > MAX_FILE_BYTES]
            gerbers = workspace / "gerbers"
            if gerbers.is_symlink():
                refused.append("gerbers/")
            elif gerbers.is_dir():
                refused += [f"gerbers/{f.name}" for f in sorted(gerbers.iterdir()) if not _regular(f)]
            if refused or oversized:
                reasons = []
                if refused:
                    reasons.append(
                        "refusing files that are symlinks or not regular files: "
                        + ", ".join(refused))
                if oversized:
                    reasons.append(
                        f"refusing files over the {MAX_FILE_BYTES}-byte size cap: "
                        + ", ".join(oversized))
                why = "; ".join(reasons)
            else:
                # The first gate. It reads files the agent wrote, so DONE
                # here is necessary, not sufficient: _entries re-checks.
                verdict = done.check_done(netlist.name, board.name)
                result["done"] = verdict.startswith("DONE")
                why = verdict
        result["reason"] = why

        if result["done"]:
            entries, total = _entries(workspace, board, netlist, secrets, result)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for name, data in entries:
                    if name != "fab/gerbers.zip":  # already redacted inside, entry by entry
                        data, n = redact(data, secrets)
                        total += n
                    result["files"].append(name)
                    z.writestr(name, data)
            _write(out / "deliverable.zip", buf.getvalue())
            result["redactions"] = total
    except _Refused as exc:
        result["done"] = False
        result["reason"] = str(exc)
        result["files"] = []
        _remove_partial(out)
    except Exception as exc:
        result["done"] = False
        result["reason"] = f"packaging error: {type(exc).__name__}: {exc}"
        result["files"] = []
        _remove_partial(out)

    # Redacted before it is cut, so a cut can't leave half a key unmatched.
    reason, n = redact(result["reason"].encode(), secrets)
    result["redactions"] += n
    result["reason"] = reason.decode(errors="replace")
    if len(result["reason"]) > MAX_REASON:
        result["reason"] = result["reason"][:MAX_REASON] + TRUNCATED
    text, n = redact(json.dumps(result, indent=2).encode(), secrets)
    result["redactions"] += n
    _write(out / "result.json", text + b"\n")
    return result


def main() -> int:
    out = pathlib.Path(os.environ.get("CIRCUIT_OUT", "/out"))
    result = package(paths.WORKSPACE, out, secrets_from_env())
    print(json.dumps({"done": result["done"], "drc_errors": result["drc_errors"],
                      "unconnected": result["unconnected"], "files": len(result["files"])}))
    return 0 if result["done"] else 1


if __name__ == "__main__":
    sys.exit(main())
