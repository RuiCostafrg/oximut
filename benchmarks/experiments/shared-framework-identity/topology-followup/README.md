# W14 topology-first follow-up: no candidate admitted

Base: `baa1920edbefc927392d8474f50d79e34d568225`, same checkout and
`perf/shared-framework-identity` branch. This is one read-only source/topology
admission audit. No new candidate build, worker, runtime hook, map consumer,
project evaluation, dependency installation, native CI, product run or scored
run occurred. Old sources, raw archives, negative control and history are unchanged.
Parent remains unsettled; no performance target or estimate is established.

## Proposed mechanism and bounded decision

The proposed new mechanism was to coalesce a nontrivial startup cohort into one
ordinary ESM implementation chunk, link its internal edges inside that body, and
retain exact per-original re-export facades for native/public ingress. Emitted
worker references could address that shared body directly while audited native
imports retain their precise export surfaces. This could remove some internal
Node linking requests *if* ingress/evaluation and all real costs qualified. No
removal is inferred from that design or from filenames, module counts or bytes.

**No-go before building or executing a candidate.** We could not admit a cohort
under the unchanged exact-facade evaluation-closure rule. For an ordinary ESM
body, importing any facade evaluates the entire cohort's top-level code; facades
do not make that code lazy. If originals A and B are grouped, preserving the
original evaluation closure for imports of either exact original facade requires
A statically reaches B and B statically reaches A. The audited graph has no
nontrivial static strongly connected component. All 231 pairs among the 22
startup-selected originals therefore have a retained ingress witness where one
cohort member was not originally initialized. A larger cohort necessarily contains
such a pair. This is a necessary-condition rejection, not execution evidence.

This admission rule deliberately preserves W14's audited resolved-file-URL ingress
surface. Its loader redirects **all 28 selected exact original URLs**, including
helper ingress to `task-utils.js`, not just the public `vitest` root. We did not
silently remove those paths, weaken evaluation markers, or infer harmless earlier
initialization from upstream `sideEffects: false`/`__PURE__` annotations. The
pairwise witnesses are exact accepted original URLs, not query identities or a
second installation. Public/native bare imports are audited separately below.

This does **not** prove every possible grouping changes an externally visible
outcome, or rule out a separate proof that certain earlier initializations are
unobservable. It does rule out admitting ordinary body coalescing under this
conservative unchanged closure/order oracle without such a proof. A compiler
with independently lazy module initialization, original resolution contexts and
true live-binding/cycle handling would be a different mechanism, not an ordinary
manual chunk. It has not been implemented or qualified. No unknown syntax was
rewritten and no registry/factory transformation was attempted.

## Audit and the strongest remaining pair

`audit.mjs` parses all **63 published distro `.js` sources** with the existing
pinned Babel parser, not only W14's worker/public graph. Every old audited source
hash must match the independently trusted W14 build manifest before its edges
can be accepted. All newly inspected files are hashed too. It retains literal
static and dynamic edge kinds, source positions, enclosing function contexts,
resolved URLs/failures, direct incoming sites from other public/native distro
files, package export/import conditions, top-level source summaries, static
closures, SCCs and pairwise witnesses. No framework module is imported/evaluated.
Computed dynamic imports are recorded as context, never promoted to static edges.

The parent's static facts are reproduced exactly:

- Worker `workers/forks.js`: 30 static Vitest modules, of which 22 are selected.
- Public `dist/index.js`: 16 static selected modules.
- Six selected originals outside the worker static closure: `acorn.C1kjbUFw.js`,
  `console.CQZ877Hi.js`, `constants.DLYM6cZY.js`, `globals.ZKtKSkEf.js`,
  `node.B9mkVmLH.js`, and public `index.js`.

These are source graph facts, not savings estimates. In particular, static
membership does not license evaluation before its original dependencies or a
native module's initialization. Six outside the worker closure does not mean
six universally unloaded modules: other public or later dynamic ingresses exist.

The named-ingress analysis includes the worker, public API, direct collector,
TestRunner and spy owners, plus every published nonselected distro file with a
literal static edge into the selected set: 31 distinct roots in total. Broader
source inspection includes `vitest/worker`, `vitest/runtime`, `vitest/node`, browser
and alternative worker source paths as *analysis*, not newly supported runtime
modes. The only startup pair with identical reachability signatures under these
named roots is **`run.CjdgFZiW.js` plus `task-utils.js`**. We did not pretend this
pair was already split by one of those named roots.

It still fails the exact per-original ingress gate. Original `task-utils.js`
imports only `pathe.M-eThtNZ.zq3TM4NT.js`; its static closure does not include the
collector. The collector imports task-utils at line 6. Grouping their bodies
would initialize the collector when an early native helper imports only the
exact task-utils file URL. The ordinary original helper does not do that.

This is more than an assertion that definitions must never move: the collector
captures `const RealDate = Date` at line 8, creates fixture WeakMaps at 1421–1423,
creates running-test state at 1852, and owns runner/defaultSuite/collectorContext
at 2549–2550/3257. Its exported `shuffle` uses `RealDate.now()` for the default
seed. A helper-only import followed by a change of `globalThis.Date` before a
later collector import distinguishes original capture timing from a shared
body's earlier capture. This is a source-level timing witness, **not an executed
fake-clock test** or a claim about outcomes on the unmodified host. No proof
that this early initialization is unobservable has been established. The audit
therefore stops instead of spending a worker/semantics run on this grouping.

## Native order, IPC boundaries and deferred paths

The audit stores source-ordered symbolic ESM dependency traversal for the named
ingresses, with non-Vitest native packages represented as **opaque barriers**.
These are not runtime traces and do not claim complete external-package cycle,
load or evaluation ordering. A candidate would still require the requested
stock/candidate phase oracle before handshake, start, public/helper ingress and
run; none was admitted to that stage.

In that symbolic worker traversal, native coverage/environment/evaluator/path/
trace/inspector modules remain interleaved with selected modules. The collector
is reached through `index.w_oBZ9N8.js` / `index.CZzC1gGg.js`, not by treating all
22 startup modules as one unordered phase. Native `base.Cof-UHJf.js` finishes
before `init-forks.BaDXQSD8.js` and the `forks.js` body. Sources give decisive
boundaries that a large manual chunk must not erase:

- `init-forks.BaDXQSD8.js:3` requires actual process IPC. Lines 5–9 bind process
  methods; line 12 registers the error listener, with a conditional SIGTERM
  listener at 11. This cannot be activated by a collector/helper-only import.
- `workers/forks.js:48` calls `workerInit`, which installs the IPC lifecycle.
  Moving that body into any shared helper implementation would activate worker
  behavior through an unrelated facade. Worker entry and init-forks are not
  admitted as a general startup cohort with helpers.
- `base.Cof-UHJf.js:62` dynamically imports console during environment setup.
- `setup-common.CqAQCXzx.js:10` imports globals only under `config.globals`;
  globals imports constants. They do not join the worker static cohort.
- `index.w_oBZ9N8.js:15` dynamically loads the default snapshot environment;
  `vitest/runtime` also exposes its node module through a separate public path.
- Acorn is static under the alternative native runner/mocker modules reached by
  deferred native paths, not the fork entry's static closure.
- Public `index.js` is a distinct API/native-helper ingress. Its export facade
  is not made eager merely because its implementation dependencies overlap.

The full incoming-site inventory preserves those differences. All six outside-
closure paths are excluded from startup grouping, without claiming that their
arbitrary relative order or all alternate environments are qualified.

The **seven W14 edges without `kind` are now explicitly recovered from AST**;
the old catch branch omitted kind on resolution failure. All seven are dynamic:

- `index.Bi0NXYPf.js:1246,1262`: `@edge-runtime/vm`, in setupVM/setup methods.
- `index.Bi0NXYPf.js:1289,1316`: `happy-dom`, in setupVM/setup methods.
- `index.Bi0NXYPf.js:1393,1461`: `jsdom`, in setupVM/setup methods.
- `traces.js:15`: `@opentelemetry/api`, inside the tracing constructor.

All are unresolved optional packages in this installation. They remain dynamic
edges with their actual function context and `ERR_MODULE_NOT_FOUND`, not static
startup dependencies, deleted edges or a reason to install extra packages.

## Actual removed versus added work and remaining gates

**Actual runtime work removed: none established. Actual new runtime work added:
none, because there is no new executable candidate.** These zeros are a stop
record, not a successful cost comparison. W14's original per-owner resolution,
source/artifact/map hashing and one-to-one replacement costs remain unchanged;
its loaded-identity traces are historical only. We do not reinterpret them as
consolidation or repeat them until green.

This follow-up performs audit setup work: explicit source/metadata/executable
reads and hashing, parser work, directory traversal and original resolution
calls. `auditOnlyCosts` records 76 explicit reads and 784 resolver invocations;
read bytes include hashing the existing Node executable. These are audit counters,
not syscall totals or resource measurements: parser/tool dependency loading,
metadata reads internal to resolution and verifier I/O are additional real work.
No CPU/wall/RSS or saving is measured. Source byte sizes are not performance
estimates. No validation, loader or map cost is subtracted from any prior data.

The existing identity assertions, M3 attribution and deliberate duplicate-collector
negative control remain exactly as committed at baa1920. Their narrow historical
pass/expected failure are not extended to this noncandidate. No new phase/order,
identity, fake-clock, semantic, inventory, fresh-job, deadline or cleanup claim is
made. Source URI/line/column consumption remains a known W14 failure; no maps are
changed, emitted frames hidden, stack strings rewritten or global mapping flag
enabled. There is no structural work-removal gate pass that would justify map
implementation or broader qualification.

## Bounded artifact/provenance contract

There is an **audit evidence artifact**, not a new installed runtime artifact.
The command takes an independently supplied expected W14 manifest digest:
`cc232a3cd8d9fb5a4459d7a7c55bec8b90b259c227ab0f26badcee24c3bf7898`.
It checks that digest before source admission. Live old audited source overrides
reject by hash; newly read distro sources, package metadata, source inputs,
conditional export/import declarations, literal original resolutions, tool
versions and executable bytes are bound in the evidence inventory. The exact
executed audit and verifier source, command, stdout/stderr/status, old manifest
and all inspected distro source bytes are compressed with it. No new facade or
map hashes exist because nothing was built.

Runtime/tool pins are unchanged: Linux arm64 Node 26.3.1 (existing verified
executable SHA-256 `a76224241ff1ebcbf90adc2eb4600ddb048d19f883c6a9140b6c3ceab004e4f0`),
Vitest/coverage 5.1.0, Vite 8.3.5, Babel parser 8.0.10, Rolldown 1.2.15 and
magic-string 1.4.5. Only Babel parser executes as the audit tool; the bundler is
not called. Actual resolver mode is the recorded installed distro/default Node
ESM resolution with `--experimental-import-meta-resolve`. Conditional source
exports, custom loaders/evaluators and other installations are not admitted by
this audit as supported runtime configurations. `package.json`/lockfile remain
unchanged; no dependencies were installed in this follow-up.

There is no new runtime native closure to attest. W14's observed native-closure
inventory is preserved but remains insufficient as full pre-execution attestation
of load-bearing mutable native dependencies. This audit's symbolic external
barriers are not such an attestation. No immutable install, authoritative selected
framework artifact, TOCTOU protection or relocatability is claimed. If a future
mechanism makes a selected installed artifact authoritative, it must independently
bind its expected digest, rebuild or reject dev/source overrides, preserve
conditional-resolution behavior and validate mutable load-bearing native bytes
and metadata. Build-only tools need not be rehashed in every child; that does not
waive any mutable native runtime closure. Those real validation costs must remain
inside its declared accounting. This rejected topology has no qualified runtime
contract to carry forward.

## Reproduction of the audit only

Use the existing pinned installation; do not rerun the old W14 candidate:

```sh
/tmp/oximut-v5-node/node-v26.3.1-linux-arm64/bin/node \
  --experimental-import-meta-resolve \
  benchmarks/experiments/shared-framework-identity/topology-followup/audit.mjs \
  /tmp/shared-framework-identity-retained/shared-framework-identity-build-2/build.json \
  cc232a3cd8d9fb5a4459d7a7c55bec8b90b259c227ab0f26badcee24c3bf7898 \
  /tmp/shared-framework-identity-topology-followup-1
python3 benchmarks/experiments/shared-framework-identity/topology-followup/verify.py \
  /tmp/shared-framework-identity-topology-followup-1
```

The output directory must be new. Both commands ran once locally. The audit
exited zero with a **no-go classification**, not candidate acceptance; stdout and
stderr were empty and status is retained. The verifier independently recomputes
closures, dynamic-edge classification, SCC/pair witnesses and source hashes.
Its passed checks are read-only structural verification, not worker tests.

The next justified gate is a separately bounded mechanism design that preserves
per-ingress lazy initialization and native barriers **and** names which executed
resolution/read/linking work disappears after adding all wrappers, manifests,
validation and maps. A validated lazy compiler or a narrow earlier-initialization
purity proof would need explicit source semantics and its own admission oracle.
This result does not authorize that implementation, a stack-only repair campaign,
product integration, snapshots, CI or scoring. The mission remains wall <= .50x
and CPU/peakRSS <= .80x Stryker with equivalent outcomes and fresh-process default;
parent stays unsettled.
