"""The local event log: one JSON line per tool call, in the project.

Flywheel design §5 (docs/superpowers/specs/2026-09-28-flywheel-design.md).
Every board run shows what the tools get right and wrong -- the refusals,
the DRC failures a layout converged through, the calls a DONE board took --
and until now all of it went away with the session. This keeps it, in
`/workspace/.etchwork/events.jsonl`, and nowhere else: the engine sends
nothing anywhere, and nothing here changes that.

**Derived facts, not designs.** Arguments are summarised (paths and scalars
as they are, `arrange` as counts per op, net-class picks as counts, never
their reasons) and the answer is kept only as its outcome, its DRC counts
and its length -- plus, when it is not a success, the tool's own first line
(at most HEADLINE characters), which says what was refused or failed.

**Never at the cost of the answer.** A log that cannot be written -- a
`.etchwork` that is a file, or a symlink out of the project -- is reported
once on stderr and otherwise ignored. `CIRCUIT_EVENTS=0` turns it off.
"""
import collections
import datetime
import functools
import inspect
import json
import os
import re
import sys
import time
import uuid

from circuit_mcp import paths

VERSION = 1
FOLDER = ".etchwork"
NAME = "events.jsonl"
ROTATED = "events.1.jsonl"
MAX_BYTES = 5 * 1024 * 1024
# One id per server process, which is one agent session over stdio.
SESSION = uuid.uuid4().hex[:12]

_seq = 0
_warned = False

# The verdicts a tool states up front. Checked in this order: "NOT DONE"
# before "DONE".
_OUTCOMES = (("NOT DONE", "not_done"), ("DONE", "done"), ("PASS", "pass"),
             ("FAIL", "fail"), ("REFUSED", "refused"),
             ("SCHEMATIC NOT AVAILABLE", "unavailable"))
# Most refusals and failures are not prefixed, so their wording is matched on
# the answer's first line. `not_found`: a lookup found nothing. `refused`: the
# tool declined the request and did nothing. `failed`: it tried, and a step of
# it failed. A test run on
# 2026-09-28 logged three layout refusals and failures as "ok" before these.
# A live run (2026-09-28) logged seven "no footprint ... here" answers as "ok".
_NOT_FOUND = re.compile("|".join([
    r"^no footprint ", r"^no symbol ", r"^no \S+ among KiCad", r"^nothing matching ",
]))
_REFUSED = re.compile("|".join([
    r"^nothing to place", r"^these footprints cannot be resolved", r"^unknown design rule",
    r"must be a length in millimetres", r"^arrange (must be|op \d+)", r"^each op must be",
    r"is outside the project folder", r"is absolute; give a path", r"is a path, not a name",
    r"^no such ", r"changed since the tools last wrote it", r"^give something to search for",
]))
_FAILED = re.compile("|".join([
    r"^layout failed at", r"^DRC failed", r"^DRC could not run", r"^kicad-cli (failed|wrote)",
    r"^the pour failed", r"^the fabrication export failed", r"^the DRC report could not be read",
    r"^the schematic (could not|was drawn, but)", r" failed \(exit -?\d+\):?$",
    r"timed out after", r"ran but wrote no netlist",
]))
# The first line kept for an answer that is not a success.
HEADLINE = 160
_SUCCESS = {"ok", "pass", "done"}
# tools._verdict's headline: "NOT DONE: 3 DRC error(s) (clearance), 1 unconnected".
_DRC = re.compile(r"(\d+) DRC error\(s\)(?: \(([^)]*)\))?, (\d+) unconnected")


def outcome(answer: str) -> str:
    """What the answer says happened, read from how it starts."""
    head = answer.lstrip()
    stated = next((name for prefix, name in _OUTCOMES if head.startswith(prefix)), None)
    if stated:
        return stated
    first = head.split("\n", 1)[0]
    if _NOT_FOUND.search(first):
        return "not_found"
    if _REFUSED.search(first):
        return "refused"
    if _FAILED.search(first):
        return "failed"
    return "ok"


def metrics(answer: str) -> dict:
    """The DRC counts from the headline a DRC-bearing tool printed, if any."""
    first = answer.lstrip().split("\n", 1)[0]
    if "DRC clean, 0 unconnected" in first:
        return {"drc_errors": 0, "unconnected": 0, "violation_types": []}
    found = _DRC.search(first)
    if not found:
        return {}
    kinds = [k.strip() for k in (found.group(2) or "").split(",") if k.strip()]
    return {"drc_errors": int(found.group(1)), "unconnected": int(found.group(3)),
            "violation_types": kinds}


def summarise(tool: str, arguments: dict) -> dict:
    """The arguments as facts about the call, not as the design itself."""
    out = {}
    for name, value in arguments.items():
        if value is None:
            continue
        if name == "positions" and isinstance(value, dict):
            out[name] = len(value)
        elif name == "arrange" and isinstance(value, list):
            out[name] = dict(collections.Counter(
                str(op.get("op", "?")) if isinstance(op, dict) else "?" for op in value))
        elif name == "rules" and isinstance(value, dict):
            out[name] = sorted(str(k) for k in value)
        elif name == "picks" and isinstance(value, list):
            out[name] = len(value)
            out["review"] = sum(1 for p in value if isinstance(p, dict) and p.get("review") is True)
        elif isinstance(value, (bool, int, float)):
            out[name] = value
        elif isinstance(value, str):
            out[name] = value[:200]
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            out[name] = [v[:50] for v in value[:20]]
        else:
            out[name] = type(value).__name__
    return out


def _warn(error: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        print(f"etchwork: the event log could not be written ({error}); "
              "tools are unaffected", file=sys.stderr)


def _write(event: dict) -> None:
    folder = paths.inside(FOLDER)       # refuses a symlink out of the project
    folder.mkdir(exist_ok=True)
    target = folder / NAME
    line = (json.dumps(event, separators=(",", ":")) + "\n").encode()
    try:
        size = os.lstat(target).st_size
    except FileNotFoundError:
        size = 0
    if size and size + len(line) > MAX_BYTES:
        os.replace(target, folder / ROTATED)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def record(tool: str, arguments: dict, ms: int, answer: str | None = None,
           error: BaseException | None = None) -> None:
    """Append one event; never raise."""
    global _seq
    if os.environ.get("CIRCUIT_EVENTS", "1") == "0":
        return
    try:
        _seq += 1
        now = datetime.datetime.now(datetime.timezone.utc)
        event = {"v": VERSION,
                 "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
                 "session": SESSION, "seq": _seq, "tool": tool, "ms": ms,
                 "args": summarise(tool, arguments)}
        if error is not None:
            # The type only: an exception's message can carry paths and content.
            event.update(outcome="error", error=type(error).__name__)
        else:
            event.update(outcome=outcome(answer), chars=len(answer))
            if event["outcome"] not in _SUCCESS:
                # What was refused or failed: the tool's own first line.
                event["headline"] = answer.lstrip().split("\n", 1)[0][:HEADLINE]
            found = metrics(answer)
            if found:
                event["metrics"] = found
        _write(event)
    except Exception as exc:  # the log must never cost the tool its answer
        _warn(exc)


def logged(fn):
    """Record every call of a tool function. The signature is kept (through
    functools.wraps), which is what FastMCP builds the tool's schema from."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = dict(bound.arguments)
        except TypeError:
            arguments = dict(kwargs)
        start = time.monotonic()
        try:
            answer = fn(*args, **kwargs)
        except BaseException as exc:
            record(fn.__name__, arguments, int((time.monotonic() - start) * 1000), error=exc)
            raise
        record(fn.__name__, arguments, int((time.monotonic() - start) * 1000),
               answer=answer if isinstance(answer, str) else str(answer))
        return answer

    return wrapper
