#!/usr/bin/env bash
set -euo pipefail
export OXIMUT_EARLY_PROJECT_BUILD=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/reuse-build-score-wrapper.sh" "$@"
