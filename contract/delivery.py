"""Where a delivered board is, and reading it through the engine image."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from contract.board import Board, BoardFormatError
from contract.errors import HarnessError

CONTRACT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Delivery:
    root: Path             # holds kicad/ and source/
    pcb: Path
    netlist: Path | None   # absent is allowed: pin names then come from the board alone


def _unzip(archive: Path, into: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if not (into / info.filename).resolve().is_relative_to(into.resolve()):
                    raise HarnessError(f"{archive.name}: member {info.filename!r} escapes the archive")
            z.extractall(into)
    except zipfile.BadZipFile as e:
        raise HarnessError(f"{archive} is not a readable zip: {e}") from e


def _find(root: Path) -> Delivery | None:
    boards = sorted((root / "kicad").glob("*.kicad_pcb"))
    if not boards:
        return None
    if len(boards) > 1:
        raise HarnessError(f"{root / 'kicad'} holds {len(boards)} boards; expected one")
    netlists = sorted((root / "source").glob("*.net"))
    return Delivery(root, boards[0], netlists[0] if len(netlists) == 1 else None)


@contextlib.contextmanager
def open_delivery(board_dir) -> Iterator[Delivery | None]:
    """The board in a run's output: `deliverable.zip` (what `etchwork job`
    writes) unpacked to a temp dir, or an already-unpacked kicad/ + source/
    tree. Yields None when there is no board. That is a non-delivery: a
    strict failure, not a harness error (spec §7.1)."""
    board_dir = Path(board_dir).resolve()
    if not board_dir.is_dir():
        raise HarnessError(f"{board_dir} is not a directory")
    archive = board_dir / "deliverable.zip"
    if archive.is_file():
        with tempfile.TemporaryDirectory(prefix="contract-") as tmp:
            root = Path(tmp).resolve()
            _unzip(archive, root)
            yield _find(root)
    else:
        yield _find(board_dir)


def load(found: Delivery, image: str, *, run=subprocess.run, timeout: int = 600) -> Board:
    """One dump inside the engine image, as a Board. Anything that goes
    wrong here is the harness's fault: the board passed DRC to be delivered."""
    inside = lambda p: "/in/" + p.relative_to(found.root).as_posix()
    cmd = ["docker", "run", "--rm", "--network", "none", "--pull", "never",
           "--user", f"{os.getuid()}:{os.getgid()}",
           "--mount", f"type=bind,src={CONTRACT_DIR},dst=/opt/contract-src/contract,readonly",
           "--mount", f"type=bind,src={found.root},dst=/in,readonly",
           "-e", "PYTHONPATH=/opt/etchwork:/opt/contract-src",
           "--entrypoint", "python3", image, "-m", "contract.dump", inside(found.pcb)]
    if found.netlist is not None:
        cmd.append(inside(found.netlist))
    try:
        done = run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HarnessError(f"could not run the engine image {image}: {e}") from e
    if done.returncode != 0:
        raise HarnessError(f"board dump failed (exit {done.returncode}); is {image} built? "
                           f"{done.stderr.strip()[-2000:]}")
    try:
        return Board(json.loads(done.stdout))
    except (ValueError, BoardFormatError) as e:
        raise HarnessError(f"board dump was unreadable: {e}") from e
