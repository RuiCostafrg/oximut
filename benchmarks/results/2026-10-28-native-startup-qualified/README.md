# Qualified workload, partial startup capture — 2026-10-28

[Run 40685338353](https://github.com/RuiCostafrg/oximut/actions/runs/40685338353),
PR #24 head `f62cdcc`, executed merge
`1b1552965477a29561c8371d524ee34e604c0df8` on native Ubuntu 24.04 x86_64.

**Workload qualification passed; full capture validation failed.** All 102 tests
and ten shared killed arithmetic edits qualified without extending deadlines.
Minimum usable-role evidence includes all three tools and all 38 Oximut test forks.
There are 64 finalized partial startup segments, 66 identities, no orphan profiles,
and no recorded instrumentation errors. Full-profile/lifecycle gaps remain errors;
this artifact does not claim a complete capture. See `interpretation.json` for counts.

No speed, CPU or RSS ratios are published from this overhead-bearing diagnostic.
Use the separately qualified unprofiled campaign for product comparisons.

`capture.tar.gz` preserves every downloaded log, report, project input, profile,
identity and event record, plus build metadata. All included original file hashes
were verified against `original-files.json`. Two large assets are not duplicated:
`oximut` and `source.tar.gz`; their original hashes are recorded in
`omitted-assets.json` and build metadata. They remain in the original CI artifact
and downloaded local artifact. The source commit identifies the exact code; this
capsule avoids recursively embedding earlier evidence archives in new snapshots.
`diagnostic.json` and `build-metadata.json` are unchanged copies for inspection.
`SHA256SUMS` covers these evidence files.

Across the 38 Oximut fork startup segments, the leaf frame compileSourceTextModule
(node:internal/modules/esm/utils) accounts for 1,034,965 of 4,611,417 sampled
microseconds. That total includes inspector, idle and scheduling effects; it is not
an exact OS CPU measurement or representative of later steady-state work. Node
loading/compilation is a candidate for a controlled follow-up, not a proven product
bottleneck. The snapshot is biased toward startup and misses unbounded tails.

The Python supervisor's instrumented self-CPU profile totals about 2.817 seconds,
with 2.697 cumulative seconds in snapshot discovery (210 calls) and 2.345 nested
seconds in process_identity (37,131 calls). These times overlap and include profiling
overhead. Never sum them, subtract Node sample durations to invent Rust CPU, or infer
an unprofiled speedup from this run. Process-tree scan work is another candidate for
independent safety review and measured experimentation.
