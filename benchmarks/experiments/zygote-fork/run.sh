#!/usr/bin/env bash
# Builds the fork addon for the glibc Node on PATH, then runs the
# feasibility checks and the startup comparison.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
include=$(dirname -- "$(dirname -- "$(command -v node)")")/include/node
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
gcc -shared -fPIC -O2 -Wall -I"$include" -o "$build/fork.node" "$here/fork.c"
export ZYGOTE_ADDON="$build/fork.node"
node --expose-internals "$here/controller.mjs"
node --expose-internals "$here/measure.mjs"
