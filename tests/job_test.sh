#!/bin/sh
# Tests for libexec/job.sh's control flow, against a fake `docker` on PATH:
# no image, container or network is ever created. The fake logs every call
# and answers the way a scenario (FAKE_*) says the real one would. Prints
# PASS/FAIL per check; exits 1 if any FAIL.
set -u
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
etchwork="$root/etchwork"
scratch="${TMPDIR:-/tmp}/etchwork-job-test.$$"
mkdir -p "$scratch/bin"
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT

fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

cat > "$scratch/bin/docker" <<'EOF'
#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
case "$1 ${2:-}" in
  "network inspect")
    case "$*" in
      *gateway_mode*) echo isolated ;; *Internal*) echo true ;; *Subnet*) echo 172.31.99.0/24 ;;
    esac
    exit 0 ;;
esac
case "$1" in
  inspect)
    case "$*" in
      *Config.Image*) echo "$FAKE_EGRESS" ;; *State.Running*) echo true ;;
      *ExitCode*) echo "${FAKE_PKG_EXIT:-0}" ;;
    esac ;;
  run)
    case "$*" in
      *"--name etchwork-job-"*-pkg*) echo pkg-id ;;
      *"--name etchwork-job-"*)
        # A signal while the agent container starts: job.sh is our parent.
        [ "${FAKE_SIGNAL_ON_RUN:-}" = 1 ] && kill -TERM "$PPID"
        echo job-id ;;
    esac ;;
  logs)
    case "$*" in
      *-pkg*) echo "packaged" ;;
      *) echo "agent: using key $OPENROUTER_API_KEY" ;;
    esac ;;
  wait)
    case "$*" in
      *-pkg*) ;;
      *) echo "${FAKE_AGENT_EXIT:-0}"; exit "${FAKE_WAIT_RC:-0}" ;;
    esac ;;
esac
exit 0
EOF
chmod +x "$scratch/bin/docker"

# run_job <name> [VAR=value ...]: a fresh job dir, then `etchwork job` in the
# foreground (a background job would start with SIGINT ignored). Sets $rc,
# $log (the docker calls) and $out (the job's output dir).
run_job() {
  name=$1; shift
  job="$scratch/$name"
  mkdir -p "$job"
  echo "make a board" > "$job/prompt.txt"
  log="$scratch/$name.docker.log"; : > "$log"
  out="$job.out"
  env PATH="$scratch/bin:$PATH" DOCKER_LOG="$log" FAKE_EGRESS=egress:test \
    ETCHWORK_EGRESS_IMAGE=egress:test OPENROUTER_API_KEY=sk-or-test-secret-value \
    CIRCUIT_EGRESS_DIR="$scratch/egress-$name" "$@" \
    "$etchwork" job runner:test "$job" >"$scratch/$name.stdout" 2>&1
  rc=$?
}

# --- 1: a finished job packages, and the agent's log is redacted ---
run_job done
[ "$rc" = 0 ] && pass "done: exit 0" || fail "done: exit 0" "exit $rc: $(cat "$scratch/done.stdout")"
if grep -q "\[redacted\]" "$out/job.log" && ! grep -q "sk-or-test-secret-value" "$out/job.log"; then
  pass "done: the key is redacted in job.log"
else
  fail "done: the key is redacted in job.log" "$(cat "$out/job.log")"
fi
grep -q "agent-exit=0" "$out/job.log" && pass "done: agent-exit recorded" || fail "done: agent-exit recorded" "$(cat "$out/job.log")"
grep -q -- "--name etchwork-job-.*-pkg .*--network none" "$log" \
  && pass "done: packaging runs with no network" || fail "done: packaging runs with no network" "$(cat "$log")"
if grep -q -- "-e OPENROUTER_API_KEY " "$log" && ! grep -q "sk-or-test-secret-value" "$log"; then
  pass "done: the key reaches docker by name, never in argv"
else
  fail "done: the key reaches docker by name, never in argv" "$(cat "$log")"
fi

# --- 2: a signal while the agent container starts removes it ---
run_job signal FAKE_SIGNAL_ON_RUN=1
[ "$rc" = 130 ] && pass "signal: exit 130" || fail "signal: exit 130" "exit $rc"
grep -q "^rm -f etchwork-job-" "$log" \
  && pass "signal: the agent container is removed" || fail "signal: the agent container is removed" "$(cat "$log")"
grep -q -- "-pkg" "$log" \
  && fail "signal: never reaches packaging" "$(cat "$log")" || pass "signal: never reaches packaging"

# --- 3: the agent outlives the wall clock ---
run_job timeout FAKE_WAIT_RC=124
[ "$rc" = 124 ] && pass "timeout: exit 124" || fail "timeout: exit 124" "exit $rc"
grep -q "^kill etchwork-job-" "$log" \
  && pass "timeout: the agent container is killed" || fail "timeout: the agent container is killed" "$(cat "$log")"
grep -q -- "-pkg" "$log" \
  && fail "timeout: never reaches packaging" "$(cat "$log")" || pass "timeout: never reaches packaging"

# --- 4: docker wait itself fails ---
run_job waitfail FAKE_WAIT_RC=1
[ "$rc" = 2 ] && pass "wait fails: exit 2" || fail "wait fails: exit 2" "exit $rc"
grep -q "docker wait failed (exit 1)" "$out/job.log" \
  && pass "wait fails: said in job.log" || fail "wait fails: said in job.log" "$(cat "$out/job.log")"

# --- 5: the packaging container's own exit code is the job's ---
run_job notdone FAKE_PKG_EXIT=1
[ "$rc" = 1 ] && pass "not done: exit 1" || fail "not done: exit 1" "exit $rc"

# --- 6: a missing key is a setup error before docker is touched ---
env -u OPENROUTER_API_KEY PATH="$scratch/bin:$PATH" DOCKER_LOG="$scratch/nokey.log" \
  ETCHWORK_EGRESS_IMAGE=egress:test "$etchwork" job runner:test "$scratch" >/dev/null 2>&1
rc=$?
if [ "$rc" != 0 ] && [ ! -s "$scratch/nokey.log" ]; then
  pass "no key: refused before any docker call"
else
  fail "no key: refused before any docker call" "exit $rc"
fi

echo "$fails failures"
[ "$fails" -eq 0 ]
