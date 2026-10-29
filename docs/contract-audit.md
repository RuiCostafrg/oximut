# R1 release contract audit

This audit maps the narrow contract to executable checks. It does not widen the
support matrix: Linux process controls and `/proc`, Linux Node **26.3.1**, and
Vitest **4.1.12** remain the boundary in [compatibility.md](compatibility.md).
Tests on another runtime would not establish support for it. The report/list
versioning rules remain in [report-schema.md](report-schema.md).

## CLI and serialized interfaces

| Required behavior | Acceptance evidence |
| --- | --- |
| Help and list succeed without running tests; bad invocation exits 2 with a diagnostic | `tests/test_contract.py`: `test_help_and_bad_invocation_exit_codes`, `test_invalid_options_have_diagnostics_and_no_report`, `test_list_v1_envelope_and_location_types` |
| `--version` / `-V` prints `oximut <Cargo package version>` and exits 0 without requiring project files | `test_contract.CliContract.test_version_matches_cargo_package_without_project_or_execution`; the version uses `CARGO_PKG_VERSION`, with no independent version constant |
| A complete all-killed run exits 0; a passing baseline-only run also exits 0 | `test_m1.VerticalSlice.test_stateful_mutants_run_with_fresh_module_state`, `test_disabled_mutations_preserve_stateful_behavior` |
| A complete run with survivors or no-coverage exits 1 | `test_m1.VerticalSlice.test_known_outcomes_and_deterministic_generation`; `test_contract.CliContract.test_only_no_coverage_is_complete_but_exits_one` isolates the no-coverage-only case; `test_m3.PreciseSelection.test_selection_matches_full_suite_and_narrows_ordinary_mutants` asserts no-coverage and checks the CLI exit against summary counts |
| Failed baselines, malformed/stale responses, process failures and failed cleanup exit 2; they never count as kills | `tests/test_m1_failures.py` and `tests/test_m2_failures.py`; baseline failure additionally checks that no mutant job executes |
| Timeout, resource exhaustion and cancellation are incomplete, excluded from kills, and exit 2 | `test_m1_failures.CliFailurePaths.test_unresponsive_adapter_is_stopped_with_its_detached_child`; `test_m5.ResourceBudget.test_tiny_budget_rejects_baseline_without_deadlock`, `test_descendant_hog_exhausts_once_without_kills_or_cache_publication`, `test_sigterm_reaps_detached_processes_and_reports_incomplete` |
| Configuration errors may fail before a report exists; an emitted incomplete report carries `summary.complete: false` | `test_contract.CliContract.test_configuration_error_has_no_report`, `test_invalid_options_have_diagnostics_and_no_report`, `test_unsupported_resolved_projects_fail_before_tests_in_both_modes`; `scripts/tests/validate-reports.test.mjs` accepts incomplete report structure |
| Normal reports and list output have separate schema-1 envelopes | `docs/report.schema.json`, `docs/list.schema.json`; `scripts/tests/validate-{reports,list}.test.mjs` reject invalid versions/types/missing fields and the other envelope; `test_contract.CliContract.test_list_v1_envelope_and_location_types` validates actual emitted list JSON |
| Completed real CLI reports validate against the report schema in CI | The existing CI M6 smoke validates fresh, persistent and selected reports with `scripts/validate-reports.mjs`; this remains fixture evidence, not final-artifact validation |

## Unsupported settings and conservative fallbacks

| Setting or uncertainty | Required response and evidence |
| --- | --- |
| Invalid worker counts, memory units/budget/reserve, timeout, execution/cache mode, seed, environment-name syntax, conflicting list/baseline options, or missing option value | Exit 2 with a diagnostic before execution: `test_m5.ResourceBudget.test_invalid_units_and_worker_counts_are_rejected` and `test_contract.CliContract.test_invalid_options_have_diagnostics_and_no_report` |
| Resolved project pool, isolation, worker count, concurrency, environment or project count violates the runner contract | Fail initialization in both execution modes, emit an incomplete report and execute no tests: `test_contract.CliContract.test_unsupported_resolved_projects_fail_before_tests_in_both_modes`. The runner sets root `fileParallelism: false`; project settings are validated after resolution. A custom environment is rejected rather than loaded. Browser projects have no support claim. |
| Vitest other than exactly 4.1.12 | Fail before creating Vitest: `packages/vitest-adapter/test/contract.test.mjs` exercises both worker entrypoint guards with a stub version 4.1.11. This tests rejection, not operation on that dependency. |
| Custom plugins, global setup, or symlinked project inputs in persistent mode | Fresh controller for each job, with a reuse diagnostic: adapter tests `M2 exposes fresh fallback for global setup and evaluates active setup imports` and `M2 uses fresh controllers for custom plugins and symlinked project inputs`. Multiple projects are rejected before this fallback can authorize execution. |
| Concurrent tests, global setup, import-time hits, suite/hooks, source reflection, native loading or incomplete attribution | Broader execution, never invented no-coverage: `tests/test_m3.py`, `test_m3_failures.AttributionFailurePaths.test_incomplete_or_invalid_inventory_never_establishes_no_coverage`, adapter `test/m3.test.mjs`, and core tests `uncertain_attribution_always_selects_full_suite`, `selection_keeps_sequential_observers_and_requires_explicit_empty_hit` |
| Unsupported JS/TS contexts, parse errors, shadowed selectors or excessive shared-transform expansion | Instrumenter tests `excludes_types_ambient_enum_namespace_and_decorators`, `excludes_computed_keys_in_erased_types_and_abstract_members`, `rejects_parse_errors_without_generating_mutants`, `switching_falls_back_for_shadowed_process_and_large_expansion`; diagnostics/exclusions or static-transform fallback, not additional language support |
| Outcome-cache inputs not asserted complete, corrupt entries/history, changed/unreadable inputs, failed baselines/cleanup | Cache miss, fresh execution, bounded fresh retry or incomplete failure as documented; no reusable successful publication from failure: `tests/test_m4.py`, especially `test_incomplete_or_undeclared_inputs_never_publish_reusable_outcomes`, `test_warm_cache_reuses_mutants_but_always_runs_baseline_and_force_agrees`, `test_corrupt_cache_entry_is_a_miss_and_force_and_no_cache_bypass_reuse`, the input-change/read-failure and shutdown-failure tests |
| Resource limits cannot admit work or sampled usage overruns | Resource-exhausted incomplete result, not a kill or a hard-cap guarantee: `tests/test_m5.py` tests measured admission, descendant exhaustion, tiny budgets, cleanup, and parallel sibling ownership |

## Evidence limits and remaining release gates

These mappings distinguish tests already in the repository from the focused
checks rerun for this change; they do not claim the entire acceptance corpus was
rerun on the release candidate. R3 must still run the full corpus on the exact
release revision and distribution. R4/R5 own artifact, provenance, and support
maintenance evidence. Other Node versions, operating systems, Vitest versions,
and arbitrary plugin/browser/framework graphs remain outside the support claim.
Node is pinned and recorded, not rejected by an exact-version runtime guard.

The guards added here close the resolved non-Node-environment gap in both
workers. No cache, scheduler, installer, package-version or distribution policy
changes are part of this audit. Sampled resource accounting remains best-effort
admission/cancellation; no hard memory or CPU-time limit is promised.

Selected test files are checked before execution for both `@vitest-environment`
and `@jest-environment` overrides, using the pinned Vitest 4.1.12 precedence
and syntax. Non-Node overrides fail explicitly in both workers; files outside
a file selection do not block it. The adapter contract test verifies rejection
before a test-module execution marker is written.

The list schema accepts optional extension fields at every object level while
reserving `runId`, `baseline`, `summary`, `cache`, and `resources` for run reports.
Required list fields and position constraints remain validated.
