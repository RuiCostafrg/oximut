//! Conservative whole-project cache. Output paths are reserved, never inputs.
use super::{CachePolicy, Execution, Options};
use oximut_core::{
    CacheReport, FingerprintProfile, Mutant, MutantResult, Mutation, Outcome, Tests,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    env, fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Command,
    time::Instant,
};

const SCHEMA: u32 = 1;
const OPERATORS: &str = "boolean-literal,comparison-boundary,arithmetic;oxc=0.152.0";
type Probe = Option<Result<Mutation, String>>;
type Instrumented = (oximut_instrument::Generated, Probe);

pub fn digest(bytes: &[u8]) -> String {
    hex_digest(&Sha256::digest(bytes))
}
fn hex_digest(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    bytes
        .iter()
        .flat_map(|byte| {
            [
                HEX[(byte >> 4) as usize] as char,
                HEX[(byte & 15) as usize] as char,
            ]
        })
        .collect()
}
fn part(hash: &mut Sha256, bytes: &[u8]) {
    hash.update((bytes.len() as u64).to_le_bytes());
    hash.update(bytes);
}
fn file_hash(path: &Path) -> Result<String, String> {
    file_hash_counted(path, &mut FingerprintProfile::default())
}
fn file_hash_counted(path: &Path, profile: &mut FingerprintProfile) -> Result<String, String> {
    let mut file =
        fs::File::open(path).map_err(|e| format!("cache input {}: {e}", path.display()))?;
    let mut hash = Sha256::new();
    let mut buffer = [0; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        profile.bytes_hashed += count as u64;
        hash.update(&buffer[..count]);
    }
    profile.files_hashed += 1;
    Ok(hex_digest(&hash.finalize()))
}

pub fn atomic<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    let parent = path.parent().ok_or("cache path has no parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    serde_json::to_writer(&mut temporary, value).map_err(|e| e.to_string())?;
    temporary.flush().map_err(|e| e.to_string())?;
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary.persist(path).map_err(|e| e.to_string())?;
    fs::File::open(parent)
        .and_then(|file| file.sync_all())
        .map_err(|e| e.to_string())
}
fn read<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let file = fs::File::open(path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            "entry-not-found".into()
        } else {
            format!("entry-unreadable: {e}")
        }
    })?;
    if file.metadata().map_err(|e| e.to_string())?.len() > 128 * 1024 * 1024 {
        return Err("entry-too-large".into());
    }
    serde_json::from_reader(file.take(128 * 1024 * 1024 + 1))
        .map_err(|_| "entry-corrupt-or-incompatible".into())
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Artifact {
    schema_version: u32,
    complete: bool,
    fingerprint: String,
    checksum: String,
    generated: oximut_instrument::Generated,
    probe: Option<Result<Mutation, String>>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Outcomes {
    schema_version: u32,
    complete: bool,
    pub fingerprint: String,
    pub run_id: String,
    pub baseline_tests: Tests,
    pub results: Vec<MutantResult>,
    checksum: String,
}
impl Outcomes {
    pub fn new(
        fingerprint: String,
        run_id: String,
        baseline_tests: Tests,
        results: Vec<MutantResult>,
    ) -> Self {
        let checksum = digest(
            &serde_json::to_vec(&(&fingerprint, &run_id, &baseline_tests, &results))
                .expect("serializable outcomes"),
        );
        Self {
            schema_version: SCHEMA,
            complete: true,
            fingerprint,
            run_id,
            baseline_tests,
            results,
            checksum,
        }
    }
    pub fn validate(&self, mutants: &[Mutant], tests: &Tests) -> bool {
        let indexed: std::collections::BTreeMap<_, _> = self
            .results
            .iter()
            .map(|result| (result.id.as_str(), result))
            .collect();
        self.checksum
            == digest(
                &serde_json::to_vec(&(
                    &self.fingerprint,
                    &self.run_id,
                    &self.baseline_tests,
                    &self.results,
                ))
                .expect("serializable outcomes"),
            )
            && self.schema_version == SCHEMA
            && self.complete
            && self.results.len() == mutants.len()
            && indexed.len() == mutants.len()
            && same(&self.baseline_tests, tests)
            && mutants.iter().all(|mutant| {
                indexed.get(mutant.id.as_str()).is_some_and(|r| {
                    r.file == mutant.file
                        && r.operator == mutant.operator
                        && r.original == mutant.original
                        && r.replacement == mutant.replacement
                        && same(&r.location, &mutant.location)
                        && match r.outcome {
                            Outcome::Killed => r.tests.failed > 0,
                            Outcome::Survived => {
                                r.tests.passed > 0 && r.tests.failed == 0 && r.errors.is_empty()
                            }
                            Outcome::NoCoverage => {
                                r.tests.passed == 0
                                    && r.tests.failed == 0
                                    && r.tests.skipped == 0
                                    && r.errors.is_empty()
                                    && r.selection.as_ref().is_some_and(|s| s.mode == "none")
                            }
                            _ => false,
                        }
                })
            })
    }
}
pub fn same<T: Serialize>(a: &T, b: &T) -> bool {
    serde_json::to_vec(a).ok() == serde_json::to_vec(b).ok()
}

#[derive(Debug, PartialEq, Eq)]
pub enum InputCheck {
    Unchanged,
    Changed,
    Unreadable(String),
}

// Advisory history only selects fresh execution. It never authorizes reuse.
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PolicyHistory {
    schema_version: u32,
    key: String,
    fingerprint_cost_ms: u64,
    fresh_work_ms: u64,
    successful_bypasses: u8,
    bypass_outcomes: bool,
}

struct PolicyState {
    path: PathBuf,
    key: String,
    previous: Option<PolicyHistory>,
    bypass: bool,
}

pub struct Cache {
    pub report: CacheReport,
    directory: PathBuf,
    tool: String,
    source_keys: Vec<String>,
    policy: Option<PolicyState>,
}
impl Cache {
    pub fn new(options: &Options) -> Self {
        let enabled = options.cache && !options.no_cache && !options.list;
        let runner_started = Instant::now();
        let tool = if enabled {
            env::current_exe()
                .map_err(|e| e.to_string())
                .and_then(|p| file_hash(&p))
        } else {
            Ok(String::new())
        };
        let runner_hash_ms = if enabled {
            elapsed_ms(runner_started)
        } else {
            0
        };
        let reason = if options.no_cache {
            "forced-no-cache"
        } else if !options.cache {
            "cache-not-requested"
        } else if options.list {
            "list-mode"
        } else if !options.cache_inputs_complete {
            "external-inputs-not-asserted-complete"
        } else {
            "cache-enabled"
        };
        let mut cache = Self {
            directory: options.root.join(".oximut/cache"),
            tool: tool.clone().unwrap_or_default(),
            source_keys: Vec::new(),
            policy: None,
            report: CacheReport {
                enabled,
                runner_hash_ms,
                outcomes_enabled: enabled && options.cache_inputs_complete,
                reason: reason.into(),
                persistence: "not-written".into(),
                ..Default::default()
            },
        };
        if enabled && tool.is_err() {
            cache.report.enabled = false;
            cache.report.outcomes_enabled = false;
            cache.report.reason = "runner-executable-unreadable".into();
        }
        cache
    }
    pub fn artifact(
        &mut self,
        options: &Options,
        identity: &str,
        path: &Path,
        source: &str,
    ) -> Result<Instrumented, String> {
        if !self.report.enabled {
            let generated = match options.execution {
                Execution::Fresh => oximut_instrument::generate(identity, path, source)?,
                Execution::Persistent => {
                    oximut_instrument::generate_switched(identity, path, source)?
                }
            };
            let probe = options
                .test_selection
                .then(|| oximut_instrument::generate_probes(identity, path, source));
            return Ok((generated, probe));
        }
        let key = digest(
            &serde_json::to_vec(&(
                SCHEMA,
                OPERATORS,
                &self.tool,
                identity,
                path,
                source,
                options.execution == Execution::Persistent,
                options.test_selection,
            ))
            .map_err(|e| e.to_string())?,
        );
        self.source_keys.push(key.clone());
        let location = self
            .directory
            .join("instrumentation")
            .join(format!("{key}.json"));
        if self.report.enabled {
            if let Ok(entry) = read::<Artifact>(&location)
                && entry.schema_version == SCHEMA
                && entry.complete
                && entry.fingerprint == key
                && entry.checksum
                    == digest(
                        &serde_json::to_vec(&(&entry.generated, &entry.probe))
                            .map_err(|e| e.to_string())?,
                    )
            {
                self.report.instrumentation_hits += 1;
                return Ok((entry.generated, entry.probe));
            }
            self.report.instrumentation_misses += 1;
        }
        let generated = match options.execution {
            Execution::Fresh => oximut_instrument::generate(identity, path, source)?,
            Execution::Persistent => oximut_instrument::generate_switched(identity, path, source)?,
        };
        let probe = options
            .test_selection
            .then(|| oximut_instrument::generate_probes(identity, path, source));
        let checksum =
            digest(&serde_json::to_vec(&(&generated, &probe)).map_err(|e| e.to_string())?);
        let entry = Artifact {
            schema_version: SCHEMA,
            complete: true,
            fingerprint: key,
            checksum,
            generated,
            probe,
        };
        if self.report.enabled
            && let Err(error) = atomic(&location, &entry)
        {
            self.report.persistence = format!("instrumentation-write-failed: {error}");
        }
        Ok((entry.generated, entry.probe))
    }
    pub fn prepare(&mut self, options: &Options) -> Option<Outcomes> {
        self.prepare_attempt(options, true)
    }
    pub fn prepare_attempt(&mut self, options: &Options, reuse: bool) -> Option<Outcomes> {
        if !self.report.outcomes_enabled {
            return None;
        }
        let adaptive = reuse && !options.baseline_only && options.cache_policy == CachePolicy::Auto;
        if adaptive && self.prepare_policy(options) {
            self.report.outcomes_enabled = false;
            self.report.reason = "outcome-cache-cost-exceeds-fresh-work".into();
            return None;
        }
        // New/corrupt history and periodic probes measure actual fresh jobs,
        // even when an otherwise valid outcome entry already exists.
        let reuse = reuse
            && (!adaptive
                || self.policy.as_ref().is_some_and(|policy| {
                    policy
                        .previous
                        .as_ref()
                        .is_some_and(|history| !history.bypass_outcomes)
                }));
        match self.fingerprint(options, "prepare") {
            Ok(key) => {
                self.report.fingerprint = Some(key.clone());
                if !reuse {
                    self.report.reason = if adaptive {
                        "outcome-cache-cost-probe"
                    } else {
                        "fresh-retry-after-input-change"
                    }
                    .into();
                    return None;
                }
                match read::<Outcomes>(&self.directory.join("outcomes").join(format!("{key}.json")))
                {
                    Ok(entry)
                        if entry.schema_version == SCHEMA
                            && entry.complete
                            && entry.fingerprint == key =>
                    {
                        self.report.reason = "candidate-awaiting-baseline-validation".into();
                        Some(entry)
                    }
                    Ok(_) => {
                        self.report.reason = "entry-schema-or-fingerprint-mismatch".into();
                        None
                    }
                    Err(reason) => {
                        self.report.reason = reason;
                        None
                    }
                }
            }
            Err(reason) => {
                self.report.outcomes_enabled = false;
                self.report.reason = format!("unsafe-or-incomplete-fingerprint: {reason}");
                None
            }
        }
    }
    fn prepare_policy(&mut self, options: &Options) -> bool {
        let mut environment_hash = Sha256::new();
        hash_environment(
            &mut environment_hash,
            &env::vars_os().collect(),
            &options.cache_env,
        );
        let Ok(bytes) = serde_json::to_vec(&(
            1_u32,
            &self.tool,
            options,
            &self.source_keys,
            hex_digest(&environment_hash.finalize()),
        )) else {
            return false;
        };
        let key = digest(&bytes);
        let path = self.directory.join("policy").join(format!("{key}.json"));
        let previous = fs::File::open(&path)
            .ok()
            .and_then(|file| {
                let mut bytes = Vec::new();
                file.take(8193).read_to_end(&mut bytes).ok()?;
                (bytes.len() <= 8192)
                    .then(|| serde_json::from_slice::<PolicyHistory>(&bytes).ok())
                    .flatten()
            })
            .filter(|history| {
                history.schema_version == 1
                    && history.key == key
                    && history.successful_bypasses <= 8
            });
        let bypass = previous
            .as_ref()
            .is_some_and(|history| history.bypass_outcomes && history.successful_bypasses < 8);
        self.policy = Some(PolicyState {
            path,
            key,
            previous,
            bypass,
        });
        bypass
    }

    pub fn record_policy(&self, complete: bool, unchanged: bool, fresh_work_ms: u64) {
        let Some(policy) = &self.policy else {
            return;
        };
        // Reuse cannot measure the mutation work it avoided. Failed runs and
        // fingerprint failures cannot supply calibration or bypass counts.
        if !complete || !unchanged || self.report.outcome_hits != 0 {
            return;
        }
        let history = if policy.bypass {
            let Some(previous) = &policy.previous else {
                return;
            };
            PolicyHistory {
                schema_version: 1,
                key: policy.key.clone(),
                fingerprint_cost_ms: previous.fingerprint_cost_ms,
                fresh_work_ms,
                successful_bypasses: previous.successful_bypasses.saturating_add(1),
                // Re-enable only once fresh work costs at least the full scan
                // estimate, giving the 125% disable threshold a hysteresis band.
                bypass_outcomes: previous.fingerprint_cost_ms > fresh_work_ms,
            }
        } else {
            if !self.report.outcomes_enabled
                || self.report.fingerprint.is_none()
                || self.report.fingerprint_profiles.is_empty()
                || self
                    .report
                    .fingerprint_profiles
                    .iter()
                    .any(|profile| !profile.completed)
            {
                return;
            }
            let fingerprint_cost_ms = self
                .report
                .fingerprint_profiles
                .iter()
                .fold(0_u64, |sum, profile| sum.saturating_add(profile.elapsed_ms));
            let was_bypassed = policy
                .previous
                .as_ref()
                .is_some_and(|history| history.bypass_outcomes);
            let bypass_outcomes = if was_bypassed {
                fingerprint_cost_ms > fresh_work_ms
            } else {
                u128::from(fingerprint_cost_ms) * 4 >= u128::from(fresh_work_ms) * 5
            };
            PolicyHistory {
                schema_version: 1,
                key: policy.key.clone(),
                fingerprint_cost_ms,
                fresh_work_ms,
                successful_bypasses: 0,
                bypass_outcomes,
            }
        };
        // This separate transaction follows successful report emission. A failed
        // advisory write merely causes a later probe; it cannot affect reuse.
        let _ = atomic(&policy.path, &history);
    }

    pub fn inputs_unchanged(&mut self, options: &Options, phase: &str) -> bool {
        self.check_inputs(options, phase) == InputCheck::Unchanged
    }
    pub fn check_inputs(&mut self, options: &Options, phase: &str) -> InputCheck {
        let Some(key) = self.report.fingerprint.clone() else {
            return InputCheck::Unchanged;
        };
        let check = match self.fingerprint(options, phase) {
            Ok(current) if current == key => InputCheck::Unchanged,
            Ok(_) => InputCheck::Changed,
            Err(error) => {
                self.report.outcomes_enabled = false;
                self.report.reason = format!("unsafe-or-incomplete-fingerprint: {error}");
                InputCheck::Unreadable(error)
            }
        };
        if check != InputCheck::Unchanged {
            self.report.persistence = "inputs-changed-during-run-not-persisted".into();
        }
        check
    }
    fn fingerprint(&mut self, options: &Options, phase: &str) -> Result<String, String> {
        let started = Instant::now();
        let mut profile = FingerprintProfile {
            phase: phase.into(),
            ..Default::default()
        };
        let result = fingerprint(options, &self.tool, &mut profile);
        profile.elapsed_ms = elapsed_ms(started);
        profile.completed = result.is_ok();
        self.report.fingerprint_profiles.push(profile);
        result
    }
    pub fn stage(
        &mut self,
        options: &Options,
        complete: bool,
        unchanged: bool,
        run_id: &str,
        tests: &Tests,
        results: &[MutantResult],
    ) -> Option<Publication> {
        if !self.report.outcomes_enabled {
            return None;
        }
        if !unchanged {
            return None;
        }
        if !complete || options.baseline_only {
            self.report.persistence = "incomplete-or-baseline-only-run-not-persisted".into();
            return None;
        }
        let key = self.report.fingerprint.as_ref()?;
        let entry = Outcomes::new(key.clone(), run_id.into(), tests.clone(), results.to_vec());
        let path = self.directory.join("outcomes").join(format!("{key}.json"));
        let result = (|| -> Result<Publication, String> {
            let parent = path.parent().ok_or("cache directory missing")?;
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            let mut temporary =
                tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
            serde_json::to_writer(&mut temporary, &entry).map_err(|e| e.to_string())?;
            temporary.flush().map_err(|e| e.to_string())?;
            temporary.as_file().sync_all().map_err(|e| e.to_string())?;
            Ok(Publication { temporary, path })
        })();
        match result {
            Ok(publication) => {
                self.report.persistence = "complete-run-staged-for-commit-after-report".into();
                Some(publication)
            }
            Err(error) => {
                self.report.persistence = format!("outcome-stage-failed: {error}");
                None
            }
        }
    }
}

/// The transaction becomes reusable only after the final report was emitted.
/// Dropping before commit (including report failures) removes the temporary file.
pub struct Publication {
    temporary: tempfile::NamedTempFile,
    path: PathBuf,
}
impl Publication {
    pub fn commit(self) -> Result<(), String> {
        self.temporary
            .persist(&self.path)
            .map_err(|e| e.to_string())?;
        fs::File::open(self.path.parent().ok_or("cache parent missing")?)
            .and_then(|file| file.sync_all())
            .map_err(|e| e.to_string())
    }
}

struct Tree<'a> {
    hash: Sha256,
    allowed: Vec<PathBuf>,
    seen: BTreeSet<PathBuf>,
    active: BTreeSet<PathBuf>,
    cache_directory: PathBuf,
    report: Option<PathBuf>,
    profile: &'a mut FingerprintProfile,
}
impl Tree<'_> {
    fn excluded(&self, path: &Path) -> bool {
        if path.starts_with(&self.cache_directory) || self.report.as_deref() == Some(path) {
            return true;
        }
        let vite_output = path
            .file_name()
            .is_some_and(|name| name == ".vite" || name == ".vite-temp");
        path.components().any(|component| {
            let name = component.as_os_str();
            name == ".git" || (vite_output && name == "node_modules")
        })
    }

    fn visit(&mut self, path: &Path) -> Result<(), String> {
        if self.excluded(path) {
            return Ok(());
        }
        part(&mut self.hash, path.as_os_str().as_encoded_bytes());
        let metadata = match fs::symlink_metadata(path) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                part(&mut self.hash, b"absent");
                return Ok(());
            }
            Err(e) => return Err(format!("{}: {e}", path.display())),
        };
        if metadata.file_type().is_symlink() {
            let link = fs::read_link(path).map_err(|e| e.to_string())?;
            part(&mut self.hash, b"symlink");
            part(&mut self.hash, link.as_os_str().as_encoded_bytes());
            let target = path
                .canonicalize()
                .map_err(|_| format!("broken-symlink: {}", path.display()))?;
            if !self.allowed.iter().any(|root| target.starts_with(root)) || self.excluded(&target) {
                return Err(format!("undeclared-external-symlink: {}", path.display()));
            }
            return self.visit(&target);
        }
        if metadata.is_dir() {
            if self.active.contains(path) {
                return Err(format!("symlink-cycle: {}", path.display()));
            }
            if !self.seen.insert(path.into()) {
                return Ok(());
            }
            part(&mut self.hash, b"directory");
            self.active.insert(path.into());
            let mut children = fs::read_dir(path)
                .map_err(|e| e.to_string())?
                .map(|entry| entry.map(|e| e.path()))
                .collect::<Result<Vec<_>, _>>()
                .map_err(|e| e.to_string())?;
            children.sort();
            for child in children {
                self.visit(&child)?;
            }
            self.active.remove(path);
        } else if metadata.is_file() {
            part(&mut self.hash, b"file");
            part(
                &mut self.hash,
                file_hash_counted(path, self.profile)?.as_bytes(),
            );
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                part(&mut self.hash, &metadata.permissions().mode().to_le_bytes());
            }
        } else {
            return Err(format!("unsupported-input-file: {}", path.display()));
        }
        Ok(())
    }
}
fn absolute(path: &Path, root: &Path) -> PathBuf {
    if path.is_absolute() {
        path.into()
    } else {
        root.join(path)
    }
}
fn runtime_identity(options: &Options) -> Result<Vec<u8>, String> {
    use std::process::Stdio;
    use std::time::{Duration, Instant};
    let mut output = tempfile::tempfile().map_err(|e| e.to_string())?;
    let launcher = env::split_paths(&env::var_os("PATH").unwrap_or_default())
        .map(|directory| absolute(&directory.join("node"), &options.root))
        .find(|path| {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
            }
            #[cfg(not(unix))]
            {
                path.is_file()
            }
        })
        .ok_or("node runtime executable not found on PATH")?;
    let mut command = Command::new(&launcher);
    command
        .args([
            "--eval",
            "console.log(JSON.stringify({executable:process.execPath,versions:process.versions}))",
        ])
        .env_remove("NODE_OPTIONS")
        .current_dir(&options.root)
        .stdin(Stdio::null())
        .stdout(output.try_clone().map_err(|e| e.to_string())?)
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let existing = super::descendants(std::process::id());
    let mut child = command.spawn().map_err(|e| format!("node runtime: {e}"))?;
    let deadline = Instant::now() + Duration::from_millis(options.timeout_ms.min(5_000));
    let mut owned = std::collections::BTreeMap::new();
    let result = loop {
        owned.extend(super::descendants(child.id()));
        owned.extend(
            super::descendants(std::process::id())
                .into_iter()
                .filter(|(pid, _)| *pid != child.id() && !existing.contains_key(pid)),
        );
        match child.try_wait() {
            Ok(Some(status)) => {
                break if status.success() {
                    Ok(())
                } else {
                    Err("node runtime identity failed".into())
                };
            }
            Err(error) => break Err(format!("node runtime wait failed: {error}")),
            _ => {}
        }
        match output.metadata() {
            Ok(metadata) if metadata.len() > 16_384 => {
                break Err("node runtime identity output too large".into());
            }
            Err(error) => break Err(format!("node runtime output: {error}")),
            _ => {}
        }
        if Instant::now() >= deadline {
            break Err("node runtime identity deadline exceeded".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    owned.extend(
        super::descendants(std::process::id())
            .into_iter()
            .filter(|(pid, _)| *pid != child.id() && !existing.contains_key(pid)),
    );
    let left_descendants = result.is_ok()
        && owned
            .iter()
            .any(|(&pid, &start)| super::still_owned(pid, start));
    super::terminate(&mut child, &owned);
    result?;
    if left_descendants {
        return Err("node runtime identity left descendant processes".into());
    }
    use std::io::{Seek, SeekFrom};
    output.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut bytes = vec![];
    output
        .take(16_385)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 16_384 {
        return Err("node runtime identity output too large".into());
    }
    let mut identity: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|_| "node runtime identity malformed")?;
    if !identity.is_object() {
        return Err("node runtime identity malformed".into());
    }
    identity["launcher"] = serde_json::to_value(launcher).map_err(|e| e.to_string())?;
    serde_json::to_vec(&identity).map_err(|e| e.to_string())
}

fn elapsed_ms(started: Instant) -> u64 {
    started.elapsed().as_millis().try_into().unwrap_or(u64::MAX)
}
fn fingerprint(
    options: &Options,
    tool: &str,
    profile: &mut FingerprintProfile,
) -> Result<String, String> {
    let started = Instant::now();
    let node = runtime_identity(options);
    profile.runtime_identity_ms = elapsed_ms(started);
    let node = node?;
    let started = Instant::now();
    let resources = super::resources::cache_policy(options);
    profile.resource_policy_ms = elapsed_ms(started);
    let resources = resources?;
    let started = Instant::now();
    let result = fingerprint_inputs(options, tool, &node, &resources, profile);
    profile.inputs_ms = elapsed_ms(started);
    result
}
#[cfg(test)]
fn fingerprint_with_runtime(options: &Options, tool: &str, node: &[u8]) -> Result<String, String> {
    fingerprint_with_policy(
        options,
        tool,
        node,
        &super::resources::cache_policy(options)?,
    )
}
#[cfg(test)]
fn fingerprint_with_policy(
    options: &Options,
    tool: &str,
    node: &[u8],
    resources: &serde_json::Value,
) -> Result<String, String> {
    fingerprint_inputs(
        options,
        tool,
        node,
        resources,
        &mut FingerprintProfile::default(),
    )
}
fn fingerprint_inputs(
    options: &Options,
    tool: &str,
    node: &[u8],
    resources: &serde_json::Value,
    profile: &mut FingerprintProfile,
) -> Result<String, String> {
    let mut paths = vec![
        options.root.clone(),
        options.config.clone(),
        options
            .adapter
            .parent()
            .ok_or("adapter has no parent")?
            .to_path_buf(),
    ];
    // Node resolves packages through every ancestor. Include manifests and actual
    // installed bytes rather than trusting a lockfile matches the installation.
    for ancestor in options
        .root
        .ancestors()
        .chain(options.adapter.ancestors().skip(1))
    {
        for name in [
            "package.json",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "bun.lock",
            "bun.lockb",
            "pnpm-workspace.yaml",
            ".npmrc",
            ".yarnrc.yml",
            "node_modules",
        ] {
            paths.push(ancestor.join(name));
        }
    }
    for input in &options.cache_inputs {
        paths.push(
            absolute(input, &options.root)
                .canonicalize()
                .map_err(|e| format!("declared-file-input: {e}"))?,
        );
    }
    let runtime: serde_json::Value =
        serde_json::from_slice(node).map_err(|_| "node runtime identity malformed")?;
    paths.push(PathBuf::from(
        runtime["executable"]
            .as_str()
            .ok_or("node executable missing")?,
    ));
    if let Some(launcher) = runtime.get("launcher").and_then(|value| value.as_str()) {
        paths.push(PathBuf::from(launcher));
    }
    paths.sort();
    paths.dedup();
    let allowed = paths
        .iter()
        .filter_map(|path| path.canonicalize().ok())
        .collect();
    let report = options
        .report
        .as_ref()
        .filter(|p| p.as_os_str() != "-")
        .map(|p| {
            absolute(
                p,
                &env::current_dir().unwrap_or_else(|_| options.root.clone()),
            )
        })
        .map(|p| p.canonicalize().unwrap_or(p));
    let mut tree = Tree {
        hash: Sha256::new(),
        allowed,
        seen: BTreeSet::new(),
        active: BTreeSet::new(),
        cache_directory: options.root.join(".oximut/cache"),
        report,
        profile,
    };
    part(
        &mut tree.hash,
        &serde_json::to_vec(&tree.report).map_err(|e| e.to_string())?,
    );
    part(
        &mut tree.hash,
        b"oximut-execution-cache-schema-1-project-wide",
    );
    part(&mut tree.hash, tool.as_bytes());
    part(&mut tree.hash, node);
    part(
        &mut tree.hash,
        &serde_json::to_vec(resources).map_err(|e| e.to_string())?,
    );
    part(&mut tree.hash, OPERATORS.as_bytes());
    part(
        &mut tree.hash,
        &serde_json::to_vec(&(
            options.execution == Execution::Persistent,
            options.test_selection,
            options.timeout_ms,
            options.workers,
            options.memory_budget_mib,
            options.memory_reserve_mib,
            super::resources::cpu_limit(),
            options.order_seed,
            &options.sources,
            &options.config,
            &options.adapter,
        ))
        .map_err(|e| e.to_string())?,
    );
    // Hash all inherited environment, including absent explicitly declared names.
    // Raw values are never written into cache files or reports.
    let environment: std::collections::BTreeMap<_, _> = env::vars_os().collect();
    hash_environment(&mut tree.hash, &environment, &options.cache_env);
    for input in &options.cache_inputs {
        let input = absolute(input, &options.root)
            .canonicalize()
            .map_err(|e| e.to_string())?;
        if tree.excluded(&input) {
            return Err("declared-input-overlaps-reserved-output".into());
        }
    }
    for path in paths {
        tree.visit(&path)?;
    }
    Ok(hex_digest(&tree.hash.finalize()))
}

fn hash_environment(
    hash: &mut Sha256,
    environment: &std::collections::BTreeMap<std::ffi::OsString, std::ffi::OsString>,
    declared: &[String],
) {
    part(hash, b"inherited-environment");
    part(hash, &(environment.len() as u64).to_le_bytes());
    for (name, value) in environment {
        part(hash, name.as_encoded_bytes());
        part(hash, value.as_encoded_bytes());
    }
    part(hash, b"declared-environment");
    part(hash, &(declared.len() as u64).to_le_bytes());
    for name in declared {
        part(hash, name.as_bytes());
        match environment.get(std::ffi::OsStr::new(name)) {
            Some(value) => {
                part(hash, b"present");
                part(hash, value.as_encoded_bytes());
            }
            None => part(hash, b"absent"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use oximut_core::Provenance;

    fn options(root: &Path) -> Options {
        Options {
            root: root.into(),
            config: root.join("vitest.config.ts"),
            sources: vec![root.join("source.ts")],
            report: None,
            timeout_ms: 1_000,
            adapter: root.join("adapter.mjs"),
            list: false,
            baseline_only: false,
            execution: Execution::Fresh,
            order_seed: None,
            test_selection: false,
            cache: true,
            cache_inputs_complete: true,
            cache_policy: CachePolicy::Always,
            no_cache: false,
            cache_inputs: vec![],
            cache_env: vec![],
            workers: 1,
            memory_budget_mib: None,
            memory_reserve_mib: 64,
        }
    }
    fn cache(root: &Path) -> Cache {
        Cache {
            directory: root.join(".oximut/cache"),
            tool: "runner-and-operators-version-a".into(),
            source_keys: Vec::new(),
            policy: None,
            report: CacheReport {
                enabled: true,
                outcomes_enabled: true,
                fingerprint: Some("fingerprint".into()),
                ..Default::default()
            },
        }
    }
    fn project() -> (tempfile::TempDir, Options, Vec<u8>) {
        let root = tempfile::tempdir().unwrap();
        for (name, contents) in [
            ("source.ts", "export const value = true;"),
            ("vitest.config.ts", "export default {};"),
            ("adapter.mjs", "// adapter"),
            ("node", "node-binary-a"),
        ] {
            fs::write(root.path().join(name), contents).unwrap();
        }
        let options = options(root.path());
        let runtime = serde_json::to_vec(&serde_json::json!({"executable":root.path().join("node"),"versions":{"node":"26.3.1","v8":"a"}})).unwrap();
        (root, options, runtime)
    }
    #[test]
    fn exclusions_match_legacy_lexical_path_policy() {
        let options = options(Path::new("/project"));
        let mut profile = FingerprintProfile::default();
        let tree = Tree {
            hash: Sha256::new(),
            allowed: vec![],
            seen: BTreeSet::new(),
            active: BTreeSet::new(),
            cache_directory: options.root.join(".oximut/cache"),
            report: Some(options.root.join("report.json")),
            profile: &mut profile,
        };
        let legacy = |path: &Path| {
            path.starts_with(options.root.join(".oximut/cache"))
                || tree.report.as_deref() == Some(path)
                || path.components().any(|c| c.as_os_str() == ".git")
                || (path.components().any(|c| c.as_os_str() == "node_modules")
                    && path
                        .file_name()
                        .is_some_and(|n| n == ".vite" || n == ".vite-temp"))
        };
        for prefix in ["/project", "/elsewhere", "relative", "/project/../other"] {
            for middle in [
                "",
                ".git",
                "node_modules",
                "node_modules/pkg",
                "node_modules/.git",
            ] {
                for leaf in [
                    "source.ts",
                    ".git",
                    ".vite",
                    ".vite-temp",
                    ".vite-other",
                    "node_modules",
                    ".oximut/cache",
                    ".oximut/cache-extra",
                    "report.json",
                ] {
                    let path = Path::new(prefix).join(middle).join(leaf);
                    assert_eq!(tree.excluded(&path), legacy(&path), "{}", path.display());
                }
            }
        }
        for path in [
            "/project/.oximut/cache",
            "/project/.oximut/cache/input",
            "/project/.oximut/cache/../input",
            "/project/report.json/child",
            "/project/node_modules/.vite/child",
            "/project/./node_modules/pkg/.vite/",
        ] {
            let path = Path::new(path);
            assert_eq!(tree.excluded(path), legacy(path), "{}", path.display());
        }
    }
    #[test]
    fn file_sha256_matches_known_answers_across_read_boundaries() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("input");
        // FIPS 180-4 SHA-256 examples include a million ASCII 'a' bytes.
        // This crosses both compression blocks and the file reader's chunks.
        for (bytes, expected) in [
            (
                Vec::new(),
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ),
            (
                b"abc".to_vec(),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            ),
            (
                vec![b'a'; 1_000_000],
                "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0",
            ),
        ] {
            fs::write(&path, &bytes).unwrap();
            assert_eq!(file_hash(&path).unwrap(), expected);
        }
    }
    #[test]
    fn profiling_counts_actual_reads_without_changing_file_digest() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("input");
        let bytes = vec![b'x'; 64 * 1024 + 17];
        fs::write(&path, &bytes).unwrap();
        let mut profile = FingerprintProfile::default();
        assert_eq!(
            file_hash_counted(&path, &mut profile).unwrap(),
            digest(&bytes)
        );
        assert_eq!(profile.files_hashed, 1);
        assert_eq!(profile.bytes_hashed, bytes.len() as u64);
        file_hash_counted(&path, &mut profile).unwrap();
        assert_eq!(profile.files_hashed, 2);
        assert_eq!(profile.bytes_hashed, 2 * bytes.len() as u64);
        assert!(file_hash_counted(&root.path().join("absent"), &mut profile).is_err());
        assert_eq!(profile.files_hashed, 2);
    }
    #[test]
    fn profiled_fingerprint_preserves_identity_and_failed_attempts() {
        let (root, mut options, runtime) = project();
        let policy = serde_json::json!({});
        let expected = fingerprint_with_policy(&options, "tool", &runtime, &policy).unwrap();
        let mut profile = FingerprintProfile::default();
        assert_eq!(
            fingerprint_inputs(&options, "tool", &runtime, &policy, &mut profile).unwrap(),
            expected
        );
        assert!(profile.files_hashed >= 4);
        assert!(profile.bytes_hashed > 0);
        let mut cache = cache(root.path());
        // A file cannot be the runtime subprocess's working directory.
        options.root = root.path().join("source.ts");
        assert!(cache.fingerprint(&options, "prepare").is_err());
        assert_eq!(cache.report.fingerprint_profiles.len(), 1);
        let failed = &cache.report.fingerprint_profiles[0];
        assert_eq!(failed.phase, "prepare");
        assert!(!failed.completed);
        assert_eq!(failed.files_hashed, 0);
        assert!(failed.elapsed_ms >= failed.runtime_identity_ms);
        cache.report.fingerprint = None;
        assert!(cache.inputs_unchanged(&options, "pre-publication"));
        assert_eq!(cache.report.fingerprint_profiles.len(), 1);
    }
    #[test]
    fn absent_declared_environment_cannot_alias_inherited_literal_absent() {
        let mut missing = Sha256::new();
        hash_environment(&mut missing, &Default::default(), &["Z".into()]);
        let mut present = Sha256::new();
        hash_environment(
            &mut present,
            &std::collections::BTreeMap::from([("Z".into(), "absent".into())]),
            &[],
        );
        assert_ne!(missing.finalize(), present.finalize());
    }
    #[test]
    fn project_fingerprint_tracks_settings_tool_operators_and_runtime() {
        let (root, mut options, runtime) = project();
        let key = fingerprint_with_runtime(&options, "tool-and-operators-a", &runtime).unwrap();
        assert_eq!(
            key,
            fingerprint_with_runtime(&options, "tool-and-operators-a", &runtime).unwrap()
        );
        assert_ne!(
            key,
            fingerprint_with_runtime(&options, "tool-and-operators-b", &runtime).unwrap()
        );
        let mut changed: serde_json::Value = serde_json::from_slice(&runtime).unwrap();
        changed["versions"]["node"] = "next-runtime".into();
        assert_ne!(
            key,
            fingerprint_with_runtime(
                &options,
                "tool-and-operators-a",
                &serde_json::to_vec(&changed).unwrap()
            )
            .unwrap()
        );
        fs::write(root.path().join("node"), "node-binary-b").unwrap();
        assert_ne!(
            key,
            fingerprint_with_runtime(&options, "tool-and-operators-a", &runtime).unwrap()
        );
        fs::write(root.path().join("node"), "node-binary-a").unwrap();
        options.timeout_ms += 1;
        assert_ne!(
            key,
            fingerprint_with_runtime(&options, "tool-and-operators-a", &runtime).unwrap()
        );
        options.timeout_ms -= 1;
        options.execution = Execution::Persistent;
        assert_ne!(
            key,
            fingerprint_with_runtime(&options, "tool-and-operators-a", &runtime).unwrap()
        );
        options.execution = Execution::Fresh;
        options.test_selection = true;
        assert_ne!(
            key,
            fingerprint_with_runtime(&options, "tool-and-operators-a", &runtime).unwrap()
        );
        options.test_selection = false;
        options.order_seed = Some(7);
        assert_ne!(
            key,
            fingerprint_with_runtime(&options, "tool-and-operators-a", &runtime).unwrap()
        );
    }
    #[test]
    fn effective_container_memory_policy_invalidates_outcomes() {
        let (_root, options, runtime) = project();
        let large = serde_json::json!({"effectiveMemoryBudgetBytes": 1024u64 * 1024 * 1024, "ancestorMemoryLimitsBytes": [1024u64 * 1024 * 1024]});
        let small = serde_json::json!({"effectiveMemoryBudgetBytes": 256u64 * 1024 * 1024, "ancestorMemoryLimitsBytes": [256u64 * 1024 * 1024]});
        assert_ne!(
            fingerprint_with_policy(&options, "tool", &runtime, &large).unwrap(),
            fingerprint_with_policy(&options, "tool", &runtime, &small).unwrap()
        );
    }
    #[test]
    fn corrupt_and_future_instrumentation_is_a_cold_miss() {
        let (root, options, _) = project();
        let mut cache = cache(root.path());
        let path = root.path().join("source.ts");
        let source = fs::read_to_string(&path).unwrap();
        assert_eq!(
            cache
                .artifact(&options, "source.ts", &path, &source)
                .unwrap()
                .0
                .mutants
                .len(),
            1
        );
        let entry = fs::read_dir(root.path().join(".oximut/cache/instrumentation"))
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        let original: serde_json::Value =
            serde_json::from_slice(&fs::read(&entry).unwrap()).unwrap();
        let mut corrupt = original.clone();
        corrupt["generated"]["mutants"] = serde_json::json!([]);
        fs::write(&entry, serde_json::to_vec(&corrupt).unwrap()).unwrap();
        assert_eq!(
            cache
                .artifact(&options, "source.ts", &path, &source)
                .unwrap()
                .0
                .mutants
                .len(),
            1
        );
        let mut future = original;
        future["schemaVersion"] = serde_json::json!(SCHEMA + 1);
        fs::write(&entry, serde_json::to_vec(&future).unwrap()).unwrap();
        assert_eq!(
            cache
                .artifact(&options, "source.ts", &path, &source)
                .unwrap()
                .0
                .mutants
                .len(),
            1
        );
        cache
            .artifact(&options, "source.ts", &path, &source)
            .unwrap();
        assert_eq!(cache.report.instrumentation_misses, 3);
        assert_eq!(cache.report.instrumentation_hits, 1);
    }
    #[test]
    fn outcomes_require_integrity_inventory_and_valid_success_evidence() {
        let (root, _, _) = project();
        let generated = oximut_instrument::generate(
            "source.ts",
            &root.path().join("source.ts"),
            "export const value = true;",
        )
        .unwrap();
        let mutant = &generated.mutants[0];
        let result = MutantResult {
            id: mutant.id.clone(),
            file: mutant.file.clone(),
            operator: mutant.operator.clone(),
            original: mutant.original.clone(),
            replacement: mutant.replacement.clone(),
            location: mutant.location.clone(),
            outcome: Outcome::Killed,
            tests: Tests {
                passed: 0,
                failed: 1,
                skipped: 0,
            },
            errors: vec![],
            selection: None,
            provenance: Provenance {
                source: "executed".into(),
                reason: "test".into(),
                fingerprint: None,
                original_run_id: None,
            },
        };
        let tests = Tests {
            passed: 1,
            failed: 0,
            skipped: 0,
        };
        let mut entry = Outcomes::new(
            "key".into(),
            "run".into(),
            tests.clone(),
            vec![result.clone()],
        );
        assert!(entry.validate(&generated.mutants, &tests));
        let mut other_mutant = mutant.clone();
        other_mutant.id.push_str("-other");
        let duplicated = Outcomes::new(
            "key".into(),
            "run".into(),
            tests.clone(),
            vec![result.clone(), result.clone()],
        );
        assert!(!duplicated.validate(&[mutant.clone(), other_mutant], &tests));
        entry.results[0].outcome = Outcome::Survived;
        assert!(!entry.validate(&generated.mutants, &tests));
        let mut invalid = result.clone();
        invalid.tests.failed = 0;
        assert!(
            !Outcomes::new("key".into(), "run".into(), tests.clone(), vec![invalid])
                .validate(&generated.mutants, &tests)
        );
        let mut incomplete = result;
        incomplete.outcome = Outcome::InfrastructureError;
        assert!(
            !Outcomes::new("key".into(), "run".into(), tests.clone(), vec![incomplete])
                .validate(&generated.mutants, &tests)
        );
        assert!(!entry.validate(&[], &tests));
    }
    #[test]
    fn staged_outcomes_are_invisible_until_report_succeeds() {
        let (root, options, _) = project();
        let mut cache = cache(root.path());
        let pending = cache
            .stage(&options, true, true, "run", &Tests::default(), &[])
            .unwrap();
        let path = root.path().join(".oximut/cache/outcomes/fingerprint.json");
        assert!(!path.exists());
        drop(pending); // Report failed or execution was interrupted.
        assert!(!path.exists());
        assert_eq!(fs::read_dir(path.parent().unwrap()).unwrap().count(), 0);
        cache
            .stage(&options, true, true, "run", &Tests::default(), &[])
            .unwrap()
            .commit()
            .unwrap();
        assert!(path.exists());
        assert!(
            cache
                .stage(&options, false, true, "failed", &Tests::default(), &[])
                .is_none()
        );
        assert_eq!(read::<Outcomes>(&path).unwrap().run_id, "run");
    }
    #[cfg(unix)]
    #[test]
    fn undeclared_external_symlinks_disable_fingerprint() {
        use std::os::unix::fs::symlink;
        let (root, mut options, runtime) = project();
        let external = tempfile::tempdir().unwrap();
        fs::write(
            external.path().join("helper.ts"),
            "export const answer = true",
        )
        .unwrap();
        symlink(external.path(), root.path().join("external")).unwrap();
        assert!(
            fingerprint_with_runtime(&options, "tool", &runtime)
                .unwrap_err()
                .contains("undeclared-external-symlink")
        );
        options.cache_inputs.push(external.path().into());
        let initial = fingerprint_with_runtime(&options, "tool", &runtime).unwrap();
        fs::write(
            external.path().join("helper.ts"),
            "export const answer = false",
        )
        .unwrap();
        assert_ne!(
            initial,
            fingerprint_with_runtime(&options, "tool", &runtime).unwrap()
        );
    }
}
