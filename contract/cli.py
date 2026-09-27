"""`python3 -m contract score <task> <board-dir> --image <engine image>`.

`etchwork contract score` supplies --image. Exit 0 when the board was
graded, whatever the verdict; 3 on a harness error; 2 on bad usage.
Output never lands in the board dir: `etchwork job` already writes a
result.json there, and it is not ours to overwrite.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contract import delivery, score
from contract.errors import HarnessError

TASKS = Path(__file__).resolve().parent / "tasks"


def _task_dir(name: str) -> Path:
    path = Path(name)
    return path if path.is_dir() else TASKS / name


def _print(result: dict, out: str) -> None:
    for row in result["clauses"]:
        print(f"{row['status'].upper():<14} {row['id']:<18} {row['detail']}")
    verdict = {True: "PASS", False: "FAIL", None: "NOT GRADED"}[result["strict_pass"]]
    line = f"strict: {verdict}"
    if not result["graded"]:
        line += f"  ({len(result['harness_errors'])} harness error(s): this run did not grade cleanly)"
    print(line)
    print(f"wrote {out}")


def main(argv: list[str] | None = None, *, load=delivery.load) -> int:
    parser = argparse.ArgumentParser(prog="contract")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score", help="grade one delivered board against a task")
    s.add_argument("task", help="a task directory, or a name under contract/tasks/")
    s.add_argument("board_dir", help="a run's output: holds deliverable.zip, or kicad/ and source/")
    s.add_argument("--image", required=True, help="the etchwork-engine image tag")
    s.add_argument("--out", default="contract-result.json",
                   help="where to write the result (default: %(default)s in the cwd)")
    args = parser.parse_args(argv)

    try:
        task = score.load_task(_task_dir(args.task))
    except HarnessError as e:
        print(f"contract: {e}", file=sys.stderr)
        return 3
    try:
        with delivery.open_delivery(args.board_dir) as found:
            board = None if found is None else load(found, args.image)
        result = score.grade(task, board)
    except HarnessError as e:
        result = score.grade(task, None, run_error=str(e))
    except Exception as e:  # anything else while opening or loading is ours too
        result = score.grade(task, None, run_error=f"{type(e).__name__}: {e}")
    try:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    except OSError as e:
        print(f"contract: cannot write {args.out}: {e}", file=sys.stderr)
        return 3
    _print(result, args.out)
    return 0 if result["graded"] else 3
