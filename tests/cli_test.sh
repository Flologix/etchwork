#!/bin/sh
# The etchwork CLI's own test suite (etchwork extraction design §7.1 / task
# 2 brief step 5). Prints PASS/FAIL per check; exits 1 if any FAIL.
set -u
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
etchwork="$root/etchwork"
scratch="${TMPDIR:-/tmp}/etchwork-cli-test.$$"
mkdir -p "$scratch"
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT

fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

json_get() {
  # json_get <file> <python-expr-on-d>
  python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print('<error: %s>' % e)
    sys.exit(0)
print($2)
" "$1"
}

# --- Build the engine image once; every init check below skips --build. ---
build_out=$("$etchwork" build 2>&1)
image=$(printf '%s\n' "$build_out" | tail -1)
case "$image" in
  IMAGE=*) ;;
  *) fail "etchwork build" "no IMAGE= line: $build_out"; echo "$fails failures"; exit 1 ;;
esac
image=${image#IMAGE=}
echo "PASS etchwork build ($image)"

# --- 1: help and bad input ---
if "$etchwork" help >/dev/null 2>&1; then pass "help exits 0"; else fail "help exits 0" "exit $?"; fi
"$etchwork" bogus >/dev/null 2>&1
rc=$?
if [ "$rc" = 2 ]; then pass "bogus command exits 2"; else fail "bogus command exits 2" "exit $rc"; fi

# --- 2: a symlink on PATH ---
mkdir -p "$scratch/bin"
ln -s "$etchwork" "$scratch/bin/etchwork"
if "$scratch/bin/etchwork" help >/dev/null 2>&1; then pass "symlink help exits 0"; else fail "symlink help exits 0" "exit $?"; fi
link_init_dir="$scratch/link-init"
mkdir -p "$link_init_dir"
link_out=$("$scratch/bin/etchwork" init "$link_init_dir" --client claude-code 2>&1)
link_image=$(printf '%s\n' "$link_out" | tail -1)
direct_init_dir="$scratch/direct-init"
mkdir -p "$direct_init_dir"
direct_out=$("$etchwork" init "$direct_init_dir" --client claude-code 2>&1)
direct_image=$(printf '%s\n' "$direct_out" | tail -1)
if [ "$link_image" = "IMAGE=$image" ] && [ "$direct_image" = "IMAGE=$image" ] && [ "$link_image" = "$direct_image" ]; then
  pass "symlink init prints the same IMAGE= as the direct path"
else
  fail "symlink init prints the same IMAGE= as the direct path" "link=$link_image direct=$direct_image want=IMAGE=$image"
fi

# --- 3: init --client claude-code on a fresh dir (not created beforehand) ---
d3="$scratch/cc-fresh/new project"
out3=$("$etchwork" init "$d3" --client claude-code 2>&1)
last3=$(printf '%s\n' "$out3" | tail -1)
if [ "$last3" = "IMAGE=$image" ]; then pass "cc fresh: last line IMAGE="; else fail "cc fresh: last line IMAGE=" "$last3"; fi
if [ -f "$d3/.mcp.json" ]; then pass "cc fresh: .mcp.json exists"; else fail "cc fresh: .mcp.json exists" "missing"; fi
has_etchwork=$(json_get "$d3/.mcp.json" "'etchwork' in d.get('mcpServers', {})")
[ "$has_etchwork" = True ] && pass "cc fresh: mcpServers.etchwork present" || fail "cc fresh: mcpServers.etchwork present" "$has_etchwork"
last_arg=$(json_get "$d3/.mcp.json" "d['mcpServers']['etchwork']['args'][-1]")
[ "$last_arg" = "$image" ] && pass "cc fresh: args ends with the IMAGE tag" || fail "cc fresh: args ends with the IMAGE tag" "$last_arg != $image"
# A missing image must fail at once, not become a Docker Hub pull that times out.
pull3=$(json_get "$d3/.mcp.json" "' '.join(d['mcpServers']['etchwork']['args'])")
case "$pull3" in
  *"--pull never"*) pass "cc fresh: docker run never pulls" ;;
  *) fail "cc fresh: docker run never pulls" "$pull3" ;;
esac
skill_name=$(grep -m1 '^name:' "$d3/.claude/skills/etchwork/SKILL.md" 2>/dev/null)
[ "$skill_name" = "name: etchwork" ] && pass "cc fresh: skill has name: etchwork" || fail "cc fresh: skill has name: etchwork" "$skill_name"
allow_has=$(json_get "$d3/.claude/settings.json" "'mcp__etchwork__*' in d.get('permissions', {}).get('allow', [])")
[ "$allow_has" = True ] && pass "cc fresh: settings.json allows mcp__etchwork__*" || fail "cc fresh: settings.json allows mcp__etchwork__*" "$allow_has"

# --- 4: a re-run keeps existing keys ---
python3 -c "
import json
d = json.load(open('$d3/.mcp.json'))
d['mcpServers']['other-server'] = {'command': 'true', 'args': []}
json.dump(d, open('$d3/.mcp.json', 'w'), indent=2)
"
python3 -c "
import json
d = json.load(open('$d3/.claude/settings.json'))
d['model'] = 'claude-x'
d.setdefault('permissions', {}).setdefault('allow', [])
if 'Bash(ls)' not in d['permissions']['allow']:
    d['permissions']['allow'].append('Bash(ls)')
json.dump(d, open('$d3/.claude/settings.json', 'w'), indent=2)
"
"$etchwork" init "$d3" --client claude-code >/dev/null 2>&1
other_kept=$(json_get "$d3/.mcp.json" "'other-server' in d.get('mcpServers', {})")
[ "$other_kept" = True ] && pass "rerun: other-server kept" || fail "rerun: other-server kept" "$other_kept"
model_kept=$(json_get "$d3/.claude/settings.json" "d.get('model')")
[ "$model_kept" = "claude-x" ] && pass "rerun: model key kept" || fail "rerun: model key kept" "$model_kept"
bash_ls_kept=$(json_get "$d3/.claude/settings.json" "'Bash(ls)' in d.get('permissions', {}).get('allow', [])")
[ "$bash_ls_kept" = True ] && pass "rerun: permissions.allow Bash(ls) kept" || fail "rerun: permissions.allow Bash(ls) kept" "$bash_ls_kept"
allow_count=$(json_get "$d3/.claude/settings.json" "d.get('permissions', {}).get('allow', []).count('mcp__etchwork__*')")
[ "$allow_count" = 1 ] && pass "rerun: mcp__etchwork__* exactly once" || fail "rerun: mcp__etchwork__* exactly once" "count=$allow_count"

# --- 5: legacy migration ---
d5a="$scratch/legacy-a"
mkdir -p "$d5a/.claude/skills/circuit"
cat > "$d5a/.mcp.json" <<EOF
{"mcpServers": {"circuit": {"command": "docker", "args": ["run", "-i", "--rm", "flai-circuit:0.3"]}}}
EOF
cat > "$d5a/.claude/skills/circuit/SKILL.md" <<'EOF'
---
name: circuit
description: old
---
old skill
EOF
out5a=$("$etchwork" init "$d5a" --client claude-code 2>&1)
if printf '%s\n' "$out5a" | grep -q "removed legacy circuit server"; then
  pass "legacy: printed removed legacy circuit server"
else
  fail "legacy: printed removed legacy circuit server" "$out5a"
fi
circuit_gone=$(json_get "$d5a/.mcp.json" "'circuit' in d.get('mcpServers', {})")
[ "$circuit_gone" = False ] && pass "legacy: .mcp.json has no circuit key" || fail "legacy: .mcp.json has no circuit key" "$circuit_gone"
if [ ! -e "$d5a/.claude/skills/circuit" ]; then pass "legacy: old skill folder removed"; else fail "legacy: old skill folder removed" "still present"; fi

d5b="$scratch/legacy-b"
mkdir -p "$d5b"
cat > "$d5b/.mcp.json" <<'EOF'
{"mcpServers": {"circuit": {"command": "docker", "args": ["run", "-i", "--rm", "my-own-image:1"]}}}
EOF
"$etchwork" init "$d5b" --client claude-code >/dev/null 2>&1
circuit_kept=$(json_get "$d5b/.mcp.json" "'circuit' in d.get('mcpServers', {})")
[ "$circuit_kept" = True ] && pass "legacy: a user's own circuit server is kept" || fail "legacy: a user's own circuit server is kept" "$circuit_kept"

# --- 6: malformed JSON ---
d6="$scratch/malformed"
mkdir -p "$d6"
printf '%s' '{not json' > "$d6/.mcp.json"
before=$(cat "$d6/.mcp.json")
before_sum=$(sha256sum "$d6/.mcp.json" | cut -d' ' -f1)
out6=$("$etchwork" init "$d6" --client claude-code 2>&1)
rc6=$?
after_sum=$(sha256sum "$d6/.mcp.json" | cut -d' ' -f1)
if [ "$rc6" = 1 ]; then pass "malformed JSON: exits 1"; else fail "malformed JSON: exits 1" "exit $rc6"; fi
if printf '%s\n' "$out6" | grep -q "is not valid JSON; fix or remove it, then re-run"; then
  pass "malformed JSON: one-line message"
else
  fail "malformed JSON: one-line message" "$out6"
fi
if [ "$before_sum" = "$after_sum" ]; then pass "malformed JSON: file byte-identical"; else fail "malformed JSON: file byte-identical" "changed"; fi
if [ -e "$d6/.claude" ]; then
  fail "malformed JSON: no partial .claude/ directory" "found $d6/.claude"
else
  pass "malformed JSON: no partial .claude/ directory"
fi

# --- 6b: JSON that parses but has the wrong shape ---
d6b="$scratch/wrong-shape"
mkdir -p "$d6b"
printf '%s' '{"mcpServers": "x"}' > "$d6b/.mcp.json"
before6b_sum=$(sha256sum "$d6b/.mcp.json" | cut -d' ' -f1)
out6b=$("$etchwork" init "$d6b" --client claude-code 2>&1)
rc6b=$?
after6b_sum=$(sha256sum "$d6b/.mcp.json" | cut -d' ' -f1)
if [ "$rc6b" = 1 ]; then pass "wrong-shape JSON: exits 1"; else fail "wrong-shape JSON: exits 1" "exit $rc6b"; fi
if printf '%s\n' "$out6b" | grep -q "is not valid JSON; fix or remove it, then re-run"; then
  pass "wrong-shape JSON: one-line message"
else
  fail "wrong-shape JSON: one-line message" "$out6b"
fi
if printf '%s\n' "$out6b" | grep -qi "traceback"; then
  fail "wrong-shape JSON: no traceback" "$out6b"
else
  pass "wrong-shape JSON: no traceback"
fi
if [ "$before6b_sum" = "$after6b_sum" ]; then pass "wrong-shape JSON: file byte-identical"; else fail "wrong-shape JSON: file byte-identical" "changed"; fi

# --- 7: path handling ---
mkdir -p "$scratch/has:colon"
"$etchwork" init "$scratch/has:colon" --client claude-code >/dev/null 2>&1
rc7=$?
[ "$rc7" = 2 ] && pass "path with ':' refused (exit 2)" || fail "path with ':' refused (exit 2)" "exit $rc7"
mkdir -p "$scratch/has space"
out7b=$("$etchwork" init "$scratch/has space" --client claude-code 2>&1)
rc7b=$?
[ "$rc7b" = 0 ] && pass "path with space works" || fail "path with space works" "exit $rc7b: $out7b"

# --- 8: codex twice ---
d8="$scratch/codex-dir"
mkdir -p "$d8"
"$etchwork" init "$d8" --client codex >/dev/null 2>&1
"$etchwork" init "$d8" --client codex >/dev/null 2>&1
count8=$(grep -c '<!-- etchwork-skill:begin -->' "$d8/AGENTS.md" 2>/dev/null || echo 0)
[ "$count8" = 1 ] && pass "codex twice: exactly one etchwork-skill:begin" || fail "codex twice: exactly one etchwork-skill:begin" "count=$count8"

out8=$("$etchwork" init "$d8" --client codex 2>&1)
stanza8=$(printf '%s\n' "$out8" | sed -n '/^\[mcp_servers.etchwork\]/,/^tool_timeout_sec/p')
args8=$(printf '%s\n' "$stanza8" | python3 -c "import sys, tomllib; print(' '.join(tomllib.loads(sys.stdin.read())['mcp_servers']['etchwork']['args']))" 2>&1)
case "$args8" in
  *"--pull never"*) pass "codex: the printed stanza parses and never pulls" ;;
  *) fail "codex: the printed stanza parses and never pulls" "$args8" ;;
esac

# --- 9: flai client ---
d9="$scratch/flai-dir"
mkdir -p "$d9"
"$etchwork" init "$d9" --client flai >/dev/null 2>&1
f9="$d9/.flai-code/skills/etchwork/SKILL.md"
if [ -f "$f9" ] && grep -q '^name: etchwork$' "$f9" && grep -q '^## In flai$' "$f9"; then
  pass "flai: skill file has the skill and the In flai heading"
else
  fail "flai: skill file has the skill and the In flai heading" "missing content in $f9"
fi
shared_false=$(json_get "$d9/.mcp.json" "d['mcpServers']['etchwork'].get('shared')")
[ "$shared_false" = False ] && pass "flai: mcp entry has shared: false" || fail "flai: mcp entry has shared: false" "$shared_false"

# --- 9b: re-running init doesn't overwrite an edited skill ---
d9b="$scratch/edited-skill"
mkdir -p "$d9b"
"$etchwork" init "$d9b" --client claude-code >/dev/null 2>&1
skill_file9b="$d9b/.claude/skills/etchwork/SKILL.md"
printf '\n<!-- user edit -->\n' >> "$skill_file9b"
edited_sum9b=$(sha256sum "$skill_file9b" | cut -d' ' -f1)
out9b=$("$etchwork" init "$d9b" --client claude-code 2>&1)
after_sum9b=$(sha256sum "$skill_file9b" | cut -d' ' -f1)
if [ "$edited_sum9b" = "$after_sum9b" ]; then
  pass "edited skill: file left untouched"
else
  fail "edited skill: file left untouched" "checksum changed"
fi
if printf '%s\n' "$out9b" | grep -q "differs from the shipped skill"; then
  pass "edited skill: printed a kept/not-overwritten message"
else
  fail "edited skill: printed a kept/not-overwritten message" "$out9b"
fi

# --- 10: no Docker ---
d10="$scratch/no-docker"
mkdir -p "$d10"
before10=$(find "$d10" -mindepth 1 | sort)
out10=$(DOCKER_HOST=unix:///nonexistent.sock "$etchwork" init "$d10" --client claude-code 2>&1)
rc10=$?
after10=$(find "$d10" -mindepth 1 | sort)
[ "$rc10" = 1 ] && pass "no docker: exits 1" || fail "no docker: exits 1" "exit $rc10"
if printf '%s\n' "$out10" | grep -q "Docker is not available"; then
  pass "no docker: message"
else
  fail "no docker: message" "$out10"
fi
[ "$before10" = "$after10" ] && pass "no docker: target dir has no new files" || fail "no docker: target dir has no new files" "before=[$before10] after=[$after10]"

# --- 11: --client left out: init detects the agent ---
# A PATH holding only the tools init needs, plus a stub for each agent CLI
# named: the real PATH may already have claude on it, which would decide
# every case below.
agent_path() {
  p="$scratch/path-${*:-none}"
  p=$(printf '%s' "$p" | tr ' ' '-')
  mkdir -p "$p"
  for t in docker python3 git sha256sum shasum sort xargs cut grep id dirname \
           readlink cat cp rm mkdir touch cmp mktemp tr sed; do
    w=$(command -v "$t" 2>/dev/null) && ln -sf "$w" "$p/$t"
  done
  for a in "$@"; do
    printf '#!/bin/sh\nexit 0\n' > "$p/$a"
    chmod +x "$p/$a"
  done
  printf '%s' "$p"
}

d11a="$scratch/detect-only-codex"
out11a=$(PATH=$(agent_path codex) "$etchwork" init "$d11a" </dev/null 2>&1)
rc11a=$?
if [ "$rc11a" = 0 ] && [ -f "$d11a/AGENTS.md" ] && [ ! -e "$d11a/.mcp.json" ] \
   && printf '%s\n' "$out11a" | grep -q "^client: codex (the only supported agent on PATH)"; then
  pass "detect: the only agent on PATH (codex)"
else
  fail "detect: the only agent on PATH (codex)" "exit $rc11a: $out11a"
fi

d11b="$scratch/detect-folder-wins"
mkdir -p "$d11b/.claude"
out11b=$(PATH=$(agent_path claude codex) "$etchwork" init "$d11b" </dev/null 2>&1)
rc11b=$?
if [ "$rc11b" = 0 ] && [ -f "$d11b/.mcp.json" ] && [ ! -e "$d11b/AGENTS.md" ] \
   && printf '%s\n' "$out11b" | grep -q "^client: claude-code (this folder already has its config)"; then
  pass "detect: the folder's own config beats PATH"
else
  fail "detect: the folder's own config beats PATH" "exit $rc11b: $out11b"
fi

d11c="$scratch/detect-ambiguous"
out11c=$(PATH=$(agent_path claude codex) "$etchwork" init "$d11c" </dev/null 2>&1)
rc11c=$?
if [ "$rc11c" = 2 ] && [ ! -e "$d11c" ] \
   && printf '%s\n' "$out11c" | grep -q -- "--client" \
   && printf '%s\n' "$out11c" | grep -q "found claude-code, codex"; then
  pass "detect: two agents and no terminal exits 2, names them, writes nothing"
else
  fail "detect: two agents and no terminal exits 2, names them, writes nothing" "exit $rc11c: $out11c"
fi

d11d="$scratch/detect-none"
out11d=$(PATH=$(agent_path) "$etchwork" init "$d11d" </dev/null 2>&1)
rc11d=$?
if [ "$rc11d" = 2 ] && [ ! -e "$d11d" ] && printf '%s\n' "$out11d" | grep -q -- "--client" \
   && printf '%s\n' "$out11d" | grep -q "no supported agent"; then
  pass "detect: no agent found and no terminal exits 2"
else
  fail "detect: no agent found and no terminal exits 2" "exit $rc11d: $out11d"
fi

d11e="$scratch/detect-flag-wins"
out11e=$(PATH=$(agent_path codex) "$etchwork" init "$d11e" --client claude-code </dev/null 2>&1)
rc11e=$?
if [ "$rc11e" = 0 ] && [ -f "$d11e/.mcp.json" ] && [ ! -e "$d11e/AGENTS.md" ] \
   && ! printf '%s\n' "$out11e" | grep -q "^client:"; then
  pass "detect: an explicit --client is used as given"
else
  fail "detect: an explicit --client is used as given" "exit $rc11e: $out11e"
fi

d11f="$scratch/detect-bad-flag"
out11f=$("$etchwork" init "$d11f" --client vscode </dev/null 2>&1)
rc11f=$?
[ "$rc11f" = 2 ] && pass "detect: an unknown --client still exits 2" || fail "detect: an unknown --client still exits 2" "exit $rc11f: $out11f"

echo "$fails failures"
[ "$fails" -eq 0 ]
