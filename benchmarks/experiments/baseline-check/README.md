# Original baseline fault injection

Oximut runs the suite twice before mutating:

1. on the original sources;
2. on the instrumented sources, with mutant switches and coverage probes.

The instrumented run must pass, and its passed, failed and skipped counts
must match the original run. `OXIMUT_EXPERIMENTAL_SKIP_ORIGINAL_BASELINE=1`
skips the original run. The instrumented run then becomes the only gate,
and the count comparison disappears. This experiment measures what that
loses.

```sh
PATH=/path/to/glibc-node/bin:$PATH \
  node benchmarks/experiments/baseline-check/run.mjs <oximut-binary> results.json
```

Each case is a tiny Vitest project whose tests observe instrumentation in a
different way. A run counts as detected when it refuses to report a complete
mutation result: exit code 2, an incomplete summary, or a baseline that did
not pass. Results are from [`results.json`](results.json), one run per case
per configuration, with Node 26.3.1 and Vitest 5.1.0:

| Case | What it simulates | Realism | Both baselines | Instrumented only |
| --- | --- | --- | --- | --- |
| control | no sensitivity | — | complete, 50% | complete, 50% |
| source-text-fails | a test asserts function source and fails under instrumentation | plausible | detected | detected |
| real-failure | a genuinely failing test | common | detected | detected |
| test-skipped-under-instrumentation | the only test for one function is skipped when code is instrumented | contrived trigger, real hazard | detected (count mismatch) | **missed: complete, score 0% instead of 50%** |
| test-added-under-instrumentation | an extra passing test exists only under instrumentation | contrived | detected (count mismatch) | missed: complete, score 50% |
| masked-real-failure | a test fails on the original code but passes on instrumented source | contrived | detected (original failed) | **missed: real failure hidden, complete at 50%** |
| stack-line | a test asserts the line of a thrown error | plausible | complete, 50% | complete, 50% |
| arity-and-name | a test asserts `Function.length` and `Function.name` | plausible | complete, 50% | complete, 50% |

Instrumentation was invisible in two of the seven sensitive cases: source
maps keep line numbers, and arity and names are preserved. Five cases
changed observable behaviour.

- With both baselines, Oximut refused all five.
- With the instrumented baseline only, it refused two. The other three
  produced complete reports.
- Two of those three reports were wrong. One gave a mutation score of 0%
  instead of 50%. The other hid a real test failure.

Observable instrumentation markers are the `process.env.OXIMUT_ACTIVE_MUTANT`
switches and the `globalThis.__oximut_m3_hit__` probe calls, which show up
in `Function.prototype.toString()`.

Limits: these are constructed cases, not a sample of real projects, so the
counts show which failure classes each gate covers. They do not show how
often each class occurs. In 226 recorded Colord runs, the check never fired.
