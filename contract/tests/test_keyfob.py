"""The grader's own proof (design spec §6): the clean fixture passes every
keyfob-base clause, and each mutant fails its own clause and no other. A
checker that passes its own mutant is broken; one that fails a neighbour's
is too coarse. Runs in the engine image only."""
import functools
from pathlib import Path

import pytest

pytest.importorskip("pcbnew")

from contract import dump, score  # noqa: E402
from contract.board import Board  # noqa: E402
from contract.tests.fixture_checks import canonical  # noqa: E402
from contract.tests.fixtures import gen_keyfob  # noqa: E402

HERE = Path(__file__).resolve().parent
TASK = HERE.parent / "tasks" / "keyfob-base"
FIXTURES = HERE / "fixtures" / "keyfob"
CLAUSES = [c.id for c in score.load_task(TASK).clauses]


@functools.lru_cache(maxsize=None)
def _dump(path: Path) -> dict:
    return dump.dump(path)


def _grade(variant: str) -> dict:
    board = Board(_dump(FIXTURES / variant / "kicad" / "board.kicad_pcb"))
    return score.grade(score.load_task(TASK), board)


def _not_passing(result):
    return {r["id"] for r in result["clauses"] if r["status"] != "pass"}


def _explain(result):
    return [(r["id"], r["status"], r["detail"]) for r in result["clauses"]]


def _dirs():
    return sorted(p.name for p in FIXTURES.iterdir() if p.name != "clean")


def test_every_clause_has_exactly_one_mutant():
    plain = [d for d in _dirs() if "--" not in d]
    assert plain == sorted(CLAUSES)
    assert sorted(gen_keyfob.MUTANTS) == sorted(CLAUSES)


def test_every_variant_is_named_for_a_clause_and_committed():
    assert [d for d in _dirs() if "--" in d] == sorted(gen_keyfob.VARIANTS)
    for name in gen_keyfob.VARIANTS:
        clause, case = name.split("--")
        assert clause in CLAUSES and case, name


def test_clean_board_passes_every_clause():
    result = _grade("clean")
    assert result["graded"], result["harness_errors"]
    assert result["strict_pass"], _explain(result)


def test_clean_board_measurements():
    rows = {r["id"]: r for r in _grade("clean")["clauses"]}
    assert rows["envelope"]["measured"] == {"width_mm": 36.0, "height_mm": 22.0}
    assert rows["decoupling"]["measured"]["cap"] == "C1"
    assert rows["decoupling"]["measured"]["distance_mm"] < 3.0
    assert rows["btn_inputs"]["measured"]["reach"] == {"SW1": ["N_SW1"], "SW2": ["N_SW2"]}


@pytest.mark.parametrize("clause", CLAUSES)
def test_mutant_fails_its_clause_and_only_it(clause):
    result = _grade(clause)
    assert result["graded"], result["harness_errors"]
    assert _not_passing(result) == {clause}, _explain(result)


@pytest.mark.parametrize("variant", sorted(gen_keyfob.VARIANTS))
def test_adversarial_variant_fails_its_clause_and_only_it(variant):
    result = _grade(variant)
    assert result["graded"], result["harness_errors"]
    assert _not_passing(result) == {variant.split("--")[0]}, _explain(result)


def test_committed_fixtures_match_the_generator(tmp_path):
    # UUIDs differ run to run, so compare what the grader sees, not bytes.
    gen_keyfob.main(tmp_path)
    for variant in sorted(p.name for p in FIXTURES.iterdir()):
        committed = _dump(FIXTURES / variant / "kicad" / "board.kicad_pcb")
        fresh = dump.dump(tmp_path / variant / "kicad" / "board.kicad_pcb")
        assert canonical(committed) == canonical(fresh), variant
