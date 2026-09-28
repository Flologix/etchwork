"""Where things are: the board's envelope, and what sits near what."""
from __future__ import annotations

import math

from contract.board import Board
from contract.checkers.base import Result
from contract.match import NetSpec, is_kind, pads_by_ref, parse_value, resolve_net, resolve_pads


def outline_within(board: Board, *, width_mm: float, height_mm: float,
                   tolerance_mm: float) -> Result:
    """The outline's bounding box fits the envelope, in either orientation:
    a board drawn rotated 90 degrees fits the same shell. The box is
    axis-aligned, so a board drawn at an angle is measured by its box."""
    o = board.outline()
    if o.bbox is None:
        return Result(False, "the board has no closed Edge.Cuts outline", None)
    w, h = o.width, o.height
    fits = lambda a, b: a <= width_mm + tolerance_mm + 1e-9 and b <= height_mm + tolerance_mm + 1e-9
    return Result(fits(w, h) or fits(h, w),
                  f"outline is {w:.2f} x {h:.2f} mm; limit {width_mm} x {height_mm} mm "
                  f"(+{tolerance_mm}), either orientation",
                  {"width_mm": round(w, 3), "height_mm": round(h, 3)})


def decoupling_near(board: Board, *, pin: NetSpec, ground: NetSpec,
                    min_farads: float, max_mm: float) -> Result:
    """A capacitor of at least min_farads across the supply pin's net and
    ground, with its supply-side pad within max_mm of the pin (pad centre to
    pad centre)."""
    targets = resolve_pads(board, pin)
    if not targets:
        return Result(False, f"no pad matches {pin.name}, so there is no supply pin to decouple")
    grounds = resolve_net(board, ground)
    if not grounds:
        return Result(False, f"no net matches {ground.name}")
    supply = {p.net for p in targets} - grounds
    by_ref = pads_by_ref(board)
    best, seen = None, []
    for f in board.footprints():
        if not is_kind(f, "C"):
            continue
        own = by_ref.get(f.ref, [])
        nets = {p.net for p in own}
        if not (nets & supply and nets & grounds):
            continue
        seen.append(f"{f.ref} {f.value}")
        value = parse_value(f.value)
        if value is None or value < min_farads:
            continue
        for cp in own:
            for tp in targets:
                if cp.net == tp.net and cp.net in supply:
                    d = math.dist(cp.pos, tp.pos)
                    if best is None or d < best[0]:
                        best = (d, f.ref, f.value, f"{tp.ref}:{tp.number}")
    if best is None:
        where = ", ".join(f"{p.ref}:{p.number}" for p in targets)
        why = (f"capacitors across it: {', '.join(seen)}; none is >= {min_farads:g} F"
               if seen else "no capacitor sits between it and ground")
        return Result(False, f"{pin.name} ({where}): {why}", {"candidates": seen})
    d, ref, value, at = best
    return Result(d <= max_mm, f"{ref} ({value}) is {d:.2f} mm from {at}; limit {max_mm} mm",
                  {"distance_mm": round(d, 3), "cap": ref, "value": value, "pin": at})
