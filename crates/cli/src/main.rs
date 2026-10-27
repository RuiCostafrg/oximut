//! Sequential fresh-reference and persistent-controller runners.
mod cache;
mod resources;
mod scheduler;
use oximut_core::{
    Coverage, Mutant, MutantResult, Mutation, Outcome, PROTOCOL, Provenance, Report, Request,
    Response, Summary, TestOutcome, Tests,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    env, fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, ExitCode, Stdio},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

const HELP: &str = "Usage: oximut [--root DIR] [--config FILE] --source FILE [--source FILE ...]\n  --report FILE       JSON report (- for stdout)\n  --timeout-ms N      Per-job deadline, default 30000 ms\n  --workers N         Maximum simultaneous mutation jobs (default 1; bounded by CPU limits)\n  --memory-budget-mib N Aggregate sampled RSS budget in MiB (1 MiB = 1048576 bytes); default host/container limit\n  --memory-reserve-mib N Reserved admission headroom in MiB (default 64)\n  --execution MODE   fresh (default) or persistent controller with fresh test forks\n  --test-selection    Collect baseline hits and conservatively select hit test files\n  --order-seed N     Deterministically shuffle mutant execution order\n  --adapter FILE      Node adapter entry point override\n  --cache             Cache instrumentation; outcomes require --cache-inputs-complete\n  --cache-policy MODE  auto (default) learns whether outcome caching saves work; always validates/reuses eligible outcomes\n  --cache-inputs-complete Assert all nondeterministic/external inputs are declared\n  --cache-input FILE  Additional input file/directory (repeatable)\n  --cache-env NAME    Additional environment input (repeatable; values stay private)\n  --no-cache, --force Bypass all cache reads and writes\n  --list              Generate mutants as JSON without executing tests\n  --baseline-only     Run original sources without mutants\nExit: 0 complete/no survivors; 1 survivors/no coverage; 2 incomplete/error.\nScore: killed/(killed+survived+no-coverage)*100; empty denominator => null.\nInvalid/timeout/infrastructure-error/resource-exhausted/skipped are excluded and make a run incomplete.";

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
enum Execution {
    Fresh,
    Persistent,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
enum CachePolicy {
    #[default]
    Auto,
    Always,
}

#[derive(serde::Serialize, serde::Deserialize)]
struct Options {
    root: PathBuf,
    config: PathBuf,
    sources: Vec<PathBuf>,
    report: Option<PathBuf>,
    timeout_ms: u64,
    adapter: PathBuf,
    list: bool,
    baseline_only: bool,
    execution: Execution,
    order_seed: Option<u64>,
    test_selection: bool,
    cache: bool,
    cache_inputs_complete: bool,
    #[serde(default)]
    cache_policy: CachePolicy,
    no_cache: bool,
    cache_inputs: Vec<PathBuf>,
    cache_env: Vec<String>,
    workers: usize,
    memory_budget_mib: Option<u64>,
    memory_reserve_mib: u64,
}

fn options() -> Result<Option<Options>, String> {
    let mut args = env::args().skip(1);
    let mut root = env::current_dir().map_err(|e| e.to_string())?;
    let mut config = PathBuf::from("vitest.config.ts");
    let mut sources = vec![];
    let mut report = None;
    let mut timeout_ms = 30_000;
    let mut adapter =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../packages/vitest-adapter/m1-runner.mjs");
    let mut adapter_override = false;
    let mut execution = Execution::Fresh;
    let mut order_seed = None;
    let mut list = false;
    let mut baseline_only = false;
    let mut test_selection = false;
    let mut cache = false;
    let mut cache_inputs_complete = false;
    let mut cache_policy = CachePolicy::Auto;
    let mut no_cache = false;
    let mut cache_inputs = vec![];
    let mut cache_env = vec![];
    let mut workers = 1;
    let mut memory_budget_mib = None;
    let mut memory_reserve_mib = 64;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--help" | "-h" => {
                println!("{HELP}");
                return Ok(None);
            }
            "--list" => list = true,
            "--baseline-only" => baseline_only = true,
            "--test-selection" => test_selection = true,
            "--cache" => cache = true,
            "--cache-inputs-complete" => cache_inputs_complete = true,
            "--no-cache" | "--force" => no_cache = true,
            "--root"
            | "--config"
            | "--source"
            | "--report"
            | "--timeout-ms"
            | "--adapter"
            | "--execution"
            | "--order-seed"
            | "--cache-policy"
            | "--cache-input"
            | "--cache-env"
            | "--workers"
            | "--memory-budget-mib"
            | "--memory-reserve-mib" => {
                let value = args
                    .next()
                    .ok_or_else(|| format!("missing value for {arg}"))?;
                match arg.as_str() {
                    "--cache-policy" => {
                        cache_policy = match value.as_str() {
                            "auto" => CachePolicy::Auto,
                            "always" => CachePolicy::Always,
                            _ => return Err("--cache-policy must be auto or always".into()),
                        };
                    }
                    "--workers" => {
                        workers = value.parse().map_err(|_| "--workers requires an integer")?
                    }
                    "--memory-budget-mib" => {
                        memory_budget_mib = Some(
                            value
                                .parse()
                                .map_err(|_| "--memory-budget-mib requires an integer in MiB")?,
                        )
                    }
                    "--memory-reserve-mib" => {
                        memory_reserve_mib = value
                            .parse()
                            .map_err(|_| "--memory-reserve-mib requires an integer in MiB")?
                    }
                    "--root" => root = value.into(),
                    "--cache-input" => cache_inputs.push(PathBuf::from(value)),
                    "--cache-env" => {
                        if value.is_empty() || value.contains('=') || value.contains('\0') {
                            return Err(
                                "--cache-env requires a valid environment variable name".into()
                            );
                        }
                        cache_env.push(value);
                    }
                    "--config" => config = value.into(),
                    "--source" => sources.push(PathBuf::from(value)),
                    "--report" => report = Some(PathBuf::from(value)),
                    "--adapter" => {
                        adapter = value.into();
                        adapter_override = true;
                    }
                    "--execution" => {
                        execution = match value.as_str() {
                            "fresh" => Execution::Fresh,
                            "persistent" => Execution::Persistent,
                            _ => return Err("--execution must be fresh or persistent".into()),
                        }
                    }
                    "--order-seed" => {
                        order_seed = Some(
                            value
                                .parse()
                                .map_err(|_| "--order-seed must be an unsigned integer")?,
                        )
                    }
                    _ => {
                        timeout_ms = value
                            .parse()
                            .map_err(|_| "--timeout-ms must be a positive integer")?
                    }
                }
            }
            _ => return Err(format!("unknown option {arg}\n{HELP}")),
        }
    }
    if execution == Execution::Persistent && !adapter_override {
        adapter = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../packages/vitest-adapter/m2-runner.mjs");
    }
    if !(1..=1024).contains(&workers) {
        return Err("--workers must be 1..1024".into());
    }
    if memory_budget_mib == Some(0)
        || memory_budget_mib.is_some_and(|n| n > u64::MAX / resources::MIB)
        || memory_reserve_mib > u64::MAX / resources::MIB
    {
        return Err(
            "memory settings require representable integer MiB; budget must be positive".into(),
        );
    }
    if memory_budget_mib.is_some_and(|n| memory_reserve_mib >= n) {
        return Err("--memory-reserve-mib must be smaller than --memory-budget-mib".into());
    }
    if timeout_ms == 0 || timeout_ms > 86_400_000 {
        return Err("--timeout-ms must be 1..86400000".into());
    }
    if list && baseline_only {
        return Err("--list and --baseline-only cannot be combined".into());
    }
    if sources.is_empty() && !baseline_only {
        return Err("at least one --source is required".into());
    }
    root = root.canonicalize().map_err(|e| format!("root: {e}"))?;
    if !root.is_dir() {
        return Err("root must be a directory".into());
    }
    let mut unique = BTreeSet::new();
    for source in sources {
        let source = root
            .join(source)
            .canonicalize()
            .map_err(|e| format!("source: {e}"))?;
        if !source.starts_with(&root) {
            return Err(format!("source {} is outside root", source.display()));
        }
        if !source.is_file() {
            return Err(format!("source {} is not a file", source.display()));
        }
        unique.insert(source);
    }
    if !list {
        config = root
            .join(config)
            .canonicalize()
            .map_err(|e| format!("config: {e}"))?;
        adapter = adapter
            .canonicalize()
            .map_err(|e| format!("adapter: {e}"))?;
    }
    if let Some(path) = &report
        && path != Path::new("-")
        && let Ok(target) = path.canonicalize()
    {
        if unique.contains(&target) || target == config || target == adapter {
            return Err("report path would overwrite a source, config, or adapter file".into());
        }
        if target.starts_with(&root)
            && matches!(
                target.extension().and_then(|ext| ext.to_str()),
                Some("js" | "jsx" | "ts" | "tsx" | "mjs" | "mts" | "cjs" | "cts")
            )
        {
            return Err("report path would overwrite a project source file".into());
        }
    }
    Ok(Some(Options {
        root,
        config,
        sources: unique.into_iter().collect(),
        report,
        timeout_ms,
        adapter,
        list,
        baseline_only,
        execution,
        order_seed,
        test_selection,
        cache,
        cache_inputs_complete,
        cache_policy,
        no_cache,
        cache_inputs,
        cache_env,
        workers,
        memory_budget_mib,
        memory_reserve_mib,
    }))
}

fn infrastructure(request: &Request, error: impl Into<String>) -> Response {
    Response {
        protocol: PROTOCOL,
        run_id: request.run_id.clone(),
        job_id: request.job_id.clone(),
        generation: request.generation,
        outcome: TestOutcome::InfrastructureError,
        tests: Tests::default(),
        errors: vec![error.into()],
        coverage: None,
    }
}

#[cfg(any(target_os = "linux", target_os = "android"))]
fn enable_subreaper() -> Result<(), String> {
    if unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) } != 0 {
        return Err(format!(
            "could not enable descendant cleanup: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(())
}
#[cfg(not(any(target_os = "linux", target_os = "android")))]
fn enable_subreaper() -> Result<(), String> {
    Err("M1 execution requires Linux or Android process-tree cleanup support".into())
}

#[cfg(any(target_os = "linux", target_os = "android"))]
fn process_identity(pid: u32) -> Option<(u32, u64)> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let fields: Vec<_> = stat.rsplit_once(')')?.1.split_whitespace().collect();
    Some((fields.get(1)?.parse().ok()?, fields.get(19)?.parse().ok()?))
}

#[cfg(any(target_os = "linux", target_os = "android"))]
fn still_owned(pid: u32, start: u64) -> bool {
    process_identity(pid).is_some_and(|(_, actual_start)| actual_start == start)
}
#[cfg(not(any(target_os = "linux", target_os = "android")))]
fn still_owned(_: u32, _: u64) -> bool {
    false
}

#[cfg(any(target_os = "linux", target_os = "android"))]
fn descendants(parent: u32) -> BTreeMap<u32, u64> {
    let mut processes = vec![];
    if let Ok(entries) = fs::read_dir("/proc") {
        for entry in entries.flatten() {
            let Ok(pid) = entry.file_name().to_string_lossy().parse::<u32>() else {
                continue;
            };
            if let Some((ppid, start)) = process_identity(pid) {
                processes.push((pid, ppid, start));
            }
        }
    }
    let mut owned = BTreeSet::from([parent]);
    loop {
        let before = owned.len();
        for &(pid, ppid, _) in &processes {
            if owned.contains(&ppid) {
                owned.insert(pid);
            }
        }
        if before == owned.len() {
            break;
        }
    }
    owned.remove(&parent);
    processes
        .into_iter()
        .filter(|(pid, _, _)| owned.contains(pid))
        .map(|(pid, _, start)| (pid, start))
        .collect()
}
#[cfg(not(any(target_os = "linux", target_os = "android")))]
fn descendants(_: u32) -> BTreeMap<u32, u64> {
    BTreeMap::new()
}

fn terminate(child: &mut std::process::Child, owned: &BTreeMap<u32, u64>) {
    #[cfg(unix)]
    unsafe {
        for (&pid, &start) in owned {
            if still_owned(pid, start) {
                libc::kill(pid as i32, libc::SIGKILL);
            }
        }
        // The direct child has not been reaped while running, so its PID cannot
        // be reused. After exit, avoid signaling a potentially reused group ID.
        if child.try_wait().ok().flatten().is_none() {
            libc::kill(-(child.id() as i32), libc::SIGKILL);
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    #[cfg(any(target_os = "linux", target_os = "android"))]
    {
        let deadline = Instant::now() + Duration::from_millis(500);
        loop {
            let mut pending = false;
            for (&pid, &start) in owned {
                if still_owned(pid, start)
                    && unsafe { libc::waitpid(pid as i32, std::ptr::null_mut(), libc::WNOHANG) }
                        == 0
                {
                    pending = true;
                }
            }
            if !pending || Instant::now() >= deadline {
                break;
            }
            thread::sleep(Duration::from_millis(5));
        }
    }
}

fn invoke(options: &Options, request: &Request) -> Response {
    let result = (|| -> Result<Response, String> {
        let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let request_path = dir.path().join("request.json");
        let response_path = dir.path().join("response.json");
        fs::write(
            &request_path,
            serde_json::to_vec(request).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        let mut command = Command::new("node");
        command
            .arg(&options.adapter)
            .arg(&request_path)
            .arg(&response_path)
            .current_dir(&options.root)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .env("OXIMUT_BUDGETED", "1")
            .env("RAYON_NUM_THREADS", "1")
            .env("UV_THREADPOOL_SIZE", "1");
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("could not start Node adapter: {e}"))?;
        let started = Instant::now();
        let limit = Duration::from_millis(options.timeout_ms + 5_000);
        let mut owned = BTreeMap::new();
        let mut last_snapshot = Instant::now() - Duration::from_secs(1);
        let status = loop {
            if last_snapshot.elapsed() >= Duration::from_millis(50) {
                owned.extend(descendants(std::process::id()));
                owned.remove(&child.id());
                last_snapshot = Instant::now();
            }
            match child.try_wait() {
                Ok(Some(status)) => break status,
                Ok(None) => {}
                Err(error) => {
                    terminate(&mut child, &owned);
                    return Err(format!("adapter wait failed: {error}"));
                }
            }
            if started.elapsed() > limit {
                owned.extend(descendants(std::process::id()));
                owned.remove(&child.id());
                terminate(&mut child, &owned);
                return Err("adapter exceeded controller deadline and was terminated".into());
            }
            thread::sleep(Duration::from_millis(10));
        };
        owned.extend(descendants(std::process::id()));
        owned.remove(&child.id());
        if !status.success() {
            terminate(&mut child, &owned);
            return Err(format!("adapter process exited unsuccessfully: {status}"));
        }
        let live: BTreeMap<_, _> = owned
            .into_iter()
            .filter(|&(pid, start)| still_owned(pid, start))
            .collect();
        if !live.is_empty() {
            terminate(&mut child, &live);
            return Err("adapter exited leaving owned descendant processes".into());
        }
        let metadata =
            fs::metadata(&response_path).map_err(|e| format!("missing adapter response: {e}"))?;
        if metadata.len() > 1_048_576 {
            return Err("adapter response exceeds 1 MiB limit".into());
        }
        let response: Response =
            serde_json::from_slice(&fs::read(&response_path).map_err(|e| e.to_string())?)
                .map_err(|e| format!("malformed adapter response: {e}"))?;
        response.validate(request)?;
        Ok(response)
    })();
    result.unwrap_or_else(|error| infrastructure(request, error))
}

// SplitMix64 supplies a stable shuffle independent of library/platform versions.
fn shuffle<T>(items: &mut [T], mut state: u64) {
    for index in (1..items.len()).rev() {
        state = state.wrapping_add(0x9e3779b97f4a7c15);
        let mut value = state;
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d049bb133111eb);
        value ^= value >> 31;
        items.swap(index, (value % (index as u64 + 1)) as usize);
    }
}

fn read_response(path: &Path, request: &Request) -> Result<Response, String> {
    let file = fs::File::open(path).map_err(|e| format!("missing adapter response: {e}"))?;
    let mut bytes = Vec::new();
    file.take(1_048_577)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 1_048_576 {
        return Err("adapter response exceeds 1 MiB limit".into());
    }
    let response: Response =
        serde_json::from_slice(&bytes).map_err(|e| format!("malformed adapter response: {e}"))?;
    response.validate(request)?;
    Ok(response)
}

struct Session {
    directory: tempfile::TempDir,
    child: std::process::Child,
    sequence: u64,
    owned: BTreeMap<u32, u64>,
    finished: bool,
}

impl Session {
    fn start(options: &Options) -> Result<Self, String> {
        let directory = tempfile::tempdir().map_err(|e| e.to_string())?;
        let mut command = Command::new("node");
        command
            .arg(&options.adapter)
            .arg(directory.path())
            .current_dir(&options.root)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .env("OXIMUT_BUDGETED", "1")
            .env("RAYON_NUM_THREADS", "1")
            .env("UV_THREADPOOL_SIZE", "1");
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let child = command
            .spawn()
            .map_err(|e| format!("could not start Node adapter: {e}"))?;
        Ok(Self {
            directory,
            child,
            sequence: 0,
            owned: BTreeMap::new(),
            finished: false,
        })
    }

    fn snapshot(&mut self) {
        self.owned.extend(descendants(std::process::id()));
        self.owned.remove(&self.child.id());
    }

    fn invoke(&mut self, request: &Request) -> Result<Response, String> {
        let bytes = serde_json::to_vec(request).map_err(|e| e.to_string())?;
        if bytes.len() > 8 * 1024 * 1024 {
            return Err("adapter request exceeds 8 MiB limit".into());
        }
        let request_path = self
            .directory
            .path()
            .join(format!("request-{}.json", self.sequence));
        let response_path = self
            .directory
            .path()
            .join(format!("response-{}.json", self.sequence));
        self.sequence += 1;
        let mut temporary =
            tempfile::NamedTempFile::new_in(self.directory.path()).map_err(|e| e.to_string())?;
        temporary.write_all(&bytes).map_err(|e| e.to_string())?;
        temporary
            .persist(&request_path)
            .map_err(|e| e.to_string())?;
        // The adapter bounds its retry within the original job deadline.
        let deadline = Instant::now() + Duration::from_millis(request.deadline_ms + 10_000);
        let mut last_snapshot = Instant::now() - Duration::from_secs(1);
        loop {
            if last_snapshot.elapsed() >= Duration::from_millis(50) {
                self.snapshot();
                last_snapshot = Instant::now();
            }
            if response_path.exists() {
                let response = read_response(&response_path, request)?;
                let _ = fs::remove_file(&response_path);
                let _ = fs::remove_file(&request_path);
                return Ok(response);
            }
            if let Some(status) = self
                .child
                .try_wait()
                .map_err(|e| format!("adapter wait failed: {e}"))?
            {
                return Err(format!(
                    "persistent adapter exited before response: {status}"
                ));
            }
            if Instant::now() >= deadline {
                return Err("persistent adapter exceeded controller deadline".into());
            }
            thread::sleep(Duration::from_millis(10));
        }
    }
}

impl Session {
    fn finish(&mut self) -> Result<(), String> {
        if self.finished {
            return Ok(());
        }
        fs::write(self.directory.path().join("stop"), b"").map_err(|e| e.to_string())?;
        let deadline = Instant::now() + Duration::from_secs(5);
        let result = loop {
            self.snapshot();
            match self.child.try_wait() {
                Ok(Some(status)) if !status.success() => {
                    break Err(format!("persistent adapter shutdown failed: {status}"));
                }
                Ok(Some(_)) => {
                    self.snapshot();
                    if self
                        .owned
                        .iter()
                        .any(|(&pid, &start)| still_owned(pid, start))
                    {
                        break Err("persistent adapter shutdown left owned descendants".into());
                    }
                    break Ok(());
                }
                Err(e) => break Err(format!("persistent adapter shutdown wait failed: {e}")),
                _ => {}
            }
            if Instant::now() >= deadline {
                break Err("persistent adapter shutdown exceeded deadline".into());
            }
            thread::sleep(Duration::from_millis(10));
        };
        self.snapshot();
        terminate(&mut self.child, &self.owned);
        self.finished = true;
        result
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.finish();
    }
}

fn execute_local(options: &Options, request: &Request, session: &mut Option<Session>) -> Response {
    if options.execution == Execution::Fresh {
        return invoke(options, request);
    }
    if session.is_none() {
        match Session::start(options) {
            Ok(started) => *session = Some(started),
            Err(error) => return infrastructure(request, error),
        }
    }
    match session
        .as_mut()
        .expect("session initialized")
        .invoke(request)
    {
        Ok(response) => response,
        Err(error) => {
            // Never accept another job on a session with untrusted protocol state.
            *session = None;
            infrastructure(request, error)
        }
    }
}

fn validate_coverage(
    coverage: &Coverage,
    mutants: &[Mutant],
    root: &Path,
    tests: &Tests,
) -> Result<(), String> {
    let ids: BTreeSet<_> = coverage.tests.iter().map(|test| &test.id).collect();
    let hits: BTreeSet<_> = coverage.hits.iter().map(|hit| &hit.mutant_id).collect();
    let expected: BTreeSet<_> = mutants.iter().map(|mutant| &mutant.id).collect();
    if ids.len() != coverage.tests.len()
        || ids.iter().any(|id| id.is_empty())
        || coverage.tests.len() as u64 != tests.passed + tests.failed + tests.skipped
        || hits.len() != coverage.hits.len()
        || hits != expected
        || coverage.hits.iter().any(|hit| {
            let unique: BTreeSet<_> = hit.tests.iter().collect();
            unique.len() != hit.tests.len() || hit.tests.iter().any(|id| !ids.contains(id))
        })
        || coverage.tests.iter().any(|test| {
            let file = Path::new(&test.file);
            !file.is_absolute()
                || !file.starts_with(root)
                || file.canonicalize().ok().as_deref() != Some(file)
        })
    {
        return Err("invalid-coverage-inventory".into());
    }
    Ok(())
}

fn selection_for(baseline: &Response, mutant: &Mutant) -> Option<oximut_core::Selection> {
    baseline.coverage.as_ref().map(|coverage| {
        let mut selected = coverage.select(&mutant.id);
        let total = baseline.tests.passed + baseline.tests.failed + baseline.tests.skipped;
        selected.total_tests = total;
        if selected.mode == "full" {
            selected.selected_tests = total;
            selected.expected_skipped = baseline.tests.skipped;
        }
        selected
    })
}

fn reusable_result<'a>(
    indexed: &BTreeMap<&str, &'a MutantResult>,
    mutant: &Mutant,
    selection: &Option<oximut_core::Selection>,
    baseline_ok: bool,
) -> Option<&'a MutantResult> {
    let previous = indexed.get(mutant.id.as_str()).copied()?;
    let no_coverage = baseline_ok
        && selection
            .as_ref()
            .is_some_and(|selected| selected.mode == "none");
    (cache::same(&previous.selection, selection)
        && (previous.outcome != Outcome::NoCoverage || no_coverage))
        .then_some(previous)
}

struct RetryEvidence {
    diagnostics: Vec<String>,
    resources: serde_json::Value,
    profiles: Vec<oximut_core::FingerprintProfile>,
    runner_hash_ms: u64,
}

enum Attempt {
    Complete(u8),
    Retry(RetryEvidence),
}

fn run(options: Options) -> Result<u8, String> {
    match run_attempt(&options, None)? {
        Attempt::Complete(code) => Ok(code),
        Attempt::Retry(evidence) => match run_attempt(&options, Some(evidence))? {
            Attempt::Complete(code) => Ok(code),
            Attempt::Retry(_) => unreachable!("the fresh retry cannot request another attempt"),
        },
    }
}

fn run_attempt(options: &Options, previous: Option<RetryEvidence>) -> Result<Attempt, String> {
    let mut cache = cache::Cache::new(options);
    let mut mutants: Vec<Mutant> = vec![];
    let retry = previous.is_some();
    let (mut diagnostics, previous_resources, previous_profiles, previous_runner_hash_ms) =
        match previous {
            Some(evidence) => (
                evidence.diagnostics,
                Some(evidence.resources),
                evidence.profiles,
                evidence.runner_hash_ms,
            ),
            None => (Vec::new(), None, Vec::new(), 0),
        };
    let mut disabled_checks = vec![];
    let mut probe_transforms = vec![];
    let mut probe_reasons = vec![];
    for path in &options.sources {
        let source = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let identity = path
            .strip_prefix(&options.root)
            .map_err(|e| e.to_string())?
            .to_string_lossy();
        let (generated, probe) = cache.artifact(options, &identity, path, &source)?;
        if let Some(probe) = probe {
            match probe {
                Ok(probe) => probe_transforms.push(probe),
                Err(reason) => probe_reasons.push(reason),
            }
        }
        if options.execution == Execution::Persistent
            && !generated
                .diagnostics
                .iter()
                .any(|d| d.contains("switching fallback"))
            && let Some(first) = generated.mutants.first()
        {
            disabled_checks.push(Mutation {
                id: format!("__oximut_disabled__-{}", disabled_checks.len()),
                file: first.file.clone(),
                code: first.code.clone(),
                map: first.map.clone(),
            });
        }
        mutants.extend(generated.mutants);
        diagnostics.extend(
            generated
                .diagnostics
                .into_iter()
                .map(|d| format!("{}: {d}", path.display())),
        );
    }
    if let Some(seed) = options.order_seed {
        shuffle(&mut mutants, seed);
    }
    diagnostics.push(match options.execution {
        Execution::Fresh => "execution=fresh; isolation=fresh-controller-and-test-process".into(),
        Execution::Persistent => "execution=persistent; isolation=fresh-test-forks; controller reuse only; baseline=original-and-disabled-switches".into(),
    });
    if options.list {
        println!(
            "{}",
            serde_json::to_string_pretty(
                &serde_json::json!({"schemaVersion":1,"mutants":mutants,"diagnostics":diagnostics})
            )
            .map_err(|e| e.to_string())?
        );
        return Ok(Attempt::Complete(0));
    }
    enable_subreaper()?;
    let mut cached = if retry {
        cache.prepare_attempt(options, false)
    } else {
        cache.prepare(options)
    };
    let run_id = format!(
        "{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos()
    );
    let mut request = Request {
        protocol: PROTOCOL,
        run_id: run_id.clone(),
        job_id: "baseline".into(),
        generation: 0,
        kind: "baseline".into(),
        root: options.root.to_string_lossy().into_owned(),
        config: options.config.to_string_lossy().into_owned(),
        deadline_ms: options.timeout_ms,
        mutation: None,
        collect_coverage: None,
        selection: None,
    };
    let mut scheduler = scheduler::Scheduler::new(options)?;
    let mut baseline = scheduler.execute(&request);
    if baseline.outcome == TestOutcome::Passed {
        for mutation in disabled_checks {
            request.generation += 1;
            request.job_id = mutation.id.clone();
            request.kind = "mutant".into();
            let file = mutation.file.clone();
            request.mutation = Some(mutation);
            let mut checked = scheduler.execute(&request);
            if checked.outcome == TestOutcome::Passed
                && (checked.tests.passed != baseline.tests.passed
                    || checked.tests.failed != baseline.tests.failed
                    || checked.tests.skipped != baseline.tests.skipped)
            {
                checked =
                    infrastructure(&request, "disabled switching changed baseline test counts");
            }
            diagnostics.push(format!(
                "disabled switching validation: {file}: {:?}",
                checked.outcome
            ));
            if checked.outcome != TestOutcome::Passed {
                baseline = checked;
                break;
            }
        }
    }
    if baseline.outcome == TestOutcome::Passed && options.test_selection {
        request.generation += 1;
        request.job_id = "coverage-baseline".into();
        request.kind = "baseline".into();
        request.mutation = None;
        request.collect_coverage = Some(probe_transforms);
        let mut collected = scheduler.execute(&request);
        if collected.outcome != TestOutcome::Passed {
            diagnostics.push("probe baseline failed; mutation execution blocked".into());
            baseline = collected;
        } else if collected.tests.passed != baseline.tests.passed
            || collected.tests.failed != baseline.tests.failed
            || collected.tests.skipped != baseline.tests.skipped
        {
            baseline = infrastructure(
                &request,
                "probe instrumentation changed baseline test counts",
            );
        } else {
            let mut coverage = collected.coverage.take().unwrap_or(Coverage {
                complete: false,
                tests: vec![],
                hits: vec![],
                reasons: vec!["missing-coverage".into()],
            });
            coverage.reasons.extend(probe_reasons);
            if let Err(reason) =
                validate_coverage(&coverage, &mutants, &options.root, &baseline.tests)
            {
                coverage.complete = false;
                coverage.reasons.push(reason);
            }
            if !coverage.reasons.is_empty() {
                coverage.complete = false;
            }
            baseline.coverage = Some(coverage);
        }
        request.collect_coverage = None;
    }
    let generation_offset = request.generation;
    let baseline_ok = baseline.outcome == TestOutcome::Passed;
    let mut results = vec![];
    let mut jobs = vec![];
    let cached_valid = baseline_ok
        && cached
            .as_ref()
            .is_some_and(|entry| entry.validate(&mutants, &baseline.tests));
    let deferred_check = !retry
        && cached_valid
        && !options.baseline_only
        && !mutants.is_empty()
        && cached.as_ref().is_some_and(|entry| {
            let indexed: BTreeMap<_, _> = entry
                .results
                .iter()
                .map(|result| (result.id.as_str(), result))
                .collect();
            mutants.iter().all(|mutant| {
                reusable_result(
                    &indexed,
                    mutant,
                    &selection_for(&baseline, mutant),
                    baseline_ok,
                )
                .is_some()
            })
        });
    let post_baseline_unchanged = if !deferred_check && (cached.is_some() || retry) {
        cache.inputs_unchanged(options, "post-baseline")
    } else {
        true
    };
    if !post_baseline_unchanged {
        cached = None;
        if cache.report.outcomes_enabled {
            cache.report.reason = "inputs-changed-during-baseline".into();
        }
    }
    if baseline_ok {
        if cached.is_some() && !cached_valid {
            cached = None;
            cache.report.reason = "cached-outcomes-failed-baseline-or-inventory-validation".into();
        }
    } else {
        cached = None;
        if cache.report.outcomes_enabled {
            cache.report.reason = "baseline-did-not-pass".into();
        }
    }
    let indexed: BTreeMap<_, _> = cached.as_ref().map_or_else(BTreeMap::new, |entry| {
        entry
            .results
            .iter()
            .map(|result| (result.id.as_str(), result))
            .collect()
    });
    if !options.baseline_only {
        for (index, mutant) in mutants.into_iter().enumerate() {
            let selection = selection_for(&baseline, &mutant);
            let no_coverage = baseline_ok
                && selection
                    .as_ref()
                    .is_some_and(|selected| selected.mode == "none");
            if let Some(entry) = &cached
                && let Some(previous) = reusable_result(&indexed, &mutant, &selection, baseline_ok)
            {
                let mut reused = previous.clone();
                reused.provenance = Provenance {
                    source: "cache".into(),
                    reason: "matching-project-fingerprint-and-fresh-baseline".into(),
                    fingerprint: cache.report.fingerprint.clone(),
                    original_run_id: Some(entry.run_id.clone()),
                };
                cache.report.outcome_hits += 1;
                results.push(reused);
                continue;
            }
            if cache.report.outcomes_enabled {
                cache.report.outcome_misses += 1;
            }
            let response = if baseline_ok && !no_coverage {
                request.job_id = mutant.id.clone();
                request.generation = generation_offset + index as u64 + 1;
                request.kind = "mutant".into();
                request.selection = selection.clone();
                request.mutation = Some(Mutation {
                    id: mutant.id.clone(),
                    file: mutant.file.clone(),
                    code: mutant.code,
                    map: mutant.map,
                });
                jobs.push((results.len(), request.clone()));
                infrastructure(&request, "pending scheduler execution")
            } else {
                Response {
                    protocol: PROTOCOL,
                    run_id: run_id.clone(),
                    job_id: mutant.id.clone(),
                    generation: generation_offset + index as u64 + 1,
                    outcome: TestOutcome::Skipped,
                    tests: Tests::default(),
                    errors: if no_coverage {
                        vec![]
                    } else {
                        vec!["baseline did not pass; mutant was not executed".into()]
                    },
                    coverage: None,
                }
            };
            results.push(MutantResult {
                id: mutant.id,
                file: mutant.file,
                operator: mutant.operator,
                original: mutant.original,
                replacement: mutant.replacement,
                location: mutant.location,
                outcome: if no_coverage {
                    Outcome::NoCoverage
                } else {
                    response.mutant_outcome()
                },
                tests: response.tests,
                errors: response.errors,
                selection,
                provenance: Provenance {
                    source: if no_coverage {
                        "baseline-coverage"
                    } else if baseline_ok {
                        "executed"
                    } else {
                        "skipped"
                    }
                    .into(),
                    reason: if cached.is_some() {
                        "fresh-selection-differs-from-cached-result".into()
                    } else {
                        cache.report.reason.clone()
                    },
                    fingerprint: cache.report.fingerprint.clone(),
                    original_run_id: None,
                },
            });
        }
    }
    let fresh_work_started = Instant::now();
    scheduler.run_jobs(jobs, &mut results);
    let fresh_work_ms = fresh_work_started
        .elapsed()
        .as_millis()
        .min(u64::MAX as u128) as u64;
    let cleanup_ok = scheduler.finish(&mut diagnostics);
    let mut resources = scheduler.report();
    let input_check = cache.check_inputs(
        options,
        if deferred_check {
            "post-baseline-and-pre-publication"
        } else {
            "pre-publication"
        },
    );
    if deferred_check
        && input_check == cache::InputCheck::Changed
        && cleanup_ok
        && baseline_ok
        && resources
            .get("exhaustion")
            .is_some_and(serde_json::Value::is_null)
        && results
            .iter()
            .all(|result| result.provenance.source == "cache")
    {
        diagnostics.push("inputs changed during baseline or teardown; retrying once with fresh mutation execution".into());
        diagnostics.push(format!("discarded clean attempt {run_id}; resources={resources}; final baseline and outcomes come from the fresh retry"));
        return Ok(Attempt::Retry(RetryEvidence {
            diagnostics,
            resources,
            profiles: cache.report.fingerprint_profiles,
            runner_hash_ms: cache.report.runner_hash_ms,
        }));
    }
    let inputs_unchanged = input_check == cache::InputCheck::Unchanged
        && (!retry || (post_baseline_unchanged && cache.report.fingerprint.is_some()));
    if retry && !inputs_unchanged {
        diagnostics.push(
            "fresh retry input validation failed; run incomplete and outcomes not persisted".into(),
        );
    }
    if let cache::InputCheck::Unreadable(error) = &input_check {
        diagnostics.push(format!("cache input validation failed: {error}"));
    }
    if let Some(previous_resources) = previous_resources {
        // Peak observations cover both attempts; accounting counters are additive.
        // Configuration and exhaustion describe the final attempt; the discarded
        // attempt's complete resource report remains in diagnostics above.
        for field in [
            "peakAggregateRssBytes",
            "measuredWorkerPeakBytes",
            "admissionWorkerCostBytes",
            "maxActiveJobs",
        ] {
            if let (Some(old), Some(current)) = (
                previous_resources[field].as_u64(),
                resources[field].as_u64(),
            ) {
                resources[field] = old.max(current).into();
            }
        }
        for field in ["samples", "admissionPauses", "recycledIdleWorkers"] {
            if let (Some(old), Some(current)) = (
                previous_resources[field].as_u64(),
                resources[field].as_u64(),
            ) {
                resources[field] = old.saturating_add(current).into();
            }
        }
        cache.report.runner_hash_ms = cache
            .report
            .runner_hash_ms
            .saturating_add(previous_runner_hash_ms);
        let mut profiles = previous_profiles;
        profiles.append(&mut cache.report.fingerprint_profiles);
        cache.report.fingerprint_profiles = profiles;
    }
    if !inputs_unchanged {
        for result in &mut results {
            if result.provenance.source == "cache" {
                result.outcome = Outcome::InfrastructureError;
                result
                    .errors
                    .push("inputs changed during run; cached evidence invalidated".into());
            }
        }
    }
    let mut summary = Summary::from_results(&results);
    summary.complete &= baseline_ok && cleanup_ok && (!retry || inputs_unchanged);
    let code = summary.exit_code();
    let publication = cache.stage(
        options,
        summary.complete,
        inputs_unchanged,
        &run_id,
        &baseline.tests,
        &results,
    );
    let mut report = Report {
        schema_version: 1,
        run_id,
        baseline,
        mutants: results,
        diagnostics,
        summary,
        cache: cache.report.clone(),
        resources,
    };
    let json = serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?;
    if let Some(path) = &options.report {
        if path == Path::new("-") {
            println!("{json}");
        } else {
            if let Some(parent) = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
            {
                fs::create_dir_all(parent).map_err(|e| format!("report directory: {e}"))?;
            }
            let parent = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
                .unwrap_or(Path::new("."));
            let mut temporary =
                tempfile::NamedTempFile::new_in(parent).map_err(|e| format!("report: {e}"))?;
            writeln!(temporary, "{json}").map_err(|e| format!("report: {e}"))?;
            temporary
                .persist(path)
                .map_err(|e| format!("report: {e}"))?;
        }
    }
    cache.record_policy(report.summary.complete, inputs_unchanged, fresh_work_ms);
    if let Some(publication) = publication {
        report.cache.persistence = match publication.commit() {
            Ok(()) => "complete-run-persisted".into(),
            Err(error) => format!("outcome-commit-failed: {error}"),
        };
    }
    if options.report.as_deref() != Some(Path::new("-")) {
        println!(
            "Baseline: {:?} ({} passed, {} failed, {} skipped)",
            report.baseline.outcome,
            report.baseline.tests.passed,
            report.baseline.tests.failed,
            report.baseline.tests.skipped
        );
        for error in &report.baseline.errors {
            println!("  {error}");
        }
        for mutant in &report.mutants {
            let outcome = serde_json::to_value(mutant.outcome).map_err(|e| e.to_string())?;
            println!(
                "{} {} {}:{}:{} {} -> {}",
                outcome.as_str().unwrap_or("unknown"),
                &mutant.id[..12],
                mutant.file,
                mutant.location.start.line,
                mutant.location.start.column,
                mutant.original,
                mutant.replacement
            );
            for error in &mutant.errors {
                println!("  {error}");
            }
            if let Some(selection) = &mutant.selection {
                println!(
                    "  Selected tests: {}/{} ({}); reasons: {}",
                    selection.selected_tests,
                    selection.total_tests,
                    selection.mode,
                    selection.reasons.join(", ")
                );
            }
        }
        let s = &report.summary;
        println!(
            "Mutants: {} total, {} killed, {} survived, {} no-coverage, {} invalid, {} timeout, {} infrastructure-error, {} resource-exhausted, {} skipped",
            s.total,
            s.killed,
            s.survived,
            s.no_coverage,
            s.invalid,
            s.timeout,
            s.infrastructure_error,
            s.resource_exhausted,
            s.skipped
        );
        let score = s
            .score
            .map_or_else(|| "n/a".into(), |value| format!("{value:.2}%"));
        println!(
            "Score: {score} ({}/{}); complete: {}",
            s.killed, s.score_denominator, s.complete
        );
        println!(
            "Cache: {}; instrumentation hits/misses: {}/{}; outcome hits/misses: {}/{}; {}",
            report.cache.reason,
            report.cache.instrumentation_hits,
            report.cache.instrumentation_misses,
            report.cache.outcome_hits,
            report.cache.outcome_misses,
            report.cache.persistence
        );
        for diagnostic in &report.diagnostics {
            println!("Diagnostic: {diagnostic}");
        }
    }
    Ok(Attempt::Complete(code))
}

fn main() -> ExitCode {
    if env::args().nth(1).as_deref() == Some("--internal-worker") {
        return match scheduler::worker_main() {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("oximut worker: {error}");
                ExitCode::from(2)
            }
        };
    }
    match options().and_then(|options| options.map_or(Ok(0), run)) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("oximut: {error}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seeded_shuffle_is_reproducible_and_preserves_every_item() {
        let mut a: Vec<_> = (0..100).collect();
        let mut b = a.clone();
        let mut c = a.clone();
        shuffle(&mut a, 42);
        shuffle(&mut b, 42);
        shuffle(&mut c, 43);
        assert_eq!(a, b);
        assert_ne!(a, c);
        a.sort();
        assert_eq!(a, (0..100).collect::<Vec<_>>());
        shuffle::<u8>(&mut [], 0);
    }

    #[test]
    fn bounded_response_reader_rejects_oversize_and_stale_evidence() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("response.json");
        let request = Request {
            protocol: PROTOCOL,
            run_id: "run".into(),
            job_id: "job".into(),
            generation: 2,
            kind: "baseline".into(),
            root: "/".into(),
            config: "/config".into(),
            deadline_ms: 100,
            mutation: None,
            collect_coverage: None,
            selection: None,
        };
        fs::write(&path, vec![b' '; 1_048_577]).unwrap();
        assert!(
            read_response(&path, &request)
                .unwrap_err()
                .contains("1 MiB")
        );
        let mut response = infrastructure(&request, "failure");
        response.generation = 1;
        fs::write(&path, serde_json::to_vec(&response).unwrap()).unwrap();
        assert!(
            read_response(&path, &request)
                .unwrap_err()
                .contains("stale")
        );
    }
}
