"""Datasheet-parts design, phase 4a: the dual_row and quad_flat families
(§6.1), from a datasheet's recommended land pattern, compared pad for pad
with KiCad's own footprints of the same packages (§9).

Pin numbering and orientation are where a hand-drawn footprint goes wrong,
so the goldens are KiCad's library files themselves, read from the image.
"""
import re
import shutil

import pytest

from circuit_mcp import makepart, parts, partspec, paths
from test_partspec import ODU, _edit

SPEC = "parts/X.part.toml"


def kicad_pads(library: str, name: str) -> list[dict]:
    """Every pad of one of KiCad's own footprints: number, at, size, drill, layers."""
    text = (paths.FP_ROOT / f"{library}.pretty" / f"{name}.kicad_mod").read_text()
    flat = re.sub(r"\s+", " ", text)
    out = []
    for m in re.finditer(r'\(pad "([^"]*)" (\S+) (\S+) \(at ([-\d.]+) ([-\d.]+)(?: [-\d.]+)?\) '
                         r'\(size ([\d.]+) ([\d.]+)\)(.*?)\(layers ([^)]*)\)', flat):
        drill = re.search(r"\(drill ([\d.]+)\)", m.group(8))
        out.append({"number": m.group(1), "x": float(m.group(4)), "y": float(m.group(5)),
                    "size": (float(m.group(6)), float(m.group(7))),
                    "drill": float(drill.group(1)) if drill else None,
                    "paste_only": m.group(9).strip() == '"F.Paste"'})
    return out


def _numbers(package):
    """The pin table a package needs: its terminals, and its exposed pad."""
    count = package.get("count", 0)
    numbers = list(range(1, count + 1))
    if "thermal_pad_mm" in package:
        numbers.append(package.get("thermal_pin", count + 1))
    return numbers


def _spec(workspace, package, name="X"):
    (workspace / "parts").mkdir(exist_ok=True)
    (workspace / "datasheets").mkdir(exist_ok=True)
    (workspace / "datasheets" / "GK1YAR-P16UC00-000L.pdf").write_bytes(b"%PDF-1.4\n")
    path = workspace / SPEC
    shutil.copy(ODU, path)

    def change(d):
        d.update(mpn=name, name=name, library="Pkg")
        d.pop("numbering", None)
        d.pop("fab", None)
        d["package"] = {"view": "top", "source": "p.9, recommended land pattern", **package}
        d["pins"] = [{"number": str(n), "name": f"P{n}", "type": "passive"} for n in _numbers(package)]
    _edit(path, change)
    spec, problems = partspec.load(SPEC)
    assert problems == [], problems
    return spec


def _compare(made: list[dict], golden: list[dict], dx=0.0, dy=0.0):
    """Every numbered golden pad made at the same place and size (±0.001 mm)."""
    by_number = {p["number"]: p for p in made if p["number"]}
    for g in (g for g in golden if g["number"]):
        m = by_number[g["number"]]
        assert (m["x"] + dx, m["y"] + dy) == pytest.approx((g["x"], g["y"]), abs=1e-3), g["number"]
        assert m["size"] == pytest.approx(g["size"], abs=1e-3), g["number"]
        assert m["drill"] == pytest.approx(g["drill"]) if g["drill"] else m["drill"] is None
    assert sorted(by_number, key=partspec._order) == sorted(
        (g["number"] for g in golden if g["number"]), key=partspec._order)


SOIC8 = {"family": "dual_row", "count": 8, "pitch_mm": 1.27, "row_spacing_mm": 4.95, "pad_mm": [1.95, 0.6]}
QFN16 = {"family": "quad_flat", "count": 16, "pitch_mm": 0.5, "row_spacing_mm": 2.925,
         "pad_mm": [0.825, 0.25], "thermal_pad_mm": [1.7, 1.7]}
DIP8 = {"family": "dual_row", "count": 8, "pitch_mm": 2.54, "row_spacing_mm": 7.62,
        "drill_mm": 0.8, "pad_mm": [1.6, 1.6]}


def test_soic8_matches_kicads_own_footprint(workspace):
    _compare(makepart.pads(_spec(workspace, SOIC8)),
             kicad_pads("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"))


def test_qfn16_matches_kicads_own_footprint_exposed_pad_included(workspace):
    made = makepart.pads(_spec(workspace, QFN16))
    golden = kicad_pads("Package_DFN_QFN", "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm")
    _compare(made, golden)
    assert _pad(made, "17")["size"] == pytest.approx((1.7, 1.7))


def test_the_exposed_pads_paste_is_split_as_kicads_is(workspace):
    made = makepart.paste(_spec(workspace, QFN16))
    golden = [g for g in kicad_pads("Package_DFN_QFN", "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm")
              if g["paste_only"]]
    assert sorted((p["x"], p["y"], p["size"]) for p in made) == pytest.approx(
        sorted((g["x"], g["y"], g["size"]) for g in golden), abs=1e-3)


def test_dip8_matches_kicads_own_footprint_up_to_its_origin(workspace):
    """KiCad's DIP footprints put pin 1 at the origin; make_part centres."""
    made = makepart.pads(_spec(workspace, DIP8))
    golden = kicad_pads("Package_DIP", "DIP-8_W7.62mm")
    one, gone = _pad(made, "1"), next(g for g in golden if g["number"] == "1")
    _compare(made, golden, dx=gone["x"] - one["x"], dy=gone["y"] - one["y"])
    assert one["shape"] == "rect"


def test_a_rectangular_quad_flat_package(workspace):
    """20 pins: 6 down each side, 4 along top and bottom."""
    made = makepart.pads(_spec(workspace, {**QFN16, "count": 20, "pins_lr": 6, "column_spacing_mm": 3.925}))
    left = [p for p in made if p["x"] < -1]
    bottom = [p for p in made if p["y"] > 1.5 and abs(p["x"]) < 1.5]
    assert [p["number"] for p in left] == ["1", "2", "3", "4", "5", "6"]
    assert [p["number"] for p in bottom] == ["7", "8", "9", "10"]
    assert _pad(made, "7")["size"] == pytest.approx((0.25, 0.825))


def test_a_bottom_view_package_is_mirrored(workspace):
    spec = _spec(workspace, {**SOIC8, "view": "bottom"})
    assert _pad(makepart.pads(spec), "1")["x"] > 0


def test_a_soic_is_made_and_described(workspace):
    _spec(workspace, SOIC8)
    out = makepart.make_part(SPEC)
    assert out.startswith("MADE"), out
    assert "8 electrical pads" in parts.describe_footprint("Pkg:X")
    text = (workspace / "fp" / "Pkg.pretty" / "X.kicad_mod").read_text()
    assert re.search(r'\(pad "1" smd roundrect \(at -2.475 -1.905\) \(size 1.95 0.6\)', text), text


def test_a_qfn_is_made_with_its_exposed_pad_and_paste(workspace):
    _spec(workspace, QFN16)
    assert makepart.make_part(SPEC).startswith("MADE")
    text = (workspace / "fp" / "Pkg.pretty" / "X.kicad_mod").read_text()
    assert re.search(r'\(pad "17" smd rect \(at 0 0\) \(size 1.7 1.7\) \(layers "F.Cu" "F.Mask"\)', text), text
    assert text.count('(layers "F.Paste")') == 4


def test_pads_too_wide_for_their_pitch_are_refused(workspace):
    _spec(workspace, {**SOIC8, "pad_mm": [1.95, 1.2]})
    out = makepart.make_part(SPEC)
    assert out.startswith("REFUSED") and "pads 1 and 2" in out, out


def _pad(pads, number):
    return next(p for p in pads if p["number"] == number)


# --- the spec format for the new families ---------------------------------------

@pytest.mark.parametrize("package, problem", [
    ({**SOIC8, "pitch_mm": 0}, "pitch_mm"),
    ({k: v for k, v in SOIC8.items() if k != "row_spacing_mm"}, "row_spacing_mm"),
    ({**SOIC8, "pad_mm": [1.95]}, "pad_mm"),
    ({**SOIC8, "count": 7}, "even"),
    ({**QFN16, "count": 18}, "4"),
    ({**QFN16, "pins_lr": 9}, "pins_lr"),
    ({**QFN16, "thermal_pin": 3}, "pad 3 is numbered twice"),
])
def test_a_bad_family_value_is_named(workspace, package, problem):
    (workspace / "parts").mkdir()
    (workspace / "datasheets").mkdir()
    (workspace / "datasheets" / "GK1YAR-P16UC00-000L.pdf").write_bytes(b"%PDF-1.4\n")
    path = workspace / SPEC
    shutil.copy(ODU, path)
    _edit(path, lambda d: (d.pop("numbering"), d.pop("fab"), d.update(
        package={"view": "top", "source": "p.9", **package},
        pins=[{"number": str(n), "name": f"P{n}", "type": "passive"}
              for n in dict.fromkeys(_numbers(package))])))
    _, problems = partspec.load(SPEC)
    assert any(problem in p for p in problems), problems
