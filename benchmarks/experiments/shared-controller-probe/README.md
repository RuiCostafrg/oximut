# Shared Vitest controller feasibility probe

**Verdict: conditional.** One real Vitest controller can run several mutant
processes concurrently against the same physical test file, with correct selector
initialization, fork-local bail, and results matching a sequential fresh-process
oracle. This is a viable direction for a separate implementation, not evidence of
a wall-time improvement. Neither concurrent API calls nor duplicate specs within
one project are a safe solution. No product code changes are included.

Base: `6876705` (confirmed with `git log -1` before branching). Tested with local
Vitest **4.1.12**, Node **26.3.1**, Android/PRoot aarch64. No timing conclusions.

## Reproduce and evidence

From the repository root, with the existing pinned dependencies:

```sh
export PATH=/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:$PATH
ln -sfn /root/oximut/node_modules node_modules
node --test --test-timeout=60000 benchmarks/experiments/shared-controller-probe/probe.test.mjs benchmarks/experiments/shared-controller-probe/research.test.mjs
rm node_modules
```

`probe.mjs` creates one `createVitest` instance, four named projects referencing
one identical physical test module, and runs all their specifications in one
`runTestSpecifications` call. `env` sets the selector before Node starts;
`subject.mjs` captures it at module evaluation. A separate `provide` value checks
that the assignment agrees. `local-runner.mjs` disables global bail and cancels
only its own runner after a failed test. The optional experimental pool wrapper
records worker ownership, gates live processes, and adds a hard deadline.

`probe.test.mjs` starts four sequential **new controller processes**, each using
stock forks plus the same local-bail runner, to obtain the oracle. It compares
outcomes and the complete per-test state vector with the one-controller run.
It also compares a one-controller stock-pool run against that oracle. Thus the
oracle validates scheduling/isolation parity; it is not an independent validation
of Oximut's entire production outcome classifier or its current bail semantics.

Checked-in `evidence.json` records four distinct PIDs and a peak of two live
workers for the wrapped pool; every worker exits. The results are:

| Mutant | Outcome | Collected | Passed | Failed | Not run |
| --- | --- | ---: | ---: | ---: | ---: |
| killed | killed | 3 | 1 | 1 | 1 |
| survived | survived | 3 | 3 | 0 | 0 |
| timeout | timeout | 3 | 1 | 1 | 1 |
| survived-again | survived | 3 | 3 | 0 | 0 |

The shared and sequential state vectors are identical. Vitest exposes the
locally cancelled remaining test as `pending`, rather than `skipped`; the evidence
preserves that distinction. Timeout here is an async test exceeding 500 ms.

`research.test.mjs` and `research-evidence.json` additionally demonstrate:

- Duplicate same-project specs execute twice, but produce only one module result.
- A second call after the first has started waits for the first run to end.
- Two simultaneous calls can both execute before either run ends: an admission
  race, not supported independent run contexts.
- A SIGKILL crash, a synchronous infinite loop stopped by a controller-side
  3-second watchdog, and an unhandled rejection are attributed by worker/project
  ownership. A neighboring surviving mutant completes. These extra failures are
  attribution experiments, not part of the four-mutant oracle comparison.

The fixture intentionally has **one test file**, with three tests, so each mutant
has exactly one fresh OS process. Arbitrary selections spanning multiple files,
multiple environments, retries, hooks, coverage and full Oximut integration are
outside this minimal prototype. Stock Vitest normally schedules isolated work
per file, not per mutant; production must resolve that difference explicitly.

## Source references

References below are to the **installed compiled source** in the pinned package,
not a possibly different upstream revision. Paths are relative to repository root.
For compactness:

- `API` = `node_modules/vitest/dist/chunks/cli-api.B0Pz1_R7.js`
- `RUNNER` = `node_modules/vitest/dist/chunks/test.DNmyFkvJ.js`
- `HOOKS` = `node_modules/vitest/dist/chunks/index.DXx9Dtk7.js`
- `ERRORS` = `node_modules/vitest/dist/chunks/init.k9zZ9sLh.js`
- `TYPES` = `node_modules/vitest/dist/chunks/reporters.d.DtoKVV2s.d.ts`

### 1. Concurrent runTestSpecifications calls?

**Not safely as independent runs.** `API:13608` delegates directly to `runFiles`.
`API:13637-13649` starts reporting, waits for `cancelPromise` and
`runningPromise`, clears cancellation listeners, then assigns a new global
`runningPromise`. A call arriving after that assignment serializes behind it.
The overlap experiment confirms this, including the surprising second
`run-start` event before the first `run-end` (reporting happens before waiting).

There is **no atomic admission lock**: simultaneous callers can both read an
undefined `runningPromise` before either assigns it. The simultaneous experiment
shows both workers executing before either run ends. This does not make it safe:
`API:13651-13691` clears shared snapshots/errors, shares `pool`, reads global
`state.getTestModules()`, ends a global `_testRun`, and each caller finally clears
the same `runningPromise`. Results can include modules from the other call;
`perCallModules` records this. Cancellation and coverage are also shared. Do not
build a controller around this race, and do not claim calls are always serialized
or explicitly rejected. Use exactly one run call per batch.

### 2. Same file multiple times, selector delivery, and project identity

Raw duplicate specifications are accepted and execute, but **are not distinct
result identities**. `API:10633-10637` hashes relative path plus project name into
`taskId`. `API:12066-12080` replaces an existing file for the same project/path;
there is one global `idMap` (`API:11993-12000`). Changing a spec object or attaching
an arbitrary mutant field does not change this identity.

`TYPES:74-79` lists all specification options: test names, IDs, lines and tags.
There is no per-spec env or provide option. `API:10638-10643` copies only those
supported fields. `API:10723-10742` makes `provide` project-scoped. During pool
task construction, `API:3709-3750` caches env **per project**, merges controller and
project env, and copies `project.getProvidedContext()` into the task context.
Mutating controller env while tasks launch is therefore unsafe.

**Separate named projects work.** Their task hashes and reporting identities are
distinct even for the same physical file. Stock project `env` reaches
`ForksPoolWorker.start()` before process creation (`API:3154-3161`). This is earlier
than setup files and user module evaluation. The fixture captures its selector at
module evaluation and checks it against project `provide` in every mutant.
A setup file could translate `inject()` into an env selector before test imports,
but env at launch is stronger, including for initialization/preload needs.

Delivery choices:

- **Project env + stock forks:** simplest demonstrated assignment path. Requires
  a project identity per concurrently distinguishable occurrence. Project
  creation itself creates Vite servers (`API:11115-11128`), so creating one project
  for every mutant is not a production performance design.
- **Custom pool worker around exported ForksPoolWorker:** clone `options.env`, set
  an immutable mutant selector, and create the stock worker. Do not mutate the
  shared task env (`TYPES:2084` and following). The extension receives project,
  environment and method, not arbitrary spec metadata (`TYPES:2054-2075`), so it
  still needs a stable project/assignment map or must inspect the run message.
  The probe uses this extension for lifecycle, deadlines and attribution; it uses
  project env, rather than changing env in the wrapper.
- **Selector over IPC:** possible if assignment is carried in the initial context
  or an acknowledged custom message before evaluation. A late message can race
  imports. Stock workers do not interpret arbitrary mutant messages. Changing
  `providedContext` in `send(run)` can work with setup code, but relies on protocol
  shape and still does not fix same-project task identity.
- **Launcher reading an assignment:** a Node preload/custom entrypoint can read an
  immutable launch env or unique assignment file before importing Vitest. Avoid a
  shared mutable file. If the assignment arrives over IPC, the launcher must wait
  for it before importing the runtime. This owns bootstrap/lifecycle behavior;
  it is more code than the working env route, without solving result identity.

### 3. Per-mutant bail?

**Stock `bail: 1` is global, including across projects.** `HOOKS:182-191` asks the
controller for the global failure count and sends `rpc().onCancel`.
`API:2815-2817` forwards that to `cancelCurrentRun`; `API:13798-13803` broadcasts
all registered cancellation listeners. Each `PoolRunner` subscribes
(`API:2916`), and the pool itself subscribes (`API:3686`).

Set **bail: 0** and use a custom test runner. The inherited runner's `cancel()`
sets only its local `cancelRun`; subsequent tasks/suites consult that flag
(`RUNNER:4330-4345`). The probe calls it from `onAfterRunTask` on failure; neighbors
keep running and the failed mutant's third test does not execute. Merely
customizing `onAfterRunTask` while leaving `bail: 1` would not work because Vitest
wraps that hook with global cancellation.

This scopes bail to **one worker**, not automatically to all files/processes of a
mutant. To stop siblings or remove queued work for the same mutant, the controller
needs an assignment-aware scheduler and directed runner cancellation. Replacing
`PoolRunner._rpc`/`_offCancel` would be private and fragile. Prefer local runner
cancel for one grouped mutant process, and a worker wrapper/watchdog for hard
hangs. Neither test timeouts nor cooperative cancellation stop synchronous loops.

### 4. Per-mutant results and infrastructure failures?

**Ordinary module/test outcomes: yes with unique project identity.** Reporter
callbacks and returned `TestModule.project.name` identify the assignment;
`API:12624-12642` connects project-scoped queued/collected events to reporting.
The prototype reads test result states and timeout errors, and keeps raw counts.
It must not label a partially collected/crashed worker a survivor just because
no assertion failure was received: the separate `workerFailures` map overrides it.

**Aggregate unhandledErrors alone: insufficient.** `ERRORS:101-111` attaches test
name and file path, but no project/mutant identity. Four mutants here use the same
path. `API:2809-2810` sends unhandled errors into global state;
`API:12013-12047` stores a global error set. Worker-crash wrapping in
`API:3523-3526` has the worker kind but no reliable mutant identity. The evidence
contains two generic crash errors and an unhandled rejection with the shared path.

The wrapper knows its project/assignment, observes process exit, and inspects
inbound `onUnhandledError` RPC before identity is lost. It records the watchdog's
own timeout decision before SIGKILL. This demonstrated correct ownership for all
three extra failure cases while the survivor completed. Reading RPC fields `m`
and `a` is a **private birpc wire-format dependency**, not a public reporter API.
Startup, transform, suite/hook, teardown and post-completion errors need explicit
policy and attribution too; this prototype is not exhaustive certification.

### 5. Stock APIs only, or custom/private internals?

Stock configuration plus APIs supports project-scoped env/provide, duplicate
physical paths across projects, concurrent stock forks, and project-scoped normal
results. The oracle test also exercises that stock-pool route. It does **not**
provide mutant-scoped bail, hard mutant deadlines, or complete crash/error
attribution as a turnkey feature.

The minimal local-bail extension uses the test-runner hook and inherited `cancel`.
In this pinned runtime, importing `VitestTestRunner` from `vitest` inside the
custom runner returned undefined; the working `vitest/runners` export prints a
deprecation warning. That migration needs care on upgrade.

The custom worker initializer (`TYPES:2054-2075`, config at `TYPES:2844`) and
`ForksPoolWorker` (`API:3123`) are exported **experimental** APIs. The prototype
adds a strict live-process gate and uses `worker.fork` for PID/kill, a concrete
implementation dependency. It uses `ctx.pool.close()` to await worker draining
because normal run completion does not await isolated worker termination
(`API:3552-3558`). Crash handling marks runners stopped (`API:3089-3093`), so Vitest
can skip `worker.stop()`; the gate must release on actual process exit as well.

`PoolRunner` is an internal class (`API:2864`), not an exported constructor in
`vitest/node`. Direct construction or patching it, replacing `ctx.pool`, cloning
project internals to share a Vite server, changing task hashes, and intercepting
RPC all increase upgrade risk. Pin 4.1.12 and retain this executable contract suite
before considering any of them. No node_modules source is modified by this probe.

## Recommended implementation and size

Proceed only as a separate experiment/implementation with a strict version guard:

1. Keep one Vitest controller and issue one batch run at a time. Create a bounded
   number of named project lanes once, rather than one project per lifetime
   mutant. Bind one immutable assignment to a lane for the entire batch through
   process exit. Validate how to update lane env/provide through supported APIs;
   this prototype creates fixed projects and does not prove safe lane reuse.
2. Serve an immutable union of resident instrumented modules for that batch.
   Selection happens in each fork using its selector. The existing mutable
   `servedModules`/`resident` globals (`packages/vitest-adapter/m2-worker.ts:19-20`,
   `:211-226`) cannot switch while concurrent transforms are in flight. Do not
   serve different active-only source for the same URL in one shared graph.
3. Use the wrapper for immutable launch env, hard per-mutant deadline, exit
   accounting, and an assignment/error ledger. Keep `bail: 0`, with fork-local
   cancellation in the custom runner. Attribute results from project/module
   events; do not use one aggregate end-event as the job result.
4. Resolve multi-file jobs before rollout. Either accept fresh forks per
   (mutant,file), with a controller-owned per-mutant cancellation ledger and
   queue, or implement a custom scheduler that sends all selected same-environment
   files to one fresh mutant worker. Stock grouping (`API:3849-3910`) groups
   non-isolated files only under the single-worker condition, which does not
   directly express concurrent multi-file mutant lanes. A custom scheduler around
   Vitest's internal pool has a larger maintenance burden. Mixed environments may
   require several fresh workers for one mutant.
5. Test lane reuse, per-mutant selections, sibling cancellation, hard timeouts,
   crash/teardown ownership and resident-source stability against fresh-process
   Oximut oracles. Then measure **wall time on representative four-core hardware**.
   Retain the current implementation until that measurement wins.

Engineering estimate, not measured: roughly **600–1,200 production lines** across
adapter scheduling, lifecycle/protocol and result handling, plus **400–800 lines**
of integration tests for a bounded project-lane approach. A full internal pool
scheduler/multi-file implementation could add **500–1,000 lines**. This is a
multi-day architectural change, not a small replacement of `maxWorkers`.

Main risks: per-project Vite transforms/startup may erase the controller savings;
mutable resident source can cross-contaminate jobs; same-project identities can
alias results; global bail can silently return; hooks/retries and mixed
environments complicate local bail/counts; timeout/crash cleanup can leak slots;
late errors can be assigned to a reused lane; and exported experimental/private
protocol dependencies can break on upgrades. The feasibility result is positive
for the tested topology, conditional for arbitrary Oximut jobs and performance.
