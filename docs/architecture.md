# Architecture proposal

This is a design proposal, not a description of implemented behavior.

## Ownership

The Rust CLI owns configuration, mutant scheduling, deadlines, cache decisions,
resource admission, and final reports. Oxc parses sources and supports mutation
instrumentation. The Node adapter owns Vitest integration, module transformation,
test discovery, execution events, and coverage attribution.

Keep Vitest-specific APIs inside the adapter. Pin and test a compatibility range;
fail with a useful version diagnostic outside that range. A thin adapter can
still contain substantial lifecycle logic: Rust cannot replace Node test semantics.

## Execution sequence

1. Resolve config, source scope, declared inputs, versions, and resource limits.
2. Discover tests and generate eligible mutants in Rust.
3. Serve instrumented modules through an adapter transform plugin or isolated
   generated workspace. Do not overwrite user source files.
4. Run an unmutated baseline to validate the project and collect mutant coverage.
5. Resolve cache entries and a conservative test set for each pending mutant.
6. Admit jobs according to CPU and memory budgets. One active mutant per isolated
   execution context; initially one test job per worker.
7. Set the active mutant before loading affected code; execute tests and collect
   results. Reset, invalidate, or restart according to the isolation policy.
8. Persist completed results atomically, then emit a report with outcome provenance.

## Instrumentation rules

Mutation switching inserts original and replacement expressions behind a runtime
selector. Preserve evaluation order, single evaluation of side effects, lexical
scope, short-circuiting, exceptions, and source locations. Start with a limited
operator set; do not use text matching as a substitute for syntax analysis.

The inactive transform must preserve original behavior. Runtime coverage hooks
must not coerce values or evaluate expressions twice. Injected identifiers must
not collide with source bindings. Test Unicode offsets and composed source maps.
Mutants in unsupported grammar contexts receive explicit diagnostics.

IDs include normalized source identity/content, span, operator, and replacement.
Changing source content may invalidate identities initially; incremental matching
across edits is an optimization to add only with correctness tests.

## Adapter protocol

Use versioned newline-delimited JSON over dedicated pipes or another dedicated
IPC channel. User stdout/stderr must not share the protocol stream. Bound message
sizes and captured logs so a noisy test cannot consume unbounded memory.

Requests: `initialize`, `discover`, `baseline`, `runMutant`, `cancel`, `shutdown`.
Events: `ready`, `testResult`, `coverage`, `jobComplete`, `resourceSample`, `error`.
Every job message carries protocol version, run ID, job ID, and worker generation;
mutation requests also carry mutant ID, selected tests, and deadline.

Reject malformed messages, unknown versions, stale responses, duplicate terminal
events, and result/test identity mismatches. The controller enforces deadlines
even if Node's event loop is blocked. A killed worker's descendants must be reaped.

## Reuse and test identity

Keeping a controller alive is distinct from preserving loaded application modules.
Resetting mocks alone does not reset module singletons, timers, listeners, global
objects, environment mutations, or external resources. Retain Vitest isolation
where needed and compare reused execution with a fresh-process oracle.

Test identities include project, file, and stable collected test identity; names
alone are ambiguous for duplicate and parameterized tests. If the adapter cannot
target a test unambiguously, execute a larger set. Concurrent test attribution is
unsupported initially and triggers a conservative fallback.

Import-time execution, hooks shared by tests, and coverage outside a known test
context must broaden selection. For static mutants, activate before evaluation
and reload the affected graph or restart. Incomplete coverage is not no-coverage.

## Cache correctness

Use two stores: transformed artifacts and classified mutation results. Each entry
records schema/tool versions, input fingerprint, outcome provenance, and completion
state. Write through a temporary transaction/file and commit atomically.

Begin with project-wide content fingerprints. Include source/test files, imported
helpers, snapshots, config/global setup, lockfiles, runtime/tool versions, mutation
settings, and relevant declared environment/file inputs. Hash sensitive declared
inputs without writing their raw values into reports. Lockfile assumptions require
a consistent install; local workspace dependencies also need content tracking.

A passing baseline is necessary but does not prove cache validity. Undeclared
network/service state, time dependence, randomness, or incomplete dependency graphs
require cache opt-out or explicit input versioning. Do not cache crashes, resource
exhaustion, or incomplete classifications as kills. Track cache provenance per result.

## Resource semantics

The budget applies to the whole owned process tree, including Rust, adapter,
Vitest children, and test descendants. Calibrate worker cost, reserve headroom,
and use bounded queues and bounded report/log buffers. Avoid nested concurrency.

Sampled RSS is a practical pressure signal, not a guaranteed hard cap; summing RSS
can double-count shared pages. Prefer cgroup accounting for a contained Linux run
when available and report the accounting method. Heap limits alone do not bound
Node RSS or descendants. Hard enforcement is an explicit platform capability.

When resources run out, reduce admission or abort with a resource outcome. Do not
interpret an OOM kill as a test detecting a mutant. Cancel cleanly on interrupts,
preserve completed results, and cap retries for infrastructure faults.

## Validation strategy

Maintain three oracles: original versus inactive instrumentation; fresh-process
versus persistent execution; full-suite versus selected-test execution. Then compare
forced versus cached execution. Run the same mutant corpus in shuffled order to
expose leaked state. Validate semantics before performance.
