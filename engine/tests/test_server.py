import asyncio
import os
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
