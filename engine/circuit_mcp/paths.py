"""Where things live inside the container, and the one rule for paths.

Every path a tool receives comes from a model, so it is untrusted: it is
resolved (following symlinks) and must sit under its root, which stops
`../..`, absolute paths and symlinks pointing out alike.
"""
import os
import pathlib

WORKSPACE = pathlib.Path(os.environ.get("CIRCUIT_WORKSPACE", "/workspace")).resolve()
SYMBOL_ROOT = pathlib.Path(os.environ.get("CIRCUIT_SYMBOLS", "/usr/share/kicad/symbols"))
FP_ROOT = pathlib.Path(os.environ.get("CIRCUIT_FOOTPRINTS", "/usr/share/kicad/footprints"))


def inside(candidate: str, root: pathlib.Path | None = None) -> pathlib.Path:
    base = (root or WORKSPACE).resolve()
    if pathlib.PurePath(candidate).is_absolute():
        raise ValueError(f"{candidate!r} is absolute; give a path relative to the project")
    target = (base / candidate).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"{candidate!r} is outside the project folder")
    return target


def rel(path: pathlib.Path) -> str:
    return str(pathlib.Path(path).resolve().relative_to(WORKSPACE))
