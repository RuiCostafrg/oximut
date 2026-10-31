# Incomplete native CPU capture — 2026-10-28

[Run 40655919509](https://github.com/RuiCostafrg/oximut/actions/runs/40655919509),
PR #24 head `03bba4efff329e11ef86e200d5637905710702f9`, executed merge
`6eeac92033ba3a16ca124ed9cef49411a7a3361f` on native Ubuntu 24.04 x86_64.

**Workload qualification passed; capture validation failed. No ratios or performance
claims are supported.** All 102 tests and ten shared killed arithmetic edits passed
the existing workload gates with profiler overhead.

`artifact.tar.gz` retains the complete downloaded artifact, including built binary,
source archive, build provenance, projects/reports/logs, Node profiles, identities,
and supervisor pstats. `artifact-files.json` binds every original file by SHA-256.
`diagnostic.json` is copied unchanged for inspection. `SHA256SUMS` covers these files.

Four ordinary Vitest forks and 38 Oximut forks have neither exit-time profiles nor
exit observations. Vitest's installed fork-pool stop method calls `fork.kill()`
(SIGTERM), followed by bounded SIGKILL fallback. Do not alter that lifecycle to
force profile dumps. Fourteen Stryker worker-thread profiles exist but lack exit
observations; Stryker child-process identities also remain incomplete. Finalized
raw profiles may be useful partial evidence despite the failed overall capture.

The Python supervisor profile contains about 1.001 seconds of instrumented self CPU,
with roughly 0.941 cumulative seconds in snapshot discovery and 0.812 nested seconds
in process_identity. Those values overlap and must not be summed. They include
profiling overhead and do not explain the missing Node work or Rust CPU.

The follow-up segmented inspector mode seeks partial fork stacks without changing
signals/deadlines. It cannot guarantee complete captures or bounded tail loss and
must not convert this failed attempt into a successful one.
