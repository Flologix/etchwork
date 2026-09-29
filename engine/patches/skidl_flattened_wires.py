"""Patch SKiDL 2.3.0's KiCad 10 schematic emitter: a subcircuit's wires must
survive the top sheet's dangling-wire purge.

The bug: `node_to_sexp_schematic` draws each flattened subcircuit (every
@subcircuit, at flatness=1.0) by recursing into it, and the child purges its
own dangling wires against its own parts' pins. The parent then merges the
child's elements into its own list and runs the same purge again over all of
them -- but the parent's anchor list holds only the parent's own parts. A
child wire that ends on a child part's pin looks dangling there and is
dropped.

What it breaks: snap placement puts a subcircuit's decoupling caps next to
the IC's supply pin and connects them with wires, suppressing the caps'
power labels because the wire makes them redundant. With the wires purged,
the caps' supply pins are drawn connected to nothing. Measured 2026-09-28 on
the first live run (tests/fixtures/projects/decoupled): C3:1 and C4:1 cut off
+3V3 with every seed, so the board shipped without a schematic.

The fix: the parent skips wires that came from a child in its purge (the
child already checked them against the right pins), while still counting
their endpoints, so a parent wire ending on a child wire is kept as before.

Each edit must match exactly once, or the image build fails: a SKiDL release
that moves this code must be looked at again, not silently left unpatched.
"""
import importlib.util
import pathlib
import sys

EDITS = [
    (
        "    # Recurse into children.\n"
        "    for i, child in enumerate(node.children.values()):\n",
        "    # Elements a flattened child returned: it already purged its own\n"
        "    # dangling wires against its own pins (etchwork patch).\n"
        "    _child_element_ids = set()\n"
        "    # Recurse into children.\n"
        "    for i, child in enumerate(node.children.values()):\n",
    ),
    (
        "        elements.extend(sexp_list)\n",
        "        elements.extend(sexp_list)\n"
        "        _child_element_ids.update(id(_e) for _e in sexp_list)\n",
    ),
    (
        "        for _i, _pts in _wires:\n"
        "            if any(_p not in _anchor_pts and _counts.get(_p, 0) < 2 for _p in _pts):\n",
        "        for _i, _pts in _wires:\n"
        "            if id(elements[_i]) in _child_element_ids:\n"
        "                continue\n"
        "            if any(_p not in _anchor_pts and _counts.get(_p, 0) < 2 for _p in _pts):\n",
    ),
]


def main() -> int:
    package = pathlib.Path(importlib.util.find_spec("skidl").submodule_search_locations[0])
    target = package / "tools" / "kicad10" / "sexp_schematic.py"
    text = target.read_text()
    for old, new in EDITS:
        count = text.count(old)
        if count != 1:
            print(f"{target}: expected exactly one match, found {count}:\n{old}", file=sys.stderr)
            return 1
        text = text.replace(old, new)
    target.write_text(text)
    print(f"patched {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
