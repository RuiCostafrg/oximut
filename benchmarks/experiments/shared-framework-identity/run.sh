#!/usr/bin/env bash
set -euo pipefail
: "${NODE_BINARY:?Set NODE_BINARY to verified Linux Node 26.3.1}"
: "${1:?New output directory required}"
"$NODE_BINARY" --experimental-import-meta-resolve benchmarks/experiments/shared-framework-identity/probe.mjs "$1"
