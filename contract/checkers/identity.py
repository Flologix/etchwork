"""Is a part of the right kind on the board at all."""
from __future__ import annotations

from contract.board import Board
from contract.checkers.base import Result
from contract.match import PartSpec, matching


def part_present(board: Board, *, part: PartSpec, count: int = 1) -> Result:
    found = sorted(f.ref for f in matching(board, part))
    if len(found) >= count:
        return Result(True, f"{len(found)} part(s) match {part.name}: {', '.join(found)}",
                      {"matched": found, "count": len(found)})
    # Say what IS there: "the agent chose a part outside our allowlist" is a
    # different finding from "the agent left it out" (spec §5.1).
    seen = "; ".join(f"{f.ref} {f.value!r} {f.lib_id}"
                     for f in sorted(board.footprints(), key=lambda f: f.ref)[:20])
    have = f": {', '.join(found)}" if found else ""
    return Result(False, f"{len(found)} part(s) match {part.name}, need {count}{have}. "
                         f"Board has: {seen or 'no footprints'}",
                  {"matched": found, "count": len(found)})
