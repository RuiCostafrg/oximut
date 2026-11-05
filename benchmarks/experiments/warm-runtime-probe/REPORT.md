# Evidence: warm runtime versus fresh job state

2026-11-05, Node 26.3.1, Vitest 4.1.12, local Android/PRoot aarch64. These are
structural observations, not native benchmark results. Wall, CPU user/system,
peak aggregate RSS/process counts, `resources.phaseWallMs`, performance ratios
and bootstrap CIs were not measured by this probe. The separately qualified
[native control](BASELINE.md) retains all those metrics and misses wall/CPU goals.

## Executed oracle matrix

The rewritten probe uses one `createVitest` controller per case, exact test counts,
file-backed observations, and eight state assertions per file. Each first file
mutates its module state, globals, environment, a builtin object, a spy and fake
clock after its assertions. The next file requires initial state again.

| Configuration | Effective isolation | Schedule | Worker reused | Assertions passed / total |
|---|---|---|---|---:|
| forks, requested true | true | separate | no | 16 / 16 |
| forks, requested true | true | batch | no | 16 / 16 |
| forks, requested false | false | separate | no | 16 / 16 |
| forks, requested false | false | batch | yes | 9 / 16 |
| vmForks, either request | false | separate | no | 16 / 16 |
| vmForks, either request | false | batch | yes | 9 / 16 |
| vmThreads, either request | false | separate | no | 16 / 16 |
| vmThreads, either request | false | batch | yes | 9 / 16 |
| experimental warm host, requested true | true | separate | yes | 14 / 16 |

Raw records are in `evidence/stock-results.jsonl` (12 cases) and
`evidence/warm-host-results.jsonl` (one case). Every case completed, closed its
Vitest instance, and left no live controller process group. Different thread IDs
in one PID count as different workers; PID equality alone does not prove reuse.

Stock VM pools force `resolved.isolate = false` in
`coverage.DM_a_rWm.js:379`. Their grouped two-file batch uses one VM context. Thus
requesting `isolate:true` does not establish per-file realm isolation for these
pools. Each raw reused batch leaked local ESM, external CJS/ESM, a global property,
`process.env`, a custom `node:fs` property and the fake clock. Vitest restored the
spy. Separate calls used distinct workers and passed, with no forced pool close
between those stock calls; this confirms they did not amortize the worker.

## Custom warm-host mechanism and blocker

The experimental `poolRunner` reuses a `VmThreadsPoolWorker` host. Each job retains
its normal start/run/stop handshake, drains the pool, removes the old runner's
callbacks/streams and closes its RPC, while preserving the host thread. The custom
VM entrypoint keeps its dispatcher but removes completed RPC listeners before
acknowledging stop. Final shutdown explicitly terminates the retained thread.
No shared installed dependency is patched.

The two jobs used the **same PID/thread ID but different realm UUIDs**. Local ESM,
external CJS/ESM, globals, spies and fake clocks were fresh. However, the second
job still observed `process.env.OXIMUT_WARM_STATE = 'dirty'` and the custom
`fs.__oximutWarmState = 'dirty'`. These are two explicit oracle failures, not a
fresh-state implementation. Do not ship this raw custom pool.

Source explains the observations: `Pool.schedule` in
`cli-api.B0Pz1_R7.js:3543` requires a non-isolated compatible next task already
queued for stock reuse; otherwise it stops the runner. `runVmTests` in
`vm.CXMd5FHa.js:708` creates a context and external executor per task, but injects
host `process`. `init.k9zZ9sLh.js:138` creates a new evaluated-module map per
execution. Shared file/package metadata caches alone are therefore not proof of
shared evaluated CJS/ESM instances; the custom-host observations distinguish them.

The original delegated draft used separate CLI processes and constant-valued
snapshots. It did not exercise same-controller successive calls or assert absence
of contamination. It also encountered a Rolldown loading failure. The parent
review replaced that harness; the pinned native binding loaded and all final
cases above executed. The early failure is not runtime-isolation evidence.

## Next bounded experiment

Add an experimental host-state guard that detects environment/builtin mutation
and recycles the host rather than trusting contaminated state. Test both dirty
jobs (must recycle and match fresh forks) and module-only jobs (must reuse a host
while getting fresh realms/module instances). A shallow guard is not a general
fresh-state guarantee: native-addon state, native timers, descriptors, listeners,
async leaks and selectors still need investigation before any product mode.
Only after the fresh-process oracle passes a declared supported scope should a
native paired wall/CPU/RSS campaign be run. No speedup, goal or release is claimed.
