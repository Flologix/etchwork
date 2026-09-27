#!/bin/sh
# Run one board job (board-service spec §4.3-§4.4).
# Usage: ETCHWORK_EGRESS_IMAGE=<egress tag> etchwork job <runner tag> <job dir> [--probes]
# <job dir>/prompt.txt holds the prompt. OPENROUTER_API_KEY must be set.
# Outputs go to <job dir>.out/ -- never inside the job dir, whose every path
# the agent controls. Exit: 0 DONE, 1 not done, 124 timeout, 2 setup error.
set -u
runner="${1:?runner tag}"; job_arg="${2:?job dir}"; mode="${3:-}"
case "$mode" in
  ""|--probes) ;;
  *) echo "unknown argument: $mode (expected --probes or nothing)" >&2; exit 2 ;;
esac
job=$(CDPATH= cd -- "$job_arg" && pwd) || exit 2
out="$job.out"
egress_image="${ETCHWORK_EGRESS_IMAGE:?set ETCHWORK_EGRESS_IMAGE (etchwork runner build prints it)}"
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY}"

# The job network, with its gateway isolated: without the option,
# 172.31.99.1 reaches every host service bound to 0.0.0.0. Internal-ness and
# the subnet are checked too -- a network that merely happens to be named
# "etchwork-jobs" but was created some other way (wrong subnet, not internal)
# would silently give the job real connectivity.
#
# The subnet is 172.31.99.0/24, not circuit-jobs's 172.30.99.0/24: this host
# still has circuit-jobs (with circuit-egress attached) from the pre-rename
# runner, and it must be left running, so etchwork-jobs cannot reuse its
# address space (Docker refuses an overlapping pool outright). Once
# circuit-jobs is retired, etchwork-jobs could move back to .30 -- there is
# nothing else tying it to .31.
mode_now=$(docker network inspect -f '{{index .Options "com.docker.network.bridge.gateway_mode_ipv4"}}' etchwork-jobs 2>/dev/null || echo missing)
internal_now=$(docker network inspect -f '{{.Internal}}' etchwork-jobs 2>/dev/null || echo missing)
subnet_now=$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' etchwork-jobs 2>/dev/null || echo missing)
if [ "$mode_now" != isolated ] || [ "$internal_now" != true ] || [ "$subnet_now" != "172.31.99.0/24" ]; then
  # etchwork-egress is attached to etchwork-jobs; the network rm fails while it
  # is, so it must go first.
  docker rm -f etchwork-egress >/dev/null 2>&1
  docker network rm etchwork-jobs >/dev/null 2>&1
  docker network create --internal --subnet 172.31.99.0/24 \
    -o com.docker.network.bridge.gateway_mode_ipv4=isolated etchwork-jobs >/dev/null || exit 2
fi

# The proxy's allowlist follows the model setting (spec §3). Restart the proxy
# whenever the filter or its image changes.
egress_dir="${CIRCUIT_EGRESS_DIR:-$HOME/.etchwork/egress}"
mkdir -p "$egress_dir" || exit 2
printf '%s\n' "${CIRCUIT_EGRESS_HOSTS:-openrouter.ai}" | tr ',' '\n' \
  | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; /^$/d; s/\./\\./g; s/^/^/; s/$/$/' > "$egress_dir/filter.new"
running_image=$(docker inspect -f '{{.Config.Image}}' etchwork-egress 2>/dev/null || echo none)
running=$(docker inspect -f '{{.State.Running}}' etchwork-egress 2>/dev/null || echo false)
if ! cmp -s "$egress_dir/filter.new" "$egress_dir/filter" || [ "$running" != true ] || [ "$running_image" != "$egress_image" ]; then
  mv "$egress_dir/filter.new" "$egress_dir/filter"
  docker rm -f etchwork-egress >/dev/null 2>&1
  docker run -d --name etchwork-egress --network etchwork-jobs --ip 172.31.99.2 \
    --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp --tmpfs /var/run/tinyproxy \
    --memory 256m --pids-limit 64 \
    -v "$egress_dir/filter:/etc/tinyproxy/filter:ro" "$egress_image" >/dev/null || exit 2
  docker network connect bridge etchwork-egress || exit 2
else
  rm -f "$egress_dir/filter.new"
fi

# Never run the job as root.
if [ "$(id -u)" = 0 ]; then user=10001:10001; else user="$(id -u):$(id -g)"; fi
proxy=http://172.31.99.2:8888
set -- --network etchwork-jobs --read-only --tmpfs /tmp:rw,size=2g \
  --cap-drop ALL --security-opt no-new-privileges --user "$user" \
  --cpus 4 --memory 8g --memory-swap 8g --pids-limit 512 \
  -e HTTPS_PROXY=$proxy -e HTTP_PROXY=$proxy -e https_proxy=$proxy -e http_proxy=$proxy \
  -e FLAI_NO_TELEMETRY=1 -e DO_NOT_TRACK=1 -e OPENROUTER_API_KEY \
  -v "$job:/workspace"

if [ "$mode" = "--probes" ]; then
  [ "$(id -u)" = 0 ] && chown -R 10001:10001 "$job"
  docker run --rm "$@" -e PROBE_NEIGHBOUR --entrypoint /usr/local/bin/etchwork-probes "$runner"
  exit $?
fi

rm -rf "$out" && mkdir "$out" || exit 2
[ "$(id -u)" = 0 ] && chown -R 10001:10001 "$job" "$out"
name="etchwork-job-$(date +%s)-$$"
# The model is an operator setting (spec §3): passed through only if set, so
# etchwork-job's defaults (openrouter, qwen/qwen3.8-flash) apply otherwise. A
# new model must pass a live board run before it is supported.
docker run -d --name "$name" "$@" -e CIRCUIT_PROVIDER -e CIRCUIT_MODEL \
  "$runner" /workspace/prompt.txt >/dev/null || exit 2
# An interrupted script must never leave the agent container -- and the
# OPENROUTER_API_KEY it's holding -- running, AND must never fall through into
# packaging: a Ctrl-C mid-job is not "the job finished," so INT/TERM get their
# own trap that removes the container and then exits 130 outright, rather than
# just returning control to the line after `docker wait`.
trap 'docker rm -f "$name" >/dev/null 2>&1' EXIT
trap 'docker rm -f "$name" >/dev/null 2>&1; echo "interrupted" >> "$out/job.log"; exit 130' INT TERM
# `docker logs -f`'s own exit status would otherwise be swallowed by the pipe
# (only python3's status reaches `wait`); capture it to a dot-file in $out,
# which never collides with a packaging output, and remove it once read.
logs_rc_file="$out/.docker-logs-rc"
check_logs_rc() {
  if [ -f "$logs_rc_file" ]; then
    dl_rc=$(cat "$logs_rc_file")
    [ "$dl_rc" != 0 ] && echo "docker-logs-exit=$dl_rc" >> "$out/job.log"
    rm -f "$logs_rc_file"
  fi
}
( docker logs -f "$name" 2>&1; echo $? > "$logs_rc_file" ) | python3 "$ETCHWORK_ROOT/runner/redact_stream.py" > "$out/job.log" &
logs=$!
# `docker wait` runs as a true background job, not `$(...)`: this shell's
# blocking read for a command substitution's output does not get interrupted
# by a trapped signal until that read completes (verified directly against
# this host's /bin/sh -- a bare `code=$(timeout 7200 docker wait "$name")`
# left the INT/TERM trap un-fired for as long as the job kept running, which
# would have defeated the "never reach packaging" requirement outright). The
# `wait` builtin on a background job, unlike that, is specifically
# interruptible.
wait_out_file="$out/.docker-wait-out"
rm -f "$wait_out_file"
timeout 7200 docker wait "$name" > "$wait_out_file" 2>&1 &
waiter=$!
wait "$waiter"
wait_rc=$?
code_output=$(cat "$wait_out_file" 2>/dev/null)
rm -f "$wait_out_file"
if [ "$wait_rc" -eq 0 ]; then
  code="$code_output"
  timed_out=0
elif [ "$wait_rc" -eq 124 ]; then
  # A real timeout: `timeout` itself reports 124.
  docker kill "$name" >/dev/null 2>&1
  code=124
  timed_out=1
else
  # Anything else means `docker wait` (or docker itself) failed outright --
  # not the same thing as the job simply running long -- so this is a setup
  # error, not a timeout.
  docker kill "$name" >/dev/null 2>&1
  wait "$logs"; logs_rc=$?
  [ "$logs_rc" -ne 0 ] && echo "log-pipeline-exit=$logs_rc" >> "$out/job.log"
  check_logs_rc
  echo "docker wait failed (exit $wait_rc)" >> "$out/job.log"
  exit 2
fi
wait "$logs"; logs_rc=$?
[ "$logs_rc" -ne 0 ] && echo "log-pipeline-exit=$logs_rc" >> "$out/job.log"
check_logs_rc
echo "agent-exit=$code" >> "$out/job.log"
[ "$timed_out" = 1 ] && exit 124

# Package in a fresh container: every agent process is gone, no network,
# the workspace read-only, outputs only into $out.
#
# From here on $out is mounted into a running container, and what runs there
# (kicad-cli on the agent's board) can replace any path in it -- with a
# symlink to a host file, say. So nothing below reads, appends to or keeps
# control state in a path under $out while the container lives:
# - package.log is opened on fd 3 BEFORE the container exists, and tee writes
#   to that open file (/dev/fd/3), never re-resolving the path;
# - the exit code comes from `docker inspect`, not a file;
# - an interrupt reports on stderr, not into $out/job.log.
# The container is detached and waited on by a background `timeout 900
# docker wait` (CIRCUIT_PACKAGE_TIMEOUT overrides the 900), which, like the
# agent's wait above, the INT/TERM trap can interrupt; a wall-clock overrun
# kills it and exits 124.
pkg="$name-pkg"
pkg_timeout="${CIRCUIT_PACKAGE_TIMEOUT:-900}"
exec 3>"$out/package.log" || exit 2
trap 'docker rm -f "$name" "$pkg" >/dev/null 2>&1' EXIT
trap 'docker rm -f "$name" "$pkg" >/dev/null 2>&1; echo "interrupted during packaging" >&2; exit 130' INT TERM
# `docker run -d` returns as soon as the container starts, so this $(...) is
# short; a start failure is logged like the container's own output.
if ! started=$(docker run -d --name "$pkg" --network none --read-only --tmpfs /tmp:rw,size=512m \
  --cap-drop ALL --security-opt no-new-privileges --user "$user" --memory 2g --pids-limit 128 \
  -e OPENROUTER_API_KEY -e CIRCUIT_OUT=/out \
  -v "$job:/workspace:ro" -v "$out:/out" \
  --entrypoint python3 "$runner" -m circuit_mcp.package 2>&1); then
  printf 'packaging container failed to start: %s\n' "$started" | tee /dev/fd/3
  exec 3>&-
  exit 2
fi
docker logs -f "$pkg" 2>&1 | tee /dev/fd/3 &
pkg_logs=$!
timeout "$pkg_timeout" docker wait "$pkg" >/dev/null 2>&1 &
pkg_waiter=$!
wait "$pkg_waiter"
pkg_wait_rc=$?
if [ "$pkg_wait_rc" -ne 0 ]; then
  docker kill "$pkg" >/dev/null 2>&1
  wait "$pkg_logs"
  exec 3>&-
  if [ "$pkg_wait_rc" -eq 124 ]; then
    echo "packaging timed out after $pkg_timeout s" >&2
    exit 124
  fi
  echo "docker wait on the packaging container failed (exit $pkg_wait_rc)" >&2
  exit 2
fi
status=$(docker inspect -f '{{.State.ExitCode}}' "$pkg" 2>/dev/null) || status=2
wait "$pkg_logs"
exec 3>&-
exit "$status"
