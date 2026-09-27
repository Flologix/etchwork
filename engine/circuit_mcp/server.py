"""The /etchwork tools over MCP stdio. Each tool is a thin wrapper that
returns text; the deterministic work lives in parts, tools, summary."""
from mcp.server.fastmcp import FastMCP

from circuit_mcp import done, parts, summary, tools

mcp = FastMCP("etchwork")


@mcp.tool()
def find_part(query: str) -> str:
    """Search KiCad's symbol and footprint libraries by what a part IS (e.g. 'tactile switch', '3.3V LDO SOT-23')."""
    return parts.find_part(query)


@mcp.tool()
def describe_part(symbol: str) -> str:
    """Every pin of a symbol ('Library:Name'): number, name, electrical type."""
    return parts.describe_part(symbol)


@mcp.tool()
def describe_footprint(footprint: str) -> str:
    """Pads, pad sizes and courtyard size of a footprint ('Library:Name')."""
    return parts.describe_footprint(footprint)


@mcp.tool()
def run_skidl(script: str) -> str:
    """Run a SKiDL design script in the project; returns the netlist it wrote and the ERC result, or the error."""
    return tools.run_skidl(script)


@mcp.tool()
def check_netlist(netlist: str, expected: str) -> str:
    """Compare a netlist with the intended wiring in a TOML file (e.g. expected.netlist.toml)."""
    return parts.check_netlist(netlist, expected)


@mcp.tool()
def netlist_nets(netlist: str, names: bool = True) -> str:
    """Every net in a netlist and the pins on it."""
    return parts.netlist_nets(netlist, names)


@mcp.tool()
def run_layout(netlist: str, width_mm: float, height_mm: float, arrange: list | None = None,
               positions: dict | None = None, route: bool = True, rules: dict | None = None,
               overwrite: bool = False) -> str:
    """Netlist -> board -> outline -> place -> route -> DRC in one call. Applies <netlist>.netclasses.json
    when present. Prefer `arrange` ops (rotate/align/space_evenly) over raw `positions`. Refuses to replace
    a board edited outside the tools unless overwrite=true (only after the human agrees)."""
    return tools.run_layout(netlist, width_mm, height_mm, arrange=arrange, positions=positions,
                            route=route, rules=rules, overwrite=overwrite)


@mcp.tool()
def run_drc(board: str) -> str:
    """Run DRC on a board and summarise the violations."""
    return tools.run_drc(board)


@mcp.tool()
def describe_board(board: str) -> str:
    """What a board contains: size, copper, tracks, vias, zones. DRC can pass on a board with no copper."""
    return parts.describe_board(board)


@mcp.tool()
def fill_zones(board: str, net: str = "GND", layers: list | None = None, inset_mm: float = 0.5) -> str:
    """Pour a copper zone for a net (default GND on both layers) and fill it,
    and makes solid any pad whose thermal relief DRC finds starved."""
    return tools.fill_zones(board, net=net, layers=layers, inset_mm=inset_mm)


@mcp.tool()
def export_gerbers(board: str, out: str | None = None) -> str:
    """Export Gerber and drill files (default folder: gerbers/)."""
    return tools.export_gerbers(board, out)


@mcp.tool()
def summarize_project(netlist: str, board: str | None = None) -> str:
    """Write the computable README.md sections: parts, net classes, nets awaiting review, board facts."""
    return summary.summarize_project(netlist, board)


@mcp.tool()
def check_done(netlist: str, board: str | None = None) -> str:
    """Whether the project is finished: DRC current and clean, pours filled, gerbers and README newer
    than the board. DONE lists the nets awaiting the human's decision; NOT DONE lists every gap and
    the tool that closes it. Reads only; safe to call any time."""
    return done.check_done(netlist, board)


@mcp.tool()
def net_evidence(netlist: str, offset: int = 0, limit: int = 40) -> str:
    """The nets rules already decided, then a page of the others with their evidence and the closed
    menu. Pick a class and tier per open net, then call set_net_classes. Read-only."""
    return tools.net_evidence(netlist, offset=offset, limit=limit)


@mcp.tool()
def set_net_classes(netlist: str, picks: list[dict]) -> str:
    """Write <netlist stem>.netclasses.json from a list of {net, class, tier, reason, review?} picks,
    one per net net_evidence listed as open. Refuses (writing nothing) if a net is missing, unknown,
    picked twice, off the menu, missing a reason, a signal net picked at 0_5a_to_2a or higher (signal
    nets carry under 0.5 A), or a power-pin net picked as non-power without review: true; the refusal
    names every problem."""
    return tools.set_net_classes(netlist, picks)


if __name__ == "__main__":
    mcp.run()
