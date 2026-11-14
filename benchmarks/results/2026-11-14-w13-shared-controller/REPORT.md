# W13 shared-controller integration: bounded colord correctness passed

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

## First native attempt and corrected prerequisite

[Run 43648236662](https://github.com/RuiCostafrg/oximut/actions/runs/43648236662)
at exact head `6d877f0fa3dd0fef5304f04d793bd17b681891d6` passed the real
m2/shared-lane integration test with the original 25-second IPC bound. Its
fixture took about 11.5 seconds total; completed controller validation recorded
1,744 ms. Stock/candidate original baseline and inactive/A/B/A/timeout outcomes
matched. Twelve observed PID/realm identities were distinct, five candidate
forks actually used the prepared entry, and full-file fallback/URI assertions
passed. This resolves the local initialization timeout on the native fixture
without weakening the bound. It does not prove product-colord correctness.

The workflow then failed BEFORE compiling stock because the source archive
omitted the Cargo workspace member `experiments/oxc-smoke`. All product cases
were skipped. Complete raw artifact/log ZIPs, hashes, exact source equality and
a verification summary are retained in `native-build-prerequisite-43648236662/`.
GitHub's artifact digest `afa4ba69dca1381cc0a3012cf114b7b2971e8b7bd4cd3c5bf4e5c3eda1a35786`
matches the recovered ZIP. The omitted workspace is a build prerequisite error,
not a candidate semantic failure; no campaign had run.

The sole follow-up correction adds the declared workspace member to both source
archives and asserts every workspace manifest exists before cargo. Adapter,
experiment, thresholds, dependency pins and metric gates are unchanged. A new
workflow-file push reruns that corrected prerequisite and proceeds only if all
original gates pass. This is not a blind product campaign retry. Result pending.

Preparation timing scope is narrower than full end-to-end setup: the recorded
prepare wall/CPU starts after initial module/version checks and mkdir, and ends
before the final metadata write. Future performance accounting must time the
entire setup command, not treat that diagnostic duration or any omitted prefix/
suffix, extraction, validation or trace I/O as free.

The automatically triggered unchanged standalone structural workflow is completed/success: https://github.com/RuiCostafrg/oximut/actions/runs/43648236524 (head 6d877f0fa3dd0fef5304f04d793bd17b681891d6). This is separate from actual product equivalence.

## Second native attempt: harness name wiring, not a candidate assertion

[Run 43649350205](https://github.com/RuiCostafrg/oximut/actions/runs/43649350205)
at exact head `55c44d070983e27ae01604f4b6966fd1c703fd67` passed integration and
both isolated release builds. Ordinary Vitest and stock Oximut colord/workers2
ran. The stock report is complete with 10 mutants and 102 baseline tests. The
new orchestration then raised AttributeError: I called the unchanged existing
baseline-ledger validator by a nonexistent name (`disabled_validation_errors`)
instead of `phase_errors`. Stryker/candidate and remaining configurations never
ran. This is not a candidate equivalence or resource-budget pass/failure.

The full artifact/log ZIPs, exact source and binary hash verification, report,
traceback and metadata are retained in `native-harness-wiring-43649350205/`.
The actual existing `phase_errors` function is now called, with its strict rules
unchanged. Three cheap local wiring tests use the captured stock ledger, verify
it passes, and verify missing instrumented validation, reduced concurrency and
admission pauses reject. Python bytecode creation is disabled for that test to
avoid introducing generated files into the installation-validation inputs.
Adapter, bundle helper, thresholds and all mutant/inventory/sentinel gates are
unchanged. No local product command ran and no scored campaign was repeated.
A separately identified native attempt is required to complete the remaining
correctness gate; this report makes no product-qualified or performance claim.

## Final native correctness result (declared scope only)

[Run 43651190848](https://github.com/RuiCostafrg/oximut/actions/runs/43651190848)
completed successfully on exact tested head
`afab0bb18722f8d9010b8fe4695bb3bb71323b66`. Real m2/shared-lane integration,
three harness-wiring checks, both isolated release builds and all four product
correctness configurations passed. No new candidate semantic or resource
invariant failed. No assertion, threshold, budget or classification gate was
relaxed to recover the two orchestration failures above. Both failures remain.

Independent recovery recomputed exact edited-source identities directly from
raw reports plus retained original project sources, not just the green summary.
For colord at workers2 and4, all 10 IDs/outcomes agree across stock Oximut,
opt-in Oximut and Stryker. For colord-scale at workers2 and4, all 206 agree.
Raw Oximut IDs/outcomes also agree stock versus candidate. All 16 tool commands
(four ordinary/stock/Stryker/candidate groups) have valid inventory, source/scope,
sentinel, resource-measurement and cleanup evidence. Original baseline tests are
102 passed / zero failed/skipped in every Oximut report; complete instrumented
coverage and original/instrumented disabled-validation ledgers verify. Every
sentinel is killed. Matched effective workers are exactly 2 or 4 as requested,
with zero admission pauses. There are no sampled-memory errors, incomplete
classifications, cached mutant provenance, timeout/tool cleanup errors or
normal-command descendant cleanup interventions in the qualified observations.

All 432 required candidate executions have 432 distinct real fork PIDs, with
birth identities and actual command lines naming the prepared bundle. Dispatch
selectors match every exact report mutant ID once, with full-file paths retained.
No candidate mutant was executed by an unobserved stock fallback or warm/thread
worker. Baselines are intentionally and explicitly stock, not bundled. The
real-adapter integration fixture separately verifies full/file fallback, original
URI and hard-hang cleanup with 12 observed distinct PID/realm markers.

The recovered source tar's 127 regular files equal Git head `afab0bb` byte for
byte. Both raw binaries match strict isolated-build metadata: stock is exactly
main `5dfd17e53cd7b067586b3ad47363cb3ecee5873e`, candidate is the workflow head.
Installed-file inventory, six-module allowlist, 15-module native closure,
source/helper/bundle/map/registration hashes, per-config raw inputs, logs and
selector/proc evidence are retained. Node 26.3.1 Linux x64 executable SHA-256
`0b52710a27133a3baf45f0d1f10689ab874f50e5821d8e611486d17e20bcacdd`
matches the previously accepted upstream-archive/extracted-binary native proof.
Versions are npm 11.16.0, Rust/cargo 1.96.0, Python 3.12.3, Vitest/coverage 5.1.0,
Vite 8.3.5 and pinned Stryker 10.1.1. No dependency/lockfile changed.

`native-correctness-43651190848/artifact.zip` retains the complete raw artifact,
including both binaries, all project/report/output files, full worker/validation/
map traces, compressed file inventory, source tar and build/runtime provenance.
GitHub artifact ID `14135444518` has digest
`2c99541e2381378bc1b6c78c2cfef127ae158e2985ee76c1b6c82c2746eaa2dd`,
which matches the retained ZIP. Full workflow logs are in `logs.zip`.
`archive-file-hashes.json` binds every ZIP member; `verification.json` records
independent source, binary, scope, outcome, budget and fork checks, with the
executed offline checker in `verification-source.py`. No giant plaintext
installed-dependency inventory is duplicated. `correctness.json` and
`preparation.json` are readable summaries; full originals remain in the ZIP.

This is a **bounded opt-in product-colord CORRECTNESS pass**, not general product
qualification or a speedup. Coverage-provider/custom-loader support, arbitrary
escaping descendants, relocation, explicit Rust resource-exhaustion negative
fixtures and broad user configurations remain outside this new qualification.
The unchanged native budget/descendant code and all strict resource observations
in these ordinary product commands remain enforced. The native IPC fixture's
map/fallback tests do not establish every CLI compile-cache/config combination.
The source-checkout hook and absolute artifact/helper paths remain experimental;
default CLI behavior, dependency pins, selection/order, deadlines and main are
unchanged. Main stays unsettled.

## Next justified scoring plan (not executed)

A later separately authorized unprofiled paired native campaign can now compare
stock main5dfd17e, this exact opt-in and pinned Stryker at the same scopes/workers,
using existing strict correctness and build-integrity gates, one warmup and at
least five measured pairs. Include full setup-command wall/tree CPU/RSS, or
explicitly declare defensible install-time amortization, plus per-controller
full validation, per-worker artifact checks, map registration/cache costs and
invalidation. Diagnostic trace I/O is included in all current raw observations;
it must not be subtracted as an invented saving. Keeping it or disabling only
diagnostics for scoring must be declared and correctness requalified. Preserve
fresh processes, exact identities/budgets/cleanup, and all three wall <=0.50x,
CPU/peak aggregate RSS <=0.80x Stryker targets. No ratios, CIs, profiler or scored
performance comparison were produced in this task.
