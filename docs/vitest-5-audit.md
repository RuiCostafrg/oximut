# Vitest 5.1.0 adapter audit

The supported dependency pair is exactly `vitest@5.1.0` and
`@vitest/coverage-v8@5.1.0`, on Node 26.3.1. Source anchors below refer to the
published files installed by `npm ci`, relative to `node_modules/vitest/`.
Historical measurements and experimental probes remain records of Vitest 4.

Reviewed upstream [migration notes](https://vitest.dev/guide/migration/) and
[5.1.0 release notes](https://github.com/vitest-dev/vitest/releases/tag/v5.1.0).
Vite is now a peer, mock clearing defaults to true, test-name regexes use
` > ` between suites, and deprecated runner packages/entrypoints are removed.
Oximut uses Vitest test IDs for selection, not name regexes, and respects the
user's mock configuration. Its Node-only/single-project gates remain in force.

## Contracts

In this table, `I` means `dist/chunks/index.Di8au7q4.js`, `C` means
`dist/chunks/cli-api.COfZo8lK.js`.

| Internal | Vitest 5.1.0 evidence | Change and adapter action |
| --- | --- | --- |
| Per-run worker limit | I:6096,6289-6290,6176 | Preserved: grouping reads current project config, then sets pool capacity. Baselines may parallelize; mutants restore one worker. |
| Per-run bail serialization | I:4034,5287,6572,6762 | Preserved: worker start reads freshly serialized config. Keep scoped bail=1 for mutants and 0 for baselines, restored in finally. |
| Bail cancellation observation | `dist/chunks/index.w_oBZ9N8.js:196-200`, I:5109; C:13527,13563 | Preserved: worker sends `test-failure`, RPC calls `cancelCurrentRun`, listeners are cleared per run. Keep scoped public-method observer. |
| Reporter completion reason | C:12279-12289 | Preserved: cancellation reports `interrupted`. Keep real failed-test/error requirement and all error precedence. |
| Per-run environment | I:6116-6122 | Preserved: process env, root env, then project env merged each execution. Import-time mutant selector still reaches fresh workers. |
| Projects and Vite graph | C:12731,12949; I:6507,1583,4927 | Preserved: projects array and environment module graphs remain. Vite's compatibility graph supports full and file invalidation; alternating re-export/query-import tests exercise it. |
| Transform-only warming | I:1583-1584,2113-2133 | Preserved: Node uses SSR; transform requests can populate its graph without running tests or imports. Keep SSR graph traversal. |
| Fork/thread termination | I:5943-5951,5962-5985,6197-6207,5362 | Preserved: isolated runners do not enter reuse; pool close drains active/shared/exit promises and resets cancellation. Keep guarded pool close and deadline. |
| Compile-cache execArgv | I:6138-6146,5476,5492 | Preserved: project execArgv is read per execution, passed to fork, entry remains `dist/workers/forks.js`. Keep fork-only preload. |
| Stop IPC and profiling | I:5243,5280,5350-5351,5664-5697 | Preserved: request/response marker names, start/run/collect/stop and started/testfileFinished/stopped shapes remain. Compile-cache and profiling helpers need no protocol change. |
| M3 current test | `dist/index.d.ts:1047`; `dist/chunks/index.CZzC1gGg.js:8451`; `dist/chunks/run.CjdgFZiW.js:1845-1851` | Changed: runner bundled into Vitest. Replace `@vitest/runner` import with `TestRunner.getCurrentTest()` from `vitest`, sharing the executing runner's state. |
| M3 setup and test inventory | I:4038,5287,6762; C:12282 | Preserved: setup files serialize afresh; reporter modules expose test IDs/results/options. Keep ready-stream, inventory, unsupported-configuration and attribution gates. |
| File environment pragmas | I:620,1582 | Preserved: first vitest/jest pragma precedes config. Keep pre-execution Node-only rejection. |
| Filesystem transform cache | I:904,1036-1045,9239 | Changed: enabled by default; source-byte cache keys cannot distinguish mutation/probe transforms. Force `fsModuleCache: false` in M0/M1/M2, validate it in M1/M2. Initial switching tests reproduced false survivors before this fix. |
| M0 discovery/selection/coverage | C:12280; I:6153-6156 | Supported reporter coverage and specification test IDs remain. Lifecycle oracle checks fresh equivalence, exact selection, env changes, coverage and hung-tree cleanup. |

M1/M2 still reject every Vitest version other than 5.1.0 before creating Vitest;
M0 has the same exact-version guard. Fresh isolated forks remain the default.
No failure or attribution gate was relaxed.

## Benchmark control

The requested `BEFORE_COMMIT` and campaign `BASELINE` are main
`34acb9fee79a9e010f956b1326c52dc537aa1a64`. Its unchanged persistent worker,
executed with this branch's Vitest 5 dependencies, returns
`Unsupported Vitest 5.1.0; expected 4.1.12` at initialization. It cannot qualify
as a paired control with these node_modules. Even bypassing that guard would
leave the removed M3 runner import and unsafe filesystem cache. The harness
retains all gates and must report the control failure, not comparative ratios.
