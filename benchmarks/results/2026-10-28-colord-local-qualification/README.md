# Local colord qualification, 2026-10-28

This is a single sequential qualification pair, without warmups or repeated
measurements, on Linux arm64 under Android/Termux PRoot. It does not establish
native ARM support, a confidence interval, or achievement of performance targets.

Both final candidates passed the exact gate: all 102 upstream tests available,
10 matching arithmetic source edits across the five declared production files,
all killed by both tools. All 41 source files remain available. The declared
arithmetic/boolean-literal scope contains no boolean-literal edits in these files.

The baseline binary and matching adapter came from `t3/cli-mutator-family-filter`
(production implementation 4328ba8; final test-only head 4a6993b). The optimized
binary/adapter came from `perf/persistent-runner-allocation` (production 2b66d54
plus the family-filter merge; final test-only head 457fcbe). Both binaries were
built before timing; raw metadata records observed binary and adapter hashes.
Those hashes do not reconstruct a build or replace exact native CI provenance.

Raw Oximut wall/CPU/aggregate RSS: before 85.672s / 120.606s / 431792128 bytes;
after 89.739s / 127.443s / 371355648 bytes. Stryker measured 19.303s / 19.460s /
514039808 bytes before and 20.084s / 19.871s / 510144512 bytes after.
The sample shows lower Oximut memory but no speed/CPU improvement; it supports
no repeated-performance or target claim. Native paired CI is required.

The optimized phase report attributes approximately 52s to original/disabled/
coverage baselines and 37s to mutation execution. All safety checks remain.

`before-missing-dependency/` retains the failed first mutation attempt: the
baseline adapter worktree lacked its dependency link. An earlier preflight
also rejected missing dependencies in the benchmark worktree before recording
measurements. Links were restored to the pinned root dependencies, then both
qualification commands were rerun in new output directories. No source or
assertion was changed. Failed measurements remain invalid, never successful rows.

Private per-run project copies are omitted from git; the complete unmodified
corpus, configuration, input hashes and generated reports remain available in
the harness and retained output directories. Raw report/log/measurement files
are unchanged. No full repeated local campaign has run.
