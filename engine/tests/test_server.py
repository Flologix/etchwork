import asyncio
import json
import os
import pathlib
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED = {
    "find_part", "describe_part", "describe_footprint", "run_skidl", "check_netlist",
    "netlist_nets", "run_layout", "run_drc", "describe_board", "fill_zones",
    "export_gerbers", "summarize_project", "check_done", "net_evidence", "set_net_classes",
    "export_schematic",
}


def test_server_lists_exactly_the_circuit_tools():
    async def listed():
        params = StdioServerParameters(command=sys.executable, args=["-m", "circuit_mcp.server"], env=os.environ)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return {t.name for t in (await session.list_tools()).tools}

    assert asyncio.run(listed()) == EXPECTED


def test_a_tool_call_round_trips():
    async def call():
        params = StdioServerParameters(command=sys.executable, args=["-m", "circuit_mcp.server"], env=os.environ)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("describe_part", {"symbol": "Device:R"})
                return result.content[0].text

    assert "1" in asyncio.run(call())


DOCKERFILE = pathlib.Path(__file__).parent.parent / "Dockerfile"


def test_the_entrypoint_ignores_modules_in_the_project(tmp_path):
    """The container starts in /workspace, and `python3 -m` puts the working
    directory first on sys.path: a project folder named `mcp` stopped the
    server from starting."""
    entry = json.loads(re.search(r"^ENTRYPOINT (\[.*\])$", DOCKERFILE.read_text(), re.M).group(1))
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "__init__.py").write_text("raise SystemExit('shadowed by the project')\n")

    async def listed():
        params = StdioServerParameters(command=entry[0], args=entry[1:], env=os.environ,
                                       cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return {t.name for t in (await session.list_tools()).tools}

    assert asyncio.run(asyncio.wait_for(listed(), 60)) == EXPECTED
