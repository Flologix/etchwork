"""The grader's proof for the datasheet tasks (design
2026-09-29-contract-tasks §5): per task, the clean fixture passes every
clause, each mutant fails its own clause and no other, and each
adversarial variant fails exactly what its generator says (its named clause
unless the generator's EXPECT names the set). Runs in the engine image only."""
import functools
import importlib
from pathlib import Path

import pytest

pytest.importorskip("pcbnew")

from contract import dump, score  # noqa: E402
from contract.board import Board  # noqa: E402
from contract.tests.fixture_checks import canonical  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

# task -> generator module; fixtures live in fixtures/<task>/
TASKS = {
    "usbc-ldo": "gen_usbc_ldo",
    "usb-serial": "gen_usb_serial",
    "relay-driver": "gen_relay_driver",
}


def _gen(task):
    return importlib.import_module(f"contract.tests.fixtures.{TASKS[task]}")


def _task(task):
    return score.load_task(HERE.parent / "tasks" / task)


def _clauses(task):
    return [c.id for c in _task(task).clauses]


@functools.lru_cache(maxsize=None)
def _dump(path: Path) -> dict:
    return dump.dump(path)


def _grade(task, variant):
    board = Board(_dump(FIXTURES / task / variant / "kicad" / "board.kicad_pcb"))
    return score.grade(_task(task), board)


def _not_passing(result):
    return {r["id"] for r in result["clauses"] if r["status"] != "pass"}


def _explain(result):
    return [(r["id"], r["status"], r["detail"]) for r in result["clauses"]]


def _dirs(task):
    return sorted(p.name for p in (FIXTURES / task).iterdir() if p.name != "clean")


def _expected(task, variant):
    return set(getattr(_gen(task), "EXPECT", {}).get(variant, {variant.split("--")[0]}))


MUTANT_CASES = [(t, m) for t in TASKS for m in sorted(_gen(t).MUTANTS)]
VARIANT_CASES = [(t, v) for t in TASKS for v in sorted(_gen(t).VARIANTS)]


@pytest.mark.parametrize("task", TASKS)
def test_every_clause_has_exactly_one_mutant(task):
    assert sorted(_gen(task).MUTANTS) == sorted(_clauses(task))
    assert [d for d in _dirs(task) if "--" not in d] == sorted(_clauses(task))


@pytest.mark.parametrize("task", TASKS)
def test_every_variant_is_named_for_a_clause_and_committed(task):
    gen = _gen(task)
    assert [d for d in _dirs(task) if "--" in d] == sorted(gen.VARIANTS)
    for name in gen.VARIANTS:
        clause, case = name.split("--")
        assert clause in _clauses(task) and case, name
    for name, clauses in getattr(gen, "EXPECT", {}).items():
        assert name in gen.VARIANTS and set(clauses) <= set(_clauses(task)), name


@pytest.mark.parametrize("task", TASKS)
def test_clean_board_passes_every_clause(task):
    result = _grade(task, "clean")
    assert result["graded"], result["harness_errors"]
    assert result["strict_pass"], _explain(result)


@pytest.mark.parametrize("task, clause", MUTANT_CASES)
def test_mutant_fails_its_clause_and_only_it(task, clause):
    result = _grade(task, clause)
    assert result["graded"], result["harness_errors"]
    assert _not_passing(result) == {clause}, _explain(result)


@pytest.mark.parametrize("task, variant", VARIANT_CASES)
def test_adversarial_variant_fails_exactly_what_it_should(task, variant):
    result = _grade(task, variant)
    assert result["graded"], result["harness_errors"]
    assert _not_passing(result) == _expected(task, variant), _explain(result)


@pytest.mark.parametrize("task", TASKS)
def test_committed_fixtures_match_the_generator(task, tmp_path):
    # UUIDs differ run to run, so compare what the grader sees, not bytes.
    _gen(task).main(tmp_path)
    for variant in sorted(p.name for p in (FIXTURES / task).iterdir()):
        committed = _dump(FIXTURES / task / variant / "kicad" / "board.kicad_pcb")
        fresh = dump.dump(tmp_path / variant / "kicad" / "board.kicad_pcb")
        assert canonical(committed) == canonical(fresh), variant
