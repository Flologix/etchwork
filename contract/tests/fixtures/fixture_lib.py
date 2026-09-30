"""Building blocks for CONTRACT's mutation fixtures: a board of real KiCad
footprints whose pads carry chosen nets and pin names, and the loop that
writes the clean board and every edited copy of it. Each task's generator
is then only its parts and its edits. Runs in the engine image (pcbnew).

Nothing here is DRC-checked, because CONTRACT does not grade DRC: parts may
overlap. What matters is which pad is on which net, what each pin is
called, and pad-to-pad distances."""
import sys
from pathlib import Path

import pcbnew

FP_ROOT = Path("/usr/share/kicad/footprints")

# Fixed so every build() draws the same uuid sequence for the same items:
# SaveBoard serializes footprints (and their pads) sorted by uuid, so a
# random seed would give every variant its own file order and make even a
# one-line mutation diff as a full rewrite (spec §6: fixtures must be
# diffable). Any constant works; this one has no other significance.
UUID_SEED = 0xC0FFEE


def mm(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def net(board, name):
    found = board.FindNet(name)
    if found is None:
        found = pcbnew.NETINFO_ITEM(board, name)
        board.Add(found)
    return found


def outline(board, w, h):
    for item in list(board.GetDrawings()):
        if item.GetLayer() == pcbnew.Edge_Cuts:
            board.Remove(item)
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(mm(*a))
        seg.SetEnd(mm(*b))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(pcbnew.FromMM(0.1))
        board.Add(seg)


def fp(board, ref):
    return next(f for f in board.GetFootprints() if f.GetReference() == ref)


def pad(board, ref, number):
    return next(p for p in fp(board, ref).Pads() if p.GetNumber() == number)


def set_net(board, ref, number, net_name):
    # Every pad with that number: a switch, holder or connector may repeat one.
    for p in fp(board, ref).Pads():
        if p.GetNumber() == number:
            p.SetNet(net(board, net_name))


def rename_net(board, old, new):
    """Every pad on `old` moves to `new`, as if the net had been named so."""
    for p in board.GetPads():
        if p.GetNetname() == old:
            p.SetNet(net(board, new))


def place(board, ref, lib, name, value, xy, pins):
    """Add footprint lib:name as `ref` centred at xy (mm). `pins` maps pad
    number -> (net, pin name); a pad it does not list gets neither, which is
    how a footprint's mounting holes and unused pins stay unconnected."""
    part = pcbnew.FootprintLoad(str(FP_ROOT / f"{lib}.pretty"), name)
    if part is None:
        sys.exit(f"footprint {lib}:{name} is not in {FP_ROOT}")
    part.SetFPID(pcbnew.LIB_ID(lib, name))
    part.SetReference(ref)
    part.SetValue(value)
    board.Add(part)
    part.SetPosition(mm(*xy))
    for p in part.Pads():
        net_name, pin = pins.get(p.GetNumber(), ("", ""))
        if net_name:
            p.SetNet(net(board, net_name))
        if pin:
            p.SetPinFunction(pin)
    return part


def beside(board, ref, number, target_ref, target_number, dx_mm, dy_mm=0.0):
    """Move part `ref` so its pad `number` sits (dx, dy) mm from pad
    `target_number` of `target_ref`: a decoupling capacitor placed by the pin
    it serves, wherever that pin sits inside its footprint."""
    part = fp(board, ref)
    own = pad(board, ref, number).GetPosition()
    at = part.GetPosition()
    goal = pad(board, target_ref, target_number).GetPosition()
    part.SetPosition(pcbnew.VECTOR2I(goal.x + pcbnew.FromMM(dx_mm) - (own.x - at.x),
                                     goal.y + pcbnew.FromMM(dy_mm) - (own.y - at.y)))


def build(parts, size, near=()):
    board = pcbnew.BOARD()
    outline(board, *size)
    for ref, (lib, name, value, xy, pins) in parts.items():
        place(board, ref, lib, name, value, xy, pins)
    for args in near:
        beside(board, *args)
    return board


def write_all(out_dir, build, edits) -> None:
    """Write <out_dir>/<name>/kicad/board.kicad_pcb for the clean board and
    for every edit in `edits` ({name: fn(board)}), each built fresh by
    `build()` under the same uuid seed."""
    out_dir = Path(out_dir)
    # KiCad's footprint-library plugin cache warms lazily on first use, and
    # a cold cache draws a different number of uuids than a warm one for
    # the same footprints -- so the first variant built in a process would
    # never line up against the rest even with identical seeding. One
    # throwaway build, before any variant is seeded and generated for real,
    # pays that one-time cost so every real build() below starts warm.
    pcbnew.KIID.SeedGenerator(UUID_SEED)
    build()
    for name in ["clean", *edits]:
        pcbnew.KIID.SeedGenerator(UUID_SEED)
        board = build()
        if name != "clean":
            edits[name](board)
        path = out_dir / name / "kicad" / "board.kicad_pcb"
        path.parent.mkdir(parents=True, exist_ok=True)
        pcbnew.SaveBoard(str(path), board)
        # SaveBoard also writes a project and a local-settings file next to
        # the board; nothing here reads either, so drop them rather than
        # leaving them for the next `./etchwork contract fixtures` to churn.
        for suffix in (".kicad_pro", ".kicad_prl"):
            path.with_suffix(suffix).unlink(missing_ok=True)
        print(f"wrote {path}")
