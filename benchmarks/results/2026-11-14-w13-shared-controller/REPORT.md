# W13 shared-controller integration: correctness gate pending

Main `5dfd17e53cd7b067586b3ad47363cb3ecee5873e` was merged into the existing
experiment branch as `c726d26c157f867e8f4ca5c18aa167345d98c8aa`. Prior probe
commits and evidence remain intact. Current integration is GPT-6.1 Sol work.
Main remains unsettled. No PR, release, merge into main or product qualification.

## Explicit scope

`OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP=/absolute/prepared/integration-manifest.json`
opts the real m2 shared-controller adapter into an installation-bound artifact.
It expands no public CLI contract. Omit the variable for stock behavior. Use the
explicit source adapter override with persistent/fresh-process execution; the
M1/fresh wrapper is outside this experiment and is not claimed as bundled.

`m2-worker.ts` loads the experiment only at explicit initialization. It checks
pinned versions/installation/artifact before Vitest starts and rejects threads,
custom plugins/runners/global setup, custom loader/preload flags, NODE_OPTIONS,
a non-Node environment, native module-runner mode and a second project Vitest.
Actual non-shared mutant execution is rejected, rather than silently using stock.
Existing transform-only warm commands remain transform-only; this adds no warm
worker or pre-spawn behavior. Baselines intentionally use the stock Vitest pool
and are explicitly recorded as such; this is not a bundled-baseline claim.

`shared-lanes.ts` accepts an optional internal hook. Its factory constructs the
same real ForksPoolWorker and overrides only its entrypoint, with stock entry
shape checks and artifact hashes before each fork. OS command line, PID/birth
identity and direct-parent checks prove a new process uses the prepared entry.
Run tracing verifies the selector against the worker's actual initial /proc
environment, then records exact whole-file dispatch. Vitest 5 config is sent
in its start message, not run; tracing reads the actual fork env accordingly.
No selection, ordering, lane bail, job deadlines, native sampler, admission,
budget, descendant verification or error-classification thresholds changed.

Preparation uses the SAME six-module allowlist, complete 15-module native
singleton closure and parent's corrected external source-map helper. No new
framework rewrite or public API substitution. Artifact paths must be absolute,
canonical and owned by the preparing checkout; changed/missing/symlinked inputs,
source/version/Node executable changes and relocation reject. Output is caller
owned, created once without overwriting; its UUID, build/source/tool/installation,
bundle/map/registration/helper and validation-inventory hashes are retained.
The output must remain available through controller/worker shutdown. Caller may
remove the owned directory afterward, or retain it as evidence; no hidden cache.

## Costs and limitations

The explicit preparation command is setup OUTSIDE product execution:

```sh
node --experimental-import-meta-resolve benchmarks/experiments/vitest5-private-bootstrap/integration.mjs /absolute/NEW-output
```

It loads Rolldown/Babel, audits/builds, hashes full installed files and adapter/
probe sources and emits compressed validation inventory. Wall and CPU are recorded.
Each new controller validates versions and executable, decompresses the inventory
and hashes ALL recorded files before creating Vitest. Each fork rehashes bundle,
map, registration and helper, and reads proc identities/command/env for diagnostics.
Worker map registration/stat/lazy parsing retains the qualified helper's costs.
Validation wall/CPU and diagnostic events are recorded; Rust samples actual
controller/worker costs. Preparation is not free, not hidden as install time and
not included in a product row; any future performance comparison must include
or explicitly amortize it, invalidation, trace I/O and cache/memory costs. No
resource/performance assumption follows from correctness-only runs.

This is a source-checkout/installation-bound opt-in, not a release or relocatable
artifact. Default product execution remains stock fresh processes. Coverage,
custom loaders and arbitrary escaping descendants gain no new support claims.

## Local evidence

Typecheck passes; the two changed adapter files pass the repository's formatter
when invoked from its package directory. No Rust build or product benchmark ran
on this fragile host.

The first real-controller fixture completed the stock sequence, but candidate
initialize hit the unchanged 25-second IPC bound before any completed validation
or bundled fork event. Preparation plus stock plus timeout took about 67 seconds.
The full-install hash pass is a plausible host I/O cost, not a measured diagnosis
of where every millisecond went. There is **no local integration pass**. The bound
was not raised and the campaign was not retried locally. This is preserved in
`local-init-timeout/`, with original input/worker artifacts and exact initial
source bytes. Three formatter line-wrap changes were reversed only in the archive
copy to recover its executed source, and verified against the failed prepared
manifest's recorded hash; live source was not reverted.

A trace LF escaping/recording bug was corrected after retaining the attempt;
this is not a threshold relaxation or candidate semantic qualification. The
native test runs the same 25-second bound before any product build/run, to
resolve whether this initialization failure persists on the requested native
host. Any real native invariant failure blocks product qualification and stops.

## Native plan (not a score)

The bounded workflow first runs the actual m2/shared-lanes integration fixture:
stock and candidate original baseline plus switched inactive/A/B/A, exact
outcomes/counts and full/file selection, full original bootstrap URI, module
freshness, hard synchronous hang cleanup and actual entry/selector PID evidence.
All raw IPC, stdout/stderr and statuses are retained; unsupported threads/paths
reject. It does not relax failed assertions or retry a failed configuration.

Only if that passes, two isolated release builds (max CARGO_BUILD_JOBS=3) pin
stock to main 5dfd17e and candidate to workflow HEAD. Stock source is extracted
from its Git tree, not another local worktree. The correctness orchestrator uses
existing real_project/m6 validations and strict compare_candidates build-integrity,
disabled-original/instrumented-baseline and cleanup gates without modifying them.
It runs one ordinary Vitest, stock Oximut, Stryker and opt-in Oximut command per
scope/config: colord10 and colord-scale206 at matched workers2 and4. Exact edited
source mutant sets/outcomes, exact Oximut IDs, ordinary test inventory, sentinel,
baseline ledgers and unchanged strict resource/admission/cleanup gates must pass.
Diagnostic selector/fork records must reconcile every executed candidate mutant;
otherwise a stock fallback cannot be called a bundled run. All resources are raw
diagnostic observations; no five-pair campaign, ratios, CIs or speed claim.

The workflow is branch-only/manual and initially triggers only when its own
workflow file is pushed, not on documentation/evidence updates. Raw evidence is
compressed with full per-file hashes; no giant repeated plaintext dependency
manifests. Native URLs and completed/pending gates are recorded after dispatch.
The next justified scoring plan exists only after this exact correctness gate
passes: a separately authorized unprofiled paired campaign accounting for setup,
per-controller/worker validation, trace overhead and full tree CPU/RSS, with
unchanged wall <=0.50x and CPU/RSS <=0.80x Stryker targets. No such scoring now.
