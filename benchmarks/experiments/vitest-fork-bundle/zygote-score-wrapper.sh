#!/usr/bin/env bash
# Early-build control plus zygote-forked mutant workers. The workflow builds
# the addon before timing starts; each run's zygote counters are kept.
set -euo pipefail
: "${OXIMUT_ZYGOTE_SCORE_ADDON:?expected the built zygote fork addon}"
: "${OXIMUT_ZYGOTE_EVIDENCE_DIR:?expected a directory for zygote counters}"
export OXIMUT_ZYGOTE_WORKERS=1
export OXIMUT_EXPERIMENTAL_RESIDENT_HELPERS=1
export OXIMUT_ZYGOTE_ADDON="$OXIMUT_ZYGOTE_SCORE_ADDON"
if test "${OXIMUT_ZYGOTE_TRACE:-}" = 1; then
  export OXIMUT_ZYGOTE_TRACE_DIR=$(mktemp -d "$OXIMUT_ZYGOTE_EVIDENCE_DIR/trace.XXXXXX")
fi
exec "$(dirname -- "${BASH_SOURCE[0]}")/early-build-score-wrapper.sh" "$@"
