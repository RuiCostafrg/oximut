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
Only `Budget::sample_with_readers` windows are measured. Coordinator input
fingerprints, coordinator cleanup, and other CLI work remain outside this
accounting profile; unmeasured CPU must not be relabeled as worker CPU.

`sampling` counts admitted sample attempts, including attempts that fail, and
measures the complete sampling operation. `discovery`, `identityReads`, and
`rssReads` count each instrumented invocation and accumulate its monotonic wall
time and Linux `getrusage(RUSAGE_THREAD)` user/system CPU time. Discovery
includes identity reads performed internally while building the process table;
`identityReads` counts the separate fresh identity validations before RSS reads.
Durations are integer nanoseconds. Identity and RSS reads include failed calls. A rate-limited call is
not counted. Null wall/CPU totals and nonzero failure counters indicate
incomplete evidence; qualification must reject it rather than treating a
missing value as zero. CPU counters cover only the CLI sampling thread, not
Node, controllers, test forks, or an external benchmark sampler.

Stage totals are nested within `sampling`: discovery and read work is included
in the overall sample. Clock/getrusage and instrumentation framework overhead
is not separately measured. These categories therefore are not an exact
partition of process-tree CPU or wall time. This implementation supplies only
structural local evidence that counters and timings are emitted; wall time,
process-tree CPU/RSS/process counts, phase ratios, and confidence intervals are
unmeasured here. It makes no native comparison or savings claim. Collect
qualified evidence later on unchanged native CI workload.

The native workflow now defaults to `capture_mode: accounting`; the existing
controller-job and startup-segment modes remain selectable. The `native-profile`
PR label starts the default diagnostic. Accounting mode runs:

```sh
python3 benchmarks/profile_native.py --binary /path/to/release/oximut \
  --accounting --output /tmp/oximut-accounting-capture-new
```

It enables the environment switch for the unchanged three-tool colord workload,
without Node inspector or Python profiler preloads. Acceptance still requires
the exact 102 tests and ten killed edits, healthy native resource accounting,
all four measured stages, reconciled admitted/discovery counts, and non-null
wall/user/system totals with zero failure counters. Zero-valued sub-millisecond
CPU deltas are allowed; missing or overflowed values are not zero measurements.
The output remains one diagnostic, with no paired ratios or savings claim.
Accounting mode also enables `OXIMUT_PROFILE_MEMORY=1` in the external benchmark
sampler. Each tool's row retains the PID, parent PID, start ticks, short command
name and RSS bytes from the sample that sets its aggregate peak. The component
sum must reconcile with that reported peak; identity validation, read ordering,
and memory error handling are unchanged, and no extra RSS reads are performed.
The snapshot is sampled across processes, not atomic. Shared pages count in
each process; independent per-process peaks must not be summed instead.

The [HTML evidence report](../benchmarks/results/2026-11-05-native-control/report.html)
can be rebuilt from retained native inputs:

```sh
python3 benchmarks/performance_report.py --output /tmp/oximut-performance.html
# After a qualified accounting capture has completed:
python3 benchmarks/performance_report.py --accounting /path/to/capture \
  --output /tmp/oximut-performance-with-accounting.html
```

The page preserves the full metrics, phase medians, bootstrap intervals,
controller sampling limits and unknown worker/accountant CPU attribution.
It never treats local Android/PRoot execution as qualified timing evidence.
