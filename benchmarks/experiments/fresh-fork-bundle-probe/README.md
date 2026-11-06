# Fresh fork bundled runtime feasibility

This experiment asks one structural question: can the pinned Vitest 4.1.12
fork worker entry and its runtime closure be bundled while creating a new OS
process for every test specification? It is not a performance comparison and
does not implement V8 snapshots or change product isolation.

The controller uses `createVitest` and the public experimental custom pool
factory. The factory constructs Vitest's real `ForksPoolWorker` and, for the
candidate only, changes its worker entrypoint to the Rolldown output. Each of
two specifications is run with a separate `runTestSpecifications` call and
`isolate: true`; each must use its own PID and realm. Stock and bundled runs
use byte-identical fixture inputs and check exact identities/counts, errors,
local ESM, external CJS/ESM, globals, environment, a builtin property, spies,
and fake timers. Controllers are separate processes. Vitest is closed and
worker exits are checked; a bounded process-group cleanup runs on timeout.

Rolldown bundles the actual `vitest/dist/workers/forks.js` static entry graph.
Node builtins and the explicitly resolved `@vitest/runner` API remain external.
The latter owns collector state that the test-facing API also imports. A fully
inlined runner produced Vitest's "failed to find the runner" error locally;
using one native collector module in each fresh process avoids that duplication.
This is a bundling boundary, not a substitute runner or shared worker.

An AST transform preserves source-module `import.meta.url`, `import.meta.resolve`
and computed-import bases. Strings containing `import.meta` are left intact;
unknown metadata access fails closed. Literal imports remain available to the
bundler. Computed imports and optional dynamic dependencies remain runtime work
and are recorded. The probe does not claim that all framework code was inlined
or that optional environments/trace providers are supported.

The probe hashes the entry, emitted module sources, bundle, shared runner files,
fixture inputs, lockfile and all four JavaScript probe/oracle sources. Tooling
dependencies are declared directly and pinned in the lockfile. Generated bundles retain original
installation URLs and are bound to that checkout; they are not relocatable
release artifacts. The first unsupported or runtime failure is retained in
`evidence.json`. There is no fallback wrapper loading the original worker entry.

## Run

On the pinned native environment (Node 26.3.1, Ubuntu 24.04 x86_64):

```sh
npm ci --ignore-scripts
node --test benchmarks/experiments/fresh-fork-bundle-probe/test-source.mjs
probe_evidence_dir=$(mktemp -d /tmp/fresh-fork-bundle-probe.XXXXXX)
node benchmarks/experiments/fresh-fork-bundle-probe/probe.mjs --output "$probe_evidence_dir/run"
```

The output path must be a new empty directory. The dedicated workflow runs the
focused behavioral checks and probe, and uploads evidence for both supported and
unsupported outcomes. Source evidence is staged immediately after checkout;
dependency, focused-test and probe logs are retained when later steps fail.
Hard cancellation or runner loss can still prevent artifact upload. Concurrency
is bounded per PR/ref without cancelling historical diagnostic attempts.
There are no automatic retries. Format version 2's `bundlePreparationMs` covers
tool loading, bundle creation, output inspection and all bundle-metadata hashing.
`bundlerLoadMs` is nested within it. Controller entry, lifetime, Vitest initialization, each job
including worker drain, and close have separate monotonic diagnostic timings.
These measurements include probe overhead and do not certify speed. Whole-tree CPU user/sys,
aggregate RSS, process counts, paired ratios/CIs, and
`resources.phaseWallMs` are unmeasured/N/A; this probe does not use the
process-tree accounting harness. No result is called passing unless the full
structural oracle completes.

The reviewed local Android/PRoot run passed 16/16 assertions in each mode with
four distinct worker PIDs and four distinct realms. This establishes only the
controlled fixture result. Local timings are not native performance evidence;
an exact native colord campaign remains required for performance claims.

The [first native structural run](evidence/2026-11-06-native-42150504514/README.md)
also passed both modes. Its complete required-metric limitations, narrower stage
timings, identities and provenance are retained. It establishes no speedup.
Later probe revisions separately time controller imports, discovery and worker
drain that were unpartitioned in that first run.

An unsupported bundle says only that this pinned entry graph needs further
semantic work or is incompatible with this bundling approach. A successful
oracle would establish only this fixture's fresh-process structure and state
reset behavior. It would not prove faster startup, colord equivalence, a V8
snapshot benefit, full Vitest compatibility, or any wall/CPU/RSS target.
