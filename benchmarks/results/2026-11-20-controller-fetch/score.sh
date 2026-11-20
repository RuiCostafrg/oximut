#!/usr/bin/env bash
# Score only the adapter change. Both variants use the same release executable.
set -euo pipefail
binary="$1"
output="$2"
work="$3"
baseline=1059dd7a9ac3ce668c0ba2580130aa9f362bcdb0
tip=$(git rev-parse HEAD)
test "$(uname -m)" = x86_64
test "$(nproc)" -ge 4
test -z "${NODE_OPTIONS:-}"
if env | cut -d= -f1 | rg -q '^OXIMUT_PROFILE_'; then
  echo 'Profiling environment present in unprofiled campaign' >&2
  exit 1
fi
git diff --quiet "$baseline" "$tip" -- crates Cargo.toml Cargo.lock rust-toolchain.toml package.json package-lock.json .node-version
mkdir "$work"
git archive "$baseline" packages/vitest-adapter | tar -x -C "$work"
ln -s "$PWD/node_modules" "$work/node_modules"
# The historical adapter ignores this switch. The candidate changes only fork fetches.
OXIMUT_EXPERIMENT_LANE_LOCAL_FETCH=1 python3 benchmarks/compare_candidates.py \
  --workload colord-scale --concurrency 4 --warmups 1 --repetitions 6 --timeout 1200 \
  --before-commit "$baseline" --before-binary "$binary" --before-adapter-dir "$work/packages/vitest-adapter" \
  --after-commit "$tip" --after-binary "$binary" --after-adapter-dir packages/vitest-adapter \
  --output "$output"
