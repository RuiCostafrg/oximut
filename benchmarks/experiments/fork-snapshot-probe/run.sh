#!/usr/bin/env bash
set -euo pipefail
export PATH=/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:$PATH
ln -sfn /root/oximut/node_modules node_modules
trap 'rm -f node_modules' EXIT
node benchmarks/experiments/fork-snapshot-probe/measure.mjs
