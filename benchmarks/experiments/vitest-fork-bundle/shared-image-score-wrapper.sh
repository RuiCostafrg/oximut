#!/usr/bin/env bash
set -euo pipefail
export OXIMUT_SHARE_PROJECT_IMAGE=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/factory-score-wrapper.sh" "$@"
