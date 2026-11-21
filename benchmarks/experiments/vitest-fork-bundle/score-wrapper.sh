#!/usr/bin/env bash
# The scoring harness times this whole invocation, including generating the image.
set -euo pipefail
test -z "${NODE_OPTIONS:-}"
: "${OXIMUT_SNAPSHOT_SCORE_BINARY:?expected the compiled Oximut executable}"
experiment=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$experiment/../../.." && pwd)
scratch=$(mktemp -d "$root/node_modules/vitest/dist/.oximut-score.XXXXXX")
seed_pid=
cleanup() {
  if test -n "$seed_pid"; then wait "$seed_pid" || true; fi
  python3 -c 'from pathlib import Path; import shutil, sys; shutil.rmtree(Path(sys.argv[1]))' "$scratch"
}
trap cleanup EXIT
ulimit -c 0
if test "${OXIMUT_SNAPSHOT_MUTANTS_ONLY:-}" = 1; then
  if test "${OXIMUT_PROJECT_FACTORY_SNAPSHOT:-}" != 1; then exit 2; fi
  (
    if ! node "$experiment/snapshot.mjs" "$scratch" ||
       ! mv "$scratch/seed.cjs" "$scratch/seed.offline.cjs"; then
      touch "$scratch/seed.failed"
      exit 1
    fi
  ) &
  seed_pid=$!
else
  node "$experiment/snapshot.mjs" "$scratch"
  node --build-snapshot --snapshot-blob="$scratch/vitest.blob" "$scratch/seed.cjs"
  mv "$scratch/seed.cjs" "$scratch/seed.offline.cjs"
fi
options="--require=\"$experiment/snapshot-flags.cjs\""
case "${SCORE_MODE:-}" in
  worker-snapshot-composed-score|worker-snapshot-rolling-score|worker-snapshot-windowed-score|worker-snapshot-late-score|worker-snapshot-factory-score|worker-snapshot-shared-image-score|worker-snapshot-single-image-score|worker-snapshot-reuse-build-score|worker-snapshot-early-build-score)
    export OXIMUT_PROJECT_BUNDLE_DIR="$scratch/project-bundle"
    mkdir "$OXIMUT_PROJECT_BUNDLE_DIR"
    options+=" --require=\"$experiment/project-bundle.cjs\""
    ;;
esac
OXIMUT_SNAPSHOT_BLOB="$scratch/vitest.blob" \
  NODE_OPTIONS="$options" \
  "$OXIMUT_SNAPSHOT_SCORE_BINARY" "$@"
