#!/usr/bin/env bash
set -euo pipefail
export OXIMUT_PROJECT_FACTORY_SNAPSHOT=1
exec "$(dirname -- "${BASH_SOURCE[0]}")/late-score-wrapper.sh" "$@"
