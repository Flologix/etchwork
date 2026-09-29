"""The layout pipeline's steps, run in-process by circuit_mcp.tools.

Ported from the old harness's kicad_service.py; the HTTP layer is gone
because the tools now run inside this container. Each pcbnew step still
runs as its own python3 subprocess, so a crash in pcbnew fails one step,
not the server.
"""
import json
import os
import pathlib
import subprocess

from circuit_mcp import fab, paths

TIMEOUT = int(os.environ.get("KICAD_TIMEOUT", "300"))

# The two checks that answer pass/fail on a design, and the flags that make
# them machine-readable. --exit-code-violations is what says pass or fail;
# app/kicad.py summarises the JSON and passes no verdict of its own.
JOBS = {"drc": ["pcb", "drc"]}

# The layout pipeline, which kicad-cli cannot do: it has no netlist import and
# no Specctra export, so every step below goes through pcbnew (in this image)
# and freerouting (a jar, because KiCad has no autorouter).
#
# Each runs as a separate `python3 -c` rather than in this process. pcbnew is a
# SWIG binding around the whole of KiCad; importing it here would make one bad
# board file take the HTTP server down with it, and a crashed subprocess is a
# 500 rather than an outage.
FREEROUTING_JAR = os.environ.get("FREEROUTING_JAR", "/opt/freerouting.jar")
ROUTER_PASSES = int(os.environ.get("KICAD_ROUTER_PASSES", "10"))

_NETLIST_TO_BOARD = """
import sys, kinet2pcb
kinet2pcb.kinet2pcb(sys.argv[1], sys.argv[2], sys.argv[3:] or None)
"""

# A rectangle on Edge.Cuts. Without one there is no board: DRC reports the
# footprints as outside the board area and the router has nothing to route in.
_OUTLINE = """
import sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
w, h = (pcbnew.FromMM(float(v)) for v in sys.argv[2:4])
for a, b in (((0,0),(w,0)), ((w,0),(w,h)), ((w,h),(0,h)), ((0,h),(0,0))):
    seg = pcbnew.PCB_SHAPE(board)
    seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
    seg.SetStart(pcbnew.VECTOR2I(*a)); seg.SetEnd(pcbnew.VECTOR2I(*b))
    seg.SetLayer(pcbnew.Edge_Cuts)
    seg.SetWidth(pcbnew.FromMM(0.1))
    board.Add(seg)
pcbnew.SaveBoard(sys.argv[1], board)
print("outline set")
"""

# Positions come from the caller as reference -> (x_mm, y_mm, rotation_deg),
# and (x, y) is where the footprint's CENTRE lands.
#
# pcbnew.SetPosition moves a footprint's anchor, which is wherever the library
# put it -- pad 1 for the Pololu breakout, a corner for a DIP, near pad 1 for
# a disc capacitor. This used to pass the caller's coordinate straight to it
# while both tool descriptions promised "centre positions", and the gap is not
# academic. Measured over session cf6ba6cf on 2026-09-17: twelve consecutive
# run_kicad_layout calls, ~2.2M prompt tokens, every one returning the same
# "129 net(s) left unrouted", because the agent was computing centres against
# a tool that placed anchors. It said so itself -- "the tool's documentation
# says 'centre positions' but the netlist proves otherwise" -- after deriving
# the arithmetic eight separate ways.
#
# Fixed here rather than in the description, because centre is the model the
# agent can actually compute with: describe_footprint reports a SIZE, and
# nothing anywhere reports a footprint's internal anchor offset. A truthful
# "these are anchor positions" would have been honest and still unusable.
#
# Rotation is applied BEFORE the box is measured -- a rotated DIP-28 is 36mm
# wide where an upright one is 10mm, so measuring first would centre the part
# it used to be. The courtyard is preferred over the bounding box because the
# courtyard is what DRC tests for overlap; GetBoundingBox is the fallback for
# a footprint that declares none, and excludes text so silkscreen cannot drag
# the centre off the copper.
_PLACE = """
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
want = json.loads(sys.argv[2])
have = {f.GetReference(): f for f in board.GetFootprints()}
missing = sorted(set(want) - set(have))
if missing:
    sys.exit("no such footprint(s) on the board: " + ", ".join(missing))


def extent(fp):
    \"\"\"The box DRC will test, after whatever rotation is already applied.\"\"\"
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        try:
            poly = fp.GetCourtyard(layer)
            if poly and poly.OutlineCount():
                return poly.BBox()
        except Exception:
            pass
    return fp.GetBoundingBox(False, False)


moved = []
for ref, (x, y, rot) in want.items():
    fp = have[ref]
    fp.SetOrientationDegrees(float(rot))
    box = extent(fp)
    # The anchor's offset from the centre, which is the whole correction.
    anchor = fp.GetPosition()
    off_x = anchor.x - box.GetCenter().x
    off_y = anchor.y - box.GetCenter().y
    fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(float(x)) + off_x,
                                   pcbnew.FromMM(float(y)) + off_y))
    moved.append("%s(%.2fx%.2f)" % (ref, pcbnew.ToMM(box.GetWidth()),
                                    pcbnew.ToMM(box.GetHeight())))
pcbnew.SaveBoard(sys.argv[1], board)
# The sizes travel back with the refs: the caller is doing centre arithmetic
# and the number it most needs is how much room each part actually took.
print("placed (centre, size mm): " + ", ".join(sorted(moved)))
"""

# Relational placement. The agent knows what it wants -- "the resistors in a
# row, evenly spread" -- and does not know that R_Axial_DIN0207 is 13 mm long
# lying flat. Measured: given only a count of unrouted nets to work from, the
# model reached one unrouted net and then oscillated (1, 3, 1, 4, 3) because
# it was adjusting millimetres with no view of the geometry.
#
# So the arithmetic happens here, where the bounding boxes are, and a request
# that cannot fit is REFUSED WITH THE NUMBERS rather than quietly overlapped.
_ARRANGE = """
import json, sys, pcbnew

board = pcbnew.LoadBoard(sys.argv[1])
ops = json.loads(sys.argv[2])
have = {f.GetReference(): f for f in board.GetFootprints()}

def need(refs):
    missing = sorted(set(refs) - set(have))
    if missing:
        sys.exit("no such footprint(s) on the board: " + ", ".join(missing))
    return [have[r] for r in refs]

def extent(fp, axis):
    # Text is excluded: a silkscreen reference hanging off the side is not a
    # collision, and including it would refuse placements that fit fine.
    box = fp.GetBoundingBox(False, False)
    return pcbnew.ToMM(box.GetWidth() if axis == "x" else box.GetHeight())

done = []
for op in ops:
    kind = op["op"]
    fps = need(op["refs"])
    if kind == "rotate":
        for fp in fps:
            fp.SetOrientationDegrees(float(op["degrees"]))
        done.append(f"rotated {len(fps)} to {op['degrees']}deg")
    elif kind == "align":
        axis, at = op["axis"], pcbnew.FromMM(float(op["at"]))
        for fp in fps:
            pos = fp.GetPosition()
            fp.SetPosition(pcbnew.VECTOR2I(at, pos.y) if axis == "x"
                           else pcbnew.VECTOR2I(pos.x, at))
        done.append(f"aligned {len(fps)} on {axis}={op['at']}")
    elif kind == "space_evenly":
        axis = op["axis"]
        lo, hi = float(op["from"]), float(op["to"])
        sizes = [extent(fp, axis) for fp in fps]
        span, total = hi - lo, sum(sizes)
        if total > span:
            sys.exit(
                f"space_evenly cannot fit {', '.join(op['refs'])} between "
                f"{axis}={lo} and {axis}={hi}: they are "
                f"{total:.1f}mm of footprint in {span:.1f}mm of room. "
                f"Widen the range, rotate them, or use fewer per row.")
        gap = (span - total) / (len(fps) + 1)
        cursor = lo + gap
        for fp, size in zip(fps, sizes):
            centre = pcbnew.FromMM(cursor + size / 2)
            pos = fp.GetPosition()
            fp.SetPosition(pcbnew.VECTOR2I(centre, pos.y) if axis == "x"
                           else pcbnew.VECTOR2I(pos.x, centre))
            cursor += size + gap
        done.append(f"spaced {len(fps)} on {axis} with {gap:.1f}mm gaps")
    else:
        sys.exit(f"unknown op {kind!r}; expected rotate, align or space_evenly")

pcbnew.SaveBoard(sys.argv[1], board)
print("; ".join(done))
"""

# Board design rules, through pcbnew rather than through the file.
#
# The board's own minimums are what DRC checks against, and they default to a
# conservative 0.2/0.3 that a fine-pitch footprint violates by existing: a
# USB-C receptacle's neighbouring pads are 0.15 mm apart and a QFN's thermal
# vias drill 0.2 mm. Measured 2026-09-16 on a real board, 107 of 111 DRC
# errors were track_width, drill_out_of_range and clearance -- rule settings,
# not placement mistakes, and placement cannot fix one of them.
#
# Set on BOTH the design settings and the default net class. The first is what
# DRC measures; the second is what the Specctra export hands the router, so a
# rule set only on the first produces a board routed at 0.2 mm and then
# measured against 0.15 mm.
#
# The clearance goes to EVERY net class, not only the default. DRC measures a
# net against its class's clearance, and the flai classes are applied before
# the rules step at 0.2 mm; release check 2 kept 30 errors between a USB-C
# receptacle's own pads, 0.156 mm apart, with a 0.15 mm fab. Clearance is the
# fab's capability, and v0.1 classifies no voltage that would need more.
#
# Through the API and not by editing the s-expression. An agent hand-writing a
# `net_settings` block into the file on 2026-09-16 guessed the syntax wrong and
# left a board that would not load at all -- pcbnew.LoadBoard returned None and
# every later step failed on something unrelated.
_RULES = """
import json, sys, pcbnew

board = pcbnew.LoadBoard(sys.argv[1])
if board is None:
    sys.exit("the board could not be loaded")
floors = json.loads(sys.argv[2])
want = json.loads(sys.argv[3])
ds = board.GetDesignSettings()
default = board.GetAllNetClasses()["Default"]

# name -> (design-settings field, net-class setter or None)
KNOBS = {
    "clearance_mm":           ("m_MinClearance",        "SetClearance"),
    "track_width_mm":         ("m_TrackMinWidth",       "SetTrackWidth"),
    "via_diameter_mm":        ("m_ViasMinSize",         "SetViaDiameter"),
    "via_drill_mm":           (None,                    "SetViaDrill"),
    "through_hole_drill_mm":  ("m_MinThroughDrill",     None),
    "annular_ring_mm":        ("m_ViasMinAnnularWidth", None),
    "hole_to_hole_mm":        ("m_HoleToHoleMin",       None),
    "hole_clearance_mm":      ("m_HoleClearance",       None),
    "edge_clearance_mm":      ("m_CopperEdgeClearance", None),
}

unknown = sorted((set(want) | set(floors)) - set(KNOBS))
if unknown:
    sys.exit("unknown rule(s): " + ", ".join(unknown) +
             "; known: " + ", ".join(sorted(KNOBS)))

# The fab profile's floors first: design-settings minimums only. A net class
# is the design's choice (set_net_classes) and is never lowered to a fab minimum.
for name, value in sorted(floors.items()):
    field, _ = KNOBS[name]
    if field:
        setattr(ds, field, pcbnew.FromMM(float(value)))

applied = []
for name, value in sorted(want.items()):
    field, setter = KNOBS[name]
    nm = pcbnew.FromMM(float(value))
    if field:
        setattr(ds, field, nm)
    if setter:
        getattr(default, setter)(nm)
    if name == "clearance_mm":
        for nc in board.GetAllNetClasses().values():
            nc.SetClearance(nm)
    applied.append(name + "=" + str(value) + "mm")

pcbnew.SaveBoard(sys.argv[1], board)
print("rules set: " + ", ".join(applied))
"""


_ZONE = """
import json, sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
net_name, layers, inset_mm = sys.argv[2], json.loads(sys.argv[3]), float(sys.argv[4])

net = board.FindNet(net_name)
if net is None or net.GetNetCode() <= 0:
    raise SystemExit("no net named %r on this board" % net_name)

box = board.GetBoardEdgesBoundingBox()
if box.GetWidth() <= 0 or box.GetHeight() <= 0:
    raise SystemExit("the board has no outline, so a pour has no boundary")
inset = pcbnew.FromMM(inset_mm)
left, top = box.GetLeft() + inset, box.GetTop() + inset
right, bottom = box.GetRight() - inset, box.GetBottom() - inset
if right <= left or bottom <= top:
    raise SystemExit("inset %.2fmm leaves no room inside the outline" % inset_mm)

wanted = []
for name in layers:
    layer = board.GetLayerID(name)
    if layer < 0:
        raise SystemExit("no such layer on this board: %s" % name)
    wanted.append((name, layer))

# Idempotent: a zone this call would create again is removed first, so
# re-running refills rather than stacking a second pour on the same copper.
replaced = 0
for zone in list(board.Zones()):
    if zone.GetNetname() == net_name and any(zone.IsOnLayer(l) for _, l in wanted):
        board.Remove(zone)
        replaced += 1

for name, layer in wanted:
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer)
    zone.SetNet(net)
    zone.AddPolygon(pcbnew.VECTOR_VECTOR2I([
        pcbnew.VECTOR2I(left, top), pcbnew.VECTOR2I(right, top),
        pcbnew.VECTOR2I(right, bottom), pcbnew.VECTOR2I(left, bottom)]))
    board.Add(zone)

pcbnew.ZONE_FILLER(board).Fill(board.Zones())
unfilled = [z.GetNetname() for z in board.Zones() if not z.IsFilled()]
area = sum(z.GetFilledArea() for z in board.Zones()) / 1e12
pcbnew.SaveBoard(sys.argv[1], board)
print(json.dumps({"zones": len(board.Zones()), "replaced": replaced,
                  "filled_area_mm2": round(area, 1), "unfilled": unfilled}))
"""


_EXPORT_DSN = """
import sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
if not pcbnew.ExportSpecctraDSN(board, sys.argv[2]):
    sys.exit("ExportSpecctraDSN failed")
print("dsn written")
"""

_IMPORT_SES = """
import sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
if not pcbnew.ImportSpecctraSES(board, sys.argv[2]):
    sys.exit("ImportSpecctraSES failed")
pcbnew.SaveBoard(sys.argv[1], board)
print("ses imported")
"""


def drc_command(source: pathlib.Path, report: pathlib.Path, kind: str = "drc") -> list[str]:
    """The one DRC command line: run_job runs it in the workspace, and the
    packaging step re-runs it on a copy of the delivered board."""
    return ["kicad-cli", *JOBS[kind], "--format", "json", "--severity-all",
            "--exit-code-violations", "-o", str(report), str(source)]


def run_job(kind: str, board: str, out: str) -> dict:
    if kind not in JOBS:
        raise ValueError(f"kind must be one of {sorted(JOBS)}")
    source = paths.inside(board)
    if not source.is_file():
        raise ValueError(f"no such file in the workspace: {board}")
    target = paths.inside(out)
    target.parent.mkdir(parents=True, exist_ok=True)

    done = subprocess.run(drc_command(source, target, kind), capture_output=True,
                          text=True, timeout=TIMEOUT)
    return {
        # 0 clean, 5 violations found, anything else is a tool failure. Passed
        # through rather than judged here for the same reason app/kicad.py
        # passes no verdict: two things computing an answer can disagree.
        "exit": done.returncode,
        "report": paths.rel(target),
        "stdout": done.stdout[-2000:],
        "stderr": done.stderr[-2000:],
    }


def python(script: str, *args: str) -> tuple[int, str]:
    """Run one of the pcbnew snippets above. Returns (exit, combined output).

    KiCad's plugin registration writes an assert to stderr on every pcbnew
    import in a headless container; it is harmless and would otherwise look
    like a failure, so the exit code decides and the text is only reported.
    """
    # -P: `python3 -c` otherwise puts the working directory, the project,
    # first on sys.path, and a `json.py` there replaced the standard library.
    done = subprocess.run(["python3", "-P", "-c", script, *args],
                          capture_output=True, text=True, timeout=TIMEOUT)
    return done.returncode, (done.stdout + done.stderr).strip()


def build_board(netlist: str, out: str, fp_libs: list[str]) -> dict:
    """A KiCad netlist becomes a board with every footprint on it, unplaced.

    kicad-cli cannot do this -- there is no `pcb import netlist` -- so it goes
    through kinet2pcb, which is why this image carries pip and pcbnew.
    """
    source = paths.inside(netlist)
    if not source.is_file():
        raise ValueError(f"no such netlist in the workspace: {netlist}")
    target = paths.inside(out)
    libs = [str(paths.inside(d)) for d in fp_libs]
    code, output = python(_NETLIST_TO_BOARD, str(source), str(target), *libs)
    if code != 0 or not target.exists():
        raise RuntimeError(f"kinet2pcb failed: {output[-600:]}")
    return {"board": paths.rel(target), "stdout": output[-600:]}


# Rule areas the router must keep tracks and vias out of, one per NPTH hole.
# KiCad's DSN export makes a round NPTH a keepout but an oval one a netless
# copper pin, so freerouting kept only its ordinary 0.2 mm from release check
# 2's oval USB-C hole: 17 tracks inside the board's hole and edge clearance.
# Added in memory to the board the DSN is exported from, never saved.
NPTH_KEEPOUT = "flai-npth-keepout"
# "hole": hole clearance only -- KiCad's own convention for the round NPTH
# keepouts it exports. "auto": max(hole clearance, edge clearance), which DRC
# also checks an NPTH hole against; measured on release2 it fences the
# connector's own pads in and loses 4 connections, so "hole" ships (the spec's
# pre-set fallback) and the leftover edge-only cases are 3b's, with the fab
# rules. None: no keepouts (a test seam).
NPTH_MARGIN: str | None = "hole"

# Shared by the export below and the tests that inspect the zones, which
# cannot read them back from a saved board because none is ever saved.
_ADD_NPTH_KEEPOUTS = """
def add_npth_keepouts(board, mode, name):
    ds = board.GetDesignSettings()
    margin = (ds.m_HoleClearance if mode == "hole"
              else max(ds.m_HoleClearance, ds.m_CopperEdgeClearance))
    copper = pcbnew.LSET()
    for layer in board.GetEnabledLayers().CuStack():
        copper.AddLayer(layer)
    count = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                continue
            # The drill hole's own outline grown by the margin -- a stadium
            # for an oval -- not the pad's copper and not a bounding box: DRC
            # measures hole and edge clearance from the hole. No drill, no
            # hole, no keepout.
            poly = pcbnew.SHAPE_POLY_SET()
            if (not pad.TransformHoleToPolygon(poly, margin, pcbnew.FromMM(0.005),
                                               pcbnew.ERROR_OUTSIDE)
                    or poly.OutlineCount() == 0):
                continue
            outline = poly.Outline(0)
            zone = pcbnew.ZONE(board)
            zone.SetIsRuleArea(True)
            zone.SetDoNotAllowTracks(True)
            zone.SetDoNotAllowVias(True)
            zone.SetDoNotAllowPads(False)
            zone.SetDoNotAllowZoneFills(False)
            zone.SetDoNotAllowFootprints(False)
            zone.SetLayerSet(copper)
            zone.SetZoneName(name)
            zone.AddPolygon(pcbnew.VECTOR_VECTOR2I(
                [outline.CPoint(i) for i in range(outline.PointCount())]))
            board.Add(zone)
            count += 1
    return count, margin
"""

# argv: board, dsn, margin mode, keepout name. The real board is loaded so its
# .kicad_pro loads with it -- net classes, their assignments and the hole
# clearance live there, and a copy without it exports every net at the
# default width. The keepouts exist only in memory: nothing is saved.
_EXPORT_DSN_WITH_KEEPOUTS = "import json, sys, pcbnew\n" + _ADD_NPTH_KEEPOUTS + """
board = pcbnew.LoadBoard(sys.argv[1])
count, margin = add_npth_keepouts(board, sys.argv[3], sys.argv[4])
if not pcbnew.ExportSpecctraDSN(board, sys.argv[2]):
    sys.exit("ExportSpecctraDSN failed")
print(json.dumps({"keepouts": count, "margin_mm": round(pcbnew.ToMM(margin), 4)}))
"""


def route(board: str, passes: int) -> dict:
    """Autoroute in place: board -> Specctra DSN -> freerouting -> SES -> board.

    KiCad ships no autorouter, and `kicad-cli pcb` exports no DSN, so the
    round trip is pcbnew's ExportSpecctraDSN / ImportSpecctraSES with the
    freerouting jar between them. The board is modified in place; the DSN and
    SES are left beside it because they are the evidence of what was routed.

    The DSN carries a keepout around every NPTH hole (NPTH_KEEPOUT, sized by
    NPTH_MARGIN), added to the board in memory at export and never saved; the
    routes come back into the real board, which never had them.
    """
    target = paths.inside(board)
    if not target.is_file():
        raise ValueError(f"no such board in the workspace: {board}")
    dsn, ses = target.with_suffix(".dsn"), target.with_suffix(".ses")
    keepouts = 0
    if NPTH_MARGIN:
        code, output = python(_EXPORT_DSN_WITH_KEEPOUTS, str(target), str(dsn),
                              NPTH_MARGIN, NPTH_KEEPOUT)
        answer = next((line for line in reversed(output.splitlines())
                       if line.startswith("{")), None)
        if code != 0 or answer is None:
            raise RuntimeError(f"DSN export with NPTH keepouts failed: {output[-400:]}")
        keepouts = json.loads(answer)["keepouts"]
    else:
        code, output = python(_EXPORT_DSN, str(target), str(dsn))
    if code != 0 or not dsn.exists():
        raise RuntimeError(f"DSN export failed: {output[-400:]}")

    done = subprocess.run(
        ["java", "-jar", FREEROUTING_JAR, "-de", str(dsn), "-do", str(ses),
         "-mp", str(passes)],
        capture_output=True, text=True, timeout=TIMEOUT)
    if not ses.exists():
        raise RuntimeError(
            f"freerouting produced no session file (exit {done.returncode}): "
            f"{(done.stdout + done.stderr).strip()[-600:]}")

    code, output = python(_IMPORT_SES, str(target), str(ses))
    if code != 0:
        raise RuntimeError(f"SES import failed: {output[-400:]}")
    # Detect-only guard: the keepouts are never saved, so this should not fire.
    if NPTH_KEEPOUT in target.read_text(errors="replace"):
        raise RuntimeError(f"an {NPTH_KEEPOUT} rule area reached the saved board")
    return {
        "board": paths.rel(target),
        "dsn": paths.rel(dsn),
        "ses": paths.rel(ses),
        "router": (done.stdout + done.stderr).strip()[-600:],
        "npth_keepouts": keepouts,
    }


# The layers a fab actually needs. Explicit, because the default is "every
# layer the board has": measured on a 33-part board, that is 25 files including
# Adhesive, Courtyard, Eco1/2 and four User layers, none of which a fabricator
# reads and several of which they will ask about.
FAB_LAYERS = ("F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,"
              "F.Paste,B.Paste,Edge.Cuts")


def export_fab(board: str, out: str, layers: str = FAB_LAYERS) -> dict:
    """Gerbers and drill files: the package a board is sent to a fab as.

    **`--check-zones` is not optional and this is the measurement that says
    so.** KiCad plots what is in the file, and an unfilled zone contains no
    copper, so a board whose pour was never filled exports a gerber with the
    pour simply absent -- silently, exit 0. Measured 2026-09-18 on KiCad
    10.0.5, one board, B.Cu, three ways:

        unfilled, no --check-zones ....  11,413 bytes   (no pour)
        unfilled, --check-zones ......  118,017 bytes   (refilled at plot time)
        filled board ................   118,011 bytes

    So the flag is what stands between "the agent exported gerbers" and a
    fabricated board with no ground plane. It refills for the PLOT only -- the
    board on disk is still unfilled afterwards, which is why fill_zones writes
    the fill into the board and DRC is run after it rather than before.
    """
    source = paths.inside(board)
    if not source.is_file():
        raise ValueError(f"no such board in the workspace: {board}")
    target = paths.inside(out)
    written = plot_fab(source, target, layers)
    return {
        "dir": paths.rel(target),
        "files": written,
        "gerbers": [f for f in written if not f.endswith((".drl", ".gbrjob"))],
        "drill": [f for f in written if f.endswith(".drl")],
        # The job file names every gerber and its function; a fab that gets one
        # does not have to guess which file is which layer.
        "job": next((f for f in written if f.endswith(".gbrjob")), None),
    }


def plot_fab(source: pathlib.Path, target: pathlib.Path, layers: str = FAB_LAYERS) -> list[str]:
    """export_fab's kicad-cli commands on already-resolved paths; the names of
    the files in target afterwards. The packaging step calls this directly on
    a temp folder, which is outside the workspace and paths.inside."""
    target.mkdir(parents=True, exist_ok=True)

    gerbers = subprocess.run(
        ["kicad-cli", "pcb", "export", "gerbers", "--check-zones",
         "--layers", layers, "-o", str(target), str(source)],
        capture_output=True, text=True, timeout=TIMEOUT)
    if gerbers.returncode != 0:
        raise RuntimeError(
            f"gerber export failed: {(gerbers.stdout + gerbers.stderr)[-600:]}")

    # Separate PTH and NPTH because a fab quotes them differently, and a map
    # because a drill file alone is a list of numbers nobody can check.
    drill = subprocess.run(
        ["kicad-cli", "pcb", "export", "drill", "--format", "excellon",
         "--excellon-separate-th", "--generate-map", "--map-format", "gerberx2",
         "-o", str(target), str(source)],
        capture_output=True, text=True, timeout=TIMEOUT)
    if drill.returncode != 0:
        raise RuntimeError(
            f"drill export failed: {(drill.stdout + drill.stderr)[-600:]}")

    return sorted(f.name for f in target.iterdir() if f.is_file())


def call(kind: str, request: dict) -> dict:
    """One pipeline step, as the old HTTP service answered it.

    A missing required field raises `KeyError` from the plain `request[...]`
    lookups below -- exactly what the old `do_POST` branches did before ITS
    own outer `except (KeyError, ValueError, ...)` turned that into an HTTP
    400. That translation has to happen here now, since there is no outer
    handler left to do it: callers (this module's own docstring, and
    circuit_mcp.tools) are told bad requests raise `ValueError`, and a raw
    `KeyError` would be a different, undocumented contract for the one kind
    of bad request that happens to be a missing field.
    """
    try:
        return _call(kind, request)
    except KeyError as exc:
        raise ValueError(f"missing field {exc}") from exc


# The line fab.custom_rules() writes at the top of every `.kicad_dru` it
# produces. Its presence is how the rules step tells its own file, safe to
# rewrite, from a human's, which it backs up instead.
_RULES_HEADER = "# Written by the circuit tools' rules step"


def _call(kind: str, request: dict) -> dict:
    if kind == "pcb":
        return build_board(
            request["netlist"],
            request.get("out")
            or f"{pathlib.Path(request['netlist']).stem}.kicad_pcb",
            request.get("fp_libs") or [])
    if kind == "outline":
        target = paths.inside(request["board"])
        code, out = python(_OUTLINE, str(target),
                            str(request["width_mm"]),
                            str(request["height_mm"]))
        if code != 0:
            raise RuntimeError(out[-400:])
        return {"board": request["board"], "stdout": out[-300:]}
    if kind == "rules":
        # Always the fab profile's floors, then the agent's rules on top, then
        # the custom rules KiCad cannot hold in one number, beside the board
        # where kicad-cli's DRC reads them.
        target = paths.inside(request["board"])
        rules = request.get("rules") or {}
        code, out = python(_RULES, str(target), json.dumps(fab.floors()),
                           json.dumps(rules))
        if code != 0:
            raise RuntimeError(out[-400:])
        dru = target.with_suffix(".kicad_dru")
        kept_dru = None
        # A `.kicad_dru` without our header was never written by this step --
        # a human's, kept beside the board -- and this step rewrites that path
        # on every run_layout, so it is backed up rather than lost. Overwrites
        # any earlier backup: only the latest human file is worth keeping.
        if dru.is_file() and _RULES_HEADER not in dru.read_text(errors="replace"):
            backup = dru.with_name(dru.name + ".orig")
            dru.replace(backup)
            kept_dru = paths.rel(backup)
        dru.write_text(fab.custom_rules(rules))
        return {"board": request["board"], "stdout": out[-300:],
                "profile": fab.PROFILE, "agent_rules": sorted(rules),
                "kept_dru": kept_dru}
    if kind == "place":
        target = paths.inside(request["board"])
        code, out = python(_PLACE, str(target),
                            json.dumps(request["positions"]))
        if code != 0:
            raise RuntimeError(out[-400:])
        return {"board": request["board"], "stdout": out[-300:]}
    if kind == "arrange":
        target = paths.inside(request["board"])
        code, out = python(_ARRANGE, str(target),
                            json.dumps(request["ops"]))
        if code != 0:
            raise ValueError(out.strip().splitlines()[-1])
        return {"board": request["board"], "stdout": out[-400:]}
    if kind == "zone":
        target = paths.inside(request["board"])
        code, out = python(
            _ZONE, str(target), request.get("net") or "GND",
            json.dumps(request.get("layers") or ["F.Cu", "B.Cu"]),
            str(request.get("inset_mm", 0.5)))
        if code != 0:
            raise ValueError(out.strip().splitlines()[-1])
        return {"board": request["board"], **json.loads(out.strip().splitlines()[-1])}
    if kind == "fab":
        return export_fab(
            request["board"],
            request.get("out")
            or f"{pathlib.Path(request['board']).stem}-gerbers",
            request.get("layers") or FAB_LAYERS)
    if kind == "route":
        return route(request["board"], int(request.get("passes", ROUTER_PASSES)))

    if kind != "drc":
        raise ValueError(f"unknown step {kind!r}")
    board = request["board"]
    out = request.get("out") or f"{pathlib.Path(board).stem}.drc.json"
    return run_job("drc", board, out)
