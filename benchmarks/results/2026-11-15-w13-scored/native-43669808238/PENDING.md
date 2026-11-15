# Status: completed failure; scoring invalid/incomplete

Run43669808238 completed with **failure**. [Final report](../REPORT.md) · [Raw recovery verification](failure-recovery.json) · [Exact failure](failure-detail.json). All global ratios and wall/CPU/RSS mission acceptance are suppressed. No retry was dispatched. The success-only verifier below was not executed.

The original pending handoff is preserved verbatim below as historical context, not current instructions or status.

<details><summary>Archived pending handoff from33909fe</summary>

# Native scored campaign pending

Run: https://github.com/RuiCostafrg/oximut/actions/runs/43669808238

Owned immutable scored ref: `perf/w13-score-native-2`, candidate source `88b7d6712648979dc7438a4044abf0f63650d463`. Stock: `5dfd17e53cd7b067586b3ad47363cb3ecee5873e`. Executable adapter/probe inputs remain byte-identical to qualified lifecycle-fix source `e9a368face7d8e3fd657383c2c979e2866370a38`; all Cargo workspace source/pins remain byte-identical to stock. The first harness attempt at `dd0de59` is retained in `../native-43668813571`, including its false one-controller assertion and complete raw evidence; no rows from it are reused.

Completed in this run: both accepted native qualification receipts checked; 11 focused harness tests; typecheck; exact isolated stock/candidate release builds. The serial timed phase is running. Full artifact recovery, four-configuration correctness/build acceptance, raw resource tables, paired ratios/bootstrap95% intervals and **all three mission goals are pending**. No performance conclusion follows from the completed prerequisites.

Each candidate invocation measures a fresh full external preparation command through exit using the existing harness. Prepared execution includes every controller validation (including native recycling), all stock baselines, per-worker artifact validation, map registration/lazy consumption and existing event/proc diagnostics. First-use wall/CPU add raw stage observations per pair before ratios; sequential peak RSS takes MAX. Nothing is subtracted, and no Rust RSS is added to the harness process-tree peak. Actual sampler gaps are retained in every raw row; 20ms is only the requested interval. The qualified manifest's historical “all costs unscored” policy string was preserved as source evidence; the scoring harness's explicit outer cost policy and measurements define this campaign's costs.

Recovery: use bounded `gh run watch 43669808238 --exit-status`, then download the complete artifact.zip and logs.zip plus run/artifact API metadata. Archive contains `w13-score-raw.tar.gz` and per-file hashes. Extract under `/tmp/oximut-w13-score-43669808238/{artifact,raw}` and run `verification-source.py` from repository cwd after source completion. This read-only verifier requires all144 tool rows,24 full preparation rows,24 unique artifacts,2592 distinct bundled PIDs, complete scope/inventory/phase/resource/cleanup gates, exact source/build/runtime/artifact/helper hashes, and exact reproduction of both prepared/first-use paired evaluations. It intentionally cannot certify a failed or incomplete campaign. Preserve a real failure and stop; no automatic retries or gate relaxation.

When complete, retain full compressed artifact/logs and hashes in this directory, publish raw wall/treeCPU/sampled aggregateRSS plus prepared/first-use after/before and candidate/Stryker paired medians/CIs. Mission requires all upper bounds <=.50 wall, <=.80 CPU and <=.80 sampled RSS in all four configurations. Failure suppresses global comparisons/acceptance. A target miss is useful evidence. Five pairs remain exploratory and do not establish general noninferiority, product support, relocatability or speed outside this declared scope.

</details>
