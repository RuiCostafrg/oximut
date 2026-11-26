//! Job deadlines.
//!
//! A fixed deadline cannot fit every project: a mutant that is evaluated at
//! module load legitimately runs the whole suite, and a baseline of a large
//! suite takes minutes, while an infinite-loop mutant in a tiny file should not
//! hold a worker for as long as either. The deadline of a job is therefore
//! derived from how long the tests it runs took in the baseline, as StrykerJS
//! derives its own (`netTime * timeoutFactor + timeoutMS`):
//!
//! ```text
//! mutant deadline = baseline time of the test files the mutant runs * factor + constant
//! ```
//!
//! The constant covers what the baseline does not measure: forking a worker,
//! collecting the test files and, for the first job of a cold controller, its
//! start-up (job deadlines include it). `--timeout-ms` keeps its documented
//! meaning, an explicit deadline for every job, and turns the derivation off.
use oximut_core::{FileTiming, Response, Selection};
use std::collections::BTreeMap;

/// The longest any job may be given, the bound `--timeout-ms` always had.
pub const MAX_TIMEOUT_MS: u64 = 86_400_000;
/// Baselines (original and instrumented) run the whole suite once, with a cold
/// controller. Generous by default, like StrykerJS's five-minute dry run.
pub const DEFAULT_BASELINE_TIMEOUT_MS: u64 = 600_000;
pub const DEFAULT_TIMEOUT_FACTOR: f64 = 1.5;
pub const DEFAULT_TIMEOUT_CONSTANT_MS: u64 = 10_000;
/// Warming a spare controller runs no tests.
pub const WARM_TIMEOUT_MS: u64 = 30_000;
/// The largest `--timeout-factor`, so that `time * factor` stays representable.
pub const MAX_TIMEOUT_FACTOR: f64 = 1000.0;

#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Deadlines {
    /// `--timeout-ms`: one deadline for every job. Mutant deadlines are not derived.
    pub fixed_ms: Option<u64>,
    /// `--baseline-timeout-ms`, else `--timeout-ms`, else the default.
    pub baseline_ms: u64,
    pub factor: f64,
    pub constant_ms: u64,
}

impl Default for Deadlines {
    fn default() -> Self {
        Self {
            fixed_ms: None,
            baseline_ms: DEFAULT_BASELINE_TIMEOUT_MS,
            factor: DEFAULT_TIMEOUT_FACTOR,
            constant_ms: DEFAULT_TIMEOUT_CONSTANT_MS,
        }
    }
}

impl Deadlines {
    /// What one spare controller may take to warm.
    pub fn warm_ms(&self) -> u64 {
        self.fixed_ms.unwrap_or(WARM_TIMEOUT_MS)
    }

    /// The deadline of a mutant job that runs `selection` (`None`: the whole suite).
    pub fn for_mutant(&self, timings: &SuiteTimings, selection: Option<&Selection>) -> u64 {
        if let Some(fixed) = self.fixed_ms {
            return fixed;
        }
        let time = timings.run_time_ms(selection);
        let scaled = (time * self.factor).ceil();
        let scaled = if scaled.is_finite() {
            scaled.min(MAX_TIMEOUT_MS as f64) as u64
        } else {
            MAX_TIMEOUT_MS
        };
        scaled
            .saturating_add(self.constant_ms)
            .clamp(1, MAX_TIMEOUT_MS)
    }
}

/// What the baselines say about how long the suite takes.
#[derive(Debug, Default)]
pub struct SuiteTimings {
    /// Per test file: the longest it took in any baseline run (those run with
    /// parallel workers and probes, so the longest is the conservative one).
    files: BTreeMap<String, f64>,
    /// The longest a whole baseline job took, from the controller's view.
    wall_ms: f64,
    /// Used to scale the wall time when only some of the tests run.
    total_tests: u64,
    /// Baseline file concurrency, since a mutant runs its files one after another.
    workers: u64,
}

impl SuiteTimings {
    pub fn new(workers: usize) -> Self {
        Self {
            workers: workers.max(1) as u64,
            ..Self::default()
        }
    }

    /// Records one finished baseline job: its wall time and, when the adapter
    /// reported them, its per-file times.
    pub fn observe(&mut self, response: &Response, wall_ms: f64) {
        self.wall_ms = self.wall_ms.max(wall_ms);
        let executed = response.tests.passed + response.tests.failed + response.tests.skipped;
        self.total_tests = self.total_tests.max(executed);
        for FileTiming { file, duration_ms } in response.file_timings.iter().flatten() {
            let entry = self.files.entry(file.clone()).or_insert(0.0);
            *entry = entry.max(*duration_ms);
        }
    }

    /// The merged per-file times, for the report.
    pub fn file_timings(&self) -> Option<Vec<FileTiming>> {
        (!self.files.is_empty()).then(|| {
            self.files
                .iter()
                .map(|(file, duration_ms)| FileTiming {
                    file: file.clone(),
                    duration_ms: *duration_ms,
                })
                .collect()
        })
    }

    /// The whole suite run in one worker, files one after another, as a mutant
    /// that selects every test does.
    fn full_suite_ms(&self) -> f64 {
        if self.files.is_empty() {
            // No per-file evidence: a baseline's wall time spread over its workers.
            self.wall_ms * self.workers as f64
        } else {
            self.files.values().sum()
        }
    }

    /// How long a job that runs `selection` takes in the baseline.
    fn run_time_ms(&self, selection: Option<&Selection>) -> f64 {
        let full = self.full_suite_ms();
        let Some(selection) = selection else {
            return full;
        };
        if selection.mode != "files" {
            return full;
        }
        let measured: Option<f64> = selection
            .files
            .iter()
            .map(|file| self.files.get(file).copied())
            .sum();
        measured.unwrap_or_else(|| {
            // A file the adapter did not time: its share of the tests.
            let share = selection.selected_tests as f64 / selection.total_tests.max(1) as f64;
            full * share.clamp(0.0, 1.0)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use oximut_core::Tests;

    fn baseline(timings: &[(&str, f64)], tests: u64) -> Response {
        Response {
            lanes: None,
            protocol: 1,
            run_id: String::new(),
            job_id: String::new(),
            generation: 0,
            outcome: oximut_core::TestOutcome::Passed,
            tests: Tests {
                passed: tests,
                failed: 0,
                skipped: 0,
            },
            errors: vec![],
            coverage: None,
            resident: None,
            file_timings: Some(
                timings
                    .iter()
                    .map(|(file, duration_ms)| FileTiming {
                        file: (*file).into(),
                        duration_ms: *duration_ms,
                    })
                    .collect(),
            ),
        }
    }

    fn files(selected: &[&str], tests: u64, total: u64) -> Selection {
        Selection {
            mode: "files".into(),
            files: selected.iter().map(|file| (*file).into()).collect(),
            selected_tests: tests,
            total_tests: total,
            expected_skipped: 0,
            reasons: vec![],
        }
    }

    fn suite() -> SuiteTimings {
        let mut timings = SuiteTimings::new(4);
        timings.observe(
            &baseline(&[("/a.test.ts", 200.0), ("/b.test.ts", 40_000.0)], 10),
            12_000.0,
        );
        timings
    }

    #[test]
    fn a_mutant_is_given_the_time_of_the_files_it_runs_times_the_factor_plus_the_constant() {
        let deadlines = Deadlines::default();
        assert_eq!(
            deadlines.for_mutant(&suite(), Some(&files(&["/a.test.ts"], 4, 10))),
            300 + 10_000
        );
        assert_eq!(
            deadlines.for_mutant(
                &suite(),
                Some(&files(&["/a.test.ts", "/b.test.ts"], 10, 10))
            ),
            (40_200.0 * 1.5) as u64 + 10_000
        );
    }

    #[test]
    fn a_mutant_that_runs_the_whole_suite_is_given_the_whole_suite() {
        let deadlines = Deadlines::default();
        let whole = (40_200.0 * 1.5) as u64 + 10_000;
        let full = Selection {
            mode: "full".into(),
            ..files(&[], 10, 10)
        };
        assert_eq!(deadlines.for_mutant(&suite(), Some(&full)), whole);
        // Test selection off: no selection at all.
        assert_eq!(deadlines.for_mutant(&suite(), None), whole);
    }

    #[test]
    fn the_constant_is_the_floor_and_the_bound_holds() {
        let deadlines = Deadlines {
            constant_ms: 5_000,
            ..Deadlines::default()
        };
        let empty = SuiteTimings::new(1);
        assert_eq!(deadlines.for_mutant(&empty, None), 5_000);
        let mut huge = SuiteTimings::new(1);
        huge.observe(&baseline(&[("/a.test.ts", 1e15)], 1), 1e15);
        assert_eq!(deadlines.for_mutant(&huge, None), MAX_TIMEOUT_MS);
    }

    #[test]
    fn an_explicit_timeout_is_every_jobs_deadline() {
        let deadlines = Deadlines {
            fixed_ms: Some(1_234),
            baseline_ms: 1_234,
            ..Deadlines::default()
        };
        assert_eq!(deadlines.for_mutant(&suite(), None), 1_234);
        assert_eq!(
            deadlines.for_mutant(&suite(), Some(&files(&["/b.test.ts"], 5, 10))),
            1_234
        );
        assert_eq!(deadlines.warm_ms(), 1_234);
        assert_eq!(Deadlines::default().warm_ms(), WARM_TIMEOUT_MS);
    }

    #[test]
    fn without_per_file_times_the_baseline_wall_time_stands_in() {
        let mut timings = SuiteTimings::new(4);
        let mut response = baseline(&[], 100);
        response.file_timings = None;
        timings.observe(&response, 8_000.0);
        let deadlines = Deadlines::default();
        // The whole suite in one worker: four workers' worth of the wall time.
        assert_eq!(deadlines.for_mutant(&timings, None), 48_000 + 10_000);
        // A selection takes its share of the tests.
        assert_eq!(
            deadlines.for_mutant(&timings, Some(&files(&["/x.test.ts"], 25, 100))),
            12_000 + 10_000
        );
    }

    #[test]
    fn a_file_the_adapter_did_not_time_takes_its_share_of_the_suite() {
        let timings = suite();
        let deadlines = Deadlines::default();
        let selection = files(&["/a.test.ts", "/missing.test.ts"], 5, 10);
        assert_eq!(
            deadlines.for_mutant(&timings, Some(&selection)),
            (40_200.0 * 0.5 * 1.5) as u64 + 10_000
        );
    }

    #[test]
    fn the_slowest_baseline_run_of_a_file_counts_and_is_reported() {
        let mut timings = SuiteTimings::new(1);
        timings.observe(&baseline(&[("/a.test.ts", 100.0)], 1), 10.0);
        timings.observe(&baseline(&[("/a.test.ts", 700.0)], 1), 20.0);
        timings.observe(&baseline(&[("/a.test.ts", 300.0)], 1), 5.0);
        assert_eq!(
            timings.file_timings(),
            Some(vec![FileTiming {
                file: "/a.test.ts".into(),
                duration_ms: 700.0
            }])
        );
        assert_eq!(SuiteTimings::new(1).file_timings(), None);
    }
}
