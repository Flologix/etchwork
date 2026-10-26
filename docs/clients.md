# Clients

The etchwork engine (SKiDL, KiCad, freerouting, net classification) ships as a
Docker container and a client-neutral skill. Any MCP client that can run a
`docker run` command and load a skill/rules file can drive it — flai is one
client among several, not a requirement.

`etchwork init` wires a project folder up for a given client. It
builds (or checks for) the engine image, tagged by a hash of `engine/`,
and prints the tag as `IMAGE=<tag>` when it's done.

Without `--client`, `init` picks the client itself and prints which and why:

1. the one whose config the folder already has (`.claude/` → claude-code,
   `AGENTS.md` → codex, `.flai-code/` → flai, `.mcp.json` → marv), if
   exactly one;
2. otherwise the only supported agent on `PATH` (`claude`, `codex`, `flai`,
   `marv`);
3. otherwise it asks, in a terminal, and without one exits 2 naming the
   candidates and writing nothing. Pass `--client` in scripts and CI.

## Claude Code

```sh
etchwork init <project-dir> --client claude-code --build
```

This writes the skill to `<project-dir>/.claude/skills/etchwork/SKILL.md` and
adds a `etchwork` entry to `<project-dir>/.mcp.json` (any other MCP servers
already in that file are kept). It also adds `mcp__etchwork__*` to
`<project-dir>/.claude/settings.json`'s `permissions.allow` (creating the file
if needed; every other key in it is kept). Drop `--build` on later runs once
the image exists.

Headless — this is the form that's actually been run (§7.2): a slash-command
prompt, `--strict-mcp-config` so only the project's own `.mcp.json` server is
trusted, and a permission mode so the Write/Edit/Bash steps the skill needs
(writing `board.py` and `expected.netlist.toml`, editing the README) don't
block on a prompt that headless mode can't answer:

```sh
claude -p "/etchwork design a board for <what you need>" \
  --mcp-config .mcp.json --strict-mcp-config --permission-mode acceptEdits
```

## Codex

```sh
etchwork init <project-dir> --client codex --build
```

This writes the skill into `<project-dir>/AGENTS.md`, between
`<!-- etchwork-skill:begin -->` / `<!-- etchwork-skill:end -->` markers (re-run
to replace that block in place; nothing else in `AGENTS.md` is touched). It
also prints the MCP server stanza to add to `~/.codex/config.toml`:

```toml
[mcp_servers.etchwork]
command = "docker"
args = ["run", "-i", "--rm", "--pull", "never", "--network", "none", "--user", "<uid>:<gid>", "-v", "<project-dir>:/workspace", "<IMAGE>"]
startup_timeout_sec = 120
tool_timeout_sec = 1800
```

The timeouts are set well above Codex's own MCP defaults (about 10 s to
start, about 60 s per tool call): a cold KiCad container, and `run_layout`
with freerouting, both routinely run past a minute.

Headless (not yet verified — `codex exec` defaults to a read-only sandbox,
which may not allow the skill's `board.py`/`expected.netlist.toml` writes):

```sh
codex exec --full-auto "design a board for <what you need>"
```

## flai

```sh
etchwork init <project-dir> --client flai --build
```

This writes a `etchwork` entry to `<project-dir>/.mcp.json` (with
`"shared": false`, flai's convention for a server it manages itself) and the
skill, plus a flai-specific addendum, to
`<project-dir>/.flai-code/skills/etchwork/SKILL.md`.

```sh
cd <project-dir> && flai
```

Then type:

```
/etchwork design a board for <what you need>
```

## Marv

```sh
etchwork init <project-dir> --client marv --build
```

This writes four things:

- an `etchwork` entry in `<project-dir>/.mcp.json` — the same `docker run`
  stanza as Claude Code's, kept alongside any other servers already there.
  Marv reads a project's `.mcp.json` **only when it starts**, so start it
  after this exists;
- the skill in `<project-dir>/AGENTS.md`, between the same
  `<!-- etchwork-skill:begin -->` / `<!-- etchwork-skill:end -->` markers
  Codex's path uses (Marv reads `AGENTS.md`, so re-running either client
  replaces that one block and leaves the rest of the file alone);
- a short `## Python` block in `<project-dir>/CLAUDE.md`, between
  `<!-- etchwork-python:begin -->` / `<!-- etchwork-python:end -->`: the
  board script runs with plain `python3`, no venv or build step;
- `.gitignore` entries for what the engine and the skill write and the board
  doesn't: `.etchwork/`, `*.kicad_prl`, `*.kicad_dru.orig`, plus the paths
  the skill quotes for the engine (`parts/*.part.toml`,
  `fp/*.pretty/*.kicad_mod`, `symbols/*.kicad_sym`, `*.netclasses.json`).
  A checkout of the board stays the board; Gerber output is yours and stays
  tracked. Existing lines in the file are kept.

```sh
cd <project-dir> && marv
```

Then, once, in Marv:

```
/mcp trust
```

That approves the project's server; without it Marv won't start the engine.
Then ask in plain words, e.g. "design a board for <what you need>".

If the project's latest commit still has `.etchwork/` in it, `init` says
so: Marv's checkpoints and undo would restore the event log on every turn.
Fix it with `git rm -r --cached .etchwork`.

## Any other MCP client

Start the engine the same way `etchwork init` does:

```sh
docker run -i --rm --pull never --network none --user "$(id -u):$(id -g)" \
  -v "<project-dir>:/workspace" <IMAGE>
```

and paste `skill/SKILL.md` into whatever rules
or system-prompt file that client reads.

## Net classification

`net_evidence` / `set_net_classes` do the deterministic work (menu, evidence,
validation); picking a class and tier for each open net is a judgement call
the skill asks the client's own model to make. There's no model or API call
inside the engine container — classification quality depends entirely on the
model behind whichever client is running the skill.

The engine checks that power-pin nets get a power class, but it never checks
tiers, and the trace width follows the tier. Before fabrication, a human
checks every power net's tier in the README's net table.

## Where your data goes

The engine container runs with `--network none`: it can read and write only
the project folder mounted at `/workspace`, and reaches no network at all.
Board files, netlists and part data leave the container only through your MCP
client, which sends them (as tool calls and results) to whichever model
provider that client is configured to use. The engine itself sends nothing
anywhere.

The engine also appends one line per tool call to `.etchwork/events.jsonl` in
the project folder: the tool, its duration, its outcome and DRC counts, with
arguments summarised (paths and numbers, never script contents or net-class
reasons). It is local only; `-e CIRCUIT_EVENTS=0` in the `docker run` args
turns it off. See `docs/superpowers/specs/2026-09-28-flywheel-design.md`.
