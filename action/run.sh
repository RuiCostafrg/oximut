#!/usr/bin/env bash
set -euo pipefail

args=()
while IFS= read -r argument; do
  if [[ -n "$argument" ]]; then
    args+=("$argument")
  fi
done <<< "$OXIMUT_ACTION_ARGS"
exec "$OXIMUT_ACTION_EXECUTABLE" "${args[@]}"
