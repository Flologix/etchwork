"""A task, and grading one delivered board against it (design spec §7).

Three things are kept apart and never collapsed: a clause the board fails,
a board that was never delivered (a strict failure, spec §7.1), and a
harness error (the grader's fault, reported separately so the benchmark
never counts its own crashes against the agent).
"""
from __future__ import annotations

import inspect
import json
import tomllib
import typing
from dataclasses import dataclass
from pathlib import Path

from contract.board import Board
from contract.checkers import REGISTRY, Result
from contract.errors import HarnessError
from contract.match import NetSpec, PartSpec

PASS, FAIL, NOT_DELIVERED, ERROR = "pass", "fail", "not_delivered", "error"

_CLAUSE_KEYS = {"id", "statement", "checker", "args"}
_REQUIRED = {"id", "statement", "checker"}


@dataclass
class Clause:
    id: str
    statement: str
    checker: str
    args: dict
    # Why this clause cannot be graded at all: a checker that does not exist,
    # an alias that does not resolve. A harness error, never a failure (§5).
    error: str | None = None


@dataclass
class Task:
    name: str
    clauses: list[Clause]


def _wants(hint, cls) -> bool:
    return hint is cls or cls in typing.get_args(hint)


def _bind(checker: str, args: dict, parts: dict, nets: dict) -> dict:
    """Checker arguments with alias strings replaced by their specs. Which
    arguments are aliases is read from the checker's own annotations."""
    fn = REGISTRY.get(checker)
    if fn is None:
        raise ValueError(f"no checker named {checker!r} (have: {', '.join(sorted(REGISTRY))})")
    hints = typing.get_type_hints(fn)
    bound = {}
    for key, value in args.items():
        hint = hints.get(key)
        for cls, table, kind in ((NetSpec, nets, "nets"), (PartSpec, parts, "parts")):
            if _wants(hint, cls):
                names = value if isinstance(value, list) else [value]
                missing = [str(v) for v in names if not isinstance(v, str) or v not in table]
                if missing:
                    raise ValueError(f"{key} names no [{kind}] alias: {', '.join(missing)}")
                value = tuple(table[v] for v in names) if isinstance(value, list) else table[value]
                break
        bound[key] = value
    try:
        inspect.signature(fn).bind(None, **bound)
    except TypeError as e:
        raise ValueError(f"{checker}: {e}") from e
    return bound


def load_task(path: Path) -> Task:
    path = Path(path)
    file = path / "contract.toml"
    try:
        data = tomllib.loads(file.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise HarnessError(f"cannot read {file}: {e}") from e
    try:
        parts = {n: PartSpec.from_toml(n, t) for n, t in data.get("parts", {}).items()}
        nets = {n: NetSpec.from_toml(n, t, parts) for n, t in data.get("nets", {}).items()}
    except ValueError as e:
        raise HarnessError(f"{file}: {e}") from e
    clash = sorted(set(parts) & set(nets))
    if clash:
        raise HarnessError(f"{file}: {clash} are both [parts] and [nets] aliases")
    clauses, seen = [], set()
    for raw in data.get("clause", []):
        missing, unknown = _REQUIRED - set(raw), set(raw) - _CLAUSE_KEYS
        if missing or unknown:
            raise HarnessError(f"{file}: clause {raw.get('id', '?')}: "
                               f"missing {sorted(missing)}, unknown {sorted(unknown)}")
        if raw["id"] in seen:
            raise HarnessError(f"{file}: duplicate clause id {raw['id']!r}")
        seen.add(raw["id"])
        clause = Clause(raw["id"], raw["statement"], raw["checker"], {})
        try:
            clause.args = _bind(raw["checker"], raw.get("args", {}), parts, nets)
        except ValueError as e:
            clause.error = f"clause {raw['id']}: {e}"
        clauses.append(clause)
    if not clauses:
        raise HarnessError(f"{file} has no [[clause]] entries")
    return Task(path.name, clauses)


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _strict(rows: list[dict]) -> bool | None:
    """False if any clause failed or nothing was delivered; None (no verdict)
    if nothing failed but some clause could not be graded, so a harness
    fault is never counted as the agent's failure; True if all passed."""
    statuses = {r["status"] for r in rows}
    if statuses & {FAIL, NOT_DELIVERED}:
        return False
    if ERROR in statuses:
        return None
    return True


def grade(task: Task, board: Board | None, run_error: str | None = None) -> dict:
    """result.json for one run. `board` None means nothing was delivered;
    `run_error` means something was, and the harness could not read it."""
    rows, errors = [], []
    for clause in task.clauses:
        row = {"id": clause.id, "statement": clause.statement, "checker": clause.checker,
               "status": ERROR, "detail": "", "measured": None}
        if run_error is not None:
            row["detail"] = f"not graded: {run_error}"
        elif board is None:
            row["status"], row["detail"] = NOT_DELIVERED, "no board was delivered"
        elif clause.error is not None:
            row["detail"] = clause.error
            errors.append({"clause": clause.id, "error": clause.error})
        else:
            try:
                result = REGISTRY[clause.checker](board, **clause.args)
                if not isinstance(result, Result):
                    raise TypeError(f"returned {type(result).__name__}, not Result")
            except Exception as e:  # any checker bug is the harness's, not the board's
                message = f"{clause.checker} raised {type(e).__name__}: {e}"
                row["detail"] = message
                errors.append({"clause": clause.id, "error": message})
            else:
                row.update(status=PASS if result.passed else FAIL, detail=result.detail,
                           measured=_jsonable(result.measured))
        rows.append(row)
    if run_error is not None:
        errors.insert(0, {"clause": None, "error": run_error})
    return {
        "task": task.name,
        "delivered": board is not None or run_error is not None,
        "strict_pass": _strict(rows),
        "graded": not errors,
        "clauses": rows,
        "harness_errors": errors,
    }
