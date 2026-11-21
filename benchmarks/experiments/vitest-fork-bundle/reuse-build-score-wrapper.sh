#!/usr/bin/env bash
set -euo pipefail
export OXIMUT_REUSE_PROJECT_BUILD=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/single-image-score-wrapper.sh" "$@"
