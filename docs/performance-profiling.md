# Profiling multi-file mutation runs

Benchmark the whole command before choosing an optimization. Rust parser speed
alone does not establish faster mutation testing: the CLI also owns resource
sampling, adapters, Vitest controllers, fresh test forks, disabled-transform
validation, test selection, and report/cache work.

The initial native matched campaign is archived in
[the benchmark results](../benchmarks/results/2026-10-27-matched-native-initial/README.md).
Its global gate failed, so its ratios are diagnostic only. The larger workload
also revealed eight full disabled-transform validation runs before mutation
execution. These safety checks remain in place.

`resources.phaseWallMs` records monotonic wall durations for input/scheduler
preparation, the original baseline, all disabled-switch validation, the coverage
baseline, post-baseline validation, mutation planning/execution, teardown, and
final input validation. `resources.disabledValidationRuns` records each checked
file and its duration. These are durations within the final attempt; a discarded
cache attempt retains its resource report in diagnostics. They exclude final
report serialization/publication and are not CPU attribution or an exact
partition of external command time. A skipped phase can be near zero. Existing
whole-process-tree wall/CPU/RSS measurements remain the performance evidence.

For a representative comparison, use pinned real upstream production sources
and tests, retain their license/provenance, record every adaptation, and keep the
same test inventory available to both tools. Declare mutant families before
execution. A report-level intersection of two unequal executed workloads cannot
make their total timings comparable. Preserve failed attempts rather than
rerunning until the results look favorable.

The [article on agents and Rust](https://smallcultfollowing.com/babysteps/blog/2026/10/14/agentic-dev-and-rust/)
proposes a benchmark/profile/change feedback loop and points to
[dial9](https://github.com/dial9-rs/dial9). Dial9 provides Rust/Tokio telemetry and
CPU/memory profiling; it is not a replacement for measuring external Stryker and
Vitest process trees. Oximut currently has a synchronous scheduler and no Tokio
runtime. Start with the phase evidence above and separate Node CPU profiles for
Vitest work; add Rust sampling when those measurements identify Rust as material.
Profiling runs must stay separate from scored timing runs, and profiler overhead
must not be presented as a product regression or improvement.

The first runner optimizations retain every baseline/disabled validation and
fresh test fork. The persistent adapter reuses its bounded request buffer across
serial requests. For mutation-only transitions, Vite invalidates the outgoing
and incoming files (all query/SSR variants and importers); unrelated transforms
remain cached. Probe changes still invalidate the entire graph. These changes
require alternating-file, original-source restoration, dynamic-import, selection,
state-isolation, timeout, and descendant-cleanup regression checks. Their effect
on elapsed time, CPU, and RSS must be measured rather than assumed.
