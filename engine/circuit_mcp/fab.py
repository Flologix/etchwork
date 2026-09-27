"""The default fab profile: DRC floors, and the custom rules KiCad cannot
hold in one number.

KiCad has one hole clearance and applies its board-edge clearance to NPTH
holes too. A fab states PTH-to-track, via-to-track and NPTH-to-track as
separate numbers and the routed edge as another; those that do not fit the
design settings go into `<stem>.kicad_dru`, which kicad-cli reads from
beside the board. Pure: no pcbnew, no files written here.
"""
import functools
import pathlib
import tomllib

PROFILE = "jlcpcb-2layer-standard"
_PATH = pathlib.Path(__file__).with_name("fab_profiles.toml")

# Profile key -> the rules-step knob it sets as a floor (design settings only,
# never a net class). via_drill_mm is the smallest drill the fab takes without
# a surcharge, so it floors every through hole.
FLOORS = {
    "track_width_mm": "track_width_mm",
    "clearance_mm": "clearance_mm",
    "via_diameter_mm": "via_diameter_mm",
    "via_drill_mm": "through_hole_drill_mm",
    "annular_ring_mm": "annular_ring_mm",
    "hole_to_hole_mm": "hole_to_hole_mm",
    "hole_clearance_mm": "hole_clearance_mm",
    "edge_clearance_mm": "edge_clearance_mm",
}


@functools.cache
def profiles() -> dict:
    return tomllib.loads(_PATH.read_text())


def value(key: str, profile: str = PROFILE) -> float:
    return float(profiles()[profile][key]["value"])


def floors(profile: str = PROFILE) -> dict[str, float]:
    """`knob -> mm` for the rules step, floors only."""
    return {knob: value(key, profile) for key, knob in FLOORS.items()}


def custom_rules(rules: dict | None = None, profile: str = PROFILE) -> str:
    """The `<stem>.kicad_dru` text. An agent-given `hole_clearance_mm` is its
    fab's hole-to-copper number, so it replaces the profile's PTH and NPTH
    values too; its `edge_clearance_mm` is the routed outline only, which the
    design settings already carry. `hole_to_hole_mm` replaces the PTH pad
    hole-to-hole value and `through_hole_drill_mm` replaces the NPTH minimum
    drill value, the same way."""
    rules = rules or {}
    hole = rules.get("hole_clearance_mm")
    pth = hole if hole is not None else value("pth_hole_clearance_mm", profile)
    npth = hole if hole is not None else value("npth_hole_clearance_mm", profile)
    hole_to_hole = rules.get("hole_to_hole_mm")
    if hole_to_hole is None:
        hole_to_hole = value("pth_hole_to_hole_mm", profile)
    drill = rules.get("through_hole_drill_mm")
    if drill is None:
        drill = value("npth_min_drill_mm", profile)
    track = "(B.Type == 'Track' || B.Type == 'Arc')"
    return "\n".join([
        "(version 1)",
        f"# Written by the circuit tools' rules step: profile {profile}.",
        "# Values and their sources: circuit_mcp/fab_profiles.toml. Rewritten on",
        "# every run_layout; edit rules through `rules`, not here.",
        "",
        '(rule "PTH hole to track"',
        f'\t(condition "A.Type == \'Pad\' && A.isPlated() && {track}")',
        f"\t(constraint hole_clearance (min {pth}mm)))",
        "",
        '(rule "NPTH hole to track"',
        f'\t(condition "A.Type == \'Pad\' && !A.isPlated() && {track}")',
        f"\t(constraint hole_clearance (min {npth}mm)))",
        "",
        "# KiCad checks an NPTH hole against the board-edge rule; a fab rates",
        "# copper-to-NPTH by its hole rule, not the routed outline's.",
        '(rule "NPTH hole as edge"',
        "\t(condition \"(A.Type == 'Pad' && !A.isPlated()) || (B.Type == 'Pad' && !B.isPlated())\")",
        f"\t(constraint edge_clearance (min {npth}mm)))",
        "",
        '(rule "PTH pad hole to hole"',
        "\t(condition \"A.Type == 'Pad' && B.Type == 'Pad' && A.isPlated() && B.isPlated()\")",
        f"\t(constraint hole_to_hole (min {hole_to_hole}mm)))",
        "",
        '(rule "NPTH minimum drill"',
        "\t(condition \"A.Type == 'Pad' && !A.isPlated()\")",
        f"\t(constraint hole_size (min {drill}mm)))",
        "",
    ])
