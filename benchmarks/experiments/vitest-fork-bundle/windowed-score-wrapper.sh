#!/usr/bin/env bash
set -euo pipefail
export OXIMUT_WINDOWED_LANES=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/rolling-score-wrapper.sh" "$@"
