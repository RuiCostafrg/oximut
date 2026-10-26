//! A central event loop owns admission. Each slot is a Rust subreaper process,
//! so detached grandchildren can never be mistaken for a sibling slot's work.
use super::*;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
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
        self.snapshot();
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
        })
    }
    fn sample(&mut self, force: bool) {
        let pids: Vec<_> = self.slots.iter().flatten().map(|w| w.child.id()).collect();
        self.budget.sample(&pids, force);
    }
    fn close_slot(&mut self, slot: usize) {
        let Some(mut worker) = self.slots[slot].take() else {
            return;
        };
        worker.snapshot();
        if let Err(error) = fs::write(worker.directory.path().join("stop"), b"") {
            self.cleanup_ok = false;
            self.diagnostics
                .push(format!("scheduler shutdown failed: {error}"));
            worker.abort();
            return;
        }
        let deadline = Instant::now() + Duration::from_secs(7);
        loop {
            worker.snapshot();
            self.sample(false);
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
                    break;
                }
                Err(error) => {
                    self.cleanup_ok = false;
                    self.diagnostics
                        .push(format!("scheduler shutdown wait failed: {error}"));
                    break;
                }
                _ => {}
            }
            if Instant::now() >= deadline {
                self.cleanup_ok = false;
                self.diagnostics
                    .push("scheduler shutdown exceeded deadline".into());
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        worker.abort();
    }
    fn dispatch(
        &mut self,
        mut pending: VecDeque<(usize, Request)>,
        serial: bool,
    ) -> Vec<(usize, Response)> {
        let mut completed = vec![];
        let mut paused = false;
        let mut idle_pressure_since = None;
        while !pending.is_empty()
            || self
                .slots
                .iter()
                .flatten()
                .any(|worker| worker.busy.is_some())
        {
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
                    if let Err(error) = &result
                        && error.starts_with("scheduler worker exited")
                    {
                        self.budget.exhausted(error.clone());
                    }
                    let worker = self.slots[slot].as_mut().expect("ready worker");
                    let (index, request, _) = worker.busy.take().expect("ready job");
                    worker.sequence += 1;
                    self.budget.active -= 1;
                    let response = if let Some(reason) = &self.budget.exhaustion {
                        exhausted(&request, reason)
                    } else {
                        result.unwrap_or_else(|error| infrastructure(&request, error))
                    };
                    let untrusted = response.outcome == TestOutcome::InfrastructureError
                        || response.outcome == TestOutcome::ResourceExhausted;
                    completed.push((index, response));
                    if untrusted {
                        self.slots[slot].as_mut().unwrap().abort();
                        self.slots[slot] = None;
                    }
                }
            }
            if let Some(reason) = self.budget.exhaustion.clone() {
                for slot in 0..self.slots.len() {
                    if let Some(worker) = self.slots[slot].as_mut() {
                        if let Some((index, request, _)) = worker.busy.take() {
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
                        .map(|(index, request)| (index, exhausted(&request, &reason))),
                );
                break;
            }
            if pending.is_empty() {
                if self.budget.active > 0 {
                    thread::sleep(Duration::from_millis(10));
                }
                continue;
            }
            let active_pids: Vec<_> = self
                .slots
                .iter()
                .flatten()
                .filter(|worker| worker.busy.is_some())
                .map(|worker| worker.child.id())
                .collect();
            let capacity = self.budget.capacity(&active_pids);
            if capacity > 0 && (!serial || self.budget.active == 0) {
                let slot = self
                    .slots
                    .iter()
                    .position(|slot| slot.as_ref().is_none_or(|worker| worker.busy.is_none()))
                    .expect("capacity has slot");
                let (index, request) = pending.pop_front().unwrap();
                if self.slots[slot].is_none() {
                    match Worker::start(self.options) {
                        Ok(worker) => self.slots[slot] = Some(worker),
                        Err(error) => {
                            completed.push((index, infrastructure(&request, error)));
                            continue;
                        }
                    }
                }
                let worker = self.slots[slot].as_mut().unwrap();
                match worker.send(index, request.clone()) {
                    Ok(()) => {
                        self.budget.active += 1;
                        self.budget.max_active_jobs =
                            self.budget.max_active_jobs.max(self.budget.active);
                    }
                    Err(error) => {
                        completed.push((index, infrastructure(&request, error)));
                        worker.abort();
                        self.slots[slot] = None;
                    }
                }
                // Charge the measured cost for every admitted in-flight job,
                // including processes not visible yet at this sampling instant.
                paused = false;
                idle_pressure_since = None;
            } else if capacity == 0 {
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
                    self.close_slot(index);
                    self.budget.recycled_idle_workers += 1;
                }
                self.sample(true);
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
        for slot in 0..self.slots.len() {
            self.close_slot(slot);
        }
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
        serde_json::to_value(&self.budget).expect("resource report")
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
                let cleanup = session.as_mut().map_or(Ok(()), Session::finish);
                if let Some(session) = &session {
                    let events = session.directory.path().join("events.jsonl");
                    if let Ok(bytes) = fs::read(events) {
                        let mut modes = BTreeSet::new();
                        for line in bytes.split(|byte| *byte == b'\n') {
                            if let Ok(event) = serde_json::from_slice::<serde_json::Value>(line)
                                && let Some(mode) =
                                    event.get("reuse").and_then(|value| value.as_str())
                            {
                                modes.insert(mode.to_owned());
                            }
                        }
                        diagnostics.extend(
                            modes
                                .into_iter()
                                .map(|mode| format!("adapter observed reuse boundary: {mode}")),
                        );
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
        let response = execute_local(&options, &request, &mut session);
        cache::atomic(
            &directory.join(format!("response-{sequence}.json")),
            &response,
        )?;
        let _ = fs::remove_file(path);
    }
    unreachable!()
}
