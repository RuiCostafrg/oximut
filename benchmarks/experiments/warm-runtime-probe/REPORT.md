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

Raw records are in [stock-results.jsonl](../../results/2026-11-05-warm-runtime-probe/stock-results.jsonl) (12 cases) and
[warm-host-results.jsonl](../../results/2026-11-05-warm-runtime-probe/warm-host-results.jsonl) (one case). Every case completed, closed its
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

## Historical delegated guarded probe (`cb10fbd`)

At delegated revision `cb10fbd`, the experimental `warmVm` pool had a per-job host
guard. This section describes that historical source; the current guarded pool
is `guardedVm` and current rerun commands appear below. Pinned Vitest
`onModuleRunner` is the snapshot point after VM setup and
before user imports. The pinned VM runtime changes `SSR` from absent to `1` after
that callback; the guard accepts only that known startup transition. Other
environment key additions, deletions and value changes are reported by key
only. Own-property descriptors and descriptor values on `node:fs` are compared
without serializing their values. No environment values are logged. Because a
test-owned `SSR` change to `1` is indistinguishable from startup, that key is
excluded from the supported mutation scope.

After the normal stopped acknowledgement, the PoolRunner has closed its RPC, and
only then does the custom worker park a clean host or terminate a dirty/uncertain
host. Missing guard evidence fails closed. The next worker is created with the
controller's original environment. Final disposal terminates any parked host.
The tests retain eight assertions per file, exact file/test inventory, complete
result validation, no collection/unhandled errors, 45-second deadlines, and
process-group cleanup checks.

### Final source-hashed run matrix

All selected jobs completed with Vitest 4.1.12 on Node 26.3.1 linux/arm64.
Every case passed 16/16 assertions, had two distinct realm UUIDs, closed Vitest,
and left no live controller process group. Each evidence directory contains raw
observations, structured results, metadata with exact invocation,
the three MJS source SHA-256 hashes, and cleanup booleans. Existing historical
evidence was not modified.

| Fixture mode | Guarded host identity | Guard evidence per job | Fresh-fork oracle |
|---|---|---|---|
| module-only | Same PID/threadId for both jobs | clean / clean | 16/16 |
| env-only | Same PID, threadIds 1 then 2 | `OXIMUT_WARM_STATE` / dirty | 16/16 |
| fs-only | Same PID, threadIds 1 then 2 | `__oximutWarmState` / dirty | 16/16 |
| host-dirty | Same PID, threadIds 1 then 2 | environment and fs / dirty | 16/16 |

For module-only, the two observations have different realm UUIDs while sharing
the same worker. For each dirty mode, the second observation uses a different
threadId and realm UUID; both jobs begin with clean module/global/env/fs/spy/timer
state. Dirty channel summaries contain only names and descriptor-change keys, no
secret values. Full evidence is under `benchmarks/results/2026-11-05-warm-runtime-probe/final2-*`;
the fs-only fork oracle rerun is `final2-fork-fs-rerun` there.

Historical commands at the delegated revision `cb10fbd`, with the pinned Node
directory on `PATH` (not reproduction commands for the current checkout):

```sh
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case warmVm-true-separate --mode module-only --output benchmarks/experiments/warm-runtime-probe/evidence/final2-warm-module
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case forks-true-separate --mode module-only --output benchmarks/experiments/warm-runtime-probe/evidence/final2-fork-module
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case warmVm-true-separate --mode env-only --output benchmarks/experiments/warm-runtime-probe/evidence/final2-warm-env
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case forks-true-separate --mode env-only --output benchmarks/experiments/warm-runtime-probe/evidence/final2-fork-env
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case warmVm-true-separate --mode fs-only --output benchmarks/experiments/warm-runtime-probe/evidence/final2-warm-fs
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case forks-true-separate --mode fs-only --output benchmarks/experiments/warm-runtime-probe/evidence/final2-fork-fs-rerun
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case warmVm-true-separate --mode host-dirty --output benchmarks/experiments/warm-runtime-probe/evidence/final2-warm-host
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case forks-true-separate --mode host-dirty --output benchmarks/experiments/warm-runtime-probe/evidence/final2-fork-host
```

Validation commands:

```sh
node --check benchmarks/experiments/warm-runtime-probe/probe.mjs
node --check benchmarks/experiments/warm-runtime-probe/warm-pool.mjs
node --check benchmarks/experiments/warm-runtime-probe/warm-vm-worker.mjs
git diff --check
```

These runs are structural probes, not timing evidence. Wall/CPU/RSS/process-tree
metrics, phase ratios and confidence intervals are unmeasured/N/A. The supported
scope is limited to observed environment key changes (except the pinned `SSR`
startup transition) and shallow `node:fs` own-property descriptor changes at
the pinned pre-import/post-teardown boundary. Test-owned mutation of `SSR` is not
covered.
Mutation of object internals, asynchronous changes after observation, native
timers/listeners/file descriptors/addons, and process-wide C++ state are not
proven reset by terminating a thread. Fresh-process equivalence is not claimed;
the raw warm counterexample and stock negative controls remain required.

### Parent review and executable counterexamples

The delegated revision `cb10fbd` above called its guarded pool `warmVm`. Its
eight retained runs demonstrate the narrow guard behavior, but parent review
found three harness gaps: the raw warm pool was no longer selectable, a blanket
distinct-realm assertion rejected stock negative controls, and guarded runs did
not require zero failed assertions. The fixture-mode parser also accepted unknown
modes. These were fixed before integration. The current guarded pool is named
`guardedVm`; `warmVm` again selects the raw, unsafe host reuse control. Historical
records and source hashes above remain unchanged and do not describe the updated
source.

The parent reran the entire stock matrix, the raw warm counterexample, and the
four guarded modes plus matching fork oracles into the original
`benchmarks/experiments/warm-runtime-probe/evidence/parent-reviewed/` location.
The new metadata hashes the executed sources independently. All guarded and
fresh-fork cases pass all assertions; clean guarded jobs reuse one host with
distinct realms and dirty guarded jobs recycle it. The guarded harness now also
requires exact dirty channel names and matches each guard identity with its
independent test observation. Stock reused batches retain their failed assertions
and shared realm; raw warm reuse retains its two host-state failures. Captured
console logs were left in `/tmp` and are not part of the committed evidence.

Regression verification temporarily replayed the delegated probe and injected
one failing local-module assertion into otherwise clean guarded fixtures. The
old probe returned success despite those test failures; the fixed probe rejects
them. Replaying a stock `forks-false-batch` also fails the old blanket realm
assertion and is correctly retained as a failing-state control by the fixed
probe. The injected sources were restored before recording normal evidence.
The native `warm-runtime-probe.yml` workflow repeats this structural matrix and
preserves its source archive and raw outputs, with no performance score.

All retained captures now live under `benchmarks/results/2026-11-05-warm-runtime-probe/`.
Their bytes and original invocation/source metadata are unchanged. Moving them
to the existing results area excludes raw captures from source releases without
relaxing the archive validator's extended-header rejection. The historical
delegated sources are retained alongside the captures under `sources/cb10fbd/`.
The current validation helper is also source-hashed in new runs. It requires
observed leak fields, expected worker/realm identities, exact outcomes, exact
scenario inventory and matching controller exit status; counts alone do not
establish a leak.

To rerun the current matrix into new directories, without overwriting retained
evidence (first export the pinned Node directory on `PATH`):

```sh
probe_output=$(mktemp -d /tmp/oximut-warm-reproduction.XXXXXX)
node benchmarks/experiments/warm-runtime-probe/probe.mjs --output "$probe_output/stock"
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case warmVm-true-separate --output "$probe_output/raw-warm"
for mode in module-only env-only fs-only host-dirty; do
  node benchmarks/experiments/warm-runtime-probe/probe.mjs --case guardedVm-true-separate --mode "$mode" --output "$probe_output/guard-$mode"
  node benchmarks/experiments/warm-runtime-probe/probe.mjs --case forks-true-separate --mode "$mode" --output "$probe_output/fork-$mode"
done
```

This experiment establishes only the tested detection/recycling mechanism. It
does not justify a product mode or further optimization of this shallow guard.
Next, measure CPU spent in the remaining resource accountant as well as worker
bootstrap before choosing a production change. `resources.rs::sample_with_readers`
currently reads each process's RSS for the aggregate and again for slot totals;
each `smaps_rollup` read walks kernel mappings. That is a source-supported cost
hypothesis, not measured CPU attribution. Preserve fresh reads, sampling cadence,
admission correctness and cleanup gates while gathering diagnostic evidence.
