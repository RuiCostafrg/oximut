#!/usr/bin/env bash
set -euo pipefail
export PATH=/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:$PATH
export NODE_OPTIONS=''
unset NODE_COMPILE_CACHE
ln -sfn /root/oximut/node_modules node_modules
trap 'rm -f node_modules' EXIT
node benchmarks/experiments/fork-snapshot-probe/build.mjs
node benchmarks/experiments/fork-snapshot-probe/lifecycle.mjs
node benchmarks/experiments/fork-snapshot-probe/summarize.mjs
node benchmarks/experiments/fork-snapshot-probe/console-check.mjs
