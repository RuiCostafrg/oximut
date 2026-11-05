# Resource accountant diagnostics

Enable the opt-in CLI self-accounting profile with:

```sh
OXIMUT_PROFILE_ACCOUNTING=1 oximut --root fixtures/m1 \
  --config vitest.config.ts --source src/subject.ts --baseline-only \
  --report .oximut/accounting-profile.json
```

The resource report then includes `resources.accountingDiagnostics`. With the
variable unset or set to any other value, the key is omitted and sampling does
not read diagnostic clocks or thread CPU counters. The diagnostic is bounded:
it contains four stage totals and no per-process records, paths, or environment
values.
The four stage counters cover only `Budget::sample_with_readers` windows.
Version 2 also measures the same thread across each Budget lifetime through
its resource-report snapshot. That larger interval includes other CLI work;
its remainder is not a worker CPU measurement. CLI startup before the Budget,
other processes and gaps between retry intervals remain outside that total.

`clockVersion: 2` identifies the precision-clock format. `sampling` counts admitted sample attempts, including attempts that fail, and
measures the complete sampling operation. `discovery`, `identityReads`, and
`rssReads` count each instrumented invocation and accumulate monotonic wall
time, Linux `getrusage(RUSAGE_THREAD)` user/system CPU time, and
`CLOCK_THREAD_CPUTIME_ID` total CPU time (`selfCpuNs`). `selfUserNs` and
`selfSystemNs` are retained raw counters. They have microsecond granularity and
their charging/endpoints can disagree with short wall windows; do not interpret
them as exact stage attribution or clamp values to wall time. Missing precise
readings make totals null and increment `selfCpuFailures`.
`threadCpuSinceBudgetStartNs` measures the same CLI sampling thread from this
Budget lifetime's start through report serialization. It is not worker CPU and
does not cover program startup before Budget creation. Failures are counted in
`threadCpuFailures`. Discovery
includes identity reads performed internally while building the process table;
`identityReads` counts the separate fresh identity validations before RSS reads.
Durations are integer nanoseconds. Identity and RSS reads include failed calls. A rate-limited call is
not counted. Null wall/CPU totals and nonzero failure counters indicate
incomplete evidence; qualification must reject it rather than treating a
missing value as zero. CPU counters cover only the CLI sampling thread, not
Node, controllers, test forks, or an external benchmark sampler.

Stage totals are nested within `sampling`: discovery and read work is included
in the overall sample. Clock/getrusage and instrumentation framework overhead
is included in enclosing windows and is not separately measured. These categories therefore are not an exact
partition of process-tree CPU or wall time. The retained native capture below
provides measured sampling windows and exposes a clock-boundary limitation.
It makes no optimization or savings claim; paired scored results use a separate
campaign with these clocks disabled. Retry reports add both attempts' stage and
per-Budget thread totals alongside their resource samples. Gaps between
discarded Budget lifetimes are outside both intervals; missing totals remain
null and overflow increments failure evidence.

The native workflow now defaults to `capture_mode: accounting`; the existing
controller-job and startup-segment modes remain selectable. The `native-profile`
PR label starts the default diagnostic. Every push while the label remains
attached reruns it; remove the label after the desired capture and before
evidence-only archival pushes. Accounting mode runs:

```sh
python3 benchmarks/profile_native.py --binary /path/to/release/oximut \
  --accounting --output /tmp/oximut-accounting-capture-new
```

The separate `capture_mode: cpu-prof` dispatch exposes the harness's existing
exit-time Node CPU profiler, including worker profiles when they finalize.
Its identity/finalization/workload gates remain in place; incomplete files do
not become qualified evidence. Use it to investigate worker startup and
collection, separately from accounting mode and scored paired campaigns.

It enables the environment switch for the unchanged three-tool colord workload,
without Node inspector or Python profiler preloads. Acceptance still requires
the exact 102 tests and ten killed edits, healthy native resource accounting,
all four measured stages, reconciled admitted/discovery counts, version 2
precise CPU and Budget totals, and non-null wall/user/system totals with zero
failure counters. The HTML reader accepts legacy captures explicitly as
historical evidence; the new capture path requires version 2. Zero-valued sub-millisecond
CPU deltas are allowed; missing or overflowed values are not zero measurements.
The output remains one diagnostic, with no paired ratios or savings claim.
Accounting mode also enables `OXIMUT_PROFILE_MEMORY=1` in the external benchmark
sampler. Each tool's row retains the PID, parent PID, start ticks, short command
name and RSS bytes from the sample that sets its aggregate peak. The component
sum must reconcile with that reported peak; identity validation, read ordering,
and memory error handling are unchanged, and no extra RSS reads are performed.
Diagnostic acceptance also requires a complete current parent chain to the
captured root with consistent start times. A reparented or racing snapshot
without that chain remains unqualified; this does not change owned-tree tracking.
The snapshot is sampled across processes, not atomic. Shared pages count in
each process; independent per-process peaks must not be summed instead.

The [HTML evidence report](../benchmarks/results/performance.html)
can be rebuilt from retained native inputs:

```sh
python3 benchmarks/performance_report.py --output /tmp/oximut-performance.html
# After a qualified accounting capture has completed:
python3 benchmarks/performance_report.py --accounting /path/to/capture \
  --output /tmp/oximut-performance-with-accounting.html
```

For an external capture, retain `build-metadata.json`, `evidence-source.json`,
and a `SHA256SUMS` covering all consumed inputs as in the retained example.
The accounting build must match the paired after candidate by revision, binary
hash, adapter source hashes and execution options; filesystem paths may differ.

The page preserves the full metrics, phase medians, bootstrap intervals,
controller sampling limits and unknown worker/accountant CPU attribution.
It never treats local Android/PRoot execution as qualified timing evidence.

The first native capture is retained in
[`2026-11-05-accounting-profile`](../benchmarks/results/2026-11-05-accounting-profile/README.md).
It qualifies the workload and complete counter collection, but exposes a
fine-grained clock-boundary limitation: `identityReads` user+sys exceeds its wall
interval. Preserve those raw values; do not interpret the per-stage user/sys
split as exact CPU attribution. Version 2 aligns interval endpoints and adds
`CLOCK_THREAD_CPUTIME_ID` totals; that precision format needs its own native
capture before interpreting sampling CPU. Getrusage CPU fields are
microsecond counters converted to nanosecond units, not nanosecond precision.
