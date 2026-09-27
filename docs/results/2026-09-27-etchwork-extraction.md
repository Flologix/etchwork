# Etchwork extraction: results

Date: 2026-09-27
Spec and plan: in the flai repository, `docs/superpowers/specs/2026-09-27-etchwork-extraction-design.md` and `docs/superpowers/plans/2026-09-27-etchwork-extraction.md` (with the rest of the design history).
Status: §7.1 and §7.2 are done. §7.3 (the flai side), the export commit and go/no-go come after the export.

## §7.1 Free checks (staged `etchwork/`, in flai's branch `spec-etchwork`)

| Check | Result |
|---|---|
| 1. Engine | `etchwork build`, then `etchwork test`: **570 passed, 8 skipped**, the same as before the move (image `etchwork-engine:f6e63a7b3daf`). |
| 2. Runner | `etchwork runner build --agent-bin <selfdev flai>` gives `ENGINE=etchwork-engine:f6e63a7b3daf`, `EGRESS=etchwork-egress:64796bc81edd`, `RUNNER=etchwork-runner:<sha>-f6e63a7b3daf`. The probe procedure (runner plan, Task 3 Step 8) under the new names: default **0 failed**; control network without the isolated gateway, where the gateway probe **FAILs** as required; allowlist flip, exactly the two expected lines flipped; back to default, 0 failed; symlink-refusal packaging gives `done: false` and no `deliverable.zip`. |
| 3. `init` | `tests/cli_test.sh`: **35/35 pass**, under dash. It covers:<ul><li>`claude-code`: the `.mcp.json` key `etchwork`, `name: etchwork` and `mcp__etchwork__*` allowed, with existing keys kept;</li><li>`codex`: one `AGENTS.md` block after two runs;</li><li>`flai`: the skill and addendum;</li><li>malformed or wrong-shaped JSON: a one-line error, the file untouched and nothing written;</li><li>`:` refused, spaces accepted, and a new folder created;</li><li>Docker checked before any write;</li><li>a symlinked CLI;</li><li>the engine listing its tools over stdio.</li></ul> |
| 4. Export script | `tests/export_test.sh`: **14/14 pass**, against a local bare repo. It covers one commit, the configured identity, the provenance message, refusing a non-empty target, and a tree identical to `etchwork/`. |

**Deviations:**
- The `etchwork-jobs` network uses `172.31.99.0/24`, because flai's old `circuit-jobs` network holds `172.30.99.0/24` on the same host until flai's circuit code is removed.
- The control-run probe used a listener already on the host (`:11434`), because the sandbox refused to open a new one. The probe still FAILed, which is the proof.
- `CIRCUIT_*` environment variables keep their names, as the spec's rename table says.

## §7.2 Live check in Claude Code

A fresh folder from `etchwork init <dir> --client claude-code`, with the CNC fixture as `board.net`. The run: `claude -p "/etchwork Classify every net of board.net …" --mcp-config .mcp.json --strict-mcp-config --allowedTools mcp__etchwork__net_evidence,mcp__etchwork__set_net_classes --output-format stream-json --verbose`.

| Measure | Bar | Result |
|---|---|---|
| Run | succeeds | `is_error: false`, 5 turns, 37 s, **$0.40**, `claude-opus-5-5` |
| Skill | `/etchwork` loads | `etchwork` is in the session's skills list |
| Tool names | `mcp__etchwork__` ≥ 2, `mcp__circuit__` = 0 | `net_evidence` ×2 (paginated), `set_net_classes` ×1; **0** `mcp__circuit__` |
| Dangerous | 0 | **0** |
| Class accuracy (accepted) | ≥ 0.90 | **1.00** (50/50); tier 47/50 |
| Coverage | ≥ 0.80 | **0.943** (50/53). Review: CUT_OUT, SPN_OUT (load current unknown), VMOT |

Scored with `circuit_mcp.netclass.score` inside `etchwork-engine:f6e63a7b3daf`, against `engine/tests/fixtures/classify/cnc_xy_controller.labels.toml`.

Kill criteria §7.4: not triggered. No probe failed, no `mcp__circuit__` calls were made, and the skill loaded.

## §7.3 flai side

Pending: this comes after the export and the removal commit.

## Export

Pending: the export commit SHA and the remote.

## Go / no-go

Pending (Task 5).
