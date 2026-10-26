# JSON report reference

Normal run reports written with `--report FILE` use top-level
`schemaVersion: 1`. The matching [JSON Schema](report.schema.json) describes
the serialized report fields. It allows extra keys only inside `resources`,
which is serialized from the scheduler's resource-accounting structure and can
grow independently. The schema applies to completed invocation reports; `--list`
has a separate `{schemaVersion, mutants, diagnostics}` output and is not this
report format. This describes the current pre-alpha schema-1 emitter; historical
M1/M2 schema-1 artifacts predate the cache/resource fields and do not satisfy
this schema. Backward compatibility with those experimental reports is not claimed.

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
