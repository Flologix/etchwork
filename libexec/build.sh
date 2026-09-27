#!/bin/sh
# Builds the etchwork-engine image, tagged by a content hash of engine/.
# `. "$ETCHWORK_ROOT/libexec/build.sh" --lib` sources this without building,
# to reuse engine_hash / engine_image from the other libexec scripts.
#
# The gate below reads $0, not "$1" = --lib: dash, this host's /bin/sh, does
# not pass arguments through `.` the way bash does (a `. file --lib` sourced
# under dash leaves "$1" as whatever the *caller's* "$1" already was, so a
# "${1:-} = --lib" check here would silently run a real `docker build` on
# every sourcing script's own invocation -- masked by the build cache into
# looking like a no-op). Sourcing never changes $0 in either shell, so
# comparing it to this script's own path is a portable way to tell "sourced
# for its functions" from "run directly as `etchwork build`".
set -eu

# Linux (macOS untested). sha256sum isn't on older macOS by default, which
# ships shasum instead; fall back to that so this at least has a chance there.
if command -v sha256sum >/dev/null 2>&1; then
  sha256_prog="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  sha256_prog="shasum -a 256"
else
  echo "need sha256sum or shasum -a 256 on PATH" >&2
  exit 1
fi

engine_hash() {
  # git ls-files can come back empty -- and silently -- for a ZIP download
  # with no .git, or a "dubious ownership" safe.directory refusal. xargs -r
  # keeps that from feeding an empty sha256sum invocation, but the *list*
  # still has to be checked directly: with -r alone, an empty list makes
  # `xargs | sha256sum` hash zero files into one constant "empty" tag, and
  # every stale tree would look identical with no error at all. -z/-0
  # throughout so filenames with spaces or quotes survive too.
  list=$(mktemp) || return 1
  (cd "$ETCHWORK_ROOT" && git ls-files -z --cached --others --exclude-standard engine \
    | grep -zv -e '__pycache__/' -e '\.pytest_cache/' -e '\.pyc$') > "$list" || true
  if [ ! -s "$list" ]; then
    rm -f "$list"
    echo "engine_hash: 'git ls-files' returned no files under engine/ (git failed, or this isn't a git checkout)" >&2
    return 1
  fi
  # Paths in $list are relative to $ETCHWORK_ROOT (where git ls-files ran),
  # so sha256sum/xargs must run from there too, not from the caller's cwd.
  (cd "$ETCHWORK_ROOT" && LC_ALL=C sort -z "$list" | xargs -0 -r $sha256_prog | $sha256_prog | cut -c1-12)
  rm -f "$list"
}

engine_image() {
  # Not just `echo "etchwork-engine:$(engine_hash)"`: a failing command
  # substitution embedded in another command doesn't trip `set -e` on its
  # own, so engine_hash's failure has to be checked explicitly here to
  # actually refuse (the exit status of a bare `x=$(...)` assignment *does*
  # propagate under `set -e`, which is what lets callers like
  # `image="$(engine_image)"` fail loudly instead of building "etchwork-engine:").
  h=$(engine_hash) || return 1
  echo "etchwork-engine:$h"
}

if [ "$0" = "$ETCHWORK_ROOT/libexec/build.sh" ]; then
  image=$(engine_image) || exit 1
  docker build -t "$image" "$ETCHWORK_ROOT/engine"
  echo "IMAGE=$image"
fi
