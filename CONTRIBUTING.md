# Contributing to Etchwork

Thanks for helping. Bug reports, boards that came out wrong, and pull requests are all welcome.

## Reporting a problem

Open an issue with:

- the prompt you gave the agent, and which agent (Claude Code, Codex…);
- what you expected and what you got: a DRC error, a wrong net, a part that doesn't exist;
- if you can share it, the project folder's `board.py` and `.etchwork/events.jsonl`. The event
  log holds tool names, timings and DRC counts, not your circuit.

Check the README's **Not guaranteed** list first: a circuit that doesn't work as intended is
often a prompt issue, not an engine bug. Those reports are still useful; say so in the issue.

## Setting up

You need Linux, Docker 28 or later, git and python3.

```bash
git clone https://github.com/FlologixAI/etchwork.git
cd etchwork
./etchwork build        # builds the engine image; the first build takes a few minutes
```

## Running the tests

```bash
./etchwork test          # the engine suite (pytest) inside the engine image
sh tests/cli_test.sh     # the etchwork CLI: build, init, re-init
```

Both run in CI on every pull request. Pass extra pytest arguments through `etchwork test`, e.g.
`./etchwork test -k netclass`.

## Making a change

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Branch from `main` and keep the pull request to one change.
3. Add or update tests in `engine/tests/` for engine changes. Tests that need KiCad run inside
   the engine image, so run them through `./etchwork test`, not your host's Python.
4. Update `README.md` when behaviour a user sees changes.
5. Make sure `./etchwork test` and `tests/cli_test.sh` pass.

Changes to the engine's guarantees (DRC, connectivity, Gerbers) need a test that fails without
the change.

Issues labelled `good first issue` are a good place to start.

## Licence

Etchwork is MIT-licensed. By submitting a pull request you agree that your contribution is
licensed under the MIT License, the same as the rest of the project.
