#!/bin/sh
# Runs every test suite: the engine's and CONTRACT's (in the engine image),
# then the CLI, export and job scripts' own. Builds the image first, so an
# edit under engine/ is what gets tested. Exits 1 if any suite fails, or if a
# pytest suite skips anything but the opt-in real-routing evals: a skip that
# appears on its own (a missing module, a missing fixture) is coverage lost
# without a failure to say so.
set -u
root="$ETCHWORK_ROOT"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
failed=""

# The one skip reason that is expected: tests/test_*_eval.py opt in with
# CIRCUIT_ROUTING_EVAL=1.
EXPECTED_SKIP="set CIRCUIT_ROUTING_EVAL=1"

suite() {
  # suite <name> <command...>: run it, print its last line, remember a failure.
  name=$1; shift
  if "$@" >"$scratch/$name.out" 2>&1; then
    printf '%-10s %s\n' "$name" "$(grep -v '^[[:space:]]*$' "$scratch/$name.out" | tail -1)"
  else
    printf '%-10s FAILED (exit %s)\n' "$name" "$?"
    grep -v '^[[:space:]]*$' "$scratch/$name.out" | tail -20 | sed 's/^/    /'
    failed="$failed $name"
    return
  fi
  # pytest's -rs summary: one "SKIPPED [n] file:line: reason" per reason.
  unexpected=$(grep '^SKIPPED' "$scratch/$name.out" | grep -vF "$EXPECTED_SKIP")
  if [ -n "$unexpected" ]; then
    printf '%-10s unexpected skips:\n' "$name"
    printf '%s\n' "$unexpected" | sed 's/^/    /'
    failed="$failed $name"
  fi
}

"$root/etchwork" build >"$scratch/build.out" 2>&1 || {
  echo "build      FAILED"; tail -20 "$scratch/build.out" | sed 's/^/    /'; exit 1; }
printf '%-10s %s\n' build "$(tail -1 "$scratch/build.out")"

suite engine "$root/etchwork" test -rs
suite contract "$root/etchwork" contract test -rs
suite cli sh "$root/tests/cli_test.sh"
suite export sh "$root/tests/export_test.sh"
suite job sh "$root/tests/job_test.sh"

if [ -n "$failed" ]; then
  echo "FAILED:$failed"
  exit 1
fi
echo "all suites passed"
