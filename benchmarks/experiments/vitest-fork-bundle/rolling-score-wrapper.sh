#!/usr/bin/env bash
set -euo pipefail
export OXIMUT_EXPERIMENTAL_ROLLING_LANES=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/score-wrapper.sh" "$@"
