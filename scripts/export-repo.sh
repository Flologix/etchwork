#!/bin/sh
# Create the standalone Etchwork repo from this tree (spec §6). Run once, by the user.
# Usage: export-repo.sh <target dir> [<remote url>]
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
target="${1:?usage: export-repo.sh <target dir> [<remote url>]}"; remote="${2:-}"
name=$(git config --get user.name || true); email=$(git config --get user.email || true)
if [ -z "$name" ] || [ -z "$email" ]; then
  echo "git user.name / user.email are not configured; set them (git config --global user.name ...) and re-run" >&2
  exit 1
fi
if [ -e "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
  echo "refusing: $target exists and is not empty" >&2; exit 1
fi

# The provenance SHA comes from NOTICE, not HEAD: HEAD on this branch is
# never pushed (the branch is squashed or dropped after export), so citing
# it would point at a commit nobody outside this checkout can ever resolve.
# NOTICE names the public commit the engine code actually matches.
notice="$root/NOTICE"
src_sha=$(grep -oE 'commit [0-9a-f]{7,40}' "$notice" 2>/dev/null | head -1 | awk '{print $2}')
if [ -z "$src_sha" ]; then
  echo "cannot find a commit SHA in $notice (expected 'commit <7-40 hex chars>'); fix NOTICE and re-run" >&2
  exit 1
fi

# Copy into a scratch dir beside the target first, and only mv it into place
# once the copy and the commit both succeed -- so a mid-copy failure (e.g. a
# cached-but-deleted file under set -e) leaves nothing behind at $target for
# a re-run to trip over.
target_parent=$(dirname "$target")
mkdir -p "$target_parent"
target_parent=$(CDPATH= cd -- "$target_parent" && pwd)
tmp=$(mktemp -d "$target_parent/.etchwork-export.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

(cd "$root" && git ls-files --cached --others --exclude-standard .) | while IFS= read -r f; do
  mkdir -p "$tmp/$(dirname "$f")"; cp -p "$root/$f" "$tmp/$f"
done
git -C "$tmp" init -q -b main
git -C "$tmp" add -A
git -C "$tmp" commit -q -m "Initial import from flai (FlologixAI/flai@$src_sha)

Etchwork was developed inside the flai repository as its circuit feature;
see NOTICE. Full history of that code remains in FlologixAI/flai."
commit_sha=$(git -C "$tmp" rev-parse --short HEAD)

[ -e "$target" ] && rmdir "$target"
mv "$tmp" "$target"
trap - EXIT

echo "created $target at $commit_sha"
if [ -n "$remote" ]; then
  git -C "$target" remote add origin "$remote"
  git -C "$target" push -u origin main
fi
