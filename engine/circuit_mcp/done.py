"""The finish line, checked from the files alone.

Release check 3 (2026-09-24): the agent finished a board at ~69 min, wrote a
correct final answer, was poked to "do more validation", wrote its own
clearance checker with a footprint-rotation bug, and chased the short it
invented until the 90-min timeout. Nothing could tell it the work was done.

Done is: a board whose DRC report is current and clean, whose pours are
filled, and whose gerbers and README were produced after its last write.
Read-only by design: calling this changes nothing, so calling it again after
an auto-poke is always safe.
"""
import json
import pathlib

from circuit_mcp import kicad, kicad_lib, paths, summary, tools


def _older(path: pathlib.Path, board: pathlib.Path) -> bool:
    return path.stat().st_mtime < board.stat().st_mtime


def _gerbers(board: pathlib.Path) -> list[str]:
    folder = paths.WORKSPACE / "gerbers"
    files = [p for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []
    if not files:
        return ["no gerbers in gerbers/: run `export_gerbers` (default output folder)"]
    stale = sorted(p.name for p in files if _older(p, board))
    if stale:
        return [f"gerbers/ has files older than the board: {', '.join(stale)}: "
                "delete any that are not from this board, then run `export_gerbers`"]
    return []


def _readme(board: pathlib.Path) -> list[str]:
    readme = paths.WORKSPACE / "README.md"
    if not readme.is_file():
        return ["no README.md: run `summarize_project`"]
    text = readme.read_text(errors="replace")
    problems = []
    begins, ends = text.count(summary.BEGIN), text.count(summary.END)
    if begins != 1 or ends != 1 or text.index(summary.BEGIN) > text.index(summary.END):
        problems.append(f"README.md has malformed summary markers ({begins} begin, {ends} end): "
                        "remove the stray ones, then run `summarize_project`")
    else:
        block = text.split(summary.BEGIN, 1)[1].split(summary.END, 1)[0]
        status = next((line.strip() for line in block.splitlines()
                       if line.startswith("Status:")), "no Status line")
        if status != "Status: DRC clean":
            problems.append(f"README.md says `{status}`: run `summarize_project` once DRC is clean")
    if _older(readme, board):
        problems.append("README.md is older than the board: run `summarize_project`")
    if summary.PLACEHOLDER in text:
        problems.append(f"README.md still has the line `{summary.PLACEHOLDER}`: "
                        "replace it with what the board does")
    return problems


def _flagged(source: pathlib.Path) -> list[str]:
    _, review, _ = summary._classes(source)
    if not review:
        return ["Not classified: no netclasses file beside the netlist."]
    return review[2:]


def _facts(board: pathlib.Path, text: str) -> list[str]:
    """What a final answer states about the board, so the agent never parses
    the files itself. In the 2026-09-24 live eval it did exactly that after
    DONE -- read-only, but the kind of home-made check the skill rules out."""
    size = kicad_lib.outline_size(text)
    info = kicad_lib.read_board(text)
    report = json.loads((paths.WORKSPACE / f"{board.stem}.drc.json").read_text())
    errors, unrouted = tools._counts(report)
    warnings = sum(1 for v in kicad.violations(report) if v.get("severity") == "warning")
    return [
        f"- outline: {size[0]:g} x {size[1]:g} mm" if size else "- outline: none on Edge.Cuts",
        f"- copper layers with tracks: {', '.join(sorted(info.segments)) or 'none'}",
        f"- footprints: {info.footprints}",
        f"- DRC: {len(errors)} error(s), {len(unrouted)} unconnected, {warnings} warning(s)",
    ]


def check_done(netlist: str, board: str | None = None) -> str:
    try:
        source = paths.inside(netlist)
    except ValueError as error:
        return f"NOT DONE:\n- {netlist}: {error}"
    if not source.is_file():
        return f"NOT DONE:\n- no such netlist in the project: {netlist}"
    board = board or summary.default_board(source)
    path, missing = summary._board_path(board)
    if path is None:
        return f"NOT DONE:\n- {missing}"

    problems = []
    not_clean = tools.drc_status(path)
    if not_clean:
        problems.append(f"DRC: {not_clean}: run `run_drc` and fix what it reports")
    text = path.read_text(errors="replace")
    unfilled = kicad_lib.unfilled_zones(text)
    if unfilled:
        problems.append(f"{unfilled} copper pour(s) not filled: run `fill_zones`, then `run_drc`")
    problems += _gerbers(path)
    problems += _readme(path)

    if problems:
        return "NOT DONE:\n" + "\n".join(f"- {p}" for p in problems)
    return "\n".join([
        f"DONE: {paths.rel(path)} is DRC clean with its pours filled; gerbers/ and README.md are current.",
        "Board facts (state these in your final answer; do not re-derive them):",
        *_facts(path, text),
        "Nets awaiting the human's decision (put these in your final answer):",
        *_flagged(source),
    ])
