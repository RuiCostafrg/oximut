# Warm-runtime isolation experiment

This is a standalone structural probe of pinned Vitest 4.1.12, not a product mode
or performance benchmark. Fresh forks remain Oximut's default.

The repository now uses Vitest 5. To run this probe, first restore its dependencies
(do not commit these two files):

```sh
git show 34acb9fee79a9e010f956b1326c52dc537aa1a64:package.json > package.json
git show 34acb9fee79a9e010f956b1326c52dc537aa1a64:package-lock.json > package-lock.json
npm ci --ignore-scripts
```

From the repo root, with those dependencies and Node 26.3.1:

```sh
node benchmarks/experiments/warm-runtime-probe/probe.mjs --output /tmp/oximut-stock-probe-new
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case warmVm-true-separate --output /tmp/oximut-host-probe-new
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case guardedVm-true-separate --mode module-only --output /tmp/oximut-guard-clean-new
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case guardedVm-true-separate --mode host-dirty --output /tmp/oximut-guard-dirty-new
```

Output directories must be new. The first command runs 12 stock cases. Every
case uses one controller/Vitest instance; `separate` calls
`runTestSpecifications` twice, whereas `batch` submits both files together.
Each case executes 16 assertions and records two state snapshots to a side
channel, without parsing console formatting. Requested and effective isolation
settings are recorded separately. Deliberately failing controls retain Vitest's
exit code 1 and failed assertion counts; they are never labeled fresh.

The guarded warm-host case uses a custom `poolRunner` and a pinned private VM
entrypoint. It drains each job's stop/RPC lifecycle before it parks or recycles
the host. The unguarded `warmVm` counterexample remains executable and is unsafe
for mutation jobs. No adapter or CLI option enables this experiment. Both custom
pools require `isolate: true` and separate serial calls.

`guardedVm` is the explicitly named guarded host-reuse experiment. It is selectable
only in this probe with `--case guardedVm-true-separate`; `--mode module-only`,
`env-only`, `fs-only`, or `host-dirty` selects the mutations. A clean guard may
park the one host; any environment change other than the pinned Vitest `SSR`
startup transition (absent to `1`), any `node:fs` descriptor change, missing
guard evidence, or ambiguous result recycles it after stop acknowledgement and
RPC drain. The controller environment is never adopted from the test worker.
Guarded cases must pass every assertion, report the exact expected changed keys,
and independently demonstrate reuse or recycling using worker identities. Stock
and raw warm negative controls retain their failures and shared-state evidence.

The guard is deliberately shallow. It does not reset or prove safety for object
internals, asynchronous changes after observation, native timers/listeners/file
descriptors/addons, or process-wide C++ state. This is not a fresh-process
equivalence or product guarantee. Test-owned changes to the special `SSR` key
are outside the supported guard scope because they cannot be distinguished from
that framework startup transition.

Every controller runs in its own process group with a 45-second deadline. The
probe waits for closure and verifies the group is gone; timeout/leak/error paths
fail rather than publishing a successful case. Generated projects are removed;
input metadata, snapshots, structured results and stdout/stderr remain in the
specified evidence directory.

[Observed results and limits](REPORT.md) and [latest native control metrics](BASELINE.md)
are kept separate. Local Android/PRoot timings are not qualified performance evidence.
Retained captures are under [benchmarks/results](../../results/2026-11-05-warm-runtime-probe/).
The native workflow also runs `node --test benchmarks/experiments/warm-runtime-probe/test-validation.mjs`
to check that corrupted observations, counts, identities and exit statuses are rejected.
