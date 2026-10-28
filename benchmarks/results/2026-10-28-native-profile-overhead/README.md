# Repeated-checkpoint profiling overhead — 2026-10-28

[Run 40658648515](https://github.com/RuiCostafrg/oximut/actions/runs/40658648515),
PR #24 head `02450a0f345d29430466f5b3968fb7ed028858e6`, executed merge
`a8b5416e7ca2f3499cad78a1d5ddd0ce774fe312` on native Ubuntu 24.04 x86_64.

**Workload qualification and complete capture both failed. No performance ratios
or product speed/memory/CPU conclusions are supported.** All minimum process-role
evidence was present, with 29 identities and 7,416 partial checkpoint profiles,
but Oximut's original baseline hit its unchanged 30-second job deadline. All ten
mutants were therefore skipped. Ordinary Vitest and Stryker completed; that does
not rescue the cross-tool gate. There were no orphan profiles or recorded segment
instrumentation errors. Partial files do not establish complete lifecycle capture.

`artifact.tar.gz` retains every downloaded file, including built binary, source,
provenance, raw reports/logs, profiles and identities. `artifact-files.json` binds
the originals by SHA-256; `diagnostic.json` is copied unchanged. `SHA256SUMS`
covers these committed evidence files.

The failed mode repeatedly stopped and restarted an inspector CPU profiler after
nominal 50ms intervals. Sample time attributed to the leaf frame `post` with URL
`node:inspector` dominates each tool's retained profiles: Vitest 21,432,929 of
29,192,210 sampled microseconds; Oximut 55,327,423 of 81,334,152; Stryker
362,857,679 of 374,045,208. These are sums of profile timeDeltas grouped by sample
node callFrame and tool project cwd. They include idle/scheduling/profiling effects,
not exact OS CPU totals, and worker-thread counters must not be summed as processes.
They identify the instrumentation as intrusive rather than establish a product
CPU bottleneck.

The next experiment requests only one startup checkpoint per isolate, with no
restart and immediate session disconnect after stopping. It still includes
profiler overhead, may miss short-lived or busy workers, and is biased toward
startup. Missing tail/lifecycle data and all original qualification gates remain
failures; no deadline or assertion is extended to make the capture pass.
