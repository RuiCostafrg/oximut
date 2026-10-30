# Qualified native colord comparison, 2026-10-28

[Run 40632949685](https://github.com/RuiCostafrg/oximut/actions/runs/40632949685)
used PR head `e28d90586f72c98cd751349b468775cc26cc262a`, checked-out GitHub
merge commit `037fac9ce08aaef9428d0a6581a7bee6db2c9025`, on native Ubuntu
24.04 x86_64. Both builds completed in isolated target directories before timing.
There was one warmup and five measured rounds, alternating candidate/tool order.

Both the build-integrity and campaign-wide correctness/measurement gates passed.
The executable hashes differ, recorded commits/hashes match observed inputs, and
every after run contains all nine phase timings and five per-file disabled checks.
All 102 upstream tests and 41 production source files were available; the executed
scope is ten identical killed arithmetic edits in the five declared source files.
Boolean-literal is a declared family with no edits in those files. This is not
full-colord mutation coverage or a general application-performance claim.

## Results and limits

Median paired after/before ratios: wall **0.8152**, CPU **0.7247**, aggregate peak
RSS **0.8273**. These correspond to 18.5% lower elapsed time, 27.5% lower CPU,
and 17.3% lower RSS than the baseline candidate on this host/workload.

Median paired after/Stryker ratios: wall **2.4210**, CPU **3.3299**, aggregate
peak RSS **0.7558**. Oximut remains 2.42 times as slow and uses 3.33 times the
CPU, with 24.4% less peak RSS. The targets (wall ratio at most 0.8 and RSS ratio
at most 0.5) are **missed**. CPU is measured without an invented numeric target.
Green CI establishes valid evidence, not performance-target achievement.

The full samples and deterministic paired 95% bootstrap intervals are in
`summary.json`; the sample size is small and the confidence statement is
host/corpus-specific. All measured pairs, including an unusually slow third
baseline sample, remain included. No outliers, failed attempts or warmups were
removed to improve the result. Warmups qualify but do not contribute ratios.

Median after phase timings: original baseline 1.808s, disabled-switch validation
4.679s, coverage baseline 1.161s, mutation execution 3.856s, teardown 0.106s.
Preparation is about 0.004s. These are per-phase medians, not an exact partition
of median external wall time or CPU attribution. Full baselines, fresh test
forks, process cleanup and outcome gates remain required. Next investigation
must identify startup/validation costs without dropping correctness work.

## Retained evidence

`artifact.tar.gz` contains every downloaded artifact file, including full private
projects, raw reports/logs, samples, attempts and metadata. File bytes are unchanged;
this is a new container, not GitHub's ZIP. Original ZIP artifact ID: 12792258084;
GitHub ZIP digest:
`sha256:e72eed2ec7455ae4d9fe33cd7767ba47a25ef48d53dd29653c3ba1db0d304b2b`.
The original summary and metadata are also copied alongside for inspection.
`revalidation.json` independently reproduces the original summary from all raw
rows using the strict gate. SHA256SUMS verifies the retained files.

Before: `4a6993b564758d15d976cd0998ba490fbf64e1e1`; after: the checked-out
merge above. Full binary/adapter hashes, compiler flags, tool versions and target
directories are recorded in metadata. This evidence validates those observed
inputs; it is not a final v1 release or an attestation of a published artifact.
The earlier invalid shared-build campaign remains separately preserved and is
never rewritten into successful evidence.
