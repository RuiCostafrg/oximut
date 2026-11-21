#!/usr/bin/env bash
set -euo pipefail
export OXIMUT_SNAPSHOT_MUTANTS_ONLY=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/shared-image-score-wrapper.sh" "$@"
