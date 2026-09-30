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
                    min_farads: float, max_mm: float, each: bool = False) -> Result:
    """A capacitor of at least min_farads across the supply pin's net and
    ground, with its supply-side pad within max_mm of the pin (pad centre to
    pad centre). With `each`, every pad `pin` matches needs one, not just the
    best-placed pad; one capacitor may serve two pins it sits near."""
    targets = resolve_pads(board, pin)
    if not targets:
        return Result(False, f"no pad matches {pin.name}, so there is no supply pin to decouple")
    grounds = resolve_net(board, ground)
    if not grounds:
        return Result(False, f"no net matches {ground.name}")
    supply = {p.net for p in targets} - grounds
    if each:
        return _decoupling_each(board, pin, targets, grounds, supply, min_farads, max_mm)
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


def _decoupling_each(board: Board, pin: NetSpec, targets, grounds, supply,
                     min_farads: float, max_mm: float) -> Result:
    by_ref = pads_by_ref(board)
    caps = []  # (ref, the cap's supply-side pads), qualifying caps only
    for f in board.footprints():
        if not is_kind(f, "C"):
            continue
        own = by_ref.get(f.ref, [])
        nets = {p.net for p in own}
        value = parse_value(f.value)
        if nets & supply and nets & grounds and value is not None and value >= min_farads:
            caps.append((f.ref, [p for p in own if p.net in supply]))
    per_pin, missing = {}, []
    for tp in sorted(targets, key=lambda p: (p.ref, len(p.number), p.number)):
        at = f"{tp.ref}:{tp.number}"
        best = None
        for ref, pads in caps:
            for cp in pads:
                if cp.net == tp.net:
                    d = math.dist(cp.pos, tp.pos)
                    if best is None or d < best[0]:
                        best = (d, ref)
        per_pin[at] = None if best is None else {"cap": best[1], "distance_mm": round(best[0], 3)}
        if best is None:
            missing.append(at)
        elif best[0] > max_mm:
            missing.append(f"{at} (nearest {best[1]} at {best[0]:.2f} mm)")
    measured = {"pins": per_pin}
    if missing:
        return Result(False, f"{pin.name}: no capacitor >= {min_farads:g} F within {max_mm} mm "
                             f"of {', '.join(missing)}", measured)
    found = ", ".join(f"{at} {m['cap']} at {m['distance_mm']:.2f} mm" for at, m in per_pin.items())
    return Result(True, f"every {pin.name} pin has a capacitor >= {min_farads:g} F within "
                        f"{max_mm} mm: {found}", measured)
