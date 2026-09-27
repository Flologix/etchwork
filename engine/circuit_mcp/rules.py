"""Deterministic board rules applied around routing.

apply_netclasses writes the classes the Rust width table chose (never a
model) into the board before freerouting runs; KiCad 10 carries them into
the Specctra DSN as per-class width and clearance. Before routing, each net
is capped at its smallest copper pad, in a per-net class, so a tier width
the pads cannot take is reported as pad-limited instead of becoming a
clearance error at every pad. finish repairs what the
router leaves behind: tracks it narrowed below their class width (except at
a pad smaller than that width, which is where a neck-down belongs), and SMD
pads on the pour net that a thermal relief would starve.
"""
import json
import pathlib

from circuit_mcp import paths, service

NECK_FLOOR_MM = 0.2

_NETCLASSES = """
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
spec = json.loads(open(sys.argv[2]).read())
floor = float(sys.argv[3])
settings = board.GetDesignSettings()
ns = settings.m_NetSettings

# The smallest side of every COPPER pad on each net. A mechanical (NPTH) hole
# or a pad on no copper layer says nothing about how wide a track into the
# net can be, so it is skipped.
smallest = {}
for fp in board.GetFootprints():
    for pad in fp.Pads():
        if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH or not pad.IsOnCopperLayer():
            continue
        size = pad.GetSize()
        side = pcbnew.ToMM(min(size.x, size.y))
        net = pad.GetNetname()
        smallest[net] = min(side, smallest.get(net, side))

classes, assigned, missing, capped, applied = {}, 0, [], [], {}
for net, entry in sorted(spec.get("nets", {}).items()):
    if board.FindNet(net) is None:
        missing.append(net)
        continue
    tier_mm = float(entry["width_mm"])
    width = tier_mm
    pad = smallest.get(net)
    if pad is not None:
        width = min(width, pad)
        if pad >= floor:
            width = max(width, floor)
    width = round(width, 4)
    name = entry["netclass"]
    if width < tier_mm:
        # Its own class: a net routed at its pad's width must not drag the
        # other nets sharing its tier down with it.
        name = "%s@%g" % (name, width)
        capped.append([net, tier_mm, width])
    if name not in classes:
        nc = pcbnew.NETCLASS(name)
        nc.SetTrackWidth(pcbnew.FromMM(width))
        nc.SetClearance(pcbnew.FromMM(float(entry["clearance_mm"])))
        ns.SetNetclass(name, nc)
        classes[name] = nc
    ns.SetNetclassPatternAssignment(net, name)
    applied[net] = width
    assigned += 1
settings.m_TrackMinWidth = pcbnew.FromMM(floor)
pcbnew.SaveBoard(sys.argv[1], board)
print(json.dumps({"classes": sorted(classes), "assigned": assigned, "missing": missing,
                  "capped": capped, "applied": applied}))
"""

_FINISH = """
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
pour_net, floor = sys.argv[2], pcbnew.FromMM(float(sys.argv[3]))
ns = board.GetDesignSettings().m_NetSettings
pads_by_net = {}
for fp in board.GetFootprints():
    for pad in fp.Pads():
        pads_by_net.setdefault(pad.GetNetname(), []).append(pad)

def class_width(net):
    name = board.FindNet(net).GetNetClassName()
    nc = ns.GetNetClassByName(name) if name else None
    return nc.GetTrackWidth() if nc else floor

widened = []
for track in board.GetTracks():
    if isinstance(track, pcbnew.PCB_VIA):
        continue
    net = track.GetNetname()
    target = max(class_width(net), floor)
    for pad in pads_by_net.get(net, []):
        if pad.HitTest(track.GetStart()) or pad.HitTest(track.GetEnd()):
            size = pad.GetSize()
            target = max(min(target, min(size.x, size.y)), floor)
    if track.GetWidth() < target:
        widened.append([net, round(pcbnew.ToMM(track.GetWidth()), 4), round(pcbnew.ToMM(target), 4)])
        track.SetWidth(target)

solid = []
for fp in board.GetFootprints():
    for pad in fp.Pads():
        if pad.GetNetname() == pour_net and pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
            pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
            solid.append("%s pad %s" % (fp.GetReference(), pad.GetNumber()))

if list(board.Zones()):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
pcbnew.SaveBoard(sys.argv[1], board)
print(json.dumps({"widened": widened, "solid": solid}))
"""


def _last_json(code: int, out: str, step: str) -> dict:
    if code != 0:
        raise RuntimeError(f"{step} failed: {out[-400:]}")
    # stdout comes first and KiCad's stderr chatter after it, so the answer is
    # the last line that is a JSON object, not simply the last line.
    answer = next((line for line in reversed(out.splitlines()) if line.startswith("{")), None)
    if answer is None:
        raise RuntimeError(f"{step} printed no result: {out[-400:]}")
    return json.loads(answer)


def applied_path(netclasses: pathlib.Path) -> pathlib.Path:
    """`<stem>.netclasses.applied.json`, beside the netclasses file."""
    return netclasses.with_suffix(".applied.json")


def apply_netclasses(board: str, netclasses: str) -> dict:
    """Write the classes into the board, each net capped at its smallest
    copper pad.

    A tier width wider than the pads it must reach cannot be routed without
    clearance errors at every pad (release check 1: VBUS at 2.5 mm into a
    USB-C's 0.6 mm pads). The net is routed at the pad's width instead, in a
    class of its own named `<class>@<width>`, and listed under `capped` as
    `[net, tier_mm, applied_mm]` so the width shortfall is reported rather
    than hidden. The width every net was actually given is written to
    `<stem>.netclasses.applied.json`.
    """
    source = paths.inside(netclasses)
    code, out = service.python(
        _NETCLASSES, str(paths.inside(board)), str(source), str(NECK_FLOOR_MM))
    result = _last_json(code, out, "applying net classes")
    applied = result.pop("applied", {})
    applied_path(source).write_text(json.dumps(applied, indent=2, sort_keys=True) + "\n")
    return result


def finish(board: str, pour_net: str = "GND") -> dict:
    code, out = service.python(_FINISH, str(paths.inside(board)), pour_net, str(NECK_FLOOR_MM))
    return _last_json(code, out, "finishing the board")


# Exactly the pads named, by uuid -- a pad's KIID is what KiCad's DRC report
# carries for it, so nothing is matched by position or by reading text.
_SOLID_PADS = """
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
wanted = set(json.loads(sys.argv[2]))
changed = []
for fp in board.GetFootprints():
    for pad in fp.Pads():
        uuid = pad.m_Uuid.AsString()
        if uuid in wanted:
            pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
            changed.append(uuid)
if changed and list(board.Zones()):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
pcbnew.SaveBoard(sys.argv[1], board)
print(json.dumps({"changed": changed, "missing": sorted(wanted - set(changed))}))
"""


def solid_pads(board: str, uuids: list[str]) -> dict:
    """Give exactly the pads with these uuids a solid zone connection, refill,
    save. `{"changed": [...], "missing": [...]}`; an unknown uuid is missing,
    not an error.

    The repair for a starved thermal relief. A solid connection cannot starve
    -- it is the pad's whole outline, not spokes -- and it is only applied to
    pads KiCad's own DRC named, so every other pad keeps the relief that makes
    it easy to hand-solder.
    """
    code, out = service.python(_SOLID_PADS, str(paths.inside(board)), json.dumps(list(uuids)))
    return _last_json(code, out, "making pads solid")
