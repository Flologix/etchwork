#!/bin/sh
# Build the etchwork-engine and etchwork-egress images, and the per-job
# runner (the engine plus an agent binary). Last three lines: ENGINE=,
# EGRESS=, RUNNER=. Ported from build_circuit_runner.sh (board-service spec
# §4.1): this repo can't build the agent itself, so --agent-bin (default
# `command -v flai`) supplies it from outside.
set -eu
agent_bin=""
while [ $# -gt 0 ]; do
  case "$1" in
    --agent-bin) agent_bin="${2:?--agent-bin needs a value}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$agent_bin" ] || agent_bin=$(command -v flai || true)
if [ -z "$agent_bin" ] || [ ! -f "$agent_bin" ]; then
  echo "no agent binary found; pass --agent-bin <path> or put flai on PATH" >&2
  exit 1
fi

. "$ETCHWORK_ROOT/libexec/build.sh" --lib
hash=$(engine_hash)
engine="etchwork-engine:$hash"

sha=$(git -C "$ETCHWORK_ROOT" rev-parse --short=9 HEAD)
[ -z "$(git status --porcelain -- "$ETCHWORK_ROOT")" ] || sha="$sha-dirty"
egress_hash=$(cat "$ETCHWORK_ROOT/runner/egress/Dockerfile" "$ETCHWORK_ROOT/runner/egress/tinyproxy.conf" | sha256sum | cut -c1-12)
egress="etchwork-egress:$egress_hash"
runner="etchwork-runner:$sha-$hash"

docker build -t "$engine" "$ETCHWORK_ROOT/engine"
docker build -t "$egress" "$ETCHWORK_ROOT/runner/egress"

context=$(mktemp -d)
trap 'rm -rf "$context"' EXIT
cp "$agent_bin" "$context/flai"
cp "$ETCHWORK_ROOT/runner/etchwork-job" "$ETCHWORK_ROOT/runner/probes.sh" "$ETCHWORK_ROOT/runner/Dockerfile" "$context/"
cp "$ETCHWORK_ROOT/skill/SKILL.md" "$ETCHWORK_ROOT/skill/flai-addendum.md" "$context/"
docker build --build-arg ENGINE_IMAGE="$engine" -t "$runner" "$context"
echo "ENGINE=$engine"
echo "EGRESS=$egress"
echo "RUNNER=$runner"
