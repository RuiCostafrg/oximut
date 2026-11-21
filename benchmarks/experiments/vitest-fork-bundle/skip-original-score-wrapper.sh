#!/usr/bin/env bash
set -euo pipefail
# Ablation: the instrumented baseline is the only gate (no original-count check).
export OXIMUT_EXPERIMENTAL_SKIP_ORIGINAL_BASELINE=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/early-build-score-wrapper.sh" "$@"
