"""circuit_mcp.package: the deterministic gate between a finished run and the
customer's download (board-service spec §4.3). The workspace is untrusted:
the agent controls every path in it."""
import io
import json
import os
import shutil
import time
import zipfile

import pytest

from circuit_mcp import package, paths, schematic
from conftest import FIXTURES

PROJECT = FIXTURES / "projects" / "backoff1"
KEY = "sk-or-v1-0123456789abcdef"


@pytest.fixture
def out(tmp_path_factory):
    return tmp_path_factory.mktemp("out")


@pytest.fixture(scope="session")
def backoff1_schematic(tmp_path_factory):
    """backoff1 drawn once: generation runs its board.py and takes seconds."""
    work = tmp_path_factory.mktemp("backoff1-schematic")
    shutil.copytree(PROJECT, work, dirs_exist_ok=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(paths, "WORKSPACE", work.resolve())
        answer = schematic.export_schematic("board.py", "board.net")
    assert answer.startswith("EXPORTED"), answer
    return {name: (work / name).read_bytes() for name in schematic.files("board")}


SCHEMATIC_FILES = {}


@pytest.fixture(autouse=True)
def _schematic_files(backoff1_schematic):
    SCHEMATIC_FILES.update(backoff1_schematic)


def _finished(workspace):
    """The saved finished project, with mtimes in the order a real run leaves
    them: git checkouts don't keep mtimes, and check_done compares them."""
    shutil.copytree(PROJECT, workspace, dirs_exist_ok=True)
    for name, data in SCHEMATIC_FILES.items():
        (workspace / name).write_bytes(data)
    t = time.time() - 100
    os.utime(workspace / "board.kicad_pcb", (t, t))
    os.utime(workspace / "board.net", (t, t))
    for f in [workspace / "board.drc.json", workspace / "README.md",
              *(workspace / "gerbers").iterdir(),
              *(workspace / name for name in SCHEMATIC_FILES)]:
        os.utime(f, (t + 50, t + 50))


def _names(out):
    with zipfile.ZipFile(out / "deliverable.zip") as z:
        return set(z.namelist())


def test_a_finished_project_is_packaged(workspace, out):
    _finished(workspace)
    before = sorted(p.name for p in workspace.iterdir())
    result = package.package(workspace, out, [])
    assert result["done"], result["reason"]
    assert (result["drc_errors"], result["unconnected"]) == (0, 0)
    assert {"fab/gerbers.zip", "kicad/board.kicad_pcb", "kicad/board.kicad_pro",
            "kicad/board.kicad_dru", "source/board.py", "source/board.net",
            "README.md", "drc.json", "board.svg",
            "kicad/board.kicad_sch", "schematic.pdf", "schematic-erc.json"} <= _names(out)
    with zipfile.ZipFile(out / "deliverable.zip") as z:
        inner = zipfile.ZipFile(io.BytesIO(z.read("fab/gerbers.zip"))).namelist()
    assert any(n.endswith(".gtl") for n in inner) and any(n.endswith(".drl") for n in inner)
    assert json.loads((out / "result.json").read_text())["done"] is True
    assert sorted(p.name for p in workspace.iterdir()) == before  # nothing written into the workspace


def test_an_unfinished_project_is_not_packaged(workspace, out):
    _finished(workspace)
    shutil.rmtree(workspace / "gerbers")
    result = package.package(workspace, out, [])
    assert not result["done"]
    assert "gerbers" in result["reason"]
    assert not (out / "deliverable.zip").exists()
    assert json.loads((out / "result.json").read_text())["done"] is False


def test_an_empty_workspace_says_what_is_missing(workspace, out):
    result = package.package(workspace, out, [])
    assert not result["done"]
    assert "exactly one .kicad_pcb" in result["reason"]
    assert (out / "result.json").is_file()


def test_the_boards_own_netlist_wins_over_a_stray_one(workspace, out):
    _finished(workspace)
    shutil.copy(workspace / "board.net", workspace / "probe.net")
    result = package.package(workspace, out, [])
    assert result["done"], result["reason"]
    assert result["netlist"] == "board.net"


def test_a_symlinked_required_file_is_refused(workspace, out):
    _finished(workspace)
    (workspace / "README.md").unlink()
    (workspace / "README.md").symlink_to("/etc/hostname")
    result = package.package(workspace, out, [])
    assert not result["done"]
    assert "symlink" in result["reason"] or "README" in result["reason"]
    assert not (out / "deliverable.zip").exists()


def test_unsafe_source_names_and_symlinked_sources_are_left_out(workspace, out):
    _finished(workspace)
    (workspace / "evil\\..\\x.py").write_text("print(1)\n")
    (workspace / "link.py").symlink_to("/etc/passwd")
    result = package.package(workspace, out, [])
    assert result["done"], result["reason"]
    names = _names(out)
    assert "source/board.py" in names
    assert not any("evil" in n or "link.py" in n for n in names)


def test_secrets_are_redacted_in_everything_written(workspace, out):
    _finished(workspace)
    readme = workspace / "README.md"
    readme.write_text(readme.read_text() + f"\nDebug: key={KEY}\n")
    stamp = (workspace / "board.drc.json").stat().st_mtime
    os.utime(readme, (stamp, stamp))
    # The Gerbers are regenerated from the board, so the key has to be in the
    # board to reach them: KiCad writes the title block's rev into every
    # Gerber's %TF.ProjectId attribute.
    pcb = workspace / "board.kicad_pcb"
    board_stamp = pcb.stat().st_mtime
    pcb.write_text(pcb.read_text().replace('\t(paper "A4")\n',
                                           f'\t(paper "A4")\n\t(title_block (rev "{KEY}"))\n', 1))
    os.utime(pcb, (board_stamp, board_stamp))
    result = package.package(workspace, out, [KEY])
    assert result["done"], result["reason"]
    assert result["redactions"] >= 2
    with zipfile.ZipFile(out / "deliverable.zip") as z:
        text = z.read("README.md").decode()
        inner = zipfile.ZipFile(io.BytesIO(z.read("fab/gerbers.zip")))
        gtl = inner.read("board-F_Cu.gtl").decode()
    assert KEY not in text and "[redacted]" in text
    assert KEY not in gtl and "[redacted]" in gtl
    assert KEY not in (out / "result.json").read_text()


def test_secrets_come_from_the_named_env_vars_and_skip_short_values():
    env = {"OPENROUTER_API_KEY": KEY, "SHORT": "abc", "CIRCUIT_REDACT": "OPENROUTER_API_KEY, SHORT,MISSING"}
    assert package.secrets_from_env(env) == [KEY]
    assert package.secrets_from_env({"OPENROUTER_API_KEY": KEY}) == [KEY]


def test_redact_counts_every_occurrence():
    data, n = package.redact(f"a {KEY} b {KEY}".encode(), [KEY])
    assert n == 2 and data == b"a [redacted] b [redacted]"


# --- Fix round 1 -------------------------------------------------------


def test_invalid_drc_json_still_writes_result(workspace, out):
    _finished(workspace)
    (workspace / "board.drc.json").write_text("not json")
    result = package.package(workspace, out, [])
    assert result["done"] is False
    assert (out / "result.json").is_file()
    assert not (out / "deliverable.zip").exists()


def test_drc_json_as_a_list_still_writes_result(workspace, out):
    _finished(workspace)
    (workspace / "board.drc.json").write_text("[]")
    result = package.package(workspace, out, [])
    assert result["done"] is False
    assert (out / "result.json").is_file()
    assert not (out / "deliverable.zip").exists()


def test_an_unreadable_file_still_writes_result(workspace, out):
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    _finished(workspace)
    (workspace / "README.md").chmod(0)
    result = package.package(workspace, out, [])
    assert result["done"] is False
    assert (out / "result.json").is_file()
    assert not (out / "deliverable.zip").exists()


def test_a_file_over_the_size_cap_refuses_packaging(workspace, out, monkeypatch):
    _finished(workspace)
    monkeypatch.setattr(package, "MAX_FILE_BYTES", 1000)
    result = package.package(workspace, out, [])
    assert result["done"] is False
    assert "size" in result["reason"].lower()
    assert not (out / "deliverable.zip").exists()
    assert json.loads((out / "result.json").read_text())["done"] is False


def test_a_symlinked_gerber_file_refuses_packaging(workspace, out):
    _finished(workspace)
    (workspace / "gerbers" / "extra.gbr").symlink_to("/etc/hostname")
    result = package.package(workspace, out, [])
    assert result["done"] is False
    assert "extra.gbr" in result["reason"]
    assert not (out / "deliverable.zip").exists()


# --- Final fix wave ----------------------------------------------------

FORGED_FROM = FIXTURES / "projects" / "release2"


def test_a_forged_drc_report_is_not_trusted(workspace, out):
    """release2 has real DRC errors; the agent writes a clean report, the
    Gerbers and a README beside it anyway. check_done trusts the files; the
    packaging step re-runs DRC itself and must not."""
    shutil.copytree(FORGED_FROM, workspace, dirs_exist_ok=True)
    for name in ["board.drc.json", "README.md", "board.net",
                 "board.netclasses.json", "board.netclasses.applied.json"]:
        shutil.copy(PROJECT / name, workspace / name)
    shutil.copytree(PROJECT / "gerbers", workspace / "gerbers")
    for name, data in SCHEMATIC_FILES.items():
        (workspace / name).write_bytes(data)
    t = time.time() - 100
    os.utime(workspace / "board.kicad_pcb", (t, t))
    os.utime(workspace / "board.net", (t, t))
    for f in [workspace / "board.drc.json", workspace / "README.md",
              *(workspace / "gerbers").iterdir(),
              *(workspace / name for name in SCHEMATIC_FILES)]:
        os.utime(f, (t + 50, t + 50))
    from circuit_mcp import done
    assert done.check_done("board.net", "board.kicad_pcb").startswith("DONE")  # the forgery fools the files
    result = package.package(workspace, out, [])
    assert result["done"] is False, result["reason"]
    assert result["drc_errors"] > 0
    assert not (out / "deliverable.zip").exists()
    assert json.loads((out / "result.json").read_text())["done"] is False


def test_shipped_gerbers_are_regenerated_not_copied(workspace, out):
    _finished(workspace)
    gtl = workspace / "gerbers" / "board-F_Cu.gtl"
    stamp = gtl.stat().st_mtime
    gtl.write_text(gtl.read_text() + "G04 FORGED*\n")
    os.utime(gtl, (stamp, stamp))
    result = package.package(workspace, out, [])
    assert result["done"], result["reason"]
    with zipfile.ZipFile(out / "deliverable.zip") as z:
        inner = zipfile.ZipFile(io.BytesIO(z.read("fab/gerbers.zip")))
        assert "FORGED" not in inner.read("board-F_Cu.gtl").decode()
    assert sorted(p.name for p in (workspace / "gerbers").iterdir()) == sorted(
        p.name for p in (PROJECT / "gerbers").iterdir())  # nothing written into the workspace


def test_the_shipped_drc_report_is_the_fresh_one(workspace, out):
    _finished(workspace)
    report = workspace / "board.drc.json"
    stamp = report.stat().st_mtime
    forged = json.loads(report.read_text())
    forged["forged_marker"] = True
    report.write_text(json.dumps(forged))
    os.utime(report, (stamp, stamp))
    result = package.package(workspace, out, [])
    assert result["done"], result["reason"]
    with zipfile.ZipFile(out / "deliverable.zip") as z:
        assert "forged_marker" not in json.loads(z.read("drc.json"))


def test_a_failing_drc_rerun_refuses_packaging(workspace, out, monkeypatch):
    _finished(workspace)
    real = package.subprocess.run

    def fake(cmd, *a, **kw):
        if cmd[:3] == ["kicad-cli", "pcb", "drc"]:
            return package.subprocess.CompletedProcess(cmd, 3, "", "boom")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(package.subprocess, "run", fake)
    result = package.package(workspace, out, [])
    assert result["done"] is False
    assert "drc" in result["reason"].lower()
    assert not (out / "deliverable.zip").exists()


def test_unsafe_board_and_netlist_names_are_refused(workspace, out):
    _finished(workspace)
    evil = "..\\..\\evil"
    for suffix in (".kicad_pcb", ".net", ".drc.json"):
        (workspace / f"board{suffix}").rename(workspace / f"{evil}{suffix}")
    result = package.package(workspace, out, [])
    assert result["done"] is False
    assert "unsafe file name" in result["reason"]
    assert not (out / "deliverable.zip").exists()


def test_a_long_reason_is_truncated(workspace, out, monkeypatch):
    _finished(workspace)
    from circuit_mcp import done
    monkeypatch.setattr(done, "check_done", lambda *a: "NOT DONE:\n- " + "x" * 10000)
    result = package.package(workspace, out, [])
    assert result["done"] is False
    assert len(result["reason"]) <= 4000 + len("…[truncated]")
    assert result["reason"].endswith("…[truncated]")
    assert json.loads((out / "result.json").read_text())["reason"].endswith("…[truncated]")


# --- Task 4: packaging ships the schematic ------------------------------


def test_the_shipped_schematic_pdf_is_regenerated(workspace, out):
    _finished(workspace)
    (workspace / "board-schematic.pdf").write_bytes(b"%PDF-1.4 forged")
    os.utime(workspace / "board-schematic.pdf", (time.time(), time.time()))
    result = package.package(workspace, out, [])
    assert result["done"], result["reason"]
    with zipfile.ZipFile(out / "deliverable.zip") as z:
        assert z.read("schematic.pdf") != b"%PDF-1.4 forged"
        assert z.read("schematic.pdf").startswith(b"%PDF")
        assert z.read("kicad/board.kicad_sch") == SCHEMATIC_FILES["board.kicad_sch"]


def test_a_mismatched_schematic_refuses_packaging(workspace, out, monkeypatch):
    _finished(workspace)
    calls = []

    def after_the_gate(sch, netlist):
        # check_done (the first gate) sees a match; the packaging re-check does not.
        calls.append(sch)
        return [] if len(calls) == 1 else ["parts missing from the schematic: R9"]
    monkeypatch.setattr(schematic, "problems", after_the_gate)
    result = package.package(workspace, out, [])
    assert not result["done"]
    assert "schematic" in result["reason"] and "R9" in result["reason"]
    assert not (out / "deliverable.zip").exists()


def test_an_unprocessable_schematic_refuses_packaging(workspace, out, monkeypatch):
    _finished(workspace)

    def broken(sch, pdf):
        raise RuntimeError("schematic PDF export failed: boom")
    monkeypatch.setattr(schematic, "export_pdf", broken)
    result = package.package(workspace, out, [])
    assert not result["done"] and "boom" in result["reason"]
