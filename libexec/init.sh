#!/bin/sh
# Set a project folder up to use the etchwork engine, for a given client
# (etchwork extraction design §4.1). Usage:
#   etchwork init <dir> [--client claude-code|codex|flai] [--build]
set -eu
dir="${1:?usage: etchwork init <dir> [--client claude-code|codex|flai] [--build]}"; shift
client=""; build=0
while [ $# -gt 0 ]; do
  case "$1" in
    --client) client="${2:?--client needs a value}"; shift 2 ;;
    --build) build=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Without --client, which agent the project is for. The folder's own config
# is the strongest evidence -- someone already chose -- then the only
# supported agent installed. Anything else is asked, never guessed: a wrong
# pick writes config for an agent that will never read it. Decided before
# the folder is created, so a refusal leaves nothing behind.
agents_in_dir() {
  [ -d "$1" ] || return 0
  [ -d "$1/.claude" ] && printf ' claude-code'
  [ -f "$1/AGENTS.md" ] && printf ' codex'
  [ -d "$1/.flai-code" ] && printf ' flai'
  return 0
}
agents_on_path() {
  command -v claude >/dev/null 2>&1 && printf ' claude-code'
  command -v codex >/dev/null 2>&1 && printf ' codex'
  command -v flai >/dev/null 2>&1 && printf ' flai'
  return 0
}
only_one() {
  # shellcheck disable=SC2086
  set -- $1
  [ $# -eq 1 ] && printf '%s' "$1"
  return 0
}
if [ -z "$client" ]; then
  in_dir=$(agents_in_dir "$dir"); on_path=$(agents_on_path)
  client=$(only_one "$in_dir"); why="this folder already has its config"
  if [ -z "$client" ] && [ -z "$in_dir" ]; then
    client=$(only_one "$on_path"); why="the only supported agent on PATH"
  fi
  if [ -z "$client" ]; then
    candidates=${in_dir:-$on_path}
    listed=""
    for c in $candidates; do listed="${listed:+$listed, }$c"; done
    if [ -n "$candidates" ]; then
      found="found $listed"
    else
      found="no supported agent (claude, codex, flai) on PATH"
      candidates="claude-code codex flai"
    fi
    if [ -t 0 ] && [ -t 2 ]; then
      echo "Which agent is this project for? ($found)" >&2
      n=0
      for c in $candidates; do n=$((n + 1)); echo "  $n) $c" >&2; done
      printf 'number: ' >&2
      read -r choice || choice=""
      case "$choice" in ''|*[!0-9]*) choice=0 ;; esac
      if [ "$choice" -lt 1 ] || [ "$choice" -gt "$n" ]; then
        echo "no agent chosen; pass --client claude-code|codex|flai" >&2; exit 2
      fi
      # shellcheck disable=SC2086
      client=$(printf '%s\n' $candidates | sed -n "${choice}p"); why="chosen"
    else
      echo "can't tell which agent this project is for: $found. Pass --client claude-code|codex|flai" >&2
      exit 2
    fi
  fi
  echo "client: $client ($why)"
fi

case "$client" in
  claude-code|codex|flai) ;;
  *) echo "--client must be claude-code, codex or flai" >&2; exit 2 ;;
esac

case "$dir" in *:*) echo "the project path contains ':', which docker -v cannot mount: $dir" >&2; exit 2 ;; esac

# Checked before any file is written: a dead daemon must never leave the
# target directory half set up.
if ! docker version >/dev/null 2>&1; then
  echo "Docker is not available (is the daemon running?)" >&2
  exit 1
fi

# A new project's folder may not exist yet.
mkdir -p "$dir"
dir=$(CDPATH= cd -- "$dir" && pwd)
case "$dir" in *:*) echo "the project path contains ':', which docker -v cannot mount: $dir" >&2; exit 2 ;; esac

. "$ETCHWORK_ROOT/libexec/build.sh" --lib
image="$(engine_image)"
if ! docker image inspect "$image" >/dev/null 2>&1; then
  if [ "$build" = 1 ]; then docker build -t "$image" "$ETCHWORK_ROOT/engine"
  else echo "image $image is not built; re-run with --build" >&2; exit 1; fi
fi

uid=$(id -u); gid=$(id -g)
skill="$ETCHWORK_ROOT/skill/SKILL.md"
addendum="$ETCHWORK_ROOT/skill/flai-addendum.md"
json_merge="$ETCHWORK_ROOT/libexec/json_merge.py"

is_legacy_circuit_skill() {
  # $1: a SKILL.md path. True if it's the old circuit skill (frontmatter
  # "name: circuit"), so a plain user file of the same name is left alone.
  [ -f "$1" ] && grep -q '^name: circuit$' "$1"
}

write_skill() {
  # write_skill <dest> <shipped-content-file>. If <dest> already exists and
  # differs from the shipped content, a user has presumably edited it --
  # leave it alone and say so, rather than clobbering their edits on every
  # re-run of `etchwork init`.
  dest="$1"; content="$2"
  mkdir -p "$(dirname "$dest")"
  if [ -f "$dest" ] && ! cmp -s "$content" "$dest"; then
    echo "kept $dest (it differs from the shipped skill; not overwritten)"
  else
    cp "$content" "$dest"
  fi
}

case "$client" in
  claude-code)
    # Both JSON files this client touches are checked before anything is
    # written, the way the Docker check above guards the whole command: a
    # malformed .mcp.json must not leave a half-done .claude/skills/etchwork
    # behind.
    python3 "$json_merge" validate "$dir/.mcp.json" "$dir/.claude/settings.json"
    write_skill "$dir/.claude/skills/etchwork/SKILL.md" "$skill"
    python3 "$json_merge" mcp "$dir/.mcp.json" "$dir" "$uid" "$gid" "$image"
    python3 "$json_merge" allow "$dir/.claude/settings.json" 'mcp__etchwork__*'
    if is_legacy_circuit_skill "$dir/.claude/skills/circuit/SKILL.md"; then
      rm -rf "$dir/.claude/skills/circuit"
      echo "removed legacy circuit skill"
    fi
    echo "wrote $dir/.mcp.json and $dir/.claude/skills/etchwork/SKILL.md"
    ;;
  codex)
    begin="<!-- etchwork-skill:begin -->"; end="<!-- etchwork-skill:end -->"
    touch "$dir/AGENTS.md"
    python3 - "$dir/AGENTS.md" "$skill" "$begin" "$end" <<'PY'
import pathlib, re, sys
agents, skill, begin, end = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4]
text = agents.read_text()
block = f"{begin}\n{skill.read_text()}\n{end}\n"
text = re.sub(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "", text, flags=re.S)
agents.write_text(text.rstrip("\n") + ("\n\n" if text.strip() else "") + block)
PY
    echo "wrote the etchwork skill into $dir/AGENTS.md; add this to ~/.codex/config.toml:"
    # Emitted through python3's json.dumps, not printf: $dir goes into a TOML
    # string, and json.dumps escapes backslashes/quotes the same way TOML
    # basic strings require, so a path containing '"' or '\' still prints
    # valid TOML instead of a broken config file.
    python3 - "$uid" "$gid" "$dir" "$image" <<'PY'
import json, sys
uid, gid, dir_, image = sys.argv[1:5]
args = ["run", "-i", "--rm", "--network", "none", "--user", f"{uid}:{gid}",
        "-v", f"{dir_}:/workspace", image]
print("[mcp_servers.etchwork]")
print(f"command = {json.dumps('docker')}")
print(f"args = {json.dumps(args)}")
print("startup_timeout_sec = 120")
print("tool_timeout_sec = 1800")
PY
    ;;
  flai)
    python3 "$json_merge" mcp "$dir/.mcp.json" "$dir" "$uid" "$gid" "$image" --flai
    flai_skill_tmp=$(mktemp)
    trap 'rm -f "$flai_skill_tmp"' EXIT
    { cat "$skill"; printf '\n'; cat "$addendum"; } > "$flai_skill_tmp"
    write_skill "$dir/.flai-code/skills/etchwork/SKILL.md" "$flai_skill_tmp"
    rm -f "$flai_skill_tmp"
    trap - EXIT
    if is_legacy_circuit_skill "$dir/.flai-code/skills/circuit/SKILL.md"; then
      rm -rf "$dir/.flai-code/skills/circuit"
      echo "removed legacy circuit skill"
    fi
    echo "wrote $dir/.mcp.json and $dir/.flai-code/skills/etchwork/SKILL.md"
    ;;
esac
echo "IMAGE=$image"
