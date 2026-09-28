import json
import subprocess
import zipfile

import pytest

from contract import delivery
from contract.errors import HarnessError
from contract.tests.boards import rect

DUMP = {"outline": rect(36, 22), "footprints": [], "pads": [], "tracks": [], "zones": []}


def tree(root, boards=("board.kicad_pcb",), netlists=("board.net",)):
    (root / "kicad").mkdir(parents=True)
    (root / "source").mkdir()
    for b in boards:
        (root / "kicad" / b).write_text("(kicad_pcb)")
    for n in netlists:
        (root / "source" / n).write_text("(export)")
    return root


def test_unpacked_tree(tmp_path):
    tree(tmp_path)
    with delivery.open_delivery(tmp_path) as found:
        assert found.pcb.name == "board.kicad_pcb"
        assert found.netlist.name == "board.net"
        assert found.root == tmp_path.resolve()


def test_zip_is_unpacked_and_cleaned_up(tmp_path):
    with zipfile.ZipFile(tmp_path / "deliverable.zip", "w") as z:
        z.writestr("kicad/board.kicad_pcb", "(kicad_pcb)")
        z.writestr("source/board.net", "(export)")
    with delivery.open_delivery(tmp_path) as found:
        assert found.pcb.read_text() == "(kicad_pcb)"
        root = found.root
    assert not root.exists()


def test_missing_netlist_is_allowed(tmp_path):
    tree(tmp_path, netlists=())
    with delivery.open_delivery(tmp_path) as found:
        assert found.netlist is None


def test_empty_dir_is_not_delivered(tmp_path):
    with delivery.open_delivery(tmp_path) as found:
        assert found is None


def test_job_out_without_zip_is_not_delivered(tmp_path):
    # What `etchwork job` leaves when packaging refused the board.
    (tmp_path / "result.json").write_text(json.dumps({"done": False, "reason": "DRC"}))
    with delivery.open_delivery(tmp_path) as found:
        assert found is None


def test_two_boards_is_a_harness_error(tmp_path):
    tree(tmp_path, boards=("a.kicad_pcb", "b.kicad_pcb"))
    with pytest.raises(HarnessError):
        with delivery.open_delivery(tmp_path):
            pass


def test_not_a_directory_is_a_harness_error(tmp_path):
    with pytest.raises(HarnessError):
        with delivery.open_delivery(tmp_path / "nope"):
            pass


def test_zip_member_escaping_is_refused(tmp_path, monkeypatch):
    # Unpack under tmp_path/scratch, so an escape would land in tmp_path
    # itself, where the test can see it.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(delivery.tempfile, "tempdir", str(scratch))
    run = tmp_path / "run"
    run.mkdir()
    with zipfile.ZipFile(run / "deliverable.zip", "w") as z:
        z.writestr("kicad/board.kicad_pcb", "(kicad_pcb)")
        z.writestr("../../escaped.txt", "x")
    with pytest.raises(HarnessError, match="escapes"):
        with delivery.open_delivery(run):
            pass
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run", "scratch"]
    assert list(scratch.iterdir()) == []  # the temp dir was cleaned up too


def test_zip_without_a_board_is_a_harness_error(tmp_path):
    # Etchwork writes deliverable.zip only after packaging succeeded, so a zip
    # with no board in it is a broken harness, not a non-delivery.
    with zipfile.ZipFile(tmp_path / "deliverable.zip", "w") as z:
        z.writestr("source/board.net", "(export)")
    with pytest.raises(HarnessError, match="no kicad"):
        with delivery.open_delivery(tmp_path):
            pass


@pytest.mark.parametrize("exc", [OSError("disk"), NotImplementedError("compression type 99"),
                                 zipfile.LargeZipFile("zip64"), RuntimeError("encrypted")])
def test_other_archive_errors_are_harness_errors(tmp_path, monkeypatch, exc):
    with zipfile.ZipFile(tmp_path / "deliverable.zip", "w") as z:
        z.writestr("kicad/board.kicad_pcb", "(kicad_pcb)")

    def extractall(self, path=None, members=None, pwd=None):
        raise exc
    monkeypatch.setattr(zipfile.ZipFile, "extractall", extractall)
    with pytest.raises(HarnessError):
        with delivery.open_delivery(tmp_path):
            pass


def test_corrupt_zip_is_a_harness_error(tmp_path):
    (tmp_path / "deliverable.zip").write_text("not a zip")
    with pytest.raises(HarnessError):
        with delivery.open_delivery(tmp_path):
            pass


# --- load: docker is faked ---------------------------------------------------

def fake_run(stdout="", code=0, stderr="", raises=None):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)
    run.calls = calls
    return run


def test_load_runs_the_dump_sealed(tmp_path):
    tree(tmp_path)
    run = fake_run(json.dumps(DUMP))
    with delivery.open_delivery(tmp_path) as found:
        b = delivery.load(found, "etchwork-engine:abc", run=run)
    assert b.outline().width == 36
    cmd = run.calls[0]
    assert cmd[:2] == ["docker", "run"]
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--pull" in cmd and cmd[cmd.index("--pull") + 1] == "never"
    assert cmd[-2:] == ["/in/kicad/board.kicad_pcb", "/in/source/board.net"]


def test_load_without_netlist_passes_only_the_board(tmp_path):
    tree(tmp_path, netlists=())
    run = fake_run(json.dumps(DUMP))
    with delivery.open_delivery(tmp_path) as found:
        delivery.load(found, "img", run=run)
    assert run.calls[0][-1] == "/in/kicad/board.kicad_pcb"


@pytest.mark.parametrize("run", [
    fake_run(code=1, stderr="pcbnew could not read board.kicad_pcb"),
    fake_run(stdout="not json"),
    fake_run(stdout=json.dumps({"outline": []})),
    fake_run(raises=FileNotFoundError("docker")),
    fake_run(raises=subprocess.TimeoutExpired("docker", 600)),
])
def test_load_failures_are_harness_errors(tmp_path, run):
    tree(tmp_path)
    with delivery.open_delivery(tmp_path) as found:
        with pytest.raises(HarnessError):
            delivery.load(found, "img", run=run)
