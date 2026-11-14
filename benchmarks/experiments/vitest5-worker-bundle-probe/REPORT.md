# Bounded result: no-go for collector inlining on Vitest 5

Base: `6cc8ce0`. Branch: `perf/vitest5-worker-bundle`. Current implementation
and investigation are GPT-6.1 Sol work; PR44 at
`e036f54c6c4a255a55a411e96a353bf56c7fa995` supplied historical ideas only.
No cherry-pick, PR takeover, product integration or dependency change occurred.

## Decision

Do not score or integrate this bundle. The exact pinned runtime reproduces
collector singleton divergence when the worker collector is inlined and the
public test API remains native. This is a bounded no-go for that structural
boundary, **not a proof that every Vitest 5 bundling strategy is unsound or
unhelpful**. No speedup, mission-target success or equivalent-mutant result is
established. Main remains unsettled.

Vitest 5.1.0 no longer has PR44's external `@vitest/runner` collector boundary.
`dist/index.js` exports test/hooks from `chunks/run.CjdgFZiW.js` and TestRunner,
expect and vi from `chunks/index.CZzC1gGg.js`. The worker entry
`dist/workers/forks.js` imports `base.Cof-UHJf.js`, which calls startTests from
the same run chunk. Its module-local runner/defaultSuite/collectorContext state
must be shared with the API loaded by test code. The run chunk also owns
current-test and hook state. Externalizing the removed runner package cannot
preserve this identity.

The literal internal ESM audit finds 38 worker-reachable files (1,123,860
source bytes), 15 shared with the public API (684,847 bytes), and 23 private
files (439,013 bytes). This includes literal dynamic imports and excludes
external package closures; it is not a statement about startup CPU or savings.
The private graph includes computed environment imports, native module imports,
coverage/tracing imports, metadata resolution and loader registration. Simply
moving it into a bundle does not preserve those semantics. Optional edge VM,
happy-dom, jsdom and OpenTelemetry imports are absent and recorded explicitly.

A possible different design would externalize the native collector and all
other audited singleton owners, preserve exact native/dynamic import bases,
and bundle only approved private modules. This probe has **not** implemented
or qualified that design. Rewiring public APIs to a bundled collector would
instead require auditing the module evaluator, global API injection, custom
runner imports, mocks and external modules. Neither path has a verified
semantics-preserving rewrite here. Per the requested correctness gate, stop
this inlining approach at its first concrete identity failure.

## Execution

Pinned runtime: Node 26.3.1, Vitest/coverage-v8 5.1.0, Vite 8.3.5.
Tooling from unchanged lockfile: Rolldown 1.2.15, Babel parser 8.0.10,
magic-string 1.4.5. npm used for installation was 12.2.1. Local machine is
Android/PRoot aarch64; the completed run used the official Linux arm64 Node
26.3.1 binary, SHA-256
`c021380e64d1314d1218ab1f31e0f5b0f28f1f54ac779ef72a16c2bda0ca5c30`, verified
against the retained upstream SHASUMS256 file, followed by Linux `npm ci`.
This is local structural evidence, not native Ubuntu performance evidence.
Root `/root/oximut/node_modules` was inspected and is stale Vitest 4.1.12;
it was never used for this probe. This worktree has its own installation.

The stock inactive/A/B/A control passes the same named test four times, with
PIDs 26570/26590/26597/26604 and four distinct UUID realm markers. Selectors
are read at module import, sources stay unchanged, and pool drain follows each
job. The negative worker uses a new real OS process (26650), then reports
`Vitest failed to find the current suite` with zero collected tests. The stack
points to the native run chunk's getCurrentSuite assertion. Collection was
started by the second, bundled collector. Both controllers' process groups
are absent at close; every observed worker terminated with SIGTERM under
Vitest's normal isolated-fork shutdown.

The final harness correctly expects controller exit 1 only for this deliberate
negative control and requires the exact collector error. Four guard tests pass
on both local Android Node and Linux Node: metadata rejection, computed
import/require rejection, syntax-only URL rewriting with source map, and version
mismatch rejection. Lockfiles and package declarations remain byte unchanged.
No Rust build, heavy test suite, profiler, benchmark or CI run was started.

Broader contamination, builtin/global/env reset, fake clocks, mocks/hooks,
setup, external module state, late async work, thrown/import-time cases, source
map behavior and deadline/descendant qualification are **not passed or run**
beyond the narrow collection/selector/normal-cleanup checks above. They have
no meaning as candidate qualification after this first identity failure.

## Evidence and failed attempts

`evidence/final/` retains the complete completed run: emitted worker and map,
real virtual-runtime source, raw results/logs/statuses, immutable fixture,
observations, topology, source/tool hashes and full installation manifest.
`evidence/SHA256SUMS` binds the retained files. Absolute original source and
artifact paths remain recorded; archived artifacts are not runnable release
bundles. Failed-attempt subsets retain raw errors and available logs/results:

1. Android native binding namespace rejection, before bundling.
2. Fail-closed rejection of Rolldown's virtual runtime. Fixed by retaining and
   hashing the actual generated source, rather than hashing an unverifiable ID.
3. Topology inspection encountered an absent optional edge VM import. Fixed
   the *audit* to record missing optional edges without installing packages.
4. Stock harness tried to assign into an undefined config.env. Initialized the
   per-run env object before admission.
5. Both real runs completed, but harness incorrectly required exit 0 from the
   deliberately failing candidate. Raw result already proves the same collector
   divergence. Fixed expected exit handling; no candidate semantics changed.
6. Final completed negative-control capture, all expected assertions pass.

These were diagnosed harness/environment fixes, not blind retries of a failed
candidate. Failed subsets do not retain complete historical source snapshots or
duplicate full dependency manifests, so they are not independently reconstructible
qualification runs. The final source hash matches the committed probe.

## Next gate

There is no qualified candidate, so **no scored experiment is justified by this
result**. The smallest next structural experiment is an explicitly identity-safe
private bootstrap bundle with the collector/API closure external, tested on the
full requested stock/candidate oracle matrix in bounded native CI. If that
passes, the smallest later scored experiment is an unprofiled paired colord run
against current pinned main and Stryker, using exact original/instrumented
baselines, mutant IDs, equivalent outcomes, tree CPU and aggregate RSS gates.
Require wall <=0.50x and CPU/RSS <=0.80x with the campaign's paired confidence
bounds. Fresh-process default and all cleanup/deadline gates remain mandatory.
