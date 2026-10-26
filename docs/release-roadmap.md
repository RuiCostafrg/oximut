# Roadmap to Oximut 1.0.0

Oximut has completed M0–M6 as a feasibility prototype, but it is not yet a
supported public release. M6 explicitly holds public alpha performance claims:
the unchanged-cache run measured 22.37 s warm versus 6.12 s forced on the small
M1 fixture, the Stryker mutation scopes were unequal, and real-project, scaling,
and native-host resource-budget evidence is absent. The roadmap below separates
what is needed to support one deliberately narrow 1.0.0 configuration from
optional expansion. It does not assume that 1.0.0 must beat Stryker or support
every environment.

The existing support boundary in [compatibility.md](compatibility.md) is the
starting point: Linux with `/proc` and the required process controls; the pinned
Node, npm, Python, Rust, Oxc, and Vitest versions; one Vitest Node project; and
the documented JS/TS operators and fallback rules. Evidence currently comes
from Linux arm64 in Ubuntu userspace under Android/Termux PRoot. CI runs on
`ubuntu-24.04`, but its M6 benchmark is a single-fixture harness smoke, not a
performance or resource-budget campaign. A release must state exactly which
host configuration it supports based on release-candidate evidence.

## Required for a narrow supported 1.0.0

### R1 — Stabilize the supported contract

Keep the initial supported configuration pinned and make every unsupported
setting fail clearly or take its documented fallback. Review the CLI, report
schema, cache contract, installer instructions, and compatibility matrix
together so that they describe the same behavior. In particular, cache reuse
must retain complete-input opt-in and fresh baseline validation; selection must
retain conservative broader execution when attribution is uncertain; and
resource limits must remain described as sampled admission/cancellation rather
than a hard cap. Treat report schema versioning, CLI exit codes, and diagnostics
for incomplete/configuration-error runs as public interfaces: document which
changes require a version bump and preserve the documented exit meanings.

**Acceptance:** On the release candidate, the published compatibility table
lists exact required versions and tested host(s); each documented unsupported
case has a tested diagnostic or fallback; completed reports emitted by the CLI
are validated against the checked-in JSON Schema in CI; documented exit codes
are asserted by CLI acceptance checks; and documentation does not claim untested
compatibility or guarantees. Include the separate `--list` output contract in
the versioning notes.

### R2 — Resolve the cache release policy

Profile the full cache-enabled path, including hashing, baseline validation,
lookup, execution, and report output, while preserving M4's safety checks. The
current 22.37 s versus 6.12 s result is a material regression for the tested
unchanged-cache workload. Decide from measurements whether to improve it, make
outcome caching experimental/opt-in with the cost documented, or omit it from
the initial supported workflow. Do not weaken fingerprint completeness or
baseline validation to improve timing.

**Acceptance:** A release-candidate decision is recorded with repeatable
unchanged and changed-input measurements on the supported environment, paired
forced results, matching outcomes, and cache invalidation checks. Any shipped
cache mode has a clear supported status and published observed cost; no speedup
is claimed unless those measurements establish one.

### R3 — Complete release-candidate correctness and failure evidence

Run the full M1–M5 acceptance corpus on the exact release revision and final
distribution. Keep fresh-process execution as the reference, and verify
persistent and selected execution against it. Include malformed/stale IPC,
timeouts, worker crashes, cancellation, resource exhaustion, cache corruption,
and interrupted writes. The current test suites cover these areas, but M6's
recorded validation is not itself a release-candidate run.

**Acceptance:** All repository checks pass on the release revision; no
unexplained classification differences remain on the documented regression
corpus; infrastructure/resource failures cannot be reported as successful
test evidence; incomplete runs do not publish reusable successful cache state;
and the evidence is archived with revision, versions, commands, and results.

### R4 — Ship and verify one public distribution

Choose and document the initial distribution path. The tested source installer
is a concrete starting point, but the current manifests are private at version
`0.0.0`, the npm name check is only a point-in-time 404, and there is no public
artifact or provenance process. Define the actual package/artifact layout and
versioning for 1.0.0, recheck registry availability at release time, and build
and test the exact artifact in a clean external project after relocation.

**Acceptance:** The release artifact installs and launches outside the source
checkout using only its documented prerequisites; fresh and persistent
execution produce complete reports in an external project; version and
dependency metadata agree with the release; licensing notices are present;
and artifact checksums plus build/revision provenance are published. The
relocation smoke is part of the release CI or an explicitly archived
release-candidate check; the current CI only runs launcher unit checks, not the
full `scripts/smoke-install.py` workflow. Publish only for the target(s)
actually built and tested.

### R5 — Establish the 1.0.0 support and maintenance policy

Document the supported configuration, security/update handling for pinned
dependencies, how compatibility changes are reviewed, and how users report
reproducible defects. Add a release checklist tying the published claims to
the evidence above. The existing CI is a useful gate, but its Ubuntu x86_64
runner does not replace validation on the currently demonstrated ARM64 PRoot
environment, nor does it establish a general Linux support claim.

**Acceptance:** A release candidate has a passing CI run and a recorded manual
run on each host class claimed as supported; release notes state known limits
and the cache decision; and the project has an explicit policy for support
changes and security updates.

## Optional compatibility and performance expansion

These items are useful follow-up work, but they do not block a 1.0.0 release
whose claims stay within the tested narrow contract above. Take each on
separately and add it to the support matrix only after its own acceptance
evidence exists.

| Expansion | Evidence required before adding support or making the claim |
| --- | --- |
| More Linux distributions, kernels, architectures, or containers | Run correctness, installer, process supervision, and resource-accounting checks on each named target; document any missing `/proc`, subreaper, or cgroup behavior. |
| Additional Node, npm, Python, Rust, Vitest, or Oxc versions | Add a deliberate version matrix and run adapter lifecycle, CLI, install, and acceptance checks for each supported combination. Current adapter entrypoints reject Vitest versions other than 4.1.12. |
| Additional Vitest project features, plugins, environments, concurrency, or multiple projects | Specify behavior and isolation guarantees for each feature; add fixtures for attribution, invalidation, fallback, and failure behavior before advertising support. |
| Hard memory caps or broader resource guarantees | Add an OS-enforced mechanism and validate its limits and failure reporting on the target OS. Current sampled RSS admission/cancellation does not establish a strict cap. |
| Smaller standalone npm/runtime package, crates.io, or more binary targets | Define packaging and provenance, test clean installation and relocation per target, and publish license/dependency metadata. The current source bundle includes root development dependencies. |
| Performance comparison against Stryker | Generate equivalent mutation sets and test inventories, resolve all shared classification mismatches, then run the benchmark protocol on pinned real projects and generated scaling workloads. Report all exclusions and regressions. |
| Performance claims for CPU/memory budgets | Repeat the benchmark campaign on native Linux hosts at multiple fixed CPU and memory allocations. Report measurement method and observed overshoot; do not imply a hard cap absent OS enforcement. |

The benchmark protocol's proposed 2× median full-run speed and 30% peak-memory
reduction remain goals for a comparative performance claim, not evidence already
met and not prerequisites invented for a narrow correctness-focused release.
If performance is included in 1.0.0 positioning, the equivalent-scope,
representative-workload evidence above becomes a release gate for that claim.

## Implementation started

The first R2 slice adds per-attempt fingerprint timing and actual file/byte read
counters to the report, including failed attempts and validation passes. It
preserves complete hashing, inherited inputs, and fresh baseline validation.
This diagnoses part of the overhead; it does not complete R2 or establish a
speedup. The M6 harness archives the new cache fields without changing workload
identity. The [first diagnostic](../benchmarks/results/2026-10-26-cache-profile/README.md)
passed cache/forced agreement and located most fingerprint time in full-input
processing; repeated release-build measurements and safe optimization remain.
R1 contract checks and the remaining R2 phases follow.

## Release order

The default release gate order is: settle R1's contract, make and record the R2 cache decision,
run R3 on the release candidate, produce and validate the R4 artifact, then
complete R5's release policy and publish only the tested support scope. Any
failure that changes behavior, cache semantics, or packaging sends the affected
acceptance checks back through the release-candidate run. Optional expansion
does not need to delay a release that makes no corresponding compatibility or
performance claim.
