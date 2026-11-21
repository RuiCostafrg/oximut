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
options="--require=\"$experiment/snapshot-flags.cjs\""
if [[ "${SCORE_MODE:-}" == worker-snapshot-composed-score || "${SCORE_MODE:-}" == worker-snapshot-rolling-score || "${SCORE_MODE:-}" == worker-snapshot-windowed-score ]]; then
  export OXIMUT_PROJECT_BUNDLE_DIR="$scratch/project-bundle"
  mkdir "$OXIMUT_PROJECT_BUNDLE_DIR"
  options+=" --require=\"$experiment/project-bundle.cjs\""
fi
OXIMUT_SNAPSHOT_BLOB="$scratch/vitest.blob" \
  NODE_OPTIONS="$options" \
  "$OXIMUT_SNAPSHOT_SCORE_BINARY" "$@"
