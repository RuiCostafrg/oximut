//! A central event loop owns admission. Each slot is a Rust subreaper process,
//! so detached grandchildren can never be mistaken for a sibling slot's work.
use super::*;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
// Internal warm acknowledgements never index the public mutant result array.
const WARM_JOB: usize = usize::MAX;
static INTERRUPTED: AtomicBool = AtomicBool::new(false);
extern "C" fn interrupt_handler(_: libc::c_int) {
    INTERRUPTED.store(true, Ordering::Relaxed);
}
fn install_signals() {
    unsafe {
        libc::signal(
            libc::SIGINT,
            interrupt_handler as *const () as libc::sighandler_t,
        );
        libc::signal(
            libc::SIGTERM,
            interrupt_handler as *const () as libc::sighandler_t,
        );
    }
}

struct Worker {
    directory: tempfile::TempDir,
    child: std::process::Child,
    owned: BTreeMap<u32, u64>,
    sequence: u64,
    busy: Option<(usize, Request, Instant)>,
    finished: bool,
}
impl Worker {
    fn start(options: &Options) -> Result<Self, String> {
        let directory = tempfile::tempdir().map_err(|e| e.to_string())?;
        fs::write(
            directory.path().join("options.json"),
            serde_json::to_vec(options).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        let mut command = Command::new(env::current_exe().map_err(|e| e.to_string())?);
        command
            .arg("--internal-worker")
            .arg(directory.path())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        // Node, Vitest and every fresh test fork inherit a private per-slot
        // compile cache, removed with this directory. V8 validates cached code
        // against source content and flags; a caller's own setting wins.
        if env::var_os("NODE_COMPILE_CACHE").is_none() {
            command.env(
                "NODE_COMPILE_CACHE",
                directory.path().join("node-compile-cache"),
            );
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let child = command
            .spawn()
            .map_err(|e| format!("could not start scheduler worker: {e}"))?;
        Ok(Self {
            directory,
            child,
            owned: BTreeMap::new(),
            sequence: 0,
            busy: None,
            finished: false,
        })
    }
    fn snapshot(&mut self) {
        self.owned.extend(descendants(self.child.id()));
    }
    fn send(&mut self, index: usize, request: Request) -> Result<(), String> {
        if index == WARM_JOB {
            fs::write(
                self.directory
                    .path()
                    .join(format!("warm-{}", self.sequence)),
                b"",
            )
            .map_err(|e| e.to_string())?;
        }
        cache::atomic(
            &self
                .directory
                .path()
                .join(format!("request-{}.json", self.sequence)),
            &request,
        )?;
        self.busy = Some((index, request, Instant::now()));
        Ok(())
    }
    fn ready(&mut self) -> Option<Result<Response, String>> {
        let (_, request, started) = self.busy.as_ref()?;
        let path = self
            .directory
            .path()
            .join(format!("response-{}.json", self.sequence));
        if path.exists() {
            let result = read_response(&path, request);
            let _ = fs::remove_file(path);
            return Some(result);
        }
        match self.child.try_wait() {
            Ok(Some(status)) => {
                return Some(Err(format!(
                    "scheduler worker exited before response: {status}"
                )));
            }
            Err(error) => return Some(Err(format!("scheduler worker wait failed: {error}"))),
            _ => {}
        }
        if started.elapsed() > Duration::from_millis(request.deadline_ms + 16_000) {
            return Some(Err("scheduler worker exceeded controller deadline".into()));
        }
        None
    }
    fn abort(&mut self) {
        self.snapshot();
        terminate(&mut self.child, &self.owned);
        self.finished = true;
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        if !self.finished {
            self.abort();
        }
    }
}

pub struct Scheduler<'a> {
    options: &'a Options,
    budget: resources::Budget,
    slots: Vec<Option<Worker>>,
    diagnostics: Vec<String>,
    cleanup_ok: bool,
    spares: VecDeque<(usize, Request)>,
}
fn exhausted(request: &Request, reason: &str) -> Response {
    if reason.starts_with("scheduler worker exited") {
        return infrastructure(
            request,
            format!("{reason}; ownership became uncertain; all worker trees stopped"),
        );
    }
    if INTERRUPTED.load(Ordering::Relaxed) {
        return infrastructure(request, "run interrupted; owned processes terminated");
    }
    let mut response = infrastructure(
        request,
        format!("resource exhausted: {reason}; job will not be retried"),
    );
    response.outcome = TestOutcome::ResourceExhausted;
    response
}
impl<'a> Scheduler<'a> {
    pub fn new(options: &'a Options) -> Result<Self, String> {
        install_signals();
        let budget = resources::Budget::new(options)?;
        let slots = (0..budget.effective_workers).map(|_| None).collect();
        Ok(Self {
            options,
            budget,
            slots,
            diagnostics: vec![],
            cleanup_ok: true,
            spares: VecDeque::new(),
        })
    }
    pub fn prepare_spares(&mut self, request: &Request) {
        if self.options.execution == Execution::Persistent
            && coordinator::native_worker(&self.options.adapter).is_some()
        {
            self.spares
                .extend((1..self.slots.len()).map(|_| (WARM_JOB, request.clone())));
        }
    }
    fn sample(&mut self, force: bool) {
        self.sample_with(|budget, pids| budget.sample(pids, force));
    }
    fn sample_with(&mut self, sample: impl FnOnce(&mut resources::Budget, &[u32])) {
        let pids: Vec<_> = self.slots.iter().flatten().map(|w| w.child.id()).collect();
        let before = self.budget.samples;
        sample(&mut self.budget, &pids);
        // Ownership snapshots reuse the sample's discovery. Aborts and
        // shutdown still discover afresh before signalling anything.
        if self.budget.samples != before
            && let Some(table) = &self.budget.table
        {
            for worker in self.slots.iter_mut().flatten() {
                worker.owned.extend(table.descendants(worker.child.id()));
            }
        }
    }
    fn close_slots(&mut self, slots: &[usize]) {
        let mut stopping = vec![];
        let mut failed_signals = vec![];
        for &slot in slots {
            let Some(mut worker) = self.slots[slot].take() else {
                continue;
            };
            worker.snapshot();
            if worker
                .busy
                .as_ref()
                .is_some_and(|(index, _, _)| *index == WARM_JOB)
            {
                if let Some(result) = worker.ready() {
                    let (_, request, _) = worker.busy.take().unwrap();
                    worker.sequence += 1;
                    let response = result.unwrap_or_else(|error| infrastructure(&request, error));
                    let failed = response.outcome != TestOutcome::Skipped;
                    self.diagnostics.push(format!(
                        "spare warm {}: {}",
                        if failed { "failed" } else { "ready" },
                        response.errors.join("; ")
                    ));
                    if failed {
                        worker.abort();
                        continue;
                    }
                } else {
                    self.diagnostics
                        .push("spare warm abandoned at shutdown".into());
                    worker.abort();
                    if worker
                        .owned
                        .iter()
                        .any(|(&pid, &start)| still_owned(pid, start))
                    {
                        self.cleanup_ok = false;
                        self.diagnostics
                            .push("scheduler shutdown left owned descendants".into());
                    }
                    continue;
                }
            }
            if let Err(error) = fs::write(worker.directory.path().join("stop"), b"") {
                self.cleanup_ok = false;
                self.diagnostics
                    .push(format!("scheduler shutdown failed: {error}"));
                failed_signals.push(worker);
                continue;
            }
            stopping.push((worker, Instant::now() + Duration::from_secs(7)));
        }
        for mut worker in failed_signals {
            worker.abort();
        }
        while !stopping.is_empty() {
            let stopping_pids: Vec<_> = stopping.iter().map(|(w, _)| w.child.id()).collect();
            self.sample_with(|budget, pids| {
                let mut all = pids.to_vec();
                all.extend(stopping_pids);
                budget.sample(&all, false);
            });
            stopping.retain_mut(|(worker, deadline)| {
                worker.snapshot();
                match worker.child.try_wait() {
                    Ok(Some(status)) => {
                        if !status.success() {
                            self.cleanup_ok = false;
                            self.diagnostics.push(format!(
                                "persistent adapter shutdown failed (scheduler worker): {status}"
                            ));
                        }
                        if let Ok(value) = fs::read(worker.directory.path().join("shutdown.json"))
                            && let Ok(messages) = serde_json::from_slice::<Vec<String>>(&value)
                        {
                            self.diagnostics.extend(messages);
                        }
                        if worker
                            .owned
                            .iter()
                            .any(|(&pid, &start)| still_owned(pid, start))
                        {
                            self.cleanup_ok = false;
                            self.diagnostics
                                .push("scheduler shutdown left owned descendants".into());
                        }
                        worker.abort();
                        return false;
                    }
                    Err(error) => {
                        self.cleanup_ok = false;
                        self.diagnostics
                            .push(format!("scheduler shutdown wait failed: {error}"));
                        worker.abort();
                        return false;
                    }
                    _ => {}
                }
                if Instant::now() >= *deadline {
                    self.cleanup_ok = false;
                    self.diagnostics
                        .push("scheduler shutdown exceeded deadline".into());
                    worker.abort();
                    return false;
                }
                true
            });
            if !stopping.is_empty() {
                thread::sleep(Duration::from_millis(10));
            }
        }
    }
    fn dispatch(
        &mut self,
        mut pending: VecDeque<(usize, Request)>,
        serial: bool,
    ) -> Vec<(usize, Response)> {
        let mut completed = vec![];
        let mut paused = false;
        let mut idle_pressure_since = None;
        while !pending.is_empty() || self.budget.active > 0 || !self.spares.is_empty() {
            if INTERRUPTED.load(Ordering::Relaxed) {
                self.budget.exhausted("run interrupted by SIGINT/SIGTERM");
            }
            self.sample(false);
            // Sample before accepting every ready response. Resource failures
            // override test failures and can never contribute a mutation kill.
            for slot in 0..self.slots.len() {
                let ready = self.slots[slot].as_mut().and_then(Worker::ready);
                if let Some(result) = ready {
                    self.sample(true);
                    if self.slots[slot].as_ref().unwrap().busy.as_ref().unwrap().0 != WARM_JOB
                        && let Err(error) = &result
                        && error.starts_with("scheduler worker exited")
                    {
                        self.budget.exhausted(error.clone());
                    }
                    let worker = self.slots[slot].as_mut().expect("ready worker");
                    let (index, request, _) = worker.busy.take().expect("ready job");
                    worker.sequence += 1;
                    if index != WARM_JOB {
                        self.budget.active -= 1;
                    }
                    let response = if let Some(reason) = &self.budget.exhaustion {
                        exhausted(&request, reason)
                    } else {
                        result.unwrap_or_else(|error| infrastructure(&request, error))
                    };
                    let untrusted = (index == WARM_JOB && response.outcome != TestOutcome::Skipped)
                        || response.outcome == TestOutcome::InfrastructureError
                        || response.outcome == TestOutcome::ResourceExhausted;
                    if index == WARM_JOB {
                        self.diagnostics.push(format!(
                            "spare warm {}: {}",
                            if untrusted { "failed" } else { "ready" },
                            response.errors.join("; ")
                        ));
                    } else {
                        completed.push((index, response));
                    }
                    if untrusted {
                        self.slots[slot].as_mut().unwrap().abort();
                        self.slots[slot] = None;
                    }
                }
            }
            if let Some(reason) = self.budget.exhaustion.clone() {
                for slot in 0..self.slots.len() {
                    if let Some(worker) = self.slots[slot].as_mut() {
                        if let Some((index, request, _)) = worker.busy.take()
                            && index != WARM_JOB
                        {
                            completed.push((index, exhausted(&request, &reason)));
                            self.budget.active -= 1;
                        }
                        worker.abort();
                        self.slots[slot] = None;
                    }
                }
                completed.extend(
                    pending
                        .drain(..)
                        .filter(|(index, _)| *index != WARM_JOB)
                        .map(|(index, request)| (index, exhausted(&request, &reason))),
                );
                break;
            }
            if pending.is_empty() && self.budget.active == 0 && self.spares.is_empty() {
                break;
            }
            if pending.is_empty() && self.spares.is_empty() {
                if self.budget.active > 0 {
                    thread::sleep(Duration::from_millis(10));
                }
                continue;
            }
            // Counter samples never refresh slot costs; admit only on exact evidence.
            // Only when a job could actually be admitted now: a free slot and serial
            // ordering satisfied. A busy backlog keeps the cheap periodic cadence;
            // exact samples still run at least once per second and per response.
            let warming = pending.is_empty();
            let free_slot = self.slots.iter().enumerate().position(|(index, slot)| {
                if warming {
                    index > 0 && slot.is_none()
                } else {
                    slot.as_ref().is_none_or(|worker| worker.busy.is_none())
                }
            });
            let admissible = (!serial || warming || self.budget.active == 0) && free_slot.is_some();
            if admissible && self.budget.last_sample_was_counter {
                self.sample(true);
                if self.budget.exhaustion.is_some() {
                    continue;
                }
            }
            let active_pids: Vec<_> = self
                .slots
                .iter()
                .flatten()
                .filter(|worker| {
                    worker
                        .busy
                        .as_ref()
                        .is_some_and(|(index, _, _)| *index != WARM_JOB)
                })
                .map(|worker| worker.child.id())
                .collect();
            let capacity = self.budget.capacity(&active_pids);
            if warming && capacity == 0 {
                self.diagnostics
                    .push("spare warm skipped: resource admission refused".into());
                self.spares.clear();
                continue;
            }
            if capacity > 0
                && (!serial || warming || self.budget.active == 0)
                && let Some(slot) = free_slot
            {
                let (index, mut request) = if warming {
                    self.spares.pop_front().unwrap()
                } else {
                    pending.pop_front().unwrap()
                };
                // One serial baseline occupies the controller, but its fresh forks
                // must each have admission headroom before they are launched.
                if request.kind == "baseline" && self.options.execution == Execution::Persistent {
                    // Spares start alongside: wall time matters more than peak memory.
                    request.workers = Some(
                        self.budget
                            .baseline_fork_limit(capacity, &active_pids)
                            .min(1024),
                    );
                }
                if self.slots[slot].is_none() {
                    match Worker::start(self.options) {
                        Ok(worker) => self.slots[slot] = Some(worker),
                        Err(error) => {
                            if index == WARM_JOB {
                                self.diagnostics
                                    .push(format!("spare start failed: {error}"));
                            } else {
                                completed.push((index, infrastructure(&request, error)));
                            }
                            continue;
                        }
                    }
                }
                let worker = self.slots[slot].as_mut().unwrap();
                match worker.send(index, request.clone()) {
                    Ok(()) => {
                        if index != WARM_JOB {
                            self.budget.active += 1;
                        }
                        self.budget.max_active_jobs =
                            self.budget.max_active_jobs.max(self.budget.active);
                    }
                    Err(error) => {
                        if index == WARM_JOB {
                            self.diagnostics.push(format!("spare send failed: {error}"));
                        } else {
                            completed.push((index, infrastructure(&request, error)));
                        }
                        worker.abort();
                        self.slots[slot] = None;
                    }
                }
                // Real jobs reserve admission headroom; warm slots remain busy
                // and their entire trees remain in aggregate RSS sampling.
                paused = false;
                idle_pressure_since = None;
            } else if capacity == 0 && free_slot.is_some() {
                // Memory pressure: a slot is free but admission must wait.
                // Busy slots alone are ordinary waiting (below), relying on
                // the periodic sample and the forced sample at completion.
                if !paused {
                    self.budget.admission_pauses += 1;
                    paused = true;
                }
                let idle: Vec<_> = self
                    .slots
                    .iter()
                    .enumerate()
                    .filter_map(|(index, slot)| {
                        slot.as_ref().filter(|w| w.busy.is_none()).map(|_| index)
                    })
                    .collect();
                for index in idle {
                    self.close_slots(&[index]);
                    self.budget.recycled_idle_workers += 1;
                }
                self.sample_with(|budget, pids| budget.sample_admission(pids));
                if self.budget.active == 0 {
                    let since = idle_pressure_since.get_or_insert_with(Instant::now);
                    if since.elapsed() >= Duration::from_millis(250) {
                        self.budget.exhausted(format!("cannot admit a single job with measured worker cost {} bytes, aggregate RSS {} bytes and reserved headroom {} bytes", self.budget.measured_worker_peak_bytes, self.budget.current, self.budget.reserve_bytes));
                    }
                }
                thread::sleep(Duration::from_millis(10));
            } else {
                thread::sleep(Duration::from_millis(10));
            }
        }
        completed
    }
    pub fn execute(&mut self, request: &Request) -> Response {
        self.dispatch(VecDeque::from([(0, request.clone())]), true)
            .pop()
            .expect("one serial result")
            .1
    }
    pub fn run_jobs(&mut self, jobs: Vec<(usize, Request)>, results: &mut [MutantResult]) {
        for (index, response) in self.dispatch(jobs.into(), false) {
            results[index].outcome = response.mutant_outcome();
            results[index].tests = response.tests;
            results[index].errors = response.errors;
        }
    }
    pub fn finish(&mut self, diagnostics: &mut Vec<String>) -> bool {
        self.spares.clear();
        self.close_slots(&(0..self.slots.len()).collect::<Vec<_>>());
        // All slot roots are stopped. Any remaining descendant has been
        // adopted by the main subreaper and belongs to this run, including
        // processes orphaned before a failed slot could be sampled.
        let deadline = Instant::now() + Duration::from_millis(750);
        loop {
            let remaining = descendants(std::process::id());
            if remaining.is_empty() {
                break;
            }
            for (&pid, &start) in &remaining {
                if still_owned(pid, start) {
                    unsafe {
                        libc::kill(pid as i32, libc::SIGKILL);
                    }
                }
            }
            for (&pid, &start) in &remaining {
                if still_owned(pid, start) {
                    unsafe {
                        libc::waitpid(pid as i32, std::ptr::null_mut(), libc::WNOHANG);
                    }
                }
            }
            if Instant::now() >= deadline {
                self.cleanup_ok = false;
                self.diagnostics
                    .push("global subreaper cleanup left descendants after deadline".into());
                break;
            }
            thread::sleep(Duration::from_millis(5));
        }
        self.sample(true);
        if let Some(reason) = &self.budget.exhaustion {
            self.diagnostics
                .push(format!("resource admission stopped: {reason}"));
        }
        diagnostics.append(&mut self.diagnostics);
        self.cleanup_ok && self.budget.exhaustion.is_none()
    }
    pub fn report(&self) -> serde_json::Value {
        self.budget.report_snapshot()
    }
}

/// Private subprocess protocol. Public requests and adapter identity validation
/// are unchanged. Each worker keeps its own subreaper and persistent session.
pub fn worker_main() -> Result<(), String> {
    enable_subreaper()?;
    let directory = PathBuf::from(
        env::args()
            .nth(2)
            .ok_or("missing internal worker directory")?,
    );
    let options: Options = serde_json::from_slice(
        &fs::read(directory.join("options.json")).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    let mut session = None;
    for sequence in 0u64.. {
        let path = directory.join(format!("request-{sequence}.json"));
        while !path.exists() {
            if directory.join("stop").exists() {
                let mut diagnostics = vec![];
                let cleanup = session.as_mut().map_or(Ok(()), Backend::finish);
                if let Some(backend) = &session {
                    diagnostics.extend(
                        backend
                            .reuse_modes()
                            .into_iter()
                            .map(|mode| format!("adapter observed reuse boundary: {mode}")),
                    );
                    if let Backend::Native(coordinator) = backend {
                        diagnostics.push(format!(
                            "native coordinator: {} controllers for {} jobs",
                            coordinator.controllers_started, coordinator.jobs_run
                        ));
                    }
                }
                if let Err(error) = &cleanup {
                    diagnostics.push(error.clone());
                }
                cache::atomic(&directory.join("shutdown.json"), &diagnostics)?;
                drop(session);
                return cleanup;
            }
            thread::sleep(Duration::from_millis(5));
        }
        let bytes = fs::read(&path).map_err(|e| e.to_string())?;
        if bytes.len() > 8 * 1024 * 1024 {
            return Err("internal request exceeds 8 MiB".into());
        }
        let request = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        let response = if directory.join(format!("warm-{sequence}")).exists() {
            if session.is_none() {
                session = coordinator::native_worker(&options.adapter)
                    .map(|worker| Backend::Native(Box::new(coordinator::Coordinator::new(worker))));
            }
            match session.as_mut() {
                Some(Backend::Native(coordinator)) => coordinator.warm(&options, &request),
                _ => infrastructure(&request, "warm requires native coordinator"),
            }
        } else {
            execute_local(&options, &request, &mut session)
        };
        cache::atomic(
            &directory.join(format!("response-{sequence}.json")),
            &response,
        )?;
        let _ = fs::remove_file(path);
    }
    unreachable!()
}

#[cfg(test)]
mod tests {
    use super::*;

    static PROCESS_TEST: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn options() -> Options {
        parse_options(
            [
                "--root",
                concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/m1"),
                "--source",
                concat!(
                    env!("CARGO_MANIFEST_DIR"),
                    "/../../fixtures/m1/src/subject.ts"
                ),
                "--workers",
                "2",
            ]
            .into_iter()
            .map(str::to_string),
        )
        .unwrap()
        .unwrap()
    }

    fn worker(directory: tempfile::TempDir, script: &str, paths: &[&Path]) -> Worker {
        Worker {
            child: Command::new("python3")
                .args(["-c", script])
                .args(paths)
                .spawn()
                .unwrap(),
            directory,
            owned: BTreeMap::new(),
            sequence: 0,
            busy: None,
            finished: false,
        }
    }

    #[test]
    fn empty_dispatch_leaves_background_warm_busy() {
        let _guard = PROCESS_TEST.lock().unwrap();
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let mut spare = worker(
            tempfile::tempdir().unwrap(),
            "import time; time.sleep(60)",
            &[],
        );
        let request: Request = serde_json::from_value(serde_json::json!({
            "protocol": 1, "runId": "test", "jobId": "warm", "generation": 0,
            "kind": "baseline", "root": ".", "config": "", "deadlineMs": 0,
            "mutation": null
        }))
        .unwrap();
        spare.busy = Some((WARM_JOB, request, Instant::now() - Duration::from_secs(17)));
        scheduler.slots = vec![None, Some(spare)];
        assert!(scheduler.dispatch(VecDeque::new(), true).is_empty());
        assert!(scheduler.slots[1].as_ref().unwrap().busy.is_some());
        assert_eq!(scheduler.budget.active, 0);
    }

    fn request() -> Request {
        serde_json::from_value(serde_json::json!({
            "protocol": 1, "runId": "test", "jobId": "baseline", "generation": 0,
            "kind": "baseline", "root": ".", "config": "", "deadlineMs": 60000,
            "mutation": null
        }))
        .unwrap()
    }

    #[test]
    fn shutdown_abandons_warm_before_stop_and_reaps_descendants() {
        let _guard = PROCESS_TEST.lock().unwrap();
        enable_subreaper().unwrap();
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let directory = tempfile::tempdir().unwrap();
        let evidence = tempfile::tempdir().unwrap();
        let child_pid = evidence.path().join("child");
        let stop_seen = evidence.path().join("stop-seen");
        let script = "import pathlib,subprocess,sys,time\nchild=subprocess.Popen(['python3','-c','import time; time.sleep(60)'], start_new_session=True)\npathlib.Path(sys.argv[2]).write_text(str(child.pid))\nwhile True:\n if (pathlib.Path(sys.argv[1])/'stop').exists(): pathlib.Path(sys.argv[3]).touch()\n time.sleep(.01)";
        let mut warm = worker(
            tempfile::tempdir().unwrap(),
            script,
            &[directory.path(), &child_pid, &stop_seen],
        );
        warm.directory = directory;
        // Synchronize on the descendant existing, rather than elapsed host time.
        let watchdog = Instant::now() + Duration::from_secs(30);
        while fs::read_to_string(&child_pid)
            .unwrap_or_default()
            .is_empty()
        {
            assert!(Instant::now() < watchdog, "warm descendant did not start");
            thread::yield_now();
        }
        let pid: u32 = fs::read_to_string(&child_pid).unwrap().parse().unwrap();
        let (_, start) = process_identity(pid).unwrap();
        warm.busy = Some((WARM_JOB, request(), Instant::now()));
        scheduler.slots = vec![None, Some(warm)];
        let mut diagnostics = vec![];
        assert!(scheduler.finish(&mut diagnostics), "{diagnostics:?}");
        assert!(
            !stop_seen.exists(),
            "warm must be aborted before graceful stop"
        );
        assert!(
            diagnostics
                .iter()
                .any(|d| d.contains("spare warm abandoned at shutdown"))
        );
        assert!(!still_owned(pid, start), "warm descendant survived");
    }

    #[test]
    fn completed_baseline_starts_queued_spare_before_returning() {
        let _guard = PROCESS_TEST.lock().unwrap();
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        scheduler.budget.effective_workers = 2;
        let mut baseline = worker(
            tempfile::tempdir().unwrap(),
            "import time; time.sleep(60)",
            &[],
        );
        let request = request();
        let mut response = infrastructure(&request, "");
        response.outcome = TestOutcome::Passed;
        cache::atomic(
            &baseline.directory.path().join("response-0.json"),
            &response,
        )
        .unwrap();
        baseline.busy = Some((0, request.clone(), Instant::now()));
        scheduler.slots = vec![Some(baseline), None];
        scheduler.budget.active = 1;
        scheduler.spares.push_back((WARM_JOB, request));
        let completed = scheduler.dispatch(VecDeque::new(), true);
        assert_eq!(completed.len(), 1);
        assert!(scheduler.spares.is_empty());
        let spare = scheduler.slots[1].as_ref().expect("queued spare started");
        assert_eq!(spare.busy.as_ref().unwrap().0, WARM_JOB);
        assert!(spare.directory.path().join("warm-0").exists());
    }

    #[test]
    fn shutdown_signals_all_slots_before_waiting() {
        let _guard = PROCESS_TEST.lock().unwrap();
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let first = tempfile::tempdir().unwrap();
        let second = tempfile::tempdir().unwrap();
        let barrier = tempfile::tempdir().unwrap();
        let first_seen = barrier.path().join("first");
        let second_seen = barrier.path().join("second");
        let script = "import pathlib,sys,time\nwhile not (pathlib.Path(sys.argv[1])/'stop').exists(): time.sleep(.01)\npathlib.Path(sys.argv[2]).touch()\nwhile not pathlib.Path(sys.argv[3]).exists(): time.sleep(.01)";
        let mut a = worker(
            tempfile::tempdir().unwrap(),
            script,
            &[first.path(), &first_seen, &second_seen],
        );
        let mut b = worker(
            tempfile::tempdir().unwrap(),
            script,
            &[second.path(), &second_seen, &first_seen],
        );
        // Acknowledgements outlive slot directories, so either exit order is safe.
        a.directory = first;
        b.directory = second;
        scheduler.slots = vec![Some(a), Some(b)];
        let mut diagnostics = vec![];
        assert!(scheduler.finish(&mut diagnostics), "{diagnostics:?}");
        assert!(scheduler.slots.iter().all(Option::is_none));
    }
}
