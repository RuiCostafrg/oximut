//! Shared M1 protocol and report semantics. No execution failures count as kills.
use serde::{Deserialize, Serialize};

pub const PROTOCOL: u32 = 1;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Position {
    /// UTF-8 byte offset, zero based.
    pub offset: u32,
    /// Line and UTF-16 column, both one based.
    pub line: u32,
    pub column: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Location {
    pub start: Position,
    pub end: Position,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Mutant {
    pub id: String,
    pub file: String,
    pub operator: String,
    pub original: String,
    pub replacement: String,
    pub location: Location,
    pub code: String,
    pub map: serde_json::Value,
    /// Whether the mutated expression may be evaluated while its module loads
    /// (top level, class field initializers, static blocks, immediately invoked
    /// functions) rather than only inside a function body called later. Unknown
    /// is `true`: precollected workers must never receive such a mutant.
    #[serde(default = "load_time_unless_known")]
    pub load_time: bool,
}

fn load_time_unless_known() -> bool {
    true
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum Outcome {
    Killed,
    Survived,
    NoCoverage,
    Invalid,
    Timeout,
    InfrastructureError,
    ResourceExhausted,
    Skipped,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum TestOutcome {
    Passed,
    Failed,
    Invalid,
    Timeout,
    InfrastructureError,
    ResourceExhausted,
    Skipped,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Tests {
    pub passed: u64,
    pub failed: u64,
    pub skipped: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Mutation {
    pub id: String,
    pub file: String,
    pub code: String,
    pub map: serde_json::Value,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lanes: Option<Vec<Request>>,
    pub protocol: u32,
    pub run_id: String,
    pub job_id: String,
    pub generation: u64,
    pub kind: String,
    pub root: String,
    pub config: String,
    pub deadline_ms: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workers: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pool: Option<String>,
    pub mutation: Option<Mutation>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub collect_coverage: Option<Vec<Mutation>>,
    /// Shared switched modules served for every listed file throughout a job.
    /// The instrumented baseline validates exactly these with no active mutant;
    /// mutant jobs keep serving them so executed code equals validated code.
    /// Shared, so queued jobs never copy the modules (one allocation per set).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resident: Option<std::sync::Arc<Vec<Mutation>>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selection: Option<Selection>,
    /// Watchdog: serve this job's test workers with real forks, not the zygote.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bypass_zygote: Option<bool>,
    /// Experimental `--zygote-precollect`: `Some(true)` forks this mutant's worker
    /// from a zygote that already collected its single selected test file,
    /// `Some(false)` keeps the ordinary zygote path (load-time mutant or a
    /// selection precollection cannot serve), `None` means the option is off.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub precollect: Option<bool>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TestIdentity {
    pub id: String,
    pub file: String,
    pub name: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Hit {
    pub mutant_id: String,
    pub tests: Vec<String>,
    pub outside_test: bool,
    /// Compact wire form of `tests`, used by adapters when the list would not fit
    /// a response: inclusive `[first, last]` index runs into `Coverage::tests`, in
    /// order and without overlap. [`Coverage::expand_ranges`] turns them back into
    /// `tests`, so nothing past the transport sees this field.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub test_ranges: Vec<[u64; 2]>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Coverage {
    pub complete: bool,
    pub tests: Vec<TestIdentity>,
    pub hits: Vec<Hit>,
    /// Run-level reasons attribution cannot be trusted for any mutant.
    pub reasons: Vec<String>,
    /// Mutant ids whose source file could not carry probes, with the file and
    /// reason. Attribution for every other mutant stays sound, so only these
    /// mutants fall back to the full suite. Set by the CLI, never by an adapter.
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub unprobed: std::collections::BTreeMap<String, String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Selection {
    pub mode: String,
    pub files: Vec<String>,
    pub selected_tests: u64,
    pub total_tests: u64,
    #[serde(default)]
    pub expected_skipped: u64,
    pub reasons: Vec<String>,
}

impl Coverage {
    /// Replaces each hit's `test_ranges` with the test ids they index.
    ///
    /// A hit carries either explicit ids or ranges, never both; ranges must be
    /// ascending, disjoint and inside the inventory. Anything else is a malformed
    /// payload and leaves the coverage untouched.
    pub fn expand_ranges(&mut self) -> Result<(), String> {
        let total = self.tests.len() as u64;
        let mut expanded = Vec::with_capacity(self.hits.len());
        for hit in &self.hits {
            if hit.test_ranges.is_empty() {
                expanded.push(None);
                continue;
            }
            if !hit.tests.is_empty() {
                return Err("hit carries both test ids and test ranges".into());
            }
            let mut next = 0u64;
            let mut ids = Vec::new();
            for &[first, last] in &hit.test_ranges {
                if first < next || last < first || last >= total {
                    return Err("hit test ranges are out of order or outside the inventory".into());
                }
                ids.extend(
                    self.tests[first as usize..=last as usize]
                        .iter()
                        .map(|test| test.id.clone()),
                );
                next = last + 1;
            }
            expanded.push(Some(ids));
        }
        for (hit, ids) in self.hits.iter_mut().zip(expanded) {
            if let Some(ids) = ids {
                hit.tests = ids;
                hit.test_ranges.clear();
            }
        }
        Ok(())
    }

    /// Preserve every test in a hit file: later tests may observe state changed
    /// by an earlier test without executing the mutated expression themselves.
    pub fn select(&self, mutant_id: &str) -> Selection {
        let mut selection = Selection {
            mode: "full".into(),
            files: vec![],
            selected_tests: self.tests.len() as u64,
            total_tests: self.tests.len() as u64,
            expected_skipped: 0,
            reasons: self.reasons.clone(),
        };
        if !self.complete || !self.reasons.is_empty() {
            if !self.complete {
                selection.reasons.push("incomplete-attribution".into());
            }
            return selection;
        }
        if let Some(why) = self.unprobed.get(mutant_id) {
            selection.reasons.push(format!("unprobed-file: {why}"));
            return selection;
        }
        let hit = self.hits.iter().find(|hit| hit.mutant_id == mutant_id);
        if hit.is_none() {
            selection.reasons.push("missing-mutant-hit-record".into());
            return selection;
        }
        if hit.is_some_and(|hit| hit.outside_test) {
            selection.reasons.push("outside-test-execution".into());
            return selection;
        }
        let ids = hit.map_or(&[][..], |hit| hit.tests.as_slice());
        // Sets, so that a mutant every test of a large suite reaches costs a pass over
        // the tests, not a pass per test.
        let known: std::collections::HashSet<_> =
            self.tests.iter().map(|test| test.id.as_str()).collect();
        if ids.iter().any(|id| !known.contains(id.as_str())) {
            selection.reasons.push("unknown-test-identity".into());
            return selection;
        }
        let wanted: std::collections::HashSet<_> = ids.iter().map(String::as_str).collect();
        let files: std::collections::BTreeSet<_> = self
            .tests
            .iter()
            .filter(|test| wanted.contains(test.id.as_str()))
            .map(|test| test.file.clone())
            .collect();
        selection.selected_tests = self
            .tests
            .iter()
            .filter(|test| files.contains(&test.file))
            .count() as u64;
        selection.files = files.into_iter().collect();
        selection.mode = if selection.files.is_empty() {
            "none"
        } else {
            "files"
        }
        .into();
        selection.reasons.push(
            if selection.files.is_empty() {
                "complete-baseline-without-hits"
            } else {
                "whole-file-preserves-sequential-state-and-hooks"
            }
            .into(),
        );
        selection
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Response {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lanes: Option<Vec<Response>>,
    pub protocol: u32,
    pub run_id: String,
    pub job_id: String,
    pub generation: u64,
    pub outcome: TestOutcome,
    pub tests: Tests,
    pub errors: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coverage: Option<Coverage>,
    /// Number of resident modules the adapter registered for this job. An
    /// adapter that ignores `resident` cannot report a validated execution.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resident: Option<usize>,
    /// How long each test file took in this baseline run (import, setup and
    /// tests), the basis for per-mutant deadlines. Baselines only; optional.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub file_timings: Option<Vec<FileTiming>>,
}

/// One test file's time in a baseline run.
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct FileTiming {
    pub file: String,
    pub duration_ms: f64,
}

impl Response {
    pub fn validate(&self, request: &Request) -> Result<(), String> {
        if self.protocol != PROTOCOL
            || self.run_id != request.run_id
            || self.job_id != request.job_id
            || self.generation != request.generation
        {
            return Err("adapter returned a stale or incompatible response".into());
        }
        match (&request.lanes, &self.lanes) {
            (Some(jobs), Some(results)) if jobs.len() == results.len() => {
                if self.outcome != TestOutcome::Skipped
                    || self.tests != Tests::default()
                    || !self.errors.is_empty()
                {
                    return Err("lane batch carries a result of its own".into());
                }
                for (job, result) in jobs.iter().zip(results) {
                    if job.lanes.is_some() || result.lanes.is_some() {
                        return Err("nested lanes".into());
                    }
                    result.validate(job)?;
                }
            }
            (Some(_), None)
                if matches!(
                    self.outcome,
                    TestOutcome::InfrastructureError
                        | TestOutcome::Timeout
                        | TestOutcome::ResourceExhausted
                ) => {}
            (None, None) => {}
            _ => return Err("invalid lane response inventory".into()),
        }
        let executed_tests = self
            .tests
            .passed
            .checked_add(self.tests.failed)
            .and_then(|count| count.checked_add(self.tests.skipped))
            .ok_or("adapter test counts overflow")?;
        if self.coverage.is_some() && request.collect_coverage.is_none() {
            return Err("unsolicited adapter coverage".into());
        }
        if let Some(timings) = &self.file_timings
            && (request.kind != "baseline"
                || timings
                    .iter()
                    .any(|timing| !timing.duration_ms.is_finite() || timing.duration_ms < 0.0))
        {
            return Err("invalid or unsolicited file timings".into());
        }
        // Runner-level timeouts and failures carry no test evidence; every
        // outcome produced by executing tests must acknowledge resident modules.
        let executed = !matches!(
            self.outcome,
            TestOutcome::Timeout
                | TestOutcome::InfrastructureError
                | TestOutcome::ResourceExhausted
        );
        match (&request.resident, self.resident) {
            (Some(modules), acknowledged) if executed && acknowledged != Some(modules.len()) => {
                return Err("adapter did not acknowledge the resident modules".into());
            }
            (None, Some(_)) => return Err("unsolicited resident acknowledgement".into()),
            _ => {}
        }
        if let Some(selection) = &request.selection
            && self.outcome == TestOutcome::Passed
            && (executed_tests != selection.selected_tests
                || self.tests.skipped != selection.expected_skipped)
        {
            return Err("selected passing run did not execute the expected test inventory".into());
        }
        match self.outcome {
            TestOutcome::Passed
                if self.tests.passed == 0 || self.tests.failed != 0 || !self.errors.is_empty() =>
            {
                Err("passing response requires nonempty successful tests and no errors".into())
            }
            // A suite that throws while Vitest collects it (for example a mutant that makes
            // `describe` setup code throw) fails with an error and no failed test: that is
            // the code under test failing, and it kills. Failures of the tool itself are
            // `infrastructure-error` and never reach this arm.
            TestOutcome::Failed if self.tests.failed == 0 && self.errors.is_empty() => {
                Err("failed response requires a failed test or error evidence".into())
            }
            _ => Ok(()),
        }
    }

    pub fn mutant_outcome(&self) -> Outcome {
        match self.outcome {
            TestOutcome::Passed => Outcome::Survived,
            TestOutcome::Failed => Outcome::Killed,
            TestOutcome::Invalid => Outcome::Invalid,
            TestOutcome::Timeout => Outcome::Timeout,
            TestOutcome::InfrastructureError => Outcome::InfrastructureError,
            TestOutcome::ResourceExhausted => Outcome::ResourceExhausted,
            TestOutcome::Skipped => Outcome::Skipped,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MutantResult {
    pub id: String,
    pub file: String,
    pub operator: String,
    pub original: String,
    pub replacement: String,
    pub location: Location,
    pub outcome: Outcome,
    pub tests: Tests,
    pub errors: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub selection: Option<Selection>,
    /// The deadline this mutant's job ran under, when it was executed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deadline_ms: Option<u64>,
    pub provenance: Provenance,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Provenance {
    pub source: String,
    pub reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fingerprint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub original_run_id: Option<String>,
}

/// One full fingerprint attempt; counters describe actual reads, including repeats.
#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FingerprintProfile {
    pub phase: String,
    pub completed: bool,
    pub elapsed_ms: u64,
    pub runtime_identity_ms: u64,
    pub resource_policy_ms: u64,
    pub inputs_ms: u64,
    pub files_hashed: u64,
    pub bytes_hashed: u64,
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CacheReport {
    pub enabled: bool,
    pub outcomes_enabled: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fingerprint: Option<String>,
    pub reason: String,
    pub instrumentation_hits: usize,
    pub instrumentation_misses: usize,
    pub outcome_hits: usize,
    pub outcome_misses: usize,
    pub persistence: String,
    pub runner_hash_ms: u64,
    pub fingerprint_profiles: Vec<FingerprintProfile>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Summary {
    pub total: usize,
    pub killed: usize,
    pub survived: usize,
    pub no_coverage: usize,
    pub invalid: usize,
    pub timeout: usize,
    pub infrastructure_error: usize,
    pub resource_exhausted: usize,
    pub skipped: usize,
    pub score_denominator: usize,
    pub score: Option<f64>,
    pub complete: bool,
}

impl Summary {
    pub fn from_results(results: &[MutantResult]) -> Self {
        let count = |outcome| {
            results
                .iter()
                .filter(|result| result.outcome == outcome)
                .count()
        };
        let killed = count(Outcome::Killed);
        let survived = count(Outcome::Survived);
        let no_coverage = count(Outcome::NoCoverage);
        let denominator = killed + survived + no_coverage;
        let invalid = count(Outcome::Invalid);
        let timeout = count(Outcome::Timeout);
        let infrastructure_error = count(Outcome::InfrastructureError);
        let skipped = count(Outcome::Skipped);
        let resource_exhausted = count(Outcome::ResourceExhausted);
        Self {
            total: results.len(),
            killed,
            survived,
            no_coverage,
            invalid,
            timeout,
            infrastructure_error,
            resource_exhausted,
            skipped,
            score_denominator: denominator,
            score: (denominator > 0).then(|| 100.0 * killed as f64 / denominator as f64),
            complete: invalid + timeout + infrastructure_error + resource_exhausted + skipped == 0,
        }
    }

    /// 0: complete with no survivors; 1: survivors/no coverage; 2: incomplete/error.
    pub fn exit_code(&self) -> u8 {
        if !self.complete {
            2
        } else if self.survived + self.no_coverage > 0 {
            1
        } else {
            0
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Report {
    pub schema_version: u32,
    pub run_id: String,
    pub baseline: Response,
    pub mutants: Vec<MutantResult>,
    pub diagnostics: Vec<String>,
    pub summary: Summary,
    pub cache: CacheReport,
    pub resources: serde_json::Value,
    pub sources: Vec<SourceBinding>,
    pub mutators: MutatorSelection,
}

/// Every mutator family this version can generate, sorted.
pub const MUTATOR_FAMILIES: [&str; 3] = ["arithmetic", "boolean-literal", "comparison-boundary"];

/// Binds report evidence to the exact bytes of one `--source` file.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceBinding {
    pub file: String,
    pub sha256: String,
    pub mutants: usize,
}

/// Machine-readable mutator selection (`all` or an explicit `subset`).
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MutatorSelection {
    pub selection: &'static str,
    pub families: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_pool_is_optional_and_round_trips() {
        let value = serde_json::json!({"protocol": 1, "runId": "r", "jobId": "j", "generation": 0,
            "kind": "baseline", "root": "/p", "config": "/p/c", "deadlineMs": 10, "mutation": null});
        let mut request: Request = serde_json::from_value(value).unwrap();
        assert!(request.pool.is_none());
        assert!(
            serde_json::to_value(&request)
                .unwrap()
                .get("pool")
                .is_none()
        );
        request.pool = Some("threads".into());
        let serialized = serde_json::to_value(&request).unwrap();
        assert_eq!(serialized["pool"], "threads");
        assert_eq!(
            serde_json::from_value::<Request>(serialized).unwrap().pool,
            request.pool
        );
    }

    #[test]
    fn infrastructure_is_never_a_kill_and_empty_pass_is_rejected() {
        let request = Request {
            lanes: None,
            protocol: 1,
            run_id: "r".into(),
            job_id: "j".into(),
            generation: 1,
            kind: "mutant".into(),
            root: "/".into(),
            config: "/c".into(),
            deadline_ms: 10,
            workers: None,
            pool: None,
            mutation: None,
            collect_coverage: None,
            resident: None,
            selection: None,
            bypass_zygote: None,
            precollect: None,
        };
        let mut response = Response {
            lanes: None,
            protocol: 1,
            run_id: "r".into(),
            job_id: "j".into(),
            generation: 1,
            outcome: TestOutcome::InfrastructureError,
            tests: Tests::default(),
            errors: vec!["crash".into()],
            coverage: None,
            resident: None,
            file_timings: None,
        };
        assert_eq!(response.mutant_outcome(), Outcome::InfrastructureError);
        assert!(response.validate(&request).is_ok());
        response.outcome = TestOutcome::Passed;
        assert!(response.validate(&request).is_err());
        response.outcome = TestOutcome::Failed;
        response.errors.clear();
        assert!(response.validate(&request).is_err());
        response.tests.failed = 1;
        assert!(response.validate(&request).is_ok());
        // A suite that fails while it is collected has an error but no failed test.
        response.tests.failed = 0;
        response.errors = vec!["TypeError: x is not a function".into()];
        assert!(response.validate(&request).is_ok());
        assert_eq!(response.mutant_outcome(), Outcome::Killed);
        response.tests.failed = 1;
        response.generation = 2;
        assert!(response.validate(&request).is_err());
    }

    #[test]
    fn lane_batches_need_one_valid_result_per_lane_and_no_result_of_their_own() {
        let lane = |job: &str| Request {
            lanes: None,
            protocol: 1,
            run_id: "r".into(),
            job_id: job.into(),
            generation: 1,
            kind: "mutant".into(),
            root: "/".into(),
            config: "/c".into(),
            deadline_ms: 10,
            workers: None,
            pool: None,
            mutation: None,
            collect_coverage: None,
            resident: None,
            selection: None,
            bypass_zygote: None,
            precollect: None,
        };
        let result = |job: &str, outcome| Response {
            lanes: None,
            protocol: 1,
            run_id: "r".into(),
            job_id: job.into(),
            generation: 1,
            outcome,
            tests: Tests::default(),
            errors: vec![],
            coverage: None,
            resident: None,
            file_timings: None,
        };
        let mut batch = lane("a");
        batch.lanes = Some(vec![lane("a"), lane("b")]);
        let crashed = TestOutcome::InfrastructureError;
        let mut valid = result("a", TestOutcome::Skipped);
        valid.lanes = Some(vec![result("a", crashed), result("b", crashed)]);
        assert!(valid.validate(&batch).is_ok());

        let rejected = |change: &dyn Fn(&mut Response)| {
            let mut response = valid.clone();
            change(&mut response);
            response.validate(&batch).unwrap_err()
        };
        fn lanes(response: &mut Response) -> &mut Vec<Response> {
            response.lanes.as_mut().unwrap()
        }
        assert!(
            rejected(&|r| {
                lanes(r).pop();
            })
            .contains("inventory")
        );
        assert!(rejected(&|r| lanes(r)[1].lanes = Some(vec![])).contains("nested"));
        assert!(rejected(&|r| lanes(r)[1].job_id = "z".into()).contains("stale"));
        assert!(rejected(&|r| r.outcome = TestOutcome::Failed).contains("of its own"));
        assert!(rejected(&|r| r.errors.push("x".into())).contains("of its own"));
        assert!(rejected(&|r| r.tests.failed = 1).contains("of its own"));

        // Only a failed batch may omit the lanes, and only a batch may have them.
        let mut failed = result("a", crashed);
        assert!(failed.validate(&batch).is_ok());
        failed.outcome = TestOutcome::Skipped;
        assert!(failed.validate(&batch).unwrap_err().contains("inventory"));
        assert!(
            valid
                .validate(&lane("a"))
                .unwrap_err()
                .contains("inventory")
        );
    }

    #[test]
    fn no_results_have_no_score() {
        let summary = Summary::from_results(&[]);
        assert_eq!(summary.score, None);
        assert_eq!(summary.score_denominator, 0);
    }
    fn coverage() -> Coverage {
        Coverage {
            complete: true,
            tests: vec![
                TestIdentity {
                    id: "first".into(),
                    file: "/a.test.ts".into(),
                    name: "duplicate".into(),
                },
                TestIdentity {
                    id: "later".into(),
                    file: "/a.test.ts".into(),
                    name: "duplicate".into(),
                },
                TestIdentity {
                    id: "other".into(),
                    file: "/b.test.ts".into(),
                    name: "other".into(),
                },
            ],
            hits: vec![
                Hit {
                    mutant_id: "covered".into(),
                    tests: vec!["first".into()],
                    outside_test: false,
                    ..Default::default()
                },
                Hit {
                    mutant_id: "uncovered".into(),
                    tests: vec![],
                    outside_test: false,
                    ..Default::default()
                },
            ],
            reasons: vec![],
            unprobed: Default::default(),
        }
    }

    #[test]
    fn index_runs_expand_to_the_test_ids_they_name() {
        let mut compact = coverage();
        compact.hits = vec![
            Hit {
                mutant_id: "load".into(),
                test_ranges: vec![[0, 2]],
                ..Default::default()
            },
            Hit {
                mutant_id: "apart".into(),
                test_ranges: vec![[0, 0], [2, 2]],
                ..Default::default()
            },
            Hit {
                mutant_id: "explicit".into(),
                tests: vec!["later".into()],
                ..Default::default()
            },
        ];
        compact.expand_ranges().unwrap();
        let ids = |name: &str| {
            compact
                .hits
                .iter()
                .find(|hit| hit.mutant_id == name)
                .unwrap()
                .clone()
        };
        assert_eq!(ids("load").tests, ["first", "later", "other"]);
        assert_eq!(ids("apart").tests, ["first", "other"]);
        assert_eq!(ids("explicit").tests, ["later"]);
        assert!(compact.hits.iter().all(|hit| hit.test_ranges.is_empty()));
        // Expanded hits select as explicit ones do.
        assert_eq!(compact.select("apart").files, ["/a.test.ts", "/b.test.ts"]);
        // The ranges never reach a report.
        assert!(
            !serde_json::to_string(&compact)
                .unwrap()
                .contains("testRanges")
        );
    }

    #[test]
    fn malformed_index_runs_are_refused_and_change_nothing() {
        for ranges in [
            vec![[1, 0]],
            vec![[0, 3]],
            vec![[2, 2], [0, 0]],
            vec![[0, 1], [1, 2]],
        ] {
            let mut bad = coverage();
            bad.hits = vec![Hit {
                mutant_id: "m".into(),
                test_ranges: ranges.clone(),
                ..Default::default()
            }];
            assert!(bad.expand_ranges().is_err(), "{ranges:?}");
            assert_eq!(bad.hits[0].test_ranges, ranges);
            assert!(bad.hits[0].tests.is_empty());
        }
        let mut both = coverage();
        both.hits = vec![Hit {
            mutant_id: "m".into(),
            tests: vec!["first".into()],
            test_ranges: vec![[0, 0]],
            ..Default::default()
        }];
        assert!(both.expand_ranges().is_err());
    }

    #[test]
    fn file_timings_belong_to_baselines_and_must_be_finite() {
        let request = |kind: &str| -> Request {
            serde_json::from_value(serde_json::json!({
                "protocol": 1, "runId": "r", "jobId": "j", "generation": 1,
                "kind": kind, "root": "/p", "config": "/p/c", "deadlineMs": 10,
                "mutation": null
            }))
            .unwrap()
        };
        let response = |timings: Vec<FileTiming>| -> Response {
            serde_json::from_value(serde_json::json!({
                "protocol": 1, "runId": "r", "jobId": "j", "generation": 1,
                "outcome": "skipped", "tests": {"passed": 0, "failed": 0, "skipped": 0},
                "errors": [],
                "fileTimings": timings,
            }))
            .unwrap()
        };
        let timing = |duration_ms| FileTiming {
            file: "/a.test.ts".into(),
            duration_ms,
        };
        assert!(
            response(vec![timing(12.5)])
                .validate(&request("baseline"))
                .is_ok()
        );
        assert!(
            response(vec![timing(-1.0)])
                .validate(&request("baseline"))
                .is_err()
        );
        assert!(
            response(vec![timing(1.0)])
                .validate(&request("mutant"))
                .unwrap_err()
                .contains("file timings")
        );
    }

    #[test]
    fn selection_keeps_sequential_observers_and_requires_explicit_empty_hit() {
        let coverage = coverage();
        let selected = coverage.select("covered");
        assert_eq!(selected.mode, "files");
        assert_eq!(selected.files, ["/a.test.ts"]);
        assert_eq!(selected.selected_tests, 2);
        assert_eq!(selected.total_tests, 3);
        assert_eq!(coverage.select("uncovered").mode, "none");
        assert_eq!(coverage.select("missing").mode, "full");
    }

    #[test]
    fn an_unprobed_file_widens_only_its_own_mutants() {
        let mut coverage = coverage();
        coverage
            .unprobed
            .insert("loose".into(), "src/e.ts: probe-globalThis-conflict".into());
        // The unprobed mutant never reads as uncovered, even with no hit record.
        let loose = coverage.select("loose");
        assert_eq!(loose.mode, "full");
        assert_eq!(loose.selected_tests, 3);
        assert_eq!(
            loose.reasons,
            ["unprobed-file: src/e.ts: probe-globalThis-conflict"]
        );
        // Every other mutant keeps its narrow selection.
        assert_eq!(coverage.select("covered").mode, "files");
        assert_eq!(coverage.select("uncovered").mode, "none");
        // Run-level uncertainty still widens everything, with its own reasons.
        coverage.complete = false;
        let all = coverage.select("covered");
        assert_eq!(all.mode, "full");
        assert!(all.reasons.contains(&"incomplete-attribution".to_string()));
    }

    #[test]
    fn an_outside_test_hit_widens_only_its_own_mutant() {
        // Hits while a setup file or module loads have no test to attribute to. They
        // run the full suite for that mutant, but never make the run incomplete.
        let mut coverage = coverage();
        coverage.hits.push(Hit {
            mutant_id: "setup".into(),
            outside_test: true,
            ..Default::default()
        });
        let setup = coverage.select("setup");
        assert_eq!(setup.mode, "full");
        assert_eq!(setup.selected_tests, 3);
        assert_eq!(setup.reasons, ["outside-test-execution"]);
        assert_eq!(coverage.select("covered").mode, "files");
        assert_eq!(coverage.select("uncovered").mode, "none");
    }

    #[test]
    fn uncertain_attribution_always_selects_full_suite() {
        let mut coverage = coverage();
        coverage.hits[0].outside_test = true;
        assert_eq!(coverage.select("covered").mode, "full");
        coverage.hits[0].outside_test = false;
        coverage.hits[0].tests.push("unknown".into());
        assert_eq!(coverage.select("covered").mode, "full");
        coverage.complete = false;
        assert_eq!(coverage.select("uncovered").mode, "full");
        coverage.complete = true;
        coverage.reasons.push("unsupported-context".into());
        assert_eq!(coverage.select("uncovered").mode, "full");
    }

    #[test]
    fn selected_pass_must_match_inventory_and_coverage_must_be_requested() {
        let mut request: Request = serde_json::from_value(serde_json::json!({
            "protocol": 1, "runId": "r", "jobId": "j", "generation": 0,
            "kind": "mutant", "root": "/project", "config": "/project/vitest.config.ts",
            "deadlineMs": 1000, "mutation": null
        }))
        .unwrap();
        request.selection = Some(coverage().select("covered"));
        let mut response = Response {
            lanes: None,
            protocol: 1,
            run_id: "r".into(),
            job_id: "j".into(),
            generation: 0,
            outcome: TestOutcome::Passed,
            tests: Tests {
                passed: 1,
                failed: 0,
                skipped: 0,
            },
            errors: vec![],
            coverage: None,
            resident: None,
            file_timings: None,
        };
        assert!(response.validate(&request).is_err());
        response.tests.passed = 2;
        assert!(response.validate(&request).is_ok());
        response.coverage = Some(coverage());
        assert!(response.validate(&request).is_err());
        request.collect_coverage = Some(vec![]);
        assert!(response.validate(&request).is_ok());
        response.tests.passed = u64::MAX;
        response.tests.skipped = 1;
        assert!(response.validate(&request).is_err());
    }

    #[test]
    fn executed_results_must_acknowledge_exactly_the_resident_modules() {
        let mut request: Request = serde_json::from_value(serde_json::json!({
            "protocol": 1, "runId": "r", "jobId": "j", "generation": 0,
            "kind": "baseline", "root": "/project", "config": "/project/vitest.config.ts",
            "deadlineMs": 1000, "mutation": null
        }))
        .unwrap();
        let module = Mutation {
            id: "__oximut_resident__".into(),
            file: "/project/a.ts".into(),
            code: String::new(),
            map: serde_json::json!({}),
        };
        let mut response = Response {
            lanes: None,
            protocol: 1,
            run_id: "r".into(),
            job_id: "j".into(),
            generation: 0,
            outcome: TestOutcome::Passed,
            tests: Tests {
                passed: 1,
                failed: 0,
                skipped: 0,
            },
            errors: vec![],
            coverage: None,
            resident: Some(1),
            file_timings: None,
        };
        assert!(
            response
                .validate(&request)
                .unwrap_err()
                .contains("unsolicited")
        );
        request.resident = Some(std::sync::Arc::new(vec![module.clone(), module]));
        assert!(
            response
                .validate(&request)
                .unwrap_err()
                .contains("acknowledge")
        );
        response.resident = Some(2);
        assert!(response.validate(&request).is_ok());
        for outcome in [
            TestOutcome::Failed,
            TestOutcome::Invalid,
            TestOutcome::Skipped,
        ] {
            response.outcome = outcome;
            response.tests = Tests {
                passed: 0,
                failed: 1,
                skipped: 0,
            };
            response.resident = None;
            assert!(
                response
                    .validate(&request)
                    .unwrap_err()
                    .contains("acknowledge")
            );
        }
        // Runner-level failures carry no test evidence to validate.
        for outcome in [
            TestOutcome::Timeout,
            TestOutcome::InfrastructureError,
            TestOutcome::ResourceExhausted,
        ] {
            response.outcome = outcome;
            assert!(response.validate(&request).is_ok());
        }
    }
}
