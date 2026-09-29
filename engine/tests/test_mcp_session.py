"""The tools as a client sees them: one real stdio server, called over MCP.

test_server.py checks the server starts; this checks what crosses the
protocol. Every path a tool takes is probed with `../` and an absolute path
to a real file outside the project, so a tool that stopped calling
paths.inside would be caught here even where its own unit tests fake the
pipeline underneath.
"""
import asyncio
import os
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import FIXTURES
from test_server import EXPECTED

# Each tool's valid arguments, and which of them are paths. find_part takes
# a search query, not a path.
VALID = {
    "describe_part": {"symbol": "Device:R"},
    "describe_footprint": {"footprint": "Resistor_SMD:R_0603_1608Metric"},
    "run_skidl": {"script": "board.py"},
    "export_schematic": {"script": "board.py", "netlist": "board.net"},
    "check_netlist": {"netlist": "board.net", "expected": "expected.netlist.toml"},
    "netlist_nets": {"netlist": "board.net"},
    "run_layout": {"netlist": "board.net", "width_mm": 30, "height_mm": 20,
                   "positions": {"R1": [5, 5, 0]}},
    "run_drc": {"board": "board.kicad_pcb"},
    "describe_board": {"board": "board.kicad_pcb"},
    "fill_zones": {"board": "board.kicad_pcb"},
    "export_gerbers": {"board": "board.kicad_pcb", "out": "gerbers"},
    "summarize_project": {"netlist": "board.net", "board": "board.kicad_pcb"},
    "check_done": {"netlist": "board.net", "board": "board.kicad_pcb"},
    "net_evidence": {"netlist": "board.net"},
    "set_net_classes": {"netlist": "board.net", "picks": []},
}
PATH_ARGS = {
    "describe_part": ["symbol"], "describe_footprint": ["footprint"],
    "run_skidl": ["script"], "export_schematic": ["script", "netlist"],
    "check_netlist": ["netlist", "expected"], "netlist_nets": ["netlist"],
    "run_layout": ["netlist"], "run_drc": ["board"], "describe_board": ["board"],
    "fill_zones": ["board"], "export_gerbers": ["board", "out"],
    "summarize_project": ["netlist", "board"], "check_done": ["netlist", "board"],
    "net_evidence": ["netlist"], "set_net_classes": ["netlist"],
}
REFUSALS = ("outside the project folder", "is absolute", "is a path, not a name")
# What a bad path looks like for each kind of argument: library ids are
# `Library:Name`, the rest are files.
SUFFIX = {"symbol": ":R", "footprint": ":R", "script": ".py", "netlist": ".net",
          "expected": ".toml", "board": ".kicad_pcb", "out": ""}


def _project(tmp_path):
    """A project folder with a valid file for every argument, and beside it
    (outside) a real file for every path, so a missing check reads the file
    rather than failing on 'no such file'."""
    workspace, outside = tmp_path / "project", tmp_path
    workspace.mkdir()
    for folder in (workspace, outside):
        shutil.copy(FIXTURES / "blinker.net", folder / "board.net")
        shutil.copy(FIXTURES / "boards" / "test.kicad_pcb", folder / "board.kicad_pcb")
        (folder / "board.py").write_text("print('the script ran')\n")
        (folder / "expected.netlist.toml").write_text("[nets]\n")
    for suffix in {s for s in SUFFIX.values() if s.startswith(".")}:
        (outside / f"outside{suffix}").write_text("")
    return workspace, outside


def _session(workspace, calls):
    """Run `calls` (a list of (tool, arguments)) in one server session;
    return the results in order, then the tool list read after them."""
    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-P", "-m", "circuit_mcp.server"],
            env={**os.environ, "CIRCUIT_WORKSPACE": str(workspace)}, cwd=str(workspace))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                results = [await session.call_tool(name, arguments) for name, arguments in calls]
                # Still answering after every call: nothing a tool did wrote
                # to stdout, which is the protocol's channel.
                after = {t.name for t in (await session.list_tools()).tools}
                return results, after

    return asyncio.run(asyncio.wait_for(run(), 300))


def _snapshot(folder):
    return {p: p.stat().st_mtime_ns for p in folder.iterdir() if p.name != "project"}


def test_every_tool_with_a_path_is_probed():
    assert set(VALID) == set(PATH_ARGS) == EXPECTED - {"find_part"}


def test_every_path_argument_refuses_to_leave_the_project(tmp_path):
    workspace, outside = _project(tmp_path)
    calls = []
    for tool, names in PATH_ARGS.items():
        for name in names:
            for bad in (f"../outside{SUFFIX[name]}", f"{outside}/outside{SUFFIX[name]}"):
                if name in ("symbol", "footprint"):
                    bad = f"../lib{SUFFIX[name]}" if bad.startswith("..") else f"/etc{SUFFIX[name]}"
                calls.append((tool, {**VALID[tool], name: bad}))
    before = _snapshot(outside)

    results, after = _session(workspace, calls)

    wrong = [f"{tool}({args}) -> {result.content[0].text[:200]!r}"
             for (tool, args), result in zip(calls, results)
             if result.isError or not any(r in result.content[0].text for r in REFUSALS)]
    assert not wrong, "\n".join(wrong)
    assert _snapshot(outside) == before
    assert after == EXPECTED


def test_malformed_arguments_are_protocol_errors(tmp_path):
    """Refused by the schema FastMCP generated, before the tool runs."""
    workspace, _ = _project(tmp_path)
    calls = [
        ("run_layout", {"netlist": "board.net", "width_mm": "wide", "height_mm": 20}),
        ("run_layout", {"netlist": "board.net"}),
        ("net_evidence", {"netlist": "board.net", "offset": "first"}),
        ("set_net_classes", {"netlist": "board.net", "picks": "all of them"}),
        ("run_drc", {}),
    ]

    results, after = _session(workspace, calls)

    for (tool, args), result in zip(calls, results):
        assert result.isError, (tool, args, result.content[0].text[:200])
        assert "validation error" in result.content[0].text, (tool, args)
    assert after == EXPECTED


def test_every_call_through_the_server_is_one_event(tmp_path):
    """The local event log (flywheel design §5), end to end: the server
    records every tool it runs, in the project, and the answers are unchanged."""
    import json

    workspace, _ = _project(tmp_path)
    calls = [("netlist_nets", {"netlist": "board.net"}),
             ("describe_board", {"board": "board.kicad_pcb"}),
             ("run_drc", {"board": "../outside.kicad_pcb"})]

    results, _ = _session(workspace, calls)

    lines = (workspace / ".etchwork" / "events.jsonl").read_text().splitlines()
    logged = [json.loads(line) for line in lines]
    assert [e["tool"] for e in logged] == [name for name, _ in calls]
    assert [e["seq"] for e in logged] == [1, 2, 3]
    assert len({e["session"] for e in logged}) == 1
    assert [e["chars"] for e in logged] == [len(r.content[0].text) for r in results]
