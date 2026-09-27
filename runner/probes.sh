#!/bin/sh
# Isolation probes (board-service spec §6.1.2). Each line: PASS|FAIL <what>.
# Exit 1 if any probe fails.
command -v curl >/dev/null 2>&1 || { echo "FAIL curl missing (every must-fail probe would pass vacuously)"; exit 1; }
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; fails=$((fails + 1)); }
skip() { echo "SKIP $1"; }
expect_fail() { if sh -c "$2" >/dev/null 2>&1; then fail "$1"; else pass "$1"; fi; }
expect_ok()   { if sh -c "$2" >/dev/null 2>&1; then pass "$1"; else fail "$1"; fi; }
# A bare "is this TCP port open" check, for a host:port with no HTTP/TLS
# server on it. `curl telnet://host:port` connects instantly on success but
# then blocks waiting for the peer to send data or close -- so on a real,
# silent listener (Postgres, Ollama, a plain socket) that never happens, and
# curl's own -m timeout fires first. That timeout (exit 28) is indistinguishable
# by exit code alone from a refused or firewalled connection (exit 7/28 before
# ever connecting), so the must-fail check above passed vacuously on every
# reachable port: a scratch network with the isolated gateway option removed
# proved this (the control run), showing PASS where a real hole demanded FAIL.
# Fixed by reading curl's own verbose trace for the connect, not its exit code.
# That trace's wording is itself curl-version-dependent (older curl prints
# "Connected to ...", newer curl prints "Established connection ..."), so
# both are accepted, AND a positive control (below, first) proves this
# specific runner's curl actually matches one of them against a target that
# is always reachable -- if neither wording matches, the whole mechanism is
# blind and every "network gateway" line below would pass vacuously again.
tcp_connected() {
  out=$(curl -s -v -m 3 --noproxy '*' "telnet://$1" 2>&1 </dev/null)
  printf '%s\n' "$out" | grep -Eq "Connected to|Established connection"
}
expect_fail_tcp() { if tcp_connected "$2"; then fail "$1"; else pass "$1"; fi; }
# 172.31.99.0/24, not the pre-rename runner's 172.30.99.0/24: this host still
# runs that runner's circuit-jobs/circuit-egress, which must be left alone,
# so etchwork-jobs (etchwork/libexec/job.sh) uses a different, non-overlapping
# subnet -- both sides of this address must stay in step with it.
gw=172.31.99.1

if tcp_connected 172.31.99.2:8888; then pass "gateway-probe positive control"; else fail "gateway-probe positive control"; fi
expect_ok   "openrouter via proxy"            "curl -fsS -m 20 https://openrouter.ai/api/v1/models -o /dev/null"
expect_fail "example.com via proxy"           "curl -fsS -m 10 https://example.com -o /dev/null"
expect_fail "example.com direct"              "curl -fsS -m 10 --noproxy '*' https://example.com -o /dev/null"
# Tinyproxy's ConnectPort allowlist (443 only) means a CONNECT to :22 must be
# refused with an explicit 403, not just "the transfer as a whole failed" --
# an exit-code check can't tell a proxy-refused CONNECT apart from any other
# curl failure, so read the CONNECT response code directly.
connect22=$(curl -s -m 10 -o /dev/null -w '%{http_connect}' https://openrouter.ai:22/)
if [ "$connect22" = "403" ]; then pass "CONNECT openrouter.ai:22"; else fail "CONNECT openrouter.ai:22 (code=$connect22)"; fi
expect_fail "metadata 169.254.169.254 direct" "curl -fsS -m 5 --noproxy '*' http://169.254.169.254/ -o /dev/null"
expect_fail "metadata via proxy"              "curl -fsS -m 5 http://169.254.169.254/ -o /dev/null"
for port in 22 3000 5432 11434; do
  expect_fail_tcp "network gateway $gw:$port" "$gw:$port"
done
if [ -n "${PROBE_NEIGHBOUR:-}" ]; then
  expect_fail "neighbour $PROBE_NEIGHBOUR direct" "curl -fsS -m 5 --noproxy '*' http://$PROBE_NEIGHBOUR:8080/ -o /dev/null"
  expect_fail "neighbour via proxy"             "curl -fsS -m 5 http://$PROBE_NEIGHBOUR:8080/ -o /dev/null"
else
  skip "neighbour (PROBE_NEIGHBOUR unset)"
fi
expect_fail "DNS example.com"                 "getent hosts example.com"
expect_fail "docker socket"                   "test -e /var/run/docker.sock"
expect_fail "write /usr"                      "touch /usr/probe"
expect_fail "write /etc"                      "touch /etc/probe"
expect_ok   "write /workspace"                "touch /workspace/.probe && rm /workspace/.probe"
expect_ok   "write /tmp"                      "touch /tmp/.probe"
if [ "$(id -u)" != 0 ]; then pass "not root ($(id -u))"; else fail "running as root"; fi
if grep -Eq '^CapEff:[[:space:]]*0+$' /proc/self/status; then pass "no effective capabilities"; else fail "capabilities: $(grep CapEff /proc/self/status)"; fi
if grep -Eq '^CapBnd:[[:space:]]*0+$' /proc/self/status; then pass "empty bounding set"; else fail "bounding: $(grep CapBnd /proc/self/status)"; fi
if grep -Eq '^NoNewPrivs:[[:space:]]*1$' /proc/self/status; then pass "no_new_privs"; else fail "no_new_privs not set"; fi
echo "probes-failed=$fails"
[ "$fails" -eq 0 ]
