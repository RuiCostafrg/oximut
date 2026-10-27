# JSON report reference

Normal run reports written with `--report FILE` use top-level
`schemaVersion: 1`. The matching [JSON Schema](report.schema.json) describes
the serialized report fields. It allows extra keys only inside `resources`,
which is serialized from the scheduler's resource-accounting structure and can
grow independently. The schema applies to invocation reports, including runs with
`summary.complete: false`; `--list`
has a separate `{schemaVersion, mutants, diagnostics}` output and is not this
report format. This describes the current pre-alpha schema-1 emitter; historical
M1/M2 schema-1 artifacts predate the cache/resource fields and do not satisfy
this schema. Backward compatibility with those experimental reports is not claimed.

After `npm ci`, validate reports with:

```sh
node scripts/validate-reports.mjs .oximut/m1-report.json
node scripts/validate-reports.mjs .oximut/ci-m6
```

Explicit files are always checked. Directories recursively select `report.json`
and `*-report.json`; other JSON files such as benchmark metadata are ignored.
Every supplied path must exist, and each directory must contain a matching report.
Malformed JSON and schema violations fail the command with the offending path.
CI validates all three emitted M6 smoke reports (fresh, persistent, and selection)
against the published schema. The validator currently uses Ajv's draft-2020 module
from the locked npm dependency tree.

## Reproducible report commands

Build from the locked workspace, then run the checked-in M1 fixture:

```sh
npm ci
cargo build --locked -p oximut-cli
target/debug/oximut --root fixtures/m1 --config vitest.config.ts \
  --source src/subject.ts --report .oximut/m1-report.json
```

That fixture intentionally has survivors, so a complete run exits 1. To capture
JSON on stdout, pass `--report -`. To inspect schema behavior with the M5 options:

```sh
target/debug/oximut --root fixtures/m1 --config vitest.config.ts \
  --source src/subject.ts --execution persistent --test-selection \
  --workers 2 --memory-budget-mib 1024 --memory-reserve-mib 64 \
  --report .oximut/m5-report.json
```

The report path is relative to the current working directory. A file report is
written through a temporary file and rename. `--baseline-only` returns a report
with an empty `mutants` array and the original test evidence.

## Fields

The top-level object contains:

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Report format version, currently integer `1`. |
| `runId` | Run identity shared with adapter evidence. |
| `baseline` | Adapter response for the original, unmutated project. Its `outcome`, `tests`, `errors`, and optional M3 `coverage` describe baseline evidence. |
| `mutants` | One result per generated mutant, with stable identities; array order can vary with scheduling and execution order. Each result includes source location, operator and replacement, outcome, test counts, errors, optional selection, and provenance. |
| `diagnostics` | Run-level selection, adapter-reuse, cleanup, or resource messages. |
| `summary` | Counts by outcome, score denominator and score, and completeness. |
| `cache` | Cache opt-in/eligibility, fingerprint and reason, hit/miss counts, and persistence state. |
| `resources` | Configured/effective worker and memory policy plus sampled process-tree observations. Resource policy strings explain measurement/enforcement. |

`baseline.coverage`, when present, has `complete`, `tests`, `hits`, and
`reasons`. A test identity has `id`, `file`, and `name`. A hit has `mutantId`,
test identity IDs in `tests`, and `outsideTest`. Selection has `mode` (`full`,
`files`, or `none` in current implementation), selected `files`,
`selectedTests`, `totalTests`, `expectedSkipped`, and fallback/evidence `reasons`.
Whole files are retained to preserve sequential state and hooks.

The `cache` object reports `enabled`, `outcomesEnabled`, optional `fingerprint`,
`reason`, `instrumentationHits`, `instrumentationMisses`, `outcomeHits`,
`outcomeMisses`, and `persistence`. `enabled` describes instrumentation cache
use; outcome reuse additionally requires `--cache-inputs-complete`. Reasons
such as `cache-not-requested`, `external-inputs-not-asserted-complete`, and
`forced-no-cache` explain disabled paths. Persistence starts as
`not-written`; an eligible complete run records
`complete-run-staged-for-commit-after-report`. The JSON report is emitted before
the staged outcome transaction is committed. The later text output reports
`complete-run-persisted` or `outcome-commit-failed: ...`; the JSON staged state
alone does not prove that publication succeeded.

`--cache-policy auto` (the default) uses advisory local timing history to decide
whether to attempt outcome caching. Instrumentation caching stays enabled when
outcome caching is bypassed. A bypass records reason
`outcome-cache-cost-exceeds-fresh-work`, `outcomesEnabled: false`, zero outcome
hits, no fingerprint, and an empty `fingerprintProfiles` array. Baselines and
mutation jobs execute normally. `--cache-policy always` ignores this history and
performs the full outcome validation/reuse path. `--no-cache` bypasses both caches
and policy history.

Policy history lives separately at `.oximut/cache/policy/<key>.json`. Its key
covers the runner, serialized options, source-content artifact identities, and an
inherited-environment digest; it stores no environment values. Schema 1 records
`schemaVersion`, `key`, `fingerprintCostMs`, `freshWorkMs`, `successfulBypasses`,
and `bypassOutcomes`. History is advisory: it can only select fresh execution,
never authorize reuse or replace input validation. Missing, malformed, oversized,
or incompatible history triggers a full probe with outcome reuse disabled.

Calibration compares the full fingerprint attempts' elapsed time, including
runtime and resource-policy checks, against actual scheduled mutation-job wall
time, excluding baseline and cleanup. Only complete runs with no outcome hits
and successful report emission update history. Auto bypasses future outcome
lookups and scans when fingerprint cost is at least 125% of fresh mutation work.
Once bypassed, it re-enables probing when fresh work costs at least the prior
fingerprint estimate; this gap prevents switching around a single threshold.
After eight successful bypasses it forces a fresh calibration probe. Source or
settings changes produce a new policy key. Failures do not advance the bypass
count or write history. Policy writes are atomic and independent of outcome
publication; a failed advisory write leaves future validation requirements intact.

Cache profiling adds `runnerHashMs` (runner executable hashing) and
`fingerprintProfiles` (ordered full fingerprint attempts). These fields are optional
in the schema so earlier reports remain valid. Each attempt records `phase`
(`prepare`, `post-baseline`, `pre-publication`, or `post-baseline-and-pre-publication`), `completed`, `elapsedMs`,
`runtimeIdentityMs`, `resourcePolicyMs`, `inputsMs`, `filesHashed`, and `bytesHashed`.
Elapsed values are monotonic wall time in whole milliseconds, rounded down;
`inputsMs` includes path discovery, environment hashing, tree traversal, and full
file content hashing. Total time also includes small orchestration overhead.
`completed` means fingerprint construction succeeded, even if the resulting key
changed. Failed attempts retain timing and partial read counts; unattempted phases
have no entry. Counters count successful file hashes and actual content bytes read
(including partial reads on failure), with repeated reads counted again. They do
not include the separately timed runner hash. Disabled outcome caching emits an
empty array; disabled cache hashing reports zero runner time. Profiling does not
change cache keys, baseline validation, input coverage, or reserved report identity,
and records no paths or environment values.

A fully validated, nonempty warm run with every outcome reused performs its final
full fingerprint after worker cleanup, combining post-baseline and pre-publication
validation. A changed fingerprint on an otherwise clean, passing attempt discards
all reused evidence and retries once with newly read sources, regenerated
transforms, fresh baselines, and outcome reuse disabled. A fingerprint read error,
baseline failure, cleanup failure, or resource exhaustion never triggers retry.
The retry retains separate post-baseline and pre-publication scans; a change or
read failure during either scan makes its report incomplete and prevents outcome
publication. Mixed hits, misses, empty inventories, and baseline-only runs retain
the ordinary validation path.

After retry, diagnostics preserve the discarded attempt's resource report and
explain the retry. Baseline, mutation results, cache hit/miss counters, and resource
configuration describe the final attempt. Resource peak observations take the
maximum across attempts; sample, admission-pause, and recycled-worker counters
are summed. Fingerprint profiles retain both attempts in chronological order, and `runnerHashMs` sums both executable hashes.

Current `resources` fields are `configuredWorkers`, `effectiveWorkers`,
`cpuLimitWorkers`, `configuredBudgetBytes` (nullable), `budgetBytes`,
`reserveBytes`, `cgroups`, `measurement`, `enforcement`, `sampleIntervalMs`,
`peakAggregateRssBytes`, `measuredWorkerPeakBytes`,
`admissionWorkerCostBytes`, `maxActiveJobs`, `admissionPauses`,
`recycledIdleWorkers`, `samples`, and nullable `exhaustion`. A cgroup entry
records `limitPath`, `usagePath`, nullable `limitBytes`, `oomEventsPath`, and
nullable `initialOomEvents`. Memory values are bytes; a `null` configured budget means it
was not explicitly configured; `budgetBytes` is the resolved effective budget. Resource counters describe sampled observations
and admission decisions, not a hard memory cap.

Each mutant's `provenance.source` currently identifies `executed`,
`baseline-coverage`, `skipped`, or `cache`. `reason` explains the source or
fallback, `fingerprint` is present when cache identity applies, and
`originalRunId` identifies a cached result's prior run. Test counts on a cached
mutant retain that prior evidence; they do not count tests executed again during
the current invocation. Optional provenance, selection, and coverage fields are
omitted when absent. Resource accounting uses nullable fields as described above.

## Outcome and score semantics

Mutant outcomes are `killed`, `survived`, `no-coverage`, `invalid`, `timeout`,
`infrastructure-error`, `resource-exhausted`, and `skipped`. Test response
outcomes use the corresponding test-level values (`passed`, `failed`, `invalid`,
`timeout`, `infrastructure-error`, `resource-exhausted`, and `skipped`).
Infrastructure faults, invalid transforms, timeouts, resource exhaustion, and
skips never count as kills.

The denominator is exactly `killed + survived + noCoverage`. The score is
`100 * killed / denominator`, or JSON `null` when the denominator is zero.
Invalid, timeout, infrastructure-error, resource-exhausted, or skipped results
make the summary incomplete and are excluded from the denominator.

| Process exit | Meaning |
| --- | --- |
| `0` | Complete run with no survivors and no no-coverage mutants; also successful baseline-only execution. |
| `1` | Complete run with at least one survivor or no-coverage mutant. |
| `2` | Incomplete run, failed baseline/cleanup, or invocation/configuration error. |

Generation/configuration errors that occur before execution may print to stderr
without producing a report. An incomplete result report uses exit 2. A score is
not a pass threshold; interpret it alongside `summary.complete`, denominator,
and outcome counts.

The JSON Schema captures the serialized structure, enums, and required fields.
It cannot establish semantic conditions such as score arithmetic, consistency
between summary counts and entries, or cache provenance validity; those are
enforced by the runner and its validation paths.

## Interface versioning policy

Normal reports and `--list` are separate interfaces. Consumers must check the
integer `schemaVersion` before interpreting either. For normal reports, adding
optional fields is compatible within version 1; consumers should ignore unknown
fields. Removing or renaming fields, changing their types or meanings, changing
required fields, or changing outcome/score/exit semantics requires a new schema
version and migration notes. Diagnostic text and timing values are observations,
not stable identifiers. Consumers should tolerate new diagnostic reasons and
profiling phase names; the checked-in schema is updated when these are added.

`--list` version 1 emits an object containing `schemaVersion`, `mutants`, and
`diagnostics`, without running a baseline or mutation jobs. Each mutant contains
`id`, `file`, `operator`, `original`, `replacement`, `location`, `code`, and `map`.
Locations use zero-based UTF-8 byte offsets and one-based line/UTF-16 columns.
IDs are deterministic for the same source and instrumenter, but are opaque:
consumers must not derive locations or ordering from them, or assume stability
across instrumenter versions. Source maps are JSON values. Optional additions
are compatible; changes to these fields or their meaning require a new list
schema version. The normal-report validator does not validate list output.

For a supported release, removing CLI options, changing defaults or exit-code
meanings, and tightening accepted configurations require explicit release notes
and a compatibility review. The prototype's `0.0.0` package version is not a
promise of a frozen 1.0.0 interface. Protocol and on-disk cache versions are
independent of report/list schema versions; cache entries may be invalidated by
an implementation or dependency change without migrating user data.
