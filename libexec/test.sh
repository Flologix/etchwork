#!/bin/sh
# Runs the engine test suite inside the built etchwork-engine image.
set -eu
. "$ETCHWORK_ROOT/libexec/build.sh" --lib

docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
  -v "$ETCHWORK_ROOT/engine:/opt/etchwork-src:ro" \
  --entrypoint python3 "$(engine_image)" \
  -m pytest /opt/etchwork-src/tests -q -p no:cacheprovider "$@"
