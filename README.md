# Etchwork

A PCB design toolkit for AI coding agents. Write a sentence about what you need, and the engine produces a routed, DRC-clean, fab-ready KiCad board with Gerbers. It runs as an MCP server in Docker.

## Requirements

- **Linux** (macOS untested)
- **Docker** version 28 or later
- **git** and **python3**
- For Claude Code: the `claude` CLI

## Quick start (Claude Code)

```bash
git clone https://github.com/FlologixAI/etchwork.git && cd etchwork && ./etchwork init ~/my-board --client claude-code --build
cd ~/my-board && claude
```

Then type:

```
/etchwork design a USB-C LED blinker
```

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


## Licence

Proprietary; see [LICENSE](LICENSE). Contact FlologixAI for licensing. [NOTICE](NOTICE) records where this code came from.
