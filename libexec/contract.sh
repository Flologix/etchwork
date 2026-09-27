#!/bin/sh
# CONTRACT benchmark: grade a delivered board, or run CONTRACT's own tests.
# Grading runs on the host; only the board dump runs in the engine image.
# Tests and the fixture generator run wholly in the image, where pcbnew is.
set -eu
. "$ETCHWORK_ROOT/libexec/build.sh" --lib
image=$(engine_image)
src="type=bind,src=$ETCHWORK_ROOT/contract,dst=/opt/contract-src/contract"

sub="${1:-}"; [ $# -gt 0 ] && shift
case "$sub" in
  score)
    PYTHONPATH="$ETCHWORK_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
      exec python3 -m contract score --image "$image" "$@" ;;
  test)
    exec docker run --rm --network none --pull never --user "$(id -u):$(id -g)" \
      --mount "$src,readonly" -e PYTHONPATH=/opt/etchwork:/opt/contract-src \
      --entrypoint python3 "$image" \
      -m pytest /opt/contract-src/contract/tests -q -p no:cacheprovider "$@" ;;
  fixtures)
    # Writable: the generator writes contract/tests/fixtures/keyfob/.
    exec docker run --rm --network none --pull never --user "$(id -u):$(id -g)" \
      --mount "$src" -e PYTHONPATH=/opt/etchwork:/opt/contract-src \
      --entrypoint python3 "$image" \
      -m contract.tests.fixtures.gen_keyfob /opt/contract-src/contract/tests/fixtures/keyfob ;;
  *)
    echo "usage: etchwork contract score <task> <board dir> [--out <file>] | test [pytest args] | fixtures" >&2
    exit 2 ;;
esac
