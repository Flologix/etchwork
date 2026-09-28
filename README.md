# Etchwork

A PCB design toolkit for AI coding agents. Write a sentence about what you need, and the engine produces a routed, DRC-clean, fab-ready KiCad board with Gerbers. It runs as an MCP server in Docker.

## Requirements

- **Linux** (macOS untested)
- **Docker** version 28 or later
- **git** and **python3**
- For Claude Code: the `claude` CLI

## Quick start (Claude Code)

**1. Install once.** Clone the repo, put `etchwork` on your `PATH`, and build the engine image.
The first build takes a few minutes.

```bash
git clone https://github.com/FlologixAI/etchwork.git ~/etchwork
mkdir -p ~/.local/bin && ln -s ~/etchwork/etchwork ~/.local/bin/etchwork
etchwork build
```

(If `etchwork` isn't found afterwards, open a new terminal: `~/.local/bin` must be on your `PATH`.)

**2. Set up a project folder.** Do this once for every new board.

```bash
mkdir ~/my-board && cd ~/my-board
etchwork init .
```

`init` picks your agent itself and prints which one it chose. If several are installed it asks;
pick Claude Code, or pass `--client claude-code`. It writes three things into the folder:
`.mcp.json` (how Claude Code starts the engine), the `/etchwork` skill, and a setting that
pre-approves the engine's tools.

**3. Design.** Start Claude Code in that folder. The first time, it asks whether to trust the
`etchwork` server: approve it. `/mcp` should then list `etchwork` as connected.

```bash
claude
```

```
/etchwork design a USB-C powered LED blinker using an ATtiny85, with a 6-pin ISP header
```

Say what matters to you: size limits, connectors, power, and parts you insist on. Approve the file
writes Claude Code asks about (`board.py`, `expected.netlist.toml`, `README.md`). A run takes
from minutes to about an hour, and ends when the agent reports **DONE**.

**4. What you get**, in the project folder:

| File | What it is |
|---|---|
| `board.kicad_pcb` | The board; open it in KiCad |
| `board.kicad_sch`, `board-schematic.pdf` | A schematic drawn from `board.py`, for reading and review |
| `gerbers/` | Fabrication files; zip them and upload to your fab |
| `board.py`, `board.net` | The circuit as a SKiDL script, and its netlist |
| `README.md` | What the board is, its parts, and its trace widths |

Read the **nets awaiting your decision** in the agent's final answer: they are the calls it left
to you.

The schematic is a reference drawing, generated from `board.py` and checked to match it. Change
the circuit through the agent, not the schematic, and don't use KiCad's "Update PCB from
Schematic" on an Etchwork project: KiCad doesn't know the schematic and board belong together, and
it can rewrite the routed board. Some boards (e.g. with multi-unit parts like op-amps) come without
a schematic, when the generator can't draw them correctly; `README.md` then says so, and why.

**5. Change it** by asking in the same session, e.g. "make the board smaller" or "add another
LED". Changes go through `board.py`, and the board is re-laid out and re-checked. If you edited
the board in KiCad yourself, the agent asks before replacing your layout.

After pulling a new version of Etchwork, run `etchwork build` again, then `etchwork init .` in
each project that should use the new engine.

## Other clients

Pass `--client codex` or `--client flai` to `etchwork init`, or leave `--client` out and it
picks the agent itself: the one whose config the folder already has, else the only supported
agent installed, else it asks. See `docs/clients.md` for details.

## Where data goes

The engine container runs with no network. Your design files go only to your AI client's model provider—never anywhere else.

## The isolated runner

For self-hosted, unattended production jobs, build a runner image that includes the engine and an agent:

```bash
./etchwork runner build --agent-bin <path to a Linux flai binary>
```

The runner's agent is currently a Linux flai binary — `--agent-bin` doesn't yet
take another agent. This prints `ENGINE=`, `EGRESS=` and `RUNNER=` image tags.
Then run a job:

```bash
ETCHWORK_EGRESS_IMAGE=<EGRESS> OPENROUTER_API_KEY=<key> ./etchwork job <RUNNER> <job dir>
```

`<job dir>/prompt.txt` holds the prompt; outputs land in `<job dir>.out/`, never
inside `<job dir>` itself. There is no REST API or status endpoint — this is a
one-shot container, run to completion (or `--probes`, which runs the isolation
probes instead of a real job).

## Status

**v0.2 pilot**: simple 2-layer boards on JLCPCB defaults.

**Guaranteed:**
- the board passed KiCad DRC, re-run by the packaging step on the delivered board under the project's design rules
- all nets are connected
- the pours are filled
- the Gerbers are current
- the named pin functions (MOSI, SCK, SDA…) are wired to pins that carry them

**Not guaranteed:**
- that the circuit works as intended
- part choices and availability
- thermal and electrical margins
- anything the prompt didn't say

[CONTRACT](contract/README.md) measures how often delivered boards meet the
requirements a prompt implied, the first slice of "not guaranteed".

## Licence

Proprietary; see [LICENSE](LICENSE). Contact FlologixAI for licensing. [NOTICE](NOTICE) records where this code came from.
