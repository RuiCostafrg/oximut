# 0001: Reuse the Vitest controller, retain isolated test processes

Date: 2026-10-23. Status: accepted for the M0 prototype; revalidate before M1 changes the execution model.

## Decision

Continue with a narrow M1 vertical slice. Keep a Vitest controller alive across baseline and selected runs, using `pool: 'forks'`, `isolate: true`, `maxWorkers: 1`, and `fileParallelism: false`. Retain a fresh-controller reference for correctness comparisons. Do not implement persistent test-process execution or assume a speed benefit from controller reuse.

The M0 probe observes one controller PID across three runs, but three distinct test-worker PIDs. Every run observes fresh module evaluation and zero initial module state. The third run sees an environment value changed in the controller before dispatch; a separate fresh controller also passes with that value. This demonstrates controller reuse with fresh worker execution in the tested configuration. It does not demonstrate safe reuse of a worker, a live test module graph, or arbitrary import-time application state.

M1 should initially execute each mutant with a fresh controller/process tree as its correctness reference. Controller reuse may be an explicitly tested execution option; mutation switching, transform invalidation, source edits, and randomized mutant equivalence remain M2 work. A timeout or cancellation destroys the controller and requires replacement.

## Tested compatibility and environment

Only this exact combination is tested; no wider compatibility range is claimed:

| Component | Tested value |
| --- | --- |
| Node.js | official Linux arm64 build, `v26.3.1` |
| npm | `11.16.0` |
| Vitest and `@vitest/coverage-v8` | both `4.1.10` |
| Vite | `8.3.5`, resolved by the lockfile |
| Stryker core and Vitest runner | both `10.0.0`; baseline comparison owned by the benchmark harness |
| OS | Ubuntu userspace under Android Termux PRoot; `Linux 6.17.0-PRoot-Distro`, aarch64 |
| Process supervisor | `/data/data/com.termux/files/usr/bin/python3`, Python `3.13.13`; Linux `prctl(PR_SET_CHILD_SUBREAPER)` and `/proc` |

The default `node` in this environment is an Android/Termux executable. It cannot substitute for the pinned Linux build when loading the installed Linux native dependencies. Reproduction commands must put `/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin` first on `PATH`. The Python supervisor is a separate host executable and was tested successfully with the Linux Node child and inherited IPC descriptor.

The adapter rejects other Vitest versions at initialization. Lockfile changes or Node/Vitest/provider upgrades require rerunning this experiment and the baseline harness. Rust/Oxc do not participate in this lifecycle experiment and gain no compatibility evidence from it.

## Public API boundary

`worker.mjs` imports `createVitest` and `version` from `vitest/node`. The installed Vitest 4.1.10 public declarations expose the following lifecycle:

1. `createVitest('test', options)` and `standalone()` initialize configuration, reporters, and coverage without executing tests.
2. `collect(undefined, { staticParse: false })` performs runtime discovery. Test modules and their imports execute during collection; test callbacks do not. Collection is not a side-effect-free parse and also requires a deadline.
3. `globTestSpecifications()` produces a full run. `project.createSpecification(moduleId, { testIds })` selects exact discovered task IDs, including one of two tests with the same full name.
4. `runTestSpecifications(specifications, allTestsRun)` executes the job. `Reporter.onTestRunEnd` supplies modules, unhandled errors, and completion reason for this job. Using the return value's `testModules` directly produced historical modules after selected reruns, so only the reporter's job-scoped payload becomes result evidence.
5. `Reporter.onCoverage` provides a V8/Istanbul-style whole-run coverage map. `close()` closes Vitest before the supervisor reaps remaining descendants.

These APIs are typed in the installed `node_modules/vitest/dist/chunks/reporters.d.*.d.ts`; the prototype avoids private runner imports and name-regex selection. Public shape does not imply permanent behavioral stability. Task IDs embed Vitest's file/task identity, so the adapter's `[project, relative file, task ID]` identities are stable only across the tested repeated discovery with unchanged tests, root, and pinned tools. They are not durable cross-edit cache keys.

The adapter rejects empty, duplicate, and unknown selections. It checks that exactly the requested IDs actually executed; skipped/unselected tasks may remain in the reporter's file payload. Empty execution, interrupted runs, pending/unrecognized test states, and collection/runtime infrastructure errors cannot produce a passing result. Failing fixture runs cannot satisfy the baseline gate merely by agreeing with one another.

## Isolation and invalidation boundaries

The pure fixture and the stateful fixture exercise imports, mocks, hooks, module state, asynchronous callbacks, and top-level initialization. Three runs through one controller agree with two fresh-controller references per fixture. The generated probe additionally checks duplicate names, parameterized test collection, fresh module evaluations, and import-time environment updates. Every fixture and probe baseline must independently pass.

Controller configuration, Vite servers, and their internal caches may remain alive within a session. M0 does not measure cache hits or prove a transform-cache speedup. Test callbacks and their evaluated module graphs reside in the fresh workers observed here. Never turn off isolation to infer equivalent performance. Recreate the controller when changing configuration, plugins, setup, discovery inputs, dependency/runtime versions, or other controller-level state. Re-discover tests after source/test/configuration changes; stable identities across such changes have not been established.

Only `OXIMUT_*` experiment environment keys can change between runs; this is not an unrestricted application environment mutation API. No source edits or live mutation switching are performed. External services, files written by tests, global setup side effects, native resources, and concurrent-test attribution require additional isolation policies. Whole-run coverage is not per-test mutation attribution and cannot justify `no-coverage` classification. The coverage payload records source-file statement/function/branch totals and nonzero counters, rather than preserving a production coverage database.

## Deadlines, cancellation, and owned processes

`openController` starts a detached Python supervisor, which enables Linux child-subreaper mode and launches the Node controller. The parent and controller communicate on the inherited Node IPC descriptor. Test stdout/stderr are captured separately with a 32 KiB diagnostic cap. One operation runs per controller, and every operation has an absolute deadline; caller `AbortSignal` cancellation is also supported for runs.

Both timeout and explicit cancellation signal the supervisor, which repeatedly discovers owned descendants, verifies PID/start-time identity, kills remaining descendants, and reaps adopted children with `waitpid`. This handles a test's detached process escaping its worker process group. A private JSON report records tracked PIDs, reaping, final `/proc` states, and controller status. Cleanup succeeds only if all tracked processes are absent, rather than merely signalled or zombies. Normal `close()` performs the same cleanup after Vitest closes; a passing test that leaves a detached child is covered separately.

The CPU-hang probe first waits for a file marker written by the actual test callback after spawning a detached child. Readiness is bounded at 20 seconds, with a 30-second outer operation deadline. Only after that marker does it abort the active run. Its reported outcome is `cancelled`, and its gate requires both the known worker and detached child to be tracked, absent, and reaped, with cleanup under 3.5 seconds. A separate 1 ms startup-inclusive deadline probe requires outcome `timeout` and full cleanup, exercising the actual deadline path without claiming that its test callback started.

The supervisor's own cleanup loop is bounded at two seconds; the parent allows three seconds for supervisor termination. Failure to finish or produce complete cleanup evidence raises an infrastructure error; the parent makes a best-effort process-group kill on supervisor deadline failure. These bounds are prototype policies, not portable real-time guarantees. Python, Linux `/proc`, and working subreaper semantics are required. Non-Linux platforms are rejected. This is process ownership and cleanup, not a security sandbox or memory limit. Detached descendants can outlive an individual passing run until controller close; per-job cleanup within a reused controller needs further work before arbitrary application workloads are supported.

## Evidence and release implication

The reproducible report is `.oximut/experiment.json`, generated with:

```sh
export PATH=/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:$PATH
node packages/vitest-adapter/experiment.mjs --output .oximut/experiment.json
node --test packages/vitest-adapter/test/lifecycle.test.mjs
```

Twelve gates cover fresh/reused result agreement, passing fixture baselines, stable discovery, successful selected-result agreement, whole-run coverage, module reevaluation, actual CPU-hang cancellation, absolute deadline cleanup, passing probe runs, updated environment in the reused controller, the fresh environment reference, and normal detached-child reaping. The JSON includes raw test outcomes, coverage counts, module evaluation tokens, worker/controller PIDs, and cleanup evidence. The integration test asserts these gates and key observations.

This clears a Linux-only lifecycle feasibility gate. It does not establish persistent-worker reuse, cross-mutant isolation, broad project compatibility, per-test coverage, source invalidation, or elapsed-time/memory improvement. Performance decisions must use the separately recorded ordinary Vitest and tuned Stryker baselines and future matched fresh/reused benchmarks. Correctness remains a release prerequisite even if those measurements show no advantage.
