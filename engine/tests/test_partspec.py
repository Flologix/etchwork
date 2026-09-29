"""The part spec (datasheet-parts design §4): the one file the agent's model
writes for a part, and everything the engine can check about it before any
geometry is made. Every value cites its source; whatever the datasheet does
not state is marked unknown, with a note -- never a silent guess."""
import shutil
import tomllib

import pytest

from circuit_mcp import partspec
from conftest import FIXTURES

ODU = FIXTURES / "parts" / "GK1YAR-P16UC00-000L.part.toml"


@pytest.fixture
def odu(workspace):
    (workspace / "parts").mkdir()
    (workspace / "datasheets").mkdir()
    (workspace / "datasheets" / "GK1YAR-P16UC00-000L.pdf").write_bytes(b"%PDF-1.4\n")
    shutil.copy(ODU, workspace / "parts" / ODU.name)
    return workspace / "parts" / ODU.name


def _edit(path, change):
    """Rewrite the spec at `path` through `change(dict)`, as TOML."""
    data = tomllib.loads(path.read_text())
    change(data)
    path.write_text(_dump(data))


def _dump(data, prefix=""):
    """Enough of a TOML writer for these specs: tables, arrays of tables,
    strings, numbers, booleans and flat arrays."""
    def value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if isinstance(v, list):
            return "[" + ", ".join(value(x) for x in v) + "]"
        return repr(v)
    lines, tables = [], []
    for key, v in data.items():
        if isinstance(v, dict):
            tables.append((key, v, False))
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            tables.append((key, v, True))
        else:
            lines.append(f"{key} = {value(v)}")
    out = "\n".join(lines) + "\n"
    for key, v, many in tables:
        name = f"{prefix}{key}"
        for item in (v if many else [v]):
            out += f"\n[{'[' if many else ''}{name}{']' if many else ''}]\n" + _dump(item, f"{name}.")
    return out


def test_the_odu_spec_loads_with_no_problems(odu):
    spec, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert problems == []
    assert spec["mpn"] == "GK1YAR-P16UC00-000L"


def test_the_pads_a_spec_makes_are_counted_from_its_package(odu):
    spec, _ = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert partspec.pad_numbers(spec) == [str(n) for n in range(1, 18)]


def test_what_the_datasheet_did_not_state_is_listed(odu):
    spec, _ = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert partspec.unknowns(spec) == [
        "numbering: outer ring numbered 1-11 clockwise from the keyway side, inner 12-16; assumed"]


def test_the_writer_used_by_these_tests_round_trips_the_fixture(odu):
    before = tomllib.loads(odu.read_text())
    assert tomllib.loads(_dump(before)) == before


@pytest.mark.parametrize("field", ["mpn", "manufacturer", "datasheet", "library", "package"])
def test_a_missing_required_field_is_named(odu, field):
    _edit(odu, lambda d: d.pop(field))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any(field in p for p in problems), problems


def test_a_value_without_its_source_is_a_problem(odu):
    _edit(odu, lambda d: d["package"]["rings"][0].pop("source"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("package.rings[1]" in p and "source" in p for p in problems), problems


def test_a_pin_without_a_source_is_fine_when_the_pins_have_one(odu):
    spec, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert problems == [] and "source" not in spec["pins"][0]


def test_pins_with_no_source_anywhere_are_a_problem(odu):
    _edit(odu, lambda d: d.pop("pins_source"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("pins[1]" in p and "source" in p for p in problems), problems


def test_unknown_without_a_note_is_a_problem(odu):
    _edit(odu, lambda d: d["numbering"].pop("note"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("numbering" in p and "note" in p for p in problems), problems


def test_the_drawings_view_is_required(odu):
    """Top and bottom views are mirror images: the classic footprint error."""
    _edit(odu, lambda d: d["package"].pop("view"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("view" in p and "top" in p and "bottom" in p for p in problems), problems


def test_an_unknown_family_names_the_ones_that_exist(odu):
    _edit(odu, lambda d: d["package"].update(family="blob"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("blob" in p and "circular" in p and "quad_flat" in p for p in problems), problems


def test_pins_and_pads_must_agree(odu):
    def change(d):
        d["pins"] = [p for p in d["pins"] if p["number"] != "16"]
        d["pins"].append({"number": "18", "name": "X", "type": "passive"})
    _edit(odu, change)
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    joined = "\n".join(problems)
    assert "pad 16 has no pin" in joined and "pin 18 has no pad" in joined, problems


def test_two_rings_numbering_the_same_pins_is_a_problem(odu):
    _edit(odu, lambda d: d["package"]["rings"][1].update(first_pin=10))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("pad 10" in p and "twice" in p for p in problems), problems


def test_a_pin_type_outside_kicads_is_named_with_the_allowed_ones(odu):
    _edit(odu, lambda d: d["pins"][0].update(type="signal"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("'signal'" in p and "power_in" in p for p in problems), problems


@pytest.mark.parametrize("field, value", [
    ("diameter_mm", 0), ("diameter_mm", "4.37"), ("count", 0), ("drill_mm", -1),
    ("direction", "left"),
])
def test_a_bad_ring_value_is_named(odu, field, value):
    _edit(odu, lambda d: d["package"]["rings"][0].update({field: value}))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("package.rings[1]" in p and field in p for p in problems), problems


def test_the_datasheet_must_be_a_file_in_the_project(odu, workspace):
    _edit(odu, lambda d: d.update(datasheet="../elsewhere.pdf"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("datasheet" in p and "outside the project" in p for p in problems), problems
    _edit(odu, lambda d: d.update(datasheet="datasheets/missing.pdf"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("datasheet" in p and "no such file" in p for p in problems), problems


def test_a_library_name_that_cannot_be_a_folder_is_a_problem(odu):
    _edit(odu, lambda d: d.update(library="../ODU"))
    _, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert any("library" in p for p in problems), problems


def test_an_explicit_pad_list_is_a_family_of_its_own(odu):
    def change(d):
        d["package"] = {"family": "pads", "view": "top", "source": "p.3, land pattern"}
        d["pads"] = [{"number": "1", "at_mm": [0, 0], "size_mm": [1.5, 1.5], "drill_mm": 0.8,
                      "shape": "rect", "source": "p.3"},
                     {"number": "2", "at_mm": [2.54, 0], "size_mm": [1.5, 1.5], "drill_mm": 0.8,
                      "source": "p.3"}]
        d["pins"] = [{"number": "1", "name": "A", "type": "passive"},
                     {"number": "2", "name": "B", "type": "passive"}]
    _edit(odu, change)
    spec, problems = partspec.load("parts/GK1YAR-P16UC00-000L.part.toml")
    assert problems == [] and partspec.pad_numbers(spec) == ["1", "2"]


def test_a_spec_that_is_not_toml_is_a_problem_not_a_crash(workspace):
    (workspace / "bad.part.toml").write_text("mpn = [unclosed")
    spec, problems = partspec.load("bad.part.toml")
    assert spec is None and problems and "TOML" in problems[0]


def test_a_spec_outside_the_project_is_refused(workspace):
    spec, problems = partspec.load("../x.part.toml")
    assert spec is None and "outside the project folder" in problems[0]
