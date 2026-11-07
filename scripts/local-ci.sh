#!/usr/bin/env bash
# oximut local CI — Linux only, powered by basecamp/gh-signoff.
# Mirrors the Linux feasibility job of .github/workflows/ci.yml.
# Usage:
#   scripts/local-ci.sh               stages: tests lint security
#   scripts/local-ci.sh tests         selected stages only
#   scripts/local-ci.sh --list        list stages
#   scripts/local-ci.sh --no-signoff  run checks, don't post signoff statuses
#   scripts/local-ci.sh --help        this text
set -euo pipefail

AVAILABLE_STAGES=(tests lint security)
DEFAULT_STAGES=(tests lint security)

_fail()   { printf '✗ %s\n' "$*" >&2; exit 1; }
_log()    { printf '▸ %s\n' "$*"; }
_ok()     { printf '✓ %s\n' "$*"; }
require() { command -v "$1" >/dev/null 2>&1 || _fail "missing '$1'${2:+ — $2}"; }

case "$(uname -s)" in
  Linux) ;;
  *) _fail "Linux-only local CI; refusing to run on $(uname -s) (other platforms stay in GitHub Actions)." ;;
esac

NO_SIGNOFF=0
SELECTED=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-signoff) NO_SIGNOFF=1 ;;
    --list) printf '%s\n' "stages: ${AVAILABLE_STAGES[*]}"; exit 0 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    -*) _fail "unknown option: $1 (try --help)" ;;
    *) SELECTED+=("$1") ;;
  esac
  shift
done
[ ${#SELECTED[@]} -gt 0 ] || SELECTED=("${DEFAULT_STAGES[@]}")

if [ "$NO_SIGNOFF" = 0 ]; then
  require gh ""
  gh extension list 2>/dev/null | grep -qi "gh-signoff" \
    || _fail "gh-signoff extension missing — run: gh extension install basecamp/gh-signoff (or pass --no-signoff)"
fi

# ── stages ────────────────────────────────────────────────────────────────────

stage_tests() {
  # Full feasibility suite, in CI order (rust-toolchain.toml pins 1.96.0 via rustup).
  require cargo ""
  require python3 ""
  require node ""
  bash scripts/setup-candidate-sandbox.sh
  OXIMUT_REQUIRE_SANDBOX=1 python3 -m unittest discover -s tests -p test_candidate_sandbox.py -v
  npm ci
  npm run typecheck
  npm test
  python3 -m unittest discover -s benchmarks/profiling -p 'test_*.py' -v
  node --test scripts/tests/validate-*.test.mjs
  npm run experiment
  cargo clippy --workspace --all-targets --locked -- -D warnings
  cargo test --workspace --locked
  cargo build -p oximut-cli --locked
  OXIMUT_REQUIRE_SANDBOX=1 OXIMUT_TEST_BINARY="$PWD/target/debug/oximut" \
    python3 -m unittest discover -s tests -p test_candidate_sandbox.py -v
  python3 -m unittest discover -s tests -v
  cargo run --locked -p oximut-oxc-smoke -- fixtures/pure/src/logic.ts fixtures/stateful/src/service.ts
  python3 benchmarks/run.py --repetitions 1 --warmups 0 --timeout 180 --output .oximut/ci-baselines
  python3 benchmarks/m6.py --fixtures m1 --tools oximut --scenarios clean --repetitions 1 --warmups 0 --output .oximut/ci-m6
  node scripts/validate-reports.mjs \
    .oximut/ci-m6/measured-01-m1-oximut-clean-fresh/run/report.json \
    .oximut/ci-m6/measured-01-m1-oximut-clean-persistent/run/report.json \
    .oximut/ci-m6/measured-01-m1-oximut-clean-selection/run/report.json
}

stage_lint() {
  require cargo ""
  require npm ""
  npm run format:check
  npm run lint
  cargo fmt --all -- --check
}

stage_security() {
  require npm ""
  npm audit --audit-level=moderate
}

# ── runner ────────────────────────────────────────────────────────────────────

# Anchor to the repository that owns this script, not the caller's working
# directory — stages and status posting must use this repo's paths and HEAD,
# even when invoked from another checkout, via a symlink, or with CDPATH set
# (cubic P3/P2, CodeRabbit). readlink -f is safe: the runner is Linux-only.
repo_script="$(readlink -f -- "${BASH_SOURCE[0]}")" || _fail "cannot resolve script path"
CDPATH= cd -- "$(dirname -- "$repo_script")/.." \
  || _fail "cannot find repository root"

signoff_failed=0

# Validate all selected stage names before running anything.
for s in "${SELECTED[@]}"; do
  declare -F "stage_$s" >/dev/null || _fail "unknown stage '$s' (stages: ${AVAILABLE_STAGES[*]})"
done

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  # gh-signoff refuses to attest a dirty tree, so fail fast BEFORE the long
  # stages instead of finishing a full run with nothing posted.
  if [ "$NO_SIGNOFF" = 0 ]; then
    _fail "working tree has uncommitted changes — commit or stash them, or pass --no-signoff"
  fi
  _log "warning: working tree has uncommitted changes — results may not match HEAD"
fi

for s in "${SELECTED[@]}"; do
  _log "stage: $s"
  # Bind every status to the exact commit the stage tests (CodeRabbit Major):
  # a commit/push landing mid-stage can no longer be green-lit by mistake.
  head_sha="$(git rev-parse HEAD)"
  # Run the stage in a subshell with errexit, OUTSIDE any if-condition context:
  # inside `if ! (...)` bash suppresses errexit, so a failing command mid-stage
  # could be masked and the stage wrongly reported green (bot review P0).
  set +e
  ( set -e; "stage_$s" )
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    [ "$NO_SIGNOFF" = 0 ] && gh signoff fail --commit "$head_sha" "$s" --description "$s failed locally" >/dev/null 2>&1 || true
    _fail "stage '$s' failed"
  fi
  # A concurrent commit or uncommitted edit during the stage changes what was
  # tested while $head_sha stays — refuse to attest anything then (cubic P2/P1).
  # Scoped to signed-off runs: dirty trees are allowed with --no-signoff.
  if [ "$(git rev-parse HEAD)" != "$head_sha" ] \
     || { [ "$NO_SIGNOFF" = 0 ] && [ -n "$(git status --porcelain --untracked-files=normal)" ]; }; then
    [ "$NO_SIGNOFF" = 0 ] && gh signoff fail --commit "$head_sha" "$s" --description "$s ran while HEAD moved or tree changed" >/dev/null 2>&1 || true
    _fail "HEAD moved or working tree changed during stage '$s' — aborting instead of attesting untested code"
  fi
  _ok "$s passed"
  if [ "$NO_SIGNOFF" = 0 ]; then
    gh signoff --commit "$head_sha" "$s" >/dev/null 2>&1 \
      || { printf '⚠ could not post signoff status for %s — is HEAD pushed? gh authorized?\n' "$s" >&2; signoff_failed=1; }
  fi
done

# A missing required status is as bad as a failing stage (CodeRabbit Minor).
if [ "$signoff_failed" -ne 0 ]; then
  _fail "one or more signoff statuses could not be posted"
fi

if [ "$NO_SIGNOFF" = 1 ]; then
  _log "all done — no signoff statuses posted (was --no-signoff)."
else
  _log "all done."
fi
