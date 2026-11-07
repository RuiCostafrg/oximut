# Fork dependency snapshot feasibility probe

## Verdict: no-go for dependency-only preloading with the real stock entry

Node 26.3.1 **does establish IPC before the snapshot deserialize-main callback**.
The earlier conditional report's suspected IPC blocker is disproved. A dependency
snapshot builds and, with the console repair below, runs the real Vitest 4.1.12
fork lifecycle successfully. However, the native worker loads separate dependency
instances: the preload does not remove its module evaluation work. Child CPU
increases. Do not integrate this approach into Oximut product code.

All changes are confined to this experiment. The original `measure.mjs`,
`measurements.json` and `cache/` remain historical evidence from the first probe;
they are not the basis for this verdict.

## Reproduce and artifacts

From the repository root: `bash benchmarks/experiments/fork-snapshot-probe/run.sh`.
Requires the specified `/root/oximut/node_modules` installation, Node 26.3.1 aarch64,
and Rolldown 1.2.15 from that installation. The script links node_modules and
removes the link on exit. Generated source/blob/fixture files stay in ignored
`generated/`; blobs are runtime/architecture/build/flag specific and not committed.

- `build.mjs`: bundles dependency ESM into one CJS snapshot builder. Loads
  `@vitest/runner`, `@vitest/expect`, `chai`, `@vitest/utils`, and `@vitest/spy`
  plus their transitive dependencies. `tinyspy` is absent from the pinned
  installation; this Vitest uses `@vitest/spy`. No package was installed to fake it.
  Does **not** evaluate forks.js at build time. The deserialize callback creates
  a runtime require and loads `runtime.cjs`, which loads the actual forks.js.
- `build-result.json`: successful build, including Node's warning that
  `node:module` support in user snapshots is not fully verified.
- `lifecycle.mjs`: IPC proof and 12 runs per mode in alternating pair order
  (stock/snapshot, snapshot/stock). Stock `createVitest`, `PoolRunner`, and
  `ForksPoolWorker` lifecycle; a subclass only changes launch flags/entry/env
  and records parent timing. No hand-written replacement worker RPC protocol.
- `runtime.cjs`: identical diagnostic launcher for both modes. Imports dependency
  exports to compare identity and state, intercepts outgoing `started` to record
  child `process.resourceUsage()`, then invokes the unmodified actual worker.
  Records final self rusage in a SIGTERM handler before successful process exit.
- `lifecycle-results.json`: full raw rusage, timings, IPC messages, test outcomes,
  state observations, source/blob hashes, and summaries. `summarize.mjs` validates
  inventory/outcomes/identity and recomputes conventional medians.
- `console-check.mjs` / `console-results.json`: independent reproduction of the
  console blocker, with stock, unpatched snapshot and repaired snapshot outcomes.

The initial real snapshot lifecycle reached the handshake but collected zero
files and reported `TypeError: Console is not a constructor` from Vitest's
`dist/chunks/console.3WNpx0tS.js:createCustomConsole`. After deserialization,
`require('node:console').Console` was undefined, even after explicitly importing it
at build time. Retaining the actual constructor on a global and assigning it back
to the runtime builtin **before** loading the worker fixes this observed failure.
All snapshot timing and passing outcomes below include this experiment-only
repair. It is not evidence that arbitrary builtins are snapshot-safe.

## IPC and actual lifecycle evidence

`lifecycle-results.json.ipc`: child launched using `spawn` with an `ipc` stdio slot;
inside deserialize-main, `typeof process.send === 'function'`. Child sends ready,
parent sends `ping`, child echoes `ping` with `connected: true`; clean exit 0.
Real pool workers use stock `fork()` with advanced serialization and successfully
exchange the actual start/run/stop RPC messages. Thus neither NODE_CHANNEL_FD
setup nor IPC ordering is a blocker on this exact runtime.

Each of 24 independent workers runs exactly one file and one `fresh state` test,
with exactly one passed result, zero unhandled/module errors, and normal pool
close. The test checks runtime env, a clean global and arithmetic, then dirties
the global. Each next fork starts clean. No worker is reused across test runs.

## Relative measurements (Android/PRoot aarch64 only)

Ratio of medians, stock = 100; percentages are `(snapshot / stock - 1) * 100`.
Absolute times are retained in raw JSON solely for reproducibility. They do not
estimate native performance or establish significance on this noisy host.

| Phase | Wall delta | Child user + sys CPU delta |
| --- | ---: | ---: |
| Spawn to parent receipt of `started` | -2.96% | +6.01% |
| Whole single-file lifecycle | -3.46% | +4.95% |

Bootstrap wall starts immediately before `ForksPoolWorker.start()` spawns and
ends on receipt of Vitest's `started` response (includes its start handshake).
Bootstrap CPU is the child's getrusage self sample just before sending `started`.
Lifecycle wall covers `runTestSpecifications` through pool close/worker exit,
excluding controller/context initialization and discovery. Lifecycle CPU is child
user+system at the final SIGTERM handler, excluding its final JSON write/exit
teardown. CPU uses Node's getrusage-backed `process.resourceUsage()` in the child,
not controller CPU or elapsed time. Full raw user/sys fields are in microseconds.
Controller/Vite CPU is deliberately excluded. There is no phase-independent CPU
measurement for kernel work after the child's final sample.

Both modes use the same diagnostic shim, explicit dependency imports, state
inspection and CPU instrumentation; the stock mode has no snapshot flags. These
are instrumented real stock worker lifecycles, not untouched CLI command timings.
They cannot quantify the speed of a future loader that shares preloaded exports.
Wall variation is large, and a small apparent wall reduction alongside greater
CPU and duplicate evaluation is not a performance win. No confidence interval
or native speedup is claimed. Compile cache is disabled by run.sh.

## Module identity and freshness

Every snapshot run observes `false` for each identity comparison:
preloaded runner.test, expect.getState, chai.expect, and spy.fn versus the same
exports loaded through Node's runtime require/ESM loader. Vitest's native imports
therefore evaluate separate instances; bundling the dependencies does not seed
Node's native ESM module map. A runtime entry bundled against the same dependency
registry could change that, but was not implemented or qualified here.

The bundles are not literally free of in-memory side effects: @vitest/expect
creates global Symbol.for matcher descriptors/maps. They have no IPC/active
handle initialization in this selected graph. Captured state includes:

- Runner current-suite/task/fixture maps, mock registries, Chai config/plugins,
  weak caches and matcher maps; empty/default state is serialized once and copied
  into each fresh process. No project modules or tests are evaluated at build time.
- Global matcher symbols and a deliberately visible `__snapshotDeps`,
  `__snapshotBuild`, and saved console constructor. These diagnostic globals
  differ from stock, so universal semantic equivalence is explicitly not claimed.
- References to Date, performance.now and safe timer functions, and a
  Date.now-derived timestamp-length constant. Maps or plugin configuration
  modified while building would be copied to every fork. A bound timer/clock
  reference also merits qualification before using the preloaded runner itself.
- Chai's random/date-based fallback cache-key generation exists in the graph;
  it is lazy, not evidence that importing Chai consumes random numbers. The
  explicit diagnostic Math.random/Date.now samples **are** captured and identical
  in every snapshot child. Future random streams/real clocks were not qualified.
- Dependency color detection can read build environment/TTY state. Build cwd,
  argv, env `BUILD`, timestamp and random sample are recorded in the diagnostic
  object. Such top-level reads are frozen; live runtime env/cwd/argv must be read
  after deserialization. process.execArgv also visibly contains snapshot flags.

The actual equal-state check compares all 24 workers before test execution:
`OX_SNAPSHOT_STATE === 'RUNTIME'` (build used `BUILD`), equal live cwd and user
argv, absent dirty global, Chai config truncateThreshold 40, and function-valued
runner.test. Every observation matches stock. Each test then dirties a global,
with no inheritance by the next process. This is a narrow positive oracle,
not proof that the snapshotted copies are safe to use: native module identity
checks show the runtime uses fresh copies, while the snapshot's separate global
state and build diagnostics remain observable. Duplicate expect instances can
also interact through their shared global matcher symbols.

## Recommended next step

Stop this dependency-preload route. If pursuing snapshots further, first make an
experiment-only entry/loader that resolves **all** worker and dynamic-import
references to the same preloaded dependency registry, prove export identity, and
qualify builtin restoration and build-time global/env/clock state against fresh
forks. Only after that gate should native paired wall/child-CPU measurements be
used to decide whether maintaining a Vitest-specific bundled worker is justified.
