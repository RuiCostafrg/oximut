# Shared framework identity: bounded no-go

Based on main `5dfd17e53cd7b067586b3ad47363cb3ecee5873e`. Experiment only;
no product integration, snapshots, reuse, dependency changes, Rust build, native
workflow, performance campaign, PR or merge. Main remains unsettled. The mission
is still wall <= .50x and CPU / peak aggregate RSS <= .80x Stryker with equivalent
outcomes and fresh processes. This experiment establishes none of those targets.
No estimate is derived from source or bundle bytes.

## Decision and first substantive failure

**Stop this candidate.** Its narrow identity oracle passes, and the deliberate
duplicate ingress fails that same oracle. However, the captured test failure's
framework frames point at `file:///tmp/shared-framework-identity-build-2/bundle/owner-19.mjs`
(e.g. 1397:35), rather than the original run chunk's source URI/position.
Emitting maps is insufficient: no runtime map registration/consumption mechanism
has been qualified for redirected native frames. Do not treat that stack as
source-map qualification. The duplicate itself is the expected negative control;
the emitted framework frames are the substantive fidelity failure.

Offline topology and executed-load inspection also show that the conservative
all-original-facade boundary **does not establish removal of framework loading
work**: 28 selected originals become 28 individual framework output modules,
each containing exactly one original, plus one Rolldown runtime module. The
passing worker loads 25 of those facades and evaluates each instrumented original
once. No selected original is also natively loaded in that passing worker.
That proves replacement without duplicate native evaluation for these ingresses;
it does not demonstrate linking consolidation, fewer reads or less total work.
Original package resolution still runs before redirection. Validation additionally
reads/hashes original sources and artifacts. This is not a savings claim.

No further candidate execution followed this fidelity failure. There is no
claim that Vitest can never support a safe redirector; this particular bounded
attempt is rejected. Do not coalesce chunks merely to make counts smaller:
earlier eager initialization and lost lazy boundaries require separate proof.

## Mechanism and actual assertions

`probe.mjs` inspects actual installed ESM with Babel parser. One Rolldown
multi-entry build uses strict export entry signatures, no tree shaking, maps,
and shared output references. It discovers current API owners from actual public
export declarations, rather than assuming historical chunk names. Every selected
original gets its own exact facade. ESM export/live-binding/cycle behavior is
left to the linker; full semantic preservation is **not** inferred from that.
Modules with `import.meta`, computed import or `require` contexts stay native,
keeping original URLs, conditional resolution and lazy native imports intact.
`new.target` is not import metadata. No unknown source syntax is rewritten.
The native evaluator, environment loader and native mock implementation remain
native. Chai stays native throughout; it is not copied into a bundle.

The bootstrap registers synchronous Node resolve/load hooks before dynamically
importing the original worker URL. Each resolution first calls the original
resolver with its real importer and conditions. Only exact audited resolved
file URLs are redirected; no namespace alias replaces all imports. Selected
framework query/hash forms reject rather than collapsing identity. Nonselected
native framework modules keep their original context and their selected imports
are redirected. This handles the observed Vitest `getCachedVitestImport` bypass
of Vite aliases and the native `import(file)` evaluator path.

The stock `createVitest` / `ForksPoolWorker` pool runs one actual job through
normal IPC, with Oximut's real lane runner and M3 attribution setup. Bootstrap,
bare public API, absolute native helper imports and direct owner imports agree
by function/class identity on collector/test, current-test getter, TestRunner,
expect and spy functions. The current-test object equals the executing task;
expect state and spy clearing cross the helper boundary. LaneRunner's superclass
is that same TestRunner. M3 emits ready and a hit with that task's exact ID.
Every observed audited evaluation counter is one (25 owners loaded). Native
Chai namespace identity also agrees. Positive PID 15428, realm
`5085a2eb-5f3c-46c1-8d19-38d4a9eadb7a`, one passed test, zero errors; normal
SIGTERM shutdown and no residual controller process group.

For the negative control only, a separate hook intentionally bypasses redirection
for exactly the collector URL plus `?deliberate-unredirected-negative`. Its native
relative dependencies still use normal resolution/redirection. This creates a
second collector without erasing the query. The unchanged current-test assertion
fails: helper getter is undefined while the canonical getter returns the actual
task. PID 19321, one failed test, controller exit 1 as required, normal SIGTERM,
no residual group. The positive loader itself rejects this query; the explicit
negative-only bypass is not supported candidate behavior.

## Missing gates and restrictions

Gate 1 is narrowly demonstrated for these ingresses, not universal closure
qualification. Full exact export-name comparison against native runtime
namespaces, arbitrary cycles/live bindings, lazy boundaries across all possible
ingresses and native ESM mocking helpers remain unqualified. Query/mock forms
are rejected only where the implemented check applies; mock/importActual
semantics are not tested. Alternate installations, custom evaluators/loaders
and nondefault conditional exports are **not safely admitted or proven rejected**.
This is an additional reason this scaffold must not become a supported launcher.

Gate 2 is not passed or run: no inactive/A/B/A matrix, multi-job freshness,
full inventory, hooks matrix, importActual/mocks/fake clocks, runtime env/cwd/argv/
clock/randomness checks, external-state reset, import-time throws or full
source-map/descendant/deadline matrix. One-job IPC, actual current-test M3
attribution, cross-ingress expect/spy state and normal cleanup are the only
executed subset. Source projects/tests never execute during the build; all
instrumented initialization occurs in a child. That does not establish every
runtime-observable environment invariant. Gate 3 fails to establish work removal.
No CI dispatch is justified by this result.

The next justified work is an offline design of original-URI map consumption
and a topology that genuinely combines loading work while preserving every
required ingress's initialization and lazy boundaries. It needs a new bounded
proof and explicit invalidation/trust design before broader semantics or native
qualification. Do not rerun this scaffold until green or resume W13.

## Artifact and cost boundary

The archive preserves both build attempts, all runtime attempt outputs, emitted
code/maps, generated bootstrap/helper/fixture sources, complete resolve/load
traces, original framework sources and the narrow observed native external
closure. `build.json` binds audited source URLs/hashes, source-context sites,
resolved edges, exact facades/exports/imports, output/map hashes, source inputs,
package/tool metadata and executable hash. `verdict.json` projects actual loaded
identities, evaluation counts, resolver conditions and the preserved failure.
The archive contains 91 deduplicated audited/observed source files with a
content-addressed inventory; it is not a 10k-file full-install manifest.

Runtime bootstrap embeds a manifest digest and separately verifies loader bytes;
the loader validates audited sources, emitted outputs/maps and package metadata
before worker import. Historical runs acquired that digest while preparing the
bootstrap, so they do **not** establish independent prelaunch artifact trust.
After stopping, the harness was hardened to require an independently supplied
expected manifest digest; this CLI guard was syntax-checked, not requalified by
rerunning the candidate. Mutable manifest self-consistency is insufficient.
Trusted launcher code and its expected digest must live outside an attacker-
mutable artifact. No install immutability or TOCTOU protection is enforced here.
Source changes invalidate audited source hashes; unobserved native dependency
changes are not fully covered by this boundary. Actual trace externals, including
Chai/Vite/@vitest/mocker, are inventoried after execution, not completely attested
before execution. A production artifact contract is therefore **incomplete**.

The build is installation-bound: original absolute URLs and external resolution
contexts are retained, and relocatability is unproven. Build tools never run in
the child. The narrower boundary avoids claiming that hashing unrelated build-
only installation files is necessary each fork; it does not waive validation of
load-bearing native closure or resolver metadata. Source overrides, symlink/
condition changes and custom resolvers require rejection or renewed provenance.

All setup, Node-hook registration, original resolver calls, source/output/map
reads and hashing, instrumentation, synchronous per-event trace appends, larger
output parsing, and possible map consumption costs are **unscored and retained**.
No CPU, wall, RSS or I/O-byte saving was measured. Maps are emitted and validated
as bytes but not correctly consumed in the observed stack. Future map registration
and parsing would be additional real costs. Larger eager chunks may worsen
performance. Nothing is subtracted from W13 data or treated as free.

## Setup and evidence history

Commands run in this checkout (unchanged pinned lockfile):

```sh
npm ci --ignore-scripts --no-audit --no-fund
/tmp/oximut-v5-node/node-v26.3.1-linux-arm64/bin/node \
  /data/data/com.termux/files/usr/lib/node_modules/npm/bin/npm-cli.js \
  ci --ignore-scripts --no-audit --no-fund
NODE_BINARY=/tmp/oximut-v5-node/node-v26.3.1-linux-arm64/bin/node \
  benchmarks/experiments/shared-framework-identity/run.sh /tmp/shared-framework-identity-build-2
/tmp/oximut-v5-node/node-v26.3.1-linux-arm64/bin/node --experimental-import-meta-resolve \
  benchmarks/experiments/shared-framework-identity/identity.mjs \
  /tmp/shared-framework-identity-gate-2 /tmp/shared-framework-identity-build-2
# Historical CLI; final harness additionally requires expected digest before --negative.
/tmp/oximut-v5-node/node-v26.3.1-linux-arm64/bin/node --experimental-import-meta-resolve \
  benchmarks/experiments/shared-framework-identity/identity.mjs \
  /tmp/shared-framework-identity-negative-2 /tmp/shared-framework-identity-build-2 --negative
```

Do not execute these as acceptance instructions: candidate is stopped. The exact
build-manifest SHA-256 is
`cc232a3cd8d9fb5a4459d7a7c55bec8b90b259c227ab0f26badcee24c3bf7898`.
Runtime Node 26.3.1 Linux arm64 executable SHA-256
`a76224241ff1ebcbf90adc2eb4600ddb048d19f883c6a9140b6c3ceab004e4f0`
matches the earlier retained verified upstream runtime provenance. Local host
is Android/PRoot, not native Ubuntu performance evidence. Pins: Vitest/coverage
5.1.0, Vite 8.3.5, Rolldown 1.2.15, Babel parser 8.0.10, magic-string 1.4.5;
installation npm 12.2.1. Package/lockfile bytes are unchanged.

Historical attempts are preserved, not overwritten:

1. Android Node cannot load Rolldown's native binding; use the existing verified
   Linux binary and install existing Linux optional bindings in this checkout.
2. Build 1 conservatively misclassified `new.target` as import metadata. Build 2
   fixes that AST audit mistake before any runtime candidate execution; it adds
   the spy owner to the selected closure. No source metadata is rewritten.
3. Gate attempt 1's fixture used Function-created native import in Vitest's VM:
   `A dynamic import callback was not specified`. Moved that helper import into
   native bootstrap only; candidate bundle/loader unchanged. Gate attempt 2 passes.
4. Negative attempt 1 fails the oracle but its reporter tries to JSON serialize
   the cyclic expected test task, losing the result file. Projection to message/
   stack only fixes evidence capture. Negative attempt 2 preserves the exact
   expected failure. No candidate semantics were changed for that capture.
5. Inspecting this negative capture exposes the framework-frame failure. Stop.

Recovered historical harness/source snapshots are verified against their executed
source hashes inside the archive; generated sources and raw failures remain.
The final independent-digest CLI guard is distinguished from executed source.
`audit-evidence.py` is an offline assertion/projection, not another runtime run.

Prior evidence read: W13 scored/failure-investigation reports and parent parking
decision at `db4184c`/`be2b041`; native map-base structural report; earlier
Vitest5 worker-bundle report; `0bd21a3` snapshot README; and the current
`docs/vitest-5-audit.md`. W13's unexplained four-lane start failure remains
unexplained; its non-reproducing replay is not reliability evidence. Its private
six-module bundle/native 15-owner boundary is not revived. The old dependency
snapshot duplicated identities and increased narrow-probe CPU; this attempt uses
fresh native evaluation with redirection, not snapshots or dependency preload.
