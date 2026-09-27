# CONTRACT

A requirement-conformance benchmark for agent-designed boards. Etchwork
guarantees a delivered board is DRC-clean, connected, poured and fab-ready; it
does not guarantee the circuit does what the prompt asked. CONTRACT measures
the first slice of that gap: **does the delivered board satisfy the
requirements the prompt implied?**

Design: `docs/superpowers/specs/2026-09-27-contract-benchmark-design.md`.

## Grading a board

```sh
./etchwork build                                      # once: the engine image
./etchwork contract score keyfob-base <job dir>.out   # or any dir with kicad/ + source/
```

`<board dir>` is a run's output: either it holds `deliverable.zip` (what
`etchwork job` writes), or it is an unpacked `kicad/*.kicad_pcb` +
`source/*.net` tree. The result goes to `contract-result.json` in the current
directory (`--out` to change it). It is never written into the board dir,
whose `result.json` belongs to the job.

Exit status: 0 when the board was graded (pass or fail), 3 on a harness error.

## The result

Each clause is one of:

- `pass` / `fail`: graded, with `detail` and `measured` saying what was found
  ("outline is 38.10 x 23.00 mm", not just "fail").
- `not_delivered`: no board came out of the run. This is a **strict failure**,
  never excluded from a denominator.
- `error`: the harness could not grade it (a crashed dump, a checker bug, a
  task naming a checker that does not exist). Listed under `harness_errors`,
  and never counted as a clause failure.

Three numbers are reported together and **never collapsed**:

- **Strict pass rate**: the fraction of runs passing every clause. The
  headline number.
- **Clause satisfaction rate**: per clause, the fraction of runs passing.
- **Harness integrity**: the fraction of runs with `graded: true`.

There is no composite or mean-of-clauses score, and there never will be.
Clauses are not equally weighted, and a composite invites the indefensible claim
that a board is "87% correct". Every published number states N, the number of
runs (default 5), because the same agent does not produce the same board twice.

## Tasks

`tasks/<name>/` holds `prompt.md` (handed to the agent), `contract.toml` (held
out, never shown to the agent) and `provenance.md` (source, derivation,
supplied-vs-withheld numbers, dropped clauses). The prompt carries intent; the
contract carries numbers. Every clause is a range or a relation, never a
comparison with a reference design.

`contract.toml` declares `[parts.<alias>]` allowlists (regexes over `ref`,
`lib_id`, `value`, and a `pads` count list) and `[nets.<alias>]` function
matchers (`words` matched against pin names and net names, optionally limited to
a `part`). Clauses name a checker and pass aliases as arguments.

## Checkers

| Checker | Checks |
|---|---|
| `outline_within` | outline bounding box within W x H (+tol), either orientation |
| `part_present` | at least `count` parts match an allowlist |
| `series_part_between` | a two-terminal part of a designator kind, value in range, between two nets |
| `decoupling_near` | a cap >= N farads across supply and ground, within N mm of a supply pin |
| `net_connects` | two nets meet; or every matching part reaches a net (optionally each a distinct one) |
| `no_short` | two nets are distinct |

A checker is a pure function `(board, **args) -> Result` over `contract/board.py`.
It never touches pcbnew; the one pcbnew dump runs inside the engine image
(`contract/dump.py`). Register new checkers in `checkers/__init__.py`.

## Testing CONTRACT itself

```sh
python3 -m pytest contract/tests -q     # host: pure tests; pcbnew tests skip
./etchwork contract test                # engine image: everything
./etchwork contract fixtures            # regenerate the mutation fixtures
```

There is no golden reference board (the seed design has no licence, so none
is vendored). Trust in the grader rests on **mutation fixtures**: a clean board
that passes every clause, and one mutant per clause that fails that clause and
no other.

## Stated limits

CONTRACT measures conformance of a delivered board to a written clause list
derived from one real design, checked statically. It does not measure that the
board works. It does not simulate. It does not cover RF. It does not check
firmware, part availability, thermal or electrical margin, or EMC. A board that
passes every clause is not a validated product, and a clause that fails may
indicate a defensible alternative design rather than an error.

Net clauses find pins by function: by the pin names in the netlist's library
symbols or the pads' pin functions, or failing that by net name. A part with
neither named pins nor meaningfully named nets cannot be identified, and its
net clauses fail.
