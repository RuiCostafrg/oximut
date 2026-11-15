# Independent review follow-up

The OpenCode read-only review at `225f23c` found no high-severity outcome or
attribution bug, but identified important failure-path and measurement caveats.
This follow-up does not retrospectively enlarge the recorded qualification.

## Hook failures

A new direct shared-lane regression test injects a throwing pre-send diagnostic
hook. Before the fix, it timed out at 45 seconds: Vitest's synchronous request
throw path bypasses runner teardown, then pool close waits on an unsent live
worker. The earlier review's assumption that this path promptly drains was not
established. Test cancellation eventually cleaned up the observed child.

The fix arms the existing hard deadline before dispatch diagnostics and forwards
a diagnostic exception through the pinned fork's normal error channel. Vitest
then rejects and force-drains the task. A throwing post-start hook is explicitly
drained before start rejection, since Vitest has not installed its listeners yet.
Worker stop is idempotent so framework teardown can safely repeat it. Both paths
remain infrastructure failures; no failed hook supplies successful test evidence.

`test-hook-failure.mjs` checks both hook positions, disappearance of the original
PID/birth identity before explicit owner close, and successful recovery with a
new test process. It restores Vitest's expected failure exit code only after
all recovery assertions pass. Native product requalification is required before
scoring the revised lifecycle. Browser mode now also rejects during controller
configuration validation, before baselines, rather than only during batch setup.

## Remaining scope limits

- One-worker and disabled-shared-controller modes remain unsupported and reject
  late at the non-lane mutant path. The experiment README states this and the
  scorer must preflight only the qualified two/four-worker configurations.
- The installed-file snapshot excludes `.bin`, `.cache` and `.vite`; other
  mutable tool-cache files may cause conservative changed-input rejection.
  Non-hoisted/symlink dependency layouts are unsupported, not newly qualified.
- Prepared artifacts remain writable for diagnostic events and depend on the
  original checkout/helper/dependencies. Retaining the directory alone is not
  a relocatable replay package. Map decoding in product rows was not exercised;
  its fidelity comes from the dedicated fixtures. Unregister-event counts are
  not cleanup proof; observed process identities and empty groups are.
- The first-lane deadline rule predates this experiment in main `5dfd17e`.
  It is not a newly introduced regression; variable per-lane deadlines remain
  outside the current uniform-timeout qualification.

## Measurement decisions

Do **not subtract** controller validation time, original-baseline time, artifact
checks, map work or trace I/O from candidate observations. They are real costs.
Report full preparation-command costs separately and combine sequential setup
and execution per pair before ratios: add wall/CPU; take the maximum RSS peak.
Prepared reuse is a separate scenario with explicit invalidation/amortization.

Use harness wait4 CPU and one harness live-process aggregate RSS maximum. Rust's
separate RSS maximum must not be added to it or presented as CPU evidence. RSS
is sampled: report the requested interval and actual gaps, which can exceed
short worker lifetimes. Balanced order is required for the scored campaign;
the earlier fixed-order single-run correctness observations establish no ratios
or resource target acceptance. All ordinary/stock/Stryker environments must
exclude the opt-in hook. All prior scope/build/correctness gates remain binding.
