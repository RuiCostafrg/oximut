# Fork startup snapshot feasibility probe

## Verdict: conditional / presently no-go for Vitest's fork worker

Node 26 supports snapshots, but the documented userland builder accepts one
entrypoint and cannot load additional userland modules directly. Bundling is
the proposed workaround. Snapshot compatibility is tied exactly to Node
version, architecture, platform, V8 flags, and CPU features. A trivial snapshot
build/load worked on this Android/PRoot aarch64 host.

Vitest's `forks.js` is ESM and its top-level `workerInit()` expects the real
child-process IPC setup. A direct static-graph import failed before lifecycle
startup with `Expected worker to be run in node:child_process` because ordinary
`node -e` has no `process.send`. No bundled Vitest worker snapshot or successful
real pool lifecycle was achieved in this probe. This is a useful hard blocker:
snapshot deserialization would need a supported way to establish the child
channel before worker initialization, and the current CLI snapshot entry model
does not expose that ordering for this ESM worker.

Node's child-process `fork()` supplies `process.argv`, inherited environment,
and the IPC channel through its execArgv/serialization options. The snapshot
blob does not inherently replace fork: a process started with
`--snapshot-blob` should still be a child-process instance, but it must be
verified that the required IPC channel is established before any deserializer
callback imports/initializes Vitest. Defer channel-dependent work to the
deserialize main callback and verify `process.send` and first `message` there.
This exact handshake remains untested here.

Snapshots must be generated per exact Node runtime and should not contain open
handles, workers, timers, active IPC objects, or process-specific resources.
Even with a fresh process per test, preinitialized module singletons and globals
are copied from the same pre-run state. Audit mutable exports, globalThis,
Date/random initialization, environment and cwd reads, `process.execArgv`, and
timer/listener setup. Read run-specific values after deserialization. Validate
against the fresh-process oracle using the state leak patterns and two-run
probes in `../warm-runtime-probe/`; include mutated module exports, globals,
environment, cwd, descriptors, timers, and listener counts.

## Measurements

`measurements.json` stores eight alternating repetitions for plain startup,
trivial snapshot startup, and normal static imports of the Vitest fork worker,
with and without `NODE_COMPILE_CACHE`. Direct-import cases failed as above.
On this host, plain `node -e 0` was 116–141 ms wall and trivial snapshot was
131–159 ms; the snapshot was not faster in this noisy run. Direct fork module
imports took 0.9–1.16 s and failed. These absolute values are unrepresentative.
The original collector recorded wall time only; child CPU time was not
captured, so there is no CPU comparison or meaningful worker-graph relative
speedup evidence yet. `measure.mjs` recreates the trivial blob and records the
alternating run data. Run from the repository root with `bash
benchmarks/experiments/fork-snapshot-probe/run.sh`.

Given the supplied native report's ~55 ms fork bootstrap, the theoretical
upper bound is 55 ms/fork (100% elimination); an engineering upper bound after
retaining Node/IPC startup cannot be claimed from this probe. Next step: make a
fork-compatible ESM/CJS harness that preserves Vitest IPC, capture child CPU
via `wait4`/`/usr/bin/time`, and only then assess a bundled graph snapshot.
