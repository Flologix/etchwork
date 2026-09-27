import json

import pytest

from contract import score
from contract.checkers import REGISTRY, Result
from contract.errors import HarnessError
from contract.tests.boards import fp, make, rect

TOML = """
[parts.ic]
ref = ["^U[0-9]+$"]

[nets.supply]
words = ["VCC"]

[[clause]]
id = "envelope"
statement = "fits"
checker = "outline_within"
args = { width_mm = 37.0, height_mm = 23.4, tolerance_mm = 0.2 }

[[clause]]
id = "ic"
statement = "an IC"
checker = "part_present"
args = { part = "ic" }
"""


def task_dir(tmp_path, text=TOML, name="demo"):
    d = tmp_path / name
    d.mkdir()
    (d / "contract.toml").write_text(text)
    return d


def board(w=30.0, with_ic=True):
    return make(outline=rect(w, 20), footprints=[fp("U1", "EV1527")] if with_ic else [])


def statuses(result):
    return {r["id"]: r["status"] for r in result["clauses"]}


def test_load_binds_aliases(tmp_path):
    t = score.load_task(task_dir(tmp_path))
    assert t.name == "demo"
    assert [c.id for c in t.clauses] == ["envelope", "ic"]
    assert t.clauses[1].args["part"].name == "ic"
    assert all(c.error is None for c in t.clauses)


def test_all_pass_is_strict_pass(tmp_path):
    r = score.grade(score.load_task(task_dir(tmp_path)), board())
    assert r["strict_pass"] and r["graded"] and r["delivered"]
    assert statuses(r) == {"envelope": "pass", "ic": "pass"}
    assert r["harness_errors"] == []
    json.dumps(r)  # result.json must serialise


def test_one_failure_is_strict_fail_with_measured(tmp_path):
    r = score.grade(score.load_task(task_dir(tmp_path)), board(w=40.0))
    assert not r["strict_pass"] and r["graded"]
    assert statuses(r) == {"envelope": "fail", "ic": "pass"}
    assert r["clauses"][0]["measured"] == {"width_mm": 40.0, "height_mm": 20.0}


def test_non_delivery_is_strict_failure_not_harness_error(tmp_path):
    r = score.grade(score.load_task(task_dir(tmp_path)), None)
    assert not r["strict_pass"] and not r["delivered"]
    assert r["graded"] and r["harness_errors"] == []
    assert set(statuses(r).values()) == {"not_delivered"}


def test_run_error_is_a_harness_error_not_a_failure(tmp_path):
    r = score.grade(score.load_task(task_dir(tmp_path)), None, run_error="dump crashed")
    assert r["delivered"] and not r["graded"] and not r["strict_pass"]
    assert set(statuses(r).values()) == {"error"}
    assert r["harness_errors"] == [{"clause": None, "error": "dump crashed"}]


def test_absent_checker_is_a_clause_harness_error(tmp_path):
    text = TOML + '\n[[clause]]\nid = "ghost"\nstatement = "s"\nchecker = "pin_floating"\n'
    t = score.load_task(task_dir(tmp_path, text))
    r = score.grade(t, board())
    assert statuses(r) == {"envelope": "pass", "ic": "pass", "ghost": "error"}
    assert not r["graded"] and not r["strict_pass"]
    assert r["harness_errors"][0]["clause"] == "ghost"
    assert "pin_floating" in r["harness_errors"][0]["error"]


@pytest.mark.parametrize("args", [
    '{ part = "nope" }',                  # unknown alias
    '{ part = "ic", colour = "red" }',    # unknown argument
    '{ }',                                # missing required argument
])
def test_unbindable_args_are_clause_harness_errors(tmp_path, args):
    text = TOML + f'\n[[clause]]\nid = "bad"\nstatement = "s"\nchecker = "part_present"\nargs = {args}\n'
    t = score.load_task(task_dir(tmp_path, text))
    bad = next(c for c in t.clauses if c.id == "bad")
    assert bad.error
    assert statuses(score.grade(t, board()))["bad"] == "error"


def test_checker_that_raises_is_a_harness_error_others_still_grade(tmp_path, monkeypatch):
    def boom(board, *, width_mm, height_mm, tolerance_mm):
        raise RuntimeError("kaboom")
    monkeypatch.setitem(REGISTRY, "outline_within", boom)
    r = score.grade(score.load_task(task_dir(tmp_path)), board())
    assert statuses(r) == {"envelope": "error", "ic": "pass"}
    assert "kaboom" in r["harness_errors"][0]["error"]


def test_checker_returning_non_result_is_a_harness_error(tmp_path, monkeypatch):
    monkeypatch.setitem(REGISTRY, "outline_within", lambda board, **kw: True)
    r = score.grade(score.load_task(task_dir(tmp_path)), board())
    assert statuses(r)["envelope"] == "error"


def test_unserialisable_measured_is_stringified(tmp_path, monkeypatch):
    monkeypatch.setitem(REGISTRY, "outline_within",
                        lambda board, **kw: Result(True, "ok", {"odd": {1, 2}}))
    r = score.grade(score.load_task(task_dir(tmp_path)), board())
    json.dumps(r)


@pytest.mark.parametrize("text", [
    "not = [toml",                                                          # invalid TOML
    "[parts.x]\n",                                                          # bad alias
    "[parts.a]\nref = ['U']\n[nets.a]\nwords = ['VCC']\n",                  # alias clash
    "",                                                                     # no clauses
    '[[clause]]\nid = "a"\nstatement = "s"\nchecker = "no_short"\n' * 2,    # duplicate id
    '[[clause]]\nid = "a"\nchecker = "no_short"\n',                         # missing statement
])
def test_unusable_task_file_is_a_harness_error(tmp_path, text):
    with pytest.raises(HarnessError):
        score.load_task(task_dir(tmp_path, text))


def test_missing_task_file_is_a_harness_error(tmp_path):
    with pytest.raises(HarnessError):
        score.load_task(tmp_path / "nope")
