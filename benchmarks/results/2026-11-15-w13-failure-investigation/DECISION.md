# Parent decision: park W13; do not promote

The original repeated-trial failure remains unexplained. The single authorized
diagnostic replay did not reproduce it. Neither that replay nor the earlier
bounded correctness passes resolve reliability. No scored retry is justified
by an empty failure log. Later diagnostic formatting changes are locally tested,
not natively requalified.

Keep the branch, source history, failures, raw artifacts and diagnostics. Do not
merge the experimental hook into main, claim a speedup or derive mission ratios
from the invalid campaign. Default fresh-process behavior remains on main.

This is an engineering prioritization decision, not proof that a numerical
performance target was missed. Recorded setup and repeated full-install hashing
costs are material; the private bundle contains only six modules. A new candidate
would need a distinct mechanism and a defensible cost boundary, not subtraction
of real validation time or repetition until a campaign passes.

The next design assessment must inspect the prior Vitest 4 snapshot/worker-entry
studies as well as W13. Dependency preloading already failed to seed native module
identity and increased measured child CPU on its narrow local probe. Any larger
bundle/snapshot proposal must show how worker and public test API imports reach
the same collector/expect/mock state, retain fresh processes and runtime-dependent
initialization, and avoid the old duplicate-evaluation failure. An install-time
artifact also needs explicit build, invalidation, relocation and runtime costs.

Do not start another expensive experiment until the assessment names a concrete
mechanism, evidence of work it removes, a small falsifiable oracle and stop gates.
The overall goal remains unmet: wall at most 0.50x Stryker, CPU and peak aggregate
RSS at most 0.80x, with equivalent outcomes and reported uncertainty.
