# Experimental shared controller lanes

Opt in with `--execution persistent --workers N --experimental-shared-controller`
(N > 1, fresh-process isolation, native reference adapter). This is a measurement
build pinned to Vitest 4.1.12. There is no performance conclusion from the
Android/PRoot validation host. Compare **wall time only** on representative
hardware, with identical inputs/order and `--no-cache` for both configurations.
The existing report's phase wall times include lane setup.

## Execution

Original and instrumented baselines use slot 0 and the existing parallel-file
baseline implementation. No spare controllers are started or warmed. Mutants
are dispatched as bounded batches through that same Rust subreaper slot.
Admission uses the existing CPU/memory fork limit; the slot's complete descendant
tree is still sampled and cleaned up by the native coordinator.

At the first batch, the baseline Vitest instance closes and a lane instance starts
in the same controller process. It has one named project per lane, with the root
configuration inherited. A width change recreates that instance. Equal-width
batches reuse projects and transforms after draining the preceding pool.
Every lane serves the same immutable resident instrumented modules. Different
active-only source for a shared URL is rejected. Only the launch environment's
active-mutant selector changes; no mutable active-source transform is used.

One representative specification per lane is submitted in one
`runTestSpecifications` call. The experimental fork wrapper replaces that
lane's run-message file list with its complete selected Node-file list, in Vitest
sequencer order. Thus each mutant gets exactly one fresh OS process, including
multi-file selections; isolation still resets modules between test files.
A local runner cancels only its own fork after its first failed test, with global
bail explicitly zero. Forks are drained before the project environment changes.

## Attribution and failure

Results are read by project name and selected file. Test counts retain Vitest's
passed/failed/skipped distinctions; locally cancelled pending tests do not become
skipped counts. Transform errors are invalid, test/hook timeouts are timeout,
ordinary assertion failures are kills, and successful nonempty runs survive.
Passing selected inventories must exactly match the baseline counts.

The fork wrapper owns a project/error ledger. Unexpected exit, unhandled worker
errors and the wrapper's hard watchdog cannot become kills. Aggregate unhandled
errors have lost their project identity, so they conservatively invalidate the
whole batch. Rust validates every nested result's identity, counts, resident
acknowledgement and selection. Missing/malformed replies and outer deadlines
become infrastructure errors for every batch member. Timeout/infrastructure
results retire the controller through existing verified process-tree cleanup.
Normal batches retain post-run leak checks, resource sampling and report schema.

## Pinned dependencies

Paths below refer to installed Vitest **4.1.12**, not upstream main:

- `node_modules/vitest/dist/chunks/cli-api.B0Pz1_R7.js:10633`: project name is
  part of specification/task identity.
- `cli-api.B0Pz1_R7.js:3715`: project env copied per run; `:3150`:
  ForksPoolWorker starts a process with that env before module evaluation.
- `cli-api.B0Pz1_R7.js:2943`: PoolRunner run-message context and `:3739`:
  its file descriptors. The wrapper replaces this private file-list shape.
- `cli-api.B0Pz1_R7.js:3123`: exported experimental ForksPoolWorker;
  its private `fork` property is used for watchdog termination.
- `cli-api.B0Pz1_R7.js:3515`: completion message; `:3552`:
  isolated termination is started but not awaited by normal execution.
  The private default `ctx.pool.close()` handle drains it before lane reuse.
- `node_modules/vitest/dist/chunks/test.DNmyFkvJ.js:4330`: local runner cancel
  flag; `node_modules/vitest/dist/chunks/index.DXx9Dtk7.js:182`:
  global bail wrapper, which must stay disabled.
- The wrapper inspects private birpc `m === 'onUnhandledError'` messages before
  worker ownership is lost. Ordinary attribution uses TestModule project names.

## Scope and limitations

Node test environments only; root multi-project/global-setup configurations are
unsupported. Arbitrary custom pools/runners, retries, concurrent tests, mixed
environments, hooks/teardown edge cases and coverage-provider combinations have
not been certified. This is not a production-hardened scheduler.

Each project owns a Vite server/graph; resident *source* is shared, but compiled
transforms are not shared across those graphs. Initial project creation and
last-batch width changes can erase the controller-startup savings. Batch requests
duplicate resident payloads and stay under the existing 8 MiB transport limit.
Shared-controller switching fallbacks without resident instrumentation fail
closed. Aggregate errors can also discard an otherwise healthy neighbor's result.
Hard watchdogs intentionally produce infrastructure errors, not mutation kills
or an asserted test-timeout classification.

The existing resource schema is retained. Scheduler occupancy counts a batch as
one occupied Rust slot; maxActiveJobs records admitted lane count, and descendant
resource samples include all its lane forks.
Do not interpret controller-slot occupancy as fork count.

## Validation

The adapter contract suite uses a filesystem barrier (no duration assertions),
distinct PIDs, module-level counters, fresh-controller sequential oracles,
equal-width lane reuse, different file selections, multi-file jobs, async
timeouts, SIGKILL crashes, hard infinite loops and post-result PID checks.
The Python acceptance tests compare full/selected CLI results, check flag
constraints, and prove CLI overlap plus cleanup with a second barrier.

Validation on the pinned Node 26.3.1/Vitest 4.1.12 toolchain:

- TypeScript no-emit check, cargo fmt, Clippy with warnings denied, and CLI build pass.
- All 62 adapter tests pass, including four new shared-lane tests.
- All 83 CLI Rust tests pass (74 unit tests and 9 process-module tests).
- Biome format check passes.
- All 34 Python acceptance tests pass: tests.test_m2, tests.test_native_coordinator, and tests.test_m5.

Counterfactual: temporarily restored **all modified production files** to
`34acb9f`, kept the new tests, and rebuilt the baseline CLI. All four new adapter
tests failed. All three new Python tests detected the missing implementation
(three failing assertions/subtests and one missing-report error; the baseline
rejects the opt-in flag). Then restored the implementation and rebuilt. No
measurement timings from this host are reported.
