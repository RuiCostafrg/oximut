#!/usr/bin/env bash
# The scoring harness times this whole invocation, including generating the image.
set -euo pipefail
test -z "${NODE_OPTIONS:-}"
: "${OXIMUT_SNAPSHOT_SCORE_BINARY:?expected the compiled Oximut executable}"
experiment=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$experiment/../../.." && pwd)
scratch=$(mktemp -d "$root/node_modules/vitest/dist/.oximut-score.XXXXXX")
cleanup() {
  python3 -c 'from pathlib import Path; import shutil, sys; shutil.rmtree(Path(sys.argv[1]))' "$scratch"
}
trap cleanup EXIT
ulimit -c 0
node "$experiment/snapshot.mjs" "$scratch"
node --build-snapshot --snapshot-blob="$scratch/vitest.blob" "$scratch/seed.cjs"
mv "$scratch/seed.cjs" "$scratch/seed.offline.cjs"
OXIMUT_SNAPSHOT_BLOB="$scratch/vitest.blob" \
  NODE_OPTIONS="--require=\"$experiment/snapshot-flags.cjs\"" \
  "$OXIMUT_SNAPSHOT_SCORE_BINARY" "$@"
