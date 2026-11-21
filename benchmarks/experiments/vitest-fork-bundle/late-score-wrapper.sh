#!/usr/bin/env bash
set -euo pipefail
export OXIMUT_INLINE_LATE_RUNTIME=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/score-wrapper.sh" "$@"
