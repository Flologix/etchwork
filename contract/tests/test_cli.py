import json

import pytest

from contract import cli
from contract.board import Board
from contract.errors import HarnessError
from contract.tests.boards import make, rect

TOML = """
[[clause]]
id = "envelope"
statement = "fits"
checker = "outline_within"
args = { width_mm = 37.0, height_mm = 23.4, tolerance_mm = 0.2 }
"""


@pytest.fixture
def task(tmp_path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "contract.toml").write_text(TOML)
    return d


@pytest.fixture
def delivered(tmp_path):
    d = tmp_path / "run.out"
    (d / "kicad").mkdir(parents=True)
    (d / "kicad" / "board.kicad_pcb").write_text("(kicad_pcb)")
    return d


def loader(board=None, error=None):
    def load(found, image):
        assert image == "img"
        if error:
            raise HarnessError(error)
        return board
    return load


def run(task, board_dir, out, load):
    return cli.main(["score", str(task), str(board_dir), "--image", "img", "--out", str(out)], load=load)


def test_graded_pass(task, delivered, tmp_path, capsys):
    out = tmp_path / "r.json"
    assert run(task, delivered, out, loader(make(outline=rect(36, 22)))) == 0
    result = json.loads(out.read_text())
    assert result["strict_pass"] and result["graded"]
    lines = capsys.readouterr().out.splitlines()
    # One line per clause, then the strict verdict alone: no n-of-m composite.
    assert lines[-2] == "strict: PASS"


def test_graded_fail_still_exits_zero(task, delivered, tmp_path):
    out = tmp_path / "r.json"
    assert run(task, delivered, out, loader(make(outline=rect(40, 22)))) == 0
    assert not json.loads(out.read_text())["strict_pass"]


def test_not_delivered(task, tmp_path):
    empty = tmp_path / "empty.out"
    empty.mkdir()
    out = tmp_path / "r.json"
    assert run(task, empty, out, loader(error="must not be called")) == 0
    result = json.loads(out.read_text())
    assert not result["delivered"] and result["graded"]
    assert result["clauses"][0]["status"] == "not_delivered"


def test_dump_failure_is_exit_3_with_result(task, delivered, tmp_path):
    out = tmp_path / "r.json"
    assert run(task, delivered, out, loader(error="dump crashed")) == 3
    result = json.loads(out.read_text())
    assert not result["graded"]
    assert result["harness_errors"][0]["error"] == "dump crashed"


def test_broken_task_is_exit_3(tmp_path, delivered):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "contract.toml").write_text("not = [toml")
    assert run(bad, delivered, tmp_path / "r.json", loader()) == 3


def test_task_name_resolves_under_tasks(monkeypatch, task, delivered, tmp_path):
    monkeypatch.setattr(cli, "TASKS", task.parent)
    out = tmp_path / "r.json"
    assert cli.main(["score", "demo", str(delivered), "--image", "img", "--out", str(out)],
                    load=loader(make(outline=rect(36, 22)))) == 0


def test_default_out_does_not_clobber_job_result(task, delivered, tmp_path, monkeypatch):
    (delivered / "result.json").write_text('{"done": true}')
    monkeypatch.chdir(tmp_path)
    assert cli.main(["score", str(task), str(delivered), "--image", "img"],
                    load=loader(make(outline=rect(36, 22)))) == 0
    assert (delivered / "result.json").read_text() == '{"done": true}'
    assert (tmp_path / "contract-result.json").is_file()


def test_image_is_required(task, delivered):
    with pytest.raises(SystemExit) as e:
        cli.main(["score", str(task), str(delivered)])
    assert e.value.code == 2
