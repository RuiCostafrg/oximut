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

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
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
    pub protocol: u32,
    pub run_id: String,
    pub job_id: String,
    pub generation: u64,
    pub kind: String,
    pub root: String,
    pub config: String,
    pub deadline_ms: u64,
    pub mutation: Option<Mutation>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub collect_coverage: Option<Vec<Mutation>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selection: Option<Selection>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TestIdentity {
    pub id: String,
    pub file: String,
    pub name: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Hit {
    pub mutant_id: String,
    pub tests: Vec<String>,
    pub outside_test: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Coverage {
    pub complete: bool,
    pub tests: Vec<TestIdentity>,
    pub hits: Vec<Hit>,
    pub reasons: Vec<String>,
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
        if ids
            .iter()
            .any(|id| !self.tests.iter().any(|test| &test.id == id))
        {
            selection.reasons.push("unknown-test-identity".into());
            return selection;
        }
        let files: std::collections::BTreeSet<_> = self
            .tests
            .iter()
            .filter(|test| ids.contains(&test.id))
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
    pub protocol: u32,
    pub run_id: String,
    pub job_id: String,
    pub generation: u64,
    pub outcome: TestOutcome,
    pub tests: Tests,
    pub errors: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coverage: Option<Coverage>,
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
        let executed_tests = self
            .tests
            .passed
            .checked_add(self.tests.failed)
            .and_then(|count| count.checked_add(self.tests.skipped))
            .ok_or("adapter test counts overflow")?;
        if self.coverage.is_some() && request.collect_coverage.is_none() {
            return Err("unsolicited adapter coverage".into());
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
            TestOutcome::Failed if self.tests.failed == 0 => {
                Err("failed response requires a failed test; execution errors are not kills".into())
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
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn infrastructure_is_never_a_kill_and_empty_pass_is_rejected() {
        let request = Request {
            protocol: 1,
            run_id: "r".into(),
            job_id: "j".into(),
            generation: 1,
            kind: "mutant".into(),
            root: "/".into(),
            config: "/c".into(),
            deadline_ms: 10,
            mutation: None,
            collect_coverage: None,
            selection: None,
        };
        let mut response = Response {
            protocol: 1,
            run_id: "r".into(),
            job_id: "j".into(),
            generation: 1,
            outcome: TestOutcome::InfrastructureError,
            tests: Tests::default(),
            errors: vec!["crash".into()],
            coverage: None,
        };
        assert_eq!(response.mutant_outcome(), Outcome::InfrastructureError);
        assert!(response.validate(&request).is_ok());
        response.outcome = TestOutcome::Passed;
        assert!(response.validate(&request).is_err());
        response.outcome = TestOutcome::Failed;
        assert!(response.validate(&request).is_err());
        response.tests.failed = 1;
        assert!(response.validate(&request).is_ok());
        response.generation = 2;
        assert!(response.validate(&request).is_err());
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
                },
                Hit {
                    mutant_id: "uncovered".into(),
                    tests: vec![],
                    outside_test: false,
                },
            ],
            reasons: vec![],
        }
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
}
