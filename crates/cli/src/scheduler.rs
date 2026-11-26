//! A central event loop owns admission. Each slot is a Rust subreaper process,
//! so detached grandchildren can never be mistaken for a sibling slot's work.
use super::trace;
use super::*;
use std::collections::{BTreeSet, VecDeque};
use std::os::fd::{AsRawFd, FromRawFd, RawFd};
use std::os::unix::net::UnixStream;
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

/// Scheduler <-> slot frames on a private socket pair: a 4-byte big-endian
/// length, then a tag byte and a JSON payload. They are IPC, not durable
/// state, so nothing is synced to disk; `options.json` stays a file because
/// it is written once when the slot starts, not per job.
const RUN: u8 = b'R';
/// A RUN whose lanes repeat the batch's resident modules (the same shared set,
/// which every lane carries so the adapter can validate them identical): the
/// lanes' copies are left out of the frame and restored by the slot.
const RUN_LANES: u8 = b'L';
const WARM: u8 = b'W';
const STOP: u8 = b'S';
const RESPONSE: u8 = b'A';
const SHUTDOWN: u8 = b'D';
/// Requests keep the adapter's 8 MiB limit.
const MAX_REQUEST: usize = 8 * 1024 * 1024;

/// The frame for a job. Lane batches whose every lane holds the very same
/// resident set (by identity, never by comparing content) travel with the set
/// once instead of once more per lane; nothing else changes on the wire.
/// Identity of a job for timeline events: its slot and, for lane batches, the lane mutants.
fn trace_job(slot: usize, index: usize, request: &Request) -> serde_json::Value {
    let id = |request: &Request| request.mutation.as_ref().map(|m| m.id.clone());
    serde_json::json!({
        "slot": slot,
        "job": (index != WARM_JOB).then_some(index),
        "kind": request.kind,
        "mutant": id(request),
        "lanes": request.lanes.as_ref().map(|lanes| lanes.iter().map(id).collect::<Vec<_>>()),
    })
}

fn encode_request(index: usize, request: &Request) -> Result<(u8, Vec<u8>), String> {
    if index == WARM_JOB {
        return Ok((
            WARM,
            serde_json::to_vec(request).map_err(|e| e.to_string())?,
        ));
    }
    let Some(wire) = without_duplicate_lane_residents(request) else {
        return Ok((RUN, serde_json::to_vec(request).map_err(|e| e.to_string())?));
    };
    Ok((
        RUN_LANES,
        serde_json::to_vec(&wire).map_err(|e| e.to_string())?,
    ))
}

/// Inverse of `encode_request` for `RUN_LANES`: every lane takes the batch's
/// resident set and resolves a mutation reference against it. A frame whose
/// lanes already carry a resident set, or reference an absent module, is malformed.
fn restore_lane_resident(request: &mut Request) -> Result<(), String> {
    let (Some(set), Some(lanes)) = (&request.resident, &mut request.lanes) else {
        return Err("malformed scheduler request".into());
    };
    for lane in lanes {
        if lane.resident.replace(set.clone()).is_some() {
            return Err("malformed scheduler request".into());
        }
        if let Some(mutation) = &mut lane.mutation
            && is_resident_reference(mutation)
        {
            let module = set
                .iter()
                .find(|module| module.file == mutation.file)
                .ok_or("malformed scheduler request")?;
            mutation.code = module.code.clone();
            mutation.map = module.map.clone();
        }
    }
    Ok(())
}

fn frame(tag: u8, payload: &[u8]) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(payload.len() + 5);
    bytes.extend_from_slice(&((payload.len() + 1) as u32).to_be_bytes());
    bytes.push(tag);
    bytes.extend_from_slice(payload);
    bytes
}

/// Splits one complete frame off `buffer`, refusing any longer than `limit`
/// payload bytes before it is read in full.
fn take_frame(buffer: &mut Vec<u8>, limit: usize) -> Result<Option<(u8, Vec<u8>)>, String> {
    let Some(header) = buffer.first_chunk::<4>() else {
        return Ok(None);
    };
    let length = u32::from_be_bytes(*header) as usize;
    if length == 0 {
        return Err("malformed scheduler worker message".into());
    }
    if length - 1 > limit {
        return Err("adapter response exceeds 1 MiB limit".into());
    }
    if buffer.len() < 4 + length {
        return Ok(None);
    }
    let tag = buffer[4];
    let payload = buffer[5..4 + length].to_vec();
    buffer.drain(..4 + length);
    Ok(Some((tag, payload)))
}

/// Writes STOP to the scheduler's non-blocking end within `deadline`.
fn write_frame(channel: &mut UnixStream, bytes: &[u8], deadline: Instant) -> std::io::Result<()> {
    let mut written = 0;
    while written < bytes.len() {
        if INTERRUPTED.load(Ordering::Relaxed) {
            return Err(std::io::ErrorKind::Interrupted.into());
        }
        match channel.write(&bytes[written..]) {
            Ok(0) => return Err(std::io::ErrorKind::WriteZero.into()),
            Ok(count) => written += count,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                if Instant::now() >= deadline {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        "scheduler worker did not accept its request",
                    ));
                }
                wait::writable(
                    channel.as_raw_fd(),
                    deadline.min(Instant::now() + Duration::from_millis(50)),
                )?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

/// A peer that has gone away is reported by `Worker::ready` as an exit,
/// exactly as when requests were files it never read.
fn peer_gone(error: &std::io::Error) -> bool {
    matches!(
        error.kind(),
        std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::ConnectionReset
    )
}

/// Spawns `command` holding the other end of a fresh socket pair at its
/// inherited descriptor number, which is appended as the last argument.
fn spawn_with_channel(command: &mut Command) -> Result<(std::process::Child, UnixStream), String> {
    use std::os::unix::process::CommandExt;
    let (ours, theirs) = UnixStream::pair().map_err(|e| format!("scheduler channel: {e}"))?;
    let fd = theirs.as_raw_fd();
    command.arg(fd.to_string());
    // Only this child keeps its end across exec; every other descriptor of
    // ours, including other slots' channels, stays close-on-exec.
    unsafe {
        command.pre_exec(move || {
            if libc::fcntl(fd, libc::F_SETFD, 0) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let child = command
        .spawn()
        .map_err(|e| format!("could not start scheduler worker: {e}"))?;
    drop(theirs);
    ours.set_nonblocking(true)
        .map_err(|e| format!("scheduler channel: {e}"))?;
    Ok((child, ours))
}

struct Worker {
    /// Holds `options.json` and the slot's compile cache for its lifetime.
    _directory: tempfile::TempDir,
    child: std::process::Child,
    channel: UnixStream,
    received: Vec<u8>,
    pending: Vec<u8>,
    /// The worker closed its end, which it does only by exiting.
    hung_up: bool,
    owned: BTreeMap<u32, u64>,
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
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let (child, channel) = spawn_with_channel(&mut command)?;
        Ok(Self::with(directory, child, channel))
    }
    fn with(directory: tempfile::TempDir, child: std::process::Child, channel: UnixStream) -> Self {
        Self {
            _directory: directory,
            child,
            channel,
            received: vec![],
            pending: vec![],
            hung_up: false,
            owned: BTreeMap::new(),
            busy: None,
            finished: false,
        }
    }
    fn snapshot(&mut self) {
        self.owned.extend(descendants(self.child.id()));
    }
    fn send(&mut self, index: usize, request: Request) -> Result<(), String> {
        assert!(self.busy.is_none() && self.pending.is_empty());
        let (tag, payload) = encode_request(index, &request)?;
        self.pending = frame(tag, &payload);
        self.busy = Some((index, request, Instant::now()));
        self.flush().map_err(|error| error.to_string())
    }
    /// Advances the outgoing frame without ever waiting for socket capacity.
    fn flush(&mut self) -> std::io::Result<()> {
        let mut written = 0;
        while written < self.pending.len() {
            match self.channel.write(&self.pending[written..]) {
                Ok(0) => return Err(std::io::ErrorKind::WriteZero.into()),
                Ok(count) => written += count,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => break,
                Err(error) if peer_gone(&error) => {
                    self.hung_up = true;
                    break;
                }
                Err(error) => return Err(error),
            }
        }
        self.pending.drain(..written);
        Ok(())
    }
    /// Reads what the worker has sent, at most one frame ahead, and returns
    /// the next complete frame.
    fn receive(&mut self) -> Result<Option<(u8, Vec<u8>)>, String> {
        let mut chunk = [0u8; 65536];
        loop {
            if let Some(frame) = take_frame(&mut self.received, RESPONSE_LIMIT)? {
                return Ok(Some(frame));
            }
            if self.hung_up {
                return Ok(None);
            }
            match self.channel.read(&mut chunk) {
                Ok(0) => self.hung_up = true,
                Ok(count) => self.received.extend_from_slice(&chunk[..count]),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => return Ok(None),
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                Err(error) if peer_gone(&error) => self.hung_up = true,
                Err(error) => return Err(format!("scheduler worker channel failed: {error}")),
            }
        }
    }
    fn ready(&mut self) -> Option<Result<Response, String>> {
        self.busy.as_ref()?;
        if !self.hung_up
            && let Err(error) = self.flush()
        {
            return Some(Err(format!("scheduler worker channel failed: {error}")));
        }
        let received = self.receive();
        let (_, request, started) = self.busy.as_ref()?;
        match received {
            Ok(Some((RESPONSE, payload))) => return Some(parse_response(&payload, request)),
            Ok(Some(_)) => return Some(Err("malformed scheduler worker message".into())),
            Err(error) => return Some(Err(error)),
            Ok(None) => {}
        }
        if self.hung_up {
            // The kernel closes an exiting worker's descriptors just before
            // it becomes reapable, so this wait is short and bounded.
            let deadline = Instant::now() + Duration::from_secs(1);
            return Some(match wait::child_exit(&mut self.child, deadline) {
                Ok(Some(status)) => {
                    Err(format!("scheduler worker exited before response: {status}"))
                }
                Ok(None) => Err("scheduler worker exited before response: channel closed".into()),
                Err(error) => Err(format!("scheduler worker wait failed: {error}")),
            });
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
        if started.elapsed() > Self::limit(request) {
            return Some(Err("scheduler worker exceeded controller deadline".into()));
        }
        None
    }
    fn limit(request: &Request) -> Duration {
        Duration::from_millis(request.deadline_ms + 16_000)
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
    lanes_ran: bool,
    /// Lanes re-run on an ordinary worker after their shared batch failed.
    lanes_redispatched: u64,
    zygote: ZygoteWatchdog,
}
/// Guards `--zygote` runs against a deadlocked forked worker, which would look
/// like a timeout and could count as a detection. A Timeout that could be a
/// deadlock is rerun once with the zygote bypassed; if the rerun gives another
/// outcome, the zygote is disabled for the rest of the run. A timeout whose
/// worker the adapter saw on the CPU, or answering with the test runner's own
/// timeout, is the mutant's own and keeps its verdict without a rerun; so does
/// any timeout from an adapter that reports nothing about it, the other way
/// round: no evidence means a rerun.
#[derive(Default)]
struct ZygoteWatchdog {
    disabled: bool,
    reruns: u64,
    /// Timeouts left alone because the adapter saw the worker alive and working
    /// (see [`TimeoutKind`]): a deadlocked zygote child is neither.
    genuine: u64,
    /// Mutant indexes whose single bypassed rerun is queued or running.
    rerunning: BTreeSet<usize>,
}
/// Splits jobs into those that can be lanes of a shared controller (they carry the
/// resident module of their file) and those that cannot (see [`Scheduler::run_jobs`]).
type Jobs = Vec<(usize, Request)>;
fn split_unshared(jobs: Jobs) -> (Jobs, Jobs) {
    jobs.into_iter()
        .partition(|(_, job)| job.resident.is_some())
}
/// Whether a job still fits the request limit once wrapped as a one-lane batch.
// Leaves room for the batch's own, slightly longer, deadline field.
const MAX_LANE_BATCH: usize = 8 * 1024 * 1024 - 64;
/// Time a batch has to report once a lane is stopped at the job deadline.
pub(crate) const LANE_REPORT_MS: u64 = 1000;
/// Experimental `--rolling-lanes`: a batch holds at most this many lanes per
/// slot. The controller refills a slot as soon as its lane finishes, so the batch
/// barrier only remains at batch ends, and the response stays well under its limit.
const ROLLING_MAX_ROUNDS: usize = 32;
/// The adapter's request validation bounds job deadlines.
const MAX_DEADLINE_MS: u64 = 2_147_483_647;

/// The deadline of a lane batch: one that cannot fire before its last lane could
/// still be running.
///
/// Each lane has its own deadline from the moment it starts, and then a moment to
/// report. Lanes of a rolling batch run one after another in each slot, so the
/// batch may take, for every round of `width` lanes in request order, as long as
/// that round's slowest lane. That is a bound however the lanes happen to pair up
/// with slots: a lane starts no later than the end of the round before it.
fn batch_deadline_ms(batch: &[(usize, Request)], width: usize) -> u64 {
    batch
        .chunks(width.max(1))
        .map(|round| {
            let slowest = round.iter().map(|(_, lane)| lane.deadline_ms).max();
            slowest.unwrap_or(0).saturating_add(LANE_REPORT_MS)
        })
        .fold(0u64, u64::saturating_add)
        .min(MAX_DEADLINE_MS)
}

/// A lane batch of `batch`, run by `width` lanes at a time. A batch of more
/// lanes than the width is rolling: the controller keeps `width` lanes busy
/// and starts the next one as each finishes.
fn lane_batch(batch: &[(usize, Request)], width: usize) -> Request {
    let mut request = batch[0].1.clone();
    // The adapter keeps this many lanes, so a smaller batch reuses its context.
    let rolling = batch.len() > width;
    request.workers = Some(
        if rolling {
            width
        } else {
            width.max(batch.len())
        }
        .min(1024),
    );
    request.deadline_ms = batch_deadline_ms(batch, width);
    request.lanes = Some(batch.iter().map(|(_, r)| r.clone()).collect());
    request
}

/// Exact encoded sizes of requests that share their resident modules.
///
/// Every request of a run repeats the same resident module set (megabytes),
/// and lane assembly measures each candidate. Serializing that set once per
/// request, per lane and per batch dominated assembly, so its size is taken
/// once per set and added arithmetically: a present `resident` member costs
/// one comma, its key and its value, and nothing otherwise.
#[derive(Default)]
struct RequestSizes {
    /// Holds the set, so its address cannot be reused while cached.
    resident: Option<(Arc<Vec<Mutation>>, usize)>,
}
impl RequestSizes {
    const RESIDENT_MEMBER: usize = 1 + "\"resident\":".len();
    /// `"lanes":[` + `]` and the comma that follows the lanes member.
    const LANES_MEMBER: usize = "\"lanes\":[".len() + 1 + 1;

    fn resident_len(&mut self, set: &Arc<Vec<Mutation>>) -> usize {
        if let Some((cached, len)) = &self.resident
            && Arc::ptr_eq(cached, set)
        {
            return *len;
        }
        let len = encoded_len(&**set);
        self.resident = Some((set.clone(), len));
        len
    }
    /// What `encoded_len(request)` would return.
    fn request_len(&mut self, request: &mut Request) -> usize {
        let Some(set) = request.resident.take() else {
            return encoded_len(&*request);
        };
        let rest = encoded_len(&*request);
        request.resident = Some(set.clone());
        rest.saturating_add(Self::RESIDENT_MEMBER)
            .saturating_add(self.resident_len(&set))
    }
    /// Size of `request` as a lane of a batch whose resident set is `set`: as
    /// `without_duplicate_lane_residents` sends it, without the shared resident
    /// set and with its mutation reduced to a reference to the resident module.
    fn lane_len(&mut self, request: &mut Request, set: Option<&Arc<Vec<Mutation>>>) -> usize {
        let shared = matches!((&request.resident, set), (Some(a), Some(b)) if Arc::ptr_eq(a, b));
        if !shared {
            return self.request_len(request);
        }
        let resident = request.resident.take();
        let original = match &request.mutation {
            Some(m)
                if lane_mutation_matches_resident(m, resident.as_deref().map_or(&[], |r| r)) =>
            {
                let reference = resident_reference(m);
                Some(request.mutation.replace(reference))
            }
            _ => None,
        };
        let len = encoded_len(&*request);
        if let Some(original) = original {
            request.mutation = original;
        }
        request.resident = resident;
        len
    }
    /// Size of a batch of just this request, as it travels: the resident set
    /// once, the lane without it.
    fn batch_len(&mut self, request: &mut Request) -> usize {
        let top = self.request_len(request);
        let set = request.resident.clone();
        let lane = self.lane_len(request, set.as_ref());
        top.saturating_add(Self::LANES_MEMBER).saturating_add(lane)
    }
    /// Whether two requests carry the very same resident modules (one shared
    /// allocation, which is what lets a batch send them once) or none.
    fn same_resident(&mut self, a: &Request, b: &Request) -> bool {
        match (&a.resident, &b.resident) {
            (None, None) => true,
            (Some(a), Some(b)) => Arc::ptr_eq(a, b),
            _ => false,
        }
    }
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
            lanes_ran: false,
            lanes_redispatched: 0,
            zygote: ZygoteWatchdog::default(),
        })
    }
    pub fn prepare_spares(&mut self, request: &Request) {
        if !(self.options.shared_controller && self.slots.len() >= 2)
            && self.options.execution == Execution::Persistent
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
            if !worker.pending.is_empty() {
                // A partial request cannot be followed by STOP on this stream.
                failed_signals.push(worker);
                continue;
            }
            let stop_by = Instant::now() + Duration::from_secs(7);
            if let Err(error) = write_frame(&mut worker.channel, &frame(STOP, b""), stop_by)
                && !peer_gone(&error)
            {
                self.cleanup_ok = false;
                self.diagnostics
                    .push(format!("scheduler shutdown failed: {error}"));
                failed_signals.push(worker);
                continue;
            }
            stopping.push((worker, stop_by));
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
                        // An exited worker's frames are all buffered in the socket.
                        if let Ok(Some((SHUTDOWN, value))) = worker.receive()
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
                // Wake as soon as a slot exits. Ownership snapshots keep their
                // 10 ms cadence, so descendants started late in shutdown are
                // still seen before their slot exits and they are reparented.
                let until = stopping
                    .iter()
                    .map(|(_, deadline)| *deadline)
                    .fold(Instant::now() + Duration::from_millis(10), Instant::min);
                wait::any_exit(
                    stopping.iter().map(|(worker, _)| worker.child.id()),
                    until,
                    Duration::from_millis(10),
                    true,
                );
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
        // A mutant planned as a lane of the shared controller has the lane's small
        // constant; run on an ordinary worker (a rerun, a fallback) it also pays for
        // a cold controller and the worker's fork, so it gets the difference back.
        if self.options.lanes_expected() {
            let extra = self.options.deadlines.cold_extra_ms();
            for (_, job) in pending.iter_mut() {
                if job.kind == "mutant" && job.lanes.is_none() {
                    job.deadline_ms = job.deadline_ms.saturating_add(extra).min(MAX_DEADLINE_MS);
                }
            }
        }
        while !pending.is_empty() || self.budget.active > 0 || !self.spares.is_empty() {
            if INTERRUPTED.load(Ordering::Relaxed) {
                self.budget.exhausted("run interrupted by SIGINT/SIGTERM");
            }
            self.sample(false);
            // Sample before accepting every ready response. Resource failures
            // override test failures and can never contribute a mutation kill.
            for slot in 0..self.slots.len() {
                let ready = self.slots[slot].as_mut().and_then(Worker::ready);
                if let Some(result) = &ready {
                    trace::event("reply", || {
                        let (index, request, _) =
                            self.slots[slot].as_ref().unwrap().busy.as_ref().unwrap();
                        let mut job = trace_job(slot, *index, request);
                        job["ok"] = result.is_ok().into();
                        if let Ok(response) = &result {
                            job["outcome"] = serde_json::json!(response.outcome);
                            job["timeoutKind"] = serde_json::json!(response.timeout_kind);
                        }
                        job
                    });
                }
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
                    trace::event("slot_idle_begin", || trace_job(slot, index, &request));
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
                    } else if self.settle_rerun(index, &request, &response) {
                        // Same admission and deadline as any job, and next in line.
                        let mut again = request.clone();
                        again.bypass_zygote = Some(true);
                        self.zygote.reruns += 1;
                        self.zygote.rerunning.insert(index);
                        pending.push_front((index, again));
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
                    self.wait_for_events();
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
                    // The baseline controller's tree holds that many forks at once.
                    self.budget.lanes_per_worker = request.workers.unwrap_or(1).max(1) as u64;
                }
                let existed = self.slots[slot].is_some();
                trace::event("dispatch_begin", || trace_job(slot, index, &request));
                if existed {
                    trace::event("slot_idle_end", || trace_job(slot, index, &request));
                }
                if self.slots[slot].is_none() {
                    match Worker::start(self.options) {
                        Ok(worker) => {
                            trace::event(
                                "slot_started",
                                || serde_json::json!({ "slot": slot, "slotPid": worker.child.id() }),
                            );
                            self.slots[slot] = Some(worker)
                        }
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
                if self.zygote.disabled && request.kind == "mutant" {
                    request.bypass_zygote = Some(true);
                    for lane in request.lanes.iter_mut().flatten() {
                        lane.bypass_zygote = Some(true);
                    }
                }
                let worker = self.slots[slot].as_mut().unwrap();
                match worker.send(index, request.clone()) {
                    Ok(()) => {
                        trace::event("dispatch_sent", || trace_job(slot, index, &request));
                        if index != WARM_JOB {
                            self.budget.active += 1;
                        }
                        self.budget.max_active_jobs = self.budget.max_active_jobs.max(
                            self.budget.active
                                + request.lanes.as_ref().map_or(0, |lanes| {
                                    lanes
                                        .len()
                                        .min(request.workers.unwrap_or(usize::MAX))
                                        .saturating_sub(1)
                                }),
                        );
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
                self.wait_for_events();
            }
        }
        completed
    }
    /// Zygote watchdog bookkeeping for a mutant response. Returns whether the
    /// mutant timed out under the zygote and must be rerun once without it.
    /// A finished rerun disables the zygote when it disagrees with the timeout.
    fn settle_rerun(&mut self, index: usize, request: &Request, response: &Response) -> bool {
        if self.zygote.rerunning.remove(&index) {
            if response.outcome != TestOutcome::Timeout && !self.zygote.disabled {
                self.zygote.disabled = true;
                self.diagnostics.push(
                    "zygote worker timed out where a normal worker did not; zygote disabled for the rest of the run"
                        .into(),
                );
            }
            return false;
        }
        request.kind == "mutant" && request.lanes.is_none() && self.watching(response)
    }
    /// Whether a timed-out mutant is a watchdog case: zygote on and not yet disabled,
    /// and nothing shows the worker was alive and working when it timed out.
    fn watching(&mut self, response: &Response) -> bool {
        if !(self.options.zygote
            && !self.zygote.disabled
            && response.outcome == TestOutcome::Timeout)
        {
            return false;
        }
        if response.timeout_kind.is_some_and(TimeoutKind::is_genuine) {
            self.zygote.genuine += 1;
            return false;
        }
        true
    }
    /// Rerun timed-out lane jobs one by one with the zygote bypassed. The lane
    /// controllers give way to ordinary ones, and each rerun goes through the
    /// normal admission and per-job deadline.
    fn rerun_timeouts(&mut self, jobs: Vec<(usize, Request)>) -> Vec<(usize, Response)> {
        self.retire_lane_controllers();
        let mut pending = VecDeque::new();
        for (index, mut job) in jobs {
            job.bypass_zygote = Some(true);
            self.zygote.reruns += 1;
            self.zygote.rerunning.insert(index);
            pending.push_back((index, job));
        }
        self.dispatch(pending, false)
    }
    /// What the dispatch loop waits for: input, pending output or a hang-up on any busy
    /// slot's channel, until the next resource sample or slot deadline.
    fn events(&self) -> (Vec<libc::pollfd>, Instant) {
        let mut until = self.budget.next_sample_at();
        let mut fds = vec![];
        for worker in self.slots.iter().flatten() {
            if let Some((_, request, started)) = &worker.busy {
                if worker.hung_up {
                    // `ready` reports this on the next pass.
                    return (vec![], Instant::now());
                }
                fds.push(libc::pollfd {
                    fd: worker.channel.as_raw_fd(),
                    events: libc::POLLIN
                        | if worker.pending.is_empty() {
                            0
                        } else {
                            libc::POLLOUT
                        },
                    revents: 0,
                });
                until = until.min(*started + Worker::limit(request) + Duration::from_millis(1));
            }
        }
        (fds, until)
    }
    /// Blocks until a job event or the next sample, instead of a fixed sleep:
    /// a response is accepted as soon as it arrives.
    fn wait_for_events(&mut self) {
        let (mut fds, until) = self.events();
        if wait::poll(&mut fds, until).is_err() {
            thread::sleep(Duration::from_millis(10));
        }
        for worker in self.slots.iter_mut().flatten() {
            if !worker.pending.is_empty()
                && fds.iter().any(|fd| {
                    fd.fd == worker.channel.as_raw_fd()
                        && fd.revents & (libc::POLLHUP | libc::POLLERR) != 0
                })
            {
                worker.hung_up = true;
            }
        }
        // `ready` flushes writable slots on the next dispatch pass, before
        // checking responses and deadlines. Empty buffers drop POLLOUT interest.
    }
    pub fn execute(&mut self, request: &Request) -> Response {
        self.dispatch(VecDeque::from([(0, request.clone())]), true)
            .pop()
            .expect("one serial result")
            .1
    }
    /// A controller that ran lanes has given up its ordinary Vitest instance.
    fn retire_lane_controllers(&mut self) {
        if std::mem::take(&mut self.lanes_ran) {
            self.budget.lanes_per_worker = 1;
            self.close_slots(&(0..self.slots.len()).collect::<Vec<_>>());
        }
    }
    pub fn run_jobs(&mut self, jobs: Vec<(usize, Request)>, results: &mut [MutantResult]) {
        // Mutant jobs are priced per job until a lane batch says otherwise.
        self.budget.lanes_per_worker = 1;
        if self.options.shared_controller && self.slots.len() >= 2 {
            // A lane needs the resident module of its file. A mutant without one
            // (a static fresh mutant, from a file that cannot be switched) cannot
            // be a lane, and one such job in a batch would fail the whole batch
            // and move the rest of the run off the shared controller. It runs on
            // an ordinary worker instead, before the lanes start.
            let (jobs, unshared) = split_unshared(jobs);
            if !unshared.is_empty() {
                trace::event(
                    "unshared_jobs",
                    || serde_json::json!({ "count": unshared.len() }),
                );
                for (index, response) in self.dispatch(unshared.into(), false) {
                    results[index].outcome = response.mutant_outcome();
                    results[index].tests = response.tests;
                    results[index].errors = response.errors;
                }
                self.budget.lanes_per_worker = 1;
            }
            // Fixed at the first batch, so that later batches reuse the same lanes.
            let total = jobs.len();
            let mut width = None;
            let mut pending: VecDeque<_> = jobs.into();
            let mut sizes = RequestSizes::default();
            let mut failed_batches = 0u32;
            while let Some(first) = pending.pop_front() {
                // A request too large to wrap in a lane batch still runs normally. What
                // counts is the frame the batch travels in: the resident set once, then
                // the lane without it. Pricing the expanded job instead (the set twice)
                // sent every job of a run with more than 4 MiB of resident modules down
                // the ordinary path, one controller per worker.
                let mut first = first;
                if sizes.batch_len(&mut first.1) > MAX_LANE_BATCH {
                    self.retire_lane_controllers();
                    for (index, response) in self.dispatch(VecDeque::from([first]), false) {
                        results[index].outcome = response.mutant_outcome();
                        results[index].tests = response.tests;
                        results[index].errors = response.errors;
                    }
                    continue;
                }
                let mut encoded = sizes.batch_len(&mut first.1);
                let mut batch = vec![first];
                // Completion already took a strict broad sample before accepting
                // the previous result. Reuse it while fresh, just as ordinary
                // admission does, instead of walking the same idle tree twice.
                // The fork limit still reserves headroom for every lane; dispatch
                // rechecks exhaustion/capacity and promotes counter samples to exact.
                self.sample_with(|budget, pids| budget.sample_admission(pids));
                let pids: Vec<_> = self.slots.iter().flatten().map(|w| w.child.id()).collect();
                let capacity = self.budget.capacity(&pids);
                let limit = self.budget.baseline_fork_limit(capacity, &pids);
                let width = *width.get_or_insert(self.slots.len().min(total).min(limit));
                // Rolling needs all `width` lanes' worth of admission headroom;
                // under memory pressure the batch is smaller than the width and
                // runs without a barrier-free refill, as ever.
                let rolling = self.options.rolling_lanes && limit >= width;
                let capacity_lanes = if rolling {
                    width * ROLLING_MAX_ROUNDS
                } else {
                    limit.min(width)
                };
                let mut reason = "capacity";
                while batch.len() < capacity_lanes {
                    let Some(next) = pending.front_mut() else {
                        reason = "queue_empty";
                        break;
                    };
                    if !sizes.same_resident(&next.1, &batch[0].1) {
                        reason = "resident_mismatch";
                        break;
                    }
                    let set = batch[0].1.resident.clone();
                    let lane = sizes.lane_len(&mut next.1, set.as_ref()) + 1;
                    if encoded + lane > MAX_LANE_BATCH {
                        reason = "bytes";
                        break;
                    }
                    encoded += lane;
                    batch.push(pending.pop_front().unwrap());
                }
                trace::event("batch_built", || {
                    serde_json::json!({
                        "lanes": batch.len(), "width": width, "rolling": rolling,
                        "capacity_lanes": capacity_lanes, "limit": limit,
                        "encoded": encoded, "reason": reason, "pending": pending.len(),
                    })
                });
                // This controller's tree holds `width` lanes: price it per lane.
                self.budget.lanes_per_worker = width as u64;
                let response = self.execute(&lane_batch(&batch, width));
                self.lanes_ran = true;
                // Unsupported projects run with one controller per worker instead.
                if response.lanes.is_none()
                    && response
                        .errors
                        .iter()
                        .any(|error| error.contains("Shared lanes require"))
                {
                    self.diagnostics.push(format!(
                        "shared controller unavailable, using one controller per worker: {}",
                        response.errors.join("; ")
                    ));
                    self.budget.lanes_per_worker = 1;
                    let mut rest: Vec<_> = batch;
                    rest.extend(pending);
                    for (index, response) in self.dispatch(rest.into(), false) {
                        results[index].outcome = response.mutant_outcome();
                        results[index].tests = response.tests;
                        results[index].errors = response.errors;
                    }
                    return;
                }
                let responses = response.lanes;
                let mut timed_out = vec![];
                let mut missing = vec![];
                for (offset, (index, job)) in batch.into_iter().enumerate() {
                    let Some(item) = responses.as_ref().and_then(|r| r.get(offset)).cloned() else {
                        missing.push((index, job));
                        continue;
                    };
                    if self.watching(&item) {
                        timed_out.push((index, job));
                        continue;
                    }
                    results[index].outcome = item.mutant_outcome();
                    results[index].tests = item.tests;
                    results[index].errors = item.errors;
                }
                // Lanes with their own result keep it. The others lost their
                // controller (or its report), not a verdict: each runs once more
                // on an ordinary worker before it is reported as a failure.
                let retry = !missing.is_empty()
                    && response.outcome != TestOutcome::ResourceExhausted
                    && !INTERRUPTED.load(Ordering::Relaxed);
                if !missing.is_empty() && !retry {
                    for (index, job) in missing {
                        let item = if response.outcome == TestOutcome::ResourceExhausted {
                            exhausted(&job, &response.errors.join("; "))
                        } else {
                            infrastructure(
                                &job,
                                format!("Shared batch failed: {}", response.errors.join("; ")),
                            )
                        };
                        results[index].outcome = item.mutant_outcome();
                        results[index].tests = item.tests;
                        results[index].errors = item.errors;
                    }
                } else if retry {
                    self.lanes_redispatched += missing.len() as u64;
                    self.retire_lane_controllers();
                    for (index, response) in self.dispatch(missing.into(), false) {
                        results[index].outcome = response.mutant_outcome();
                        results[index].tests = response.tests;
                        results[index].errors = response.errors;
                    }
                    failed_batches += 1;
                    if failed_batches >= 2 && !pending.is_empty() {
                        // A controller that keeps failing would cost every batch twice.
                        self.diagnostics.push(
                            "shared controller failed twice in a row, using one controller per worker for the rest"
                                .into(),
                        );
                        for (index, response) in self.dispatch(std::mem::take(&mut pending), false)
                        {
                            results[index].outcome = response.mutant_outcome();
                            results[index].tests = response.tests;
                            results[index].errors = response.errors;
                        }
                    }
                } else {
                    failed_batches = 0;
                }
                if !timed_out.is_empty() {
                    trace::event(
                        "lane_timeouts_rerun_begin",
                        || serde_json::json!({ "count": timed_out.len() }),
                    );
                    let reruns = self.rerun_timeouts(timed_out);
                    trace::event(
                        "lane_timeouts_rerun_end",
                        || serde_json::json!({ "count": reruns.len() }),
                    );
                    for (index, response) in reruns {
                        results[index].outcome = response.mutant_outcome();
                        results[index].tests = response.tests;
                        results[index].errors = response.errors;
                    }
                }
            }
            return;
        }
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
            wait::any_exit(
                remaining.keys().copied(),
                deadline,
                Duration::from_millis(5),
                true,
            );
        }
        self.sample(true);
        if let Some(reason) = &self.budget.exhaustion {
            self.diagnostics
                .push(format!("resource admission stopped: {reason}"));
        }
        if self.lanes_redispatched > 0 {
            self.diagnostics.push(format!(
                "shared lanes: {} lanes re-dispatched after a failed batch",
                self.lanes_redispatched
            ));
        }
        if self.zygote.reruns > 0 {
            self.diagnostics.push(format!(
                "zygote watchdog: {} timeout reruns",
                self.zygote.reruns
            ));
        }
        if self.zygote.genuine > 0 {
            self.diagnostics.push(format!(
                "zygote watchdog: {} timeouts kept without a rerun (worker was running, not stalled)",
                self.zygote.genuine
            ));
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
    let mut args = env::args().skip(2);
    let directory = PathBuf::from(args.next().ok_or("missing internal worker directory")?);
    let fd: RawFd = args
        .next()
        .and_then(|fd| fd.parse().ok())
        .ok_or("missing internal worker channel")?;
    // Nothing this slot starts may hold the channel: then the slot's exit
    // alone hangs up the scheduler's end, which is how a crash is noticed.
    if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0 {
        return Err(format!(
            "internal worker channel: {}",
            std::io::Error::last_os_error()
        ));
    }
    let channel = unsafe { UnixStream::from_raw_fd(fd) };
    let options: Options = serde_json::from_slice(
        &fs::read(directory.join("options.json")).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    serve(channel, &options)
}

/// Reads one request frame, blocking. `None` means the scheduler hung up.
fn read_request(channel: &mut UnixStream) -> Result<Option<(u8, Vec<u8>)>, String> {
    let mut header = [0u8; 4];
    match channel.read_exact(&mut header) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof || peer_gone(&error) => {
            return Ok(None);
        }
        Err(error) => return Err(error.to_string()),
    }
    let length = u32::from_be_bytes(header) as usize;
    if length == 0 {
        return Err("malformed scheduler request".into());
    }
    if length - 1 > MAX_REQUEST {
        return Err("internal request exceeds 8 MiB".into());
    }
    let mut body = vec![0u8; length];
    channel.read_exact(&mut body).map_err(|e| e.to_string())?;
    let tag = body.remove(0);
    Ok(Some((tag, body)))
}

fn serve(mut channel: UnixStream, options: &Options) -> Result<(), String> {
    let mut session = None;
    loop {
        let (tag, payload) = match read_request(&mut channel)? {
            Some((STOP, _)) | None => {
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
                        diagnostics.extend(coordinator.ineligible.summary());
                    }
                }
                if let Err(error) = &cleanup {
                    diagnostics.push(error.clone());
                }
                // A scheduler that hung up still gets verified cleanup above;
                // there is just no one left to read the diagnostics.
                let bytes = serde_json::to_vec(&diagnostics).map_err(|e| e.to_string())?;
                match channel.write_all(&frame(SHUTDOWN, &bytes)) {
                    Err(error) if !peer_gone(&error) => return Err(error.to_string()),
                    _ => {}
                }
                drop(session);
                return cleanup;
            }
            Some(frame) => frame,
        };
        let mut request: Request = serde_json::from_slice(&payload).map_err(|e| e.to_string())?;
        if tag == RUN_LANES {
            restore_lane_resident(&mut request)?;
        }
        let response = match tag {
            WARM => {
                if session.is_none() {
                    session = coordinator::native_worker(&options.adapter).map(|worker| {
                        Backend::Native(Box::new(coordinator::Coordinator::new(worker)))
                    });
                }
                match session.as_mut() {
                    Some(Backend::Native(coordinator)) => coordinator.warm(options, &request),
                    _ => infrastructure(&request, "warm requires native coordinator"),
                }
            }
            RUN | RUN_LANES => execute_local(options, &request, &mut session),
            _ => return Err("malformed scheduler request".into()),
        };
        let bytes = serde_json::to_vec(&response).map_err(|e| e.to_string())?;
        channel
            .write_all(&frame(RESPONSE, &bytes))
            .map_err(|e| e.to_string())?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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

    /// Python side of the slot channel for fake workers: the descriptor is
    /// the last argument, `args` holds the rest.
    const PY: &str = "import json,os,pathlib,socket,struct,subprocess,sys,time\nch=socket.socket(fileno=int(sys.argv[-1]))\nargs=sys.argv[1:-1]\ndef recv():\n data=b''\n while len(data)<4 or len(data)<4+struct.unpack('>I',data[:4])[0]:\n  c=ch.recv(65536)\n  if not c: return None\n  data+=c\n n=struct.unpack('>I',data[:4])[0]\n return data[4:5],data[5:4+n]\ndef send(tag,payload=b''):\n ch.sendall(struct.pack('>I',len(payload)+1)+tag+payload)\n";

    /// A fake worker holding the other end of its channel.
    fn worker(script: &str, args: &[&str]) -> Worker {
        let mut command = Command::new("python3");
        command.arg("-c").arg(format!("{PY}{script}")).args(args);
        let (child, channel) = spawn_with_channel(&mut command).unwrap();
        Worker::with(tempfile::tempdir().unwrap(), child, channel)
    }

    /// A fake worker whose channel end the test holds instead.
    fn detached() -> (Worker, UnixStream) {
        let (ours, theirs) = UnixStream::pair().unwrap();
        ours.set_nonblocking(true).unwrap();
        let child = Command::new("python3")
            .args(["-c", "import time; time.sleep(60)"])
            .spawn()
            .unwrap();
        (
            Worker::with(tempfile::tempdir().unwrap(), child, ours),
            theirs,
        )
    }

    fn wait_for_file(path: &Path) -> String {
        // Synchronize on the fake's own evidence, rather than elapsed host time.
        let watchdog = Instant::now() + Duration::from_secs(30);
        loop {
            let text = fs::read_to_string(path).unwrap_or_default();
            if !text.is_empty() {
                return text;
            }
            assert!(
                Instant::now() < watchdog,
                "{} never appeared",
                path.display()
            );
            thread::yield_now();
        }
    }

    /// Waits on `fds` with a one-hour bound, so only an event can end the
    /// wait; the watchdog fails the test instead of hanging it.
    fn woken(fds: Vec<RawFd>) -> Vec<libc::c_short> {
        let (tx, rx) = std::sync::mpsc::channel();
        thread::spawn(move || {
            let _ = tx.send(wait::readable(
                &fds,
                Instant::now() + Duration::from_secs(3600),
            ));
        });
        rx.recv_timeout(Duration::from_secs(60))
            .expect("an event ended the wait")
            .unwrap()
    }

    fn timed_out(kind: Option<TimeoutKind>) -> Response {
        let mut response = infrastructure(&request(), "");
        response.outcome = TestOutcome::Timeout;
        response.timeout_kind = kind;
        response
    }

    #[test]
    fn the_watchdog_reruns_only_timeouts_that_could_be_a_stalled_zygote() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let mut options = options();
        options.zygote = true;
        let mut scheduler = Scheduler::new(&options).unwrap();
        // A worker that sat idle until its deadline, and an adapter that says
        // nothing, could be a deadlocked zygote child.
        assert!(scheduler.watching(&timed_out(Some(TimeoutKind::Idle))));
        assert!(scheduler.watching(&timed_out(None)));
        assert_eq!(scheduler.zygote.genuine, 0);
        // One that was burning CPU, or answered with the runner's own timeout, is
        // the mutant's: no rerun, and counted.
        assert!(!scheduler.watching(&timed_out(Some(TimeoutKind::Busy))));
        assert!(!scheduler.watching(&timed_out(Some(TimeoutKind::TestTimeout))));
        assert_eq!(scheduler.zygote.genuine, 2);
        // Other outcomes are never watched.
        let mut passed = timed_out(Some(TimeoutKind::Idle));
        passed.outcome = TestOutcome::Passed;
        assert!(!scheduler.watching(&passed));
        // Once the zygote is disabled nothing is rerun at all.
        scheduler.zygote.disabled = true;
        assert!(!scheduler.watching(&timed_out(Some(TimeoutKind::Idle))));
        assert!(!scheduler.watching(&timed_out(None)));
    }

    #[test]
    fn without_the_zygote_no_timeout_is_ever_watched() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let mut options = options();
        options.zygote = false;
        let mut scheduler = Scheduler::new(&options).unwrap();
        assert!(!scheduler.watching(&timed_out(None)));
        assert!(!scheduler.watching(&timed_out(Some(TimeoutKind::Idle))));
        assert_eq!(scheduler.zygote.genuine, 0);
    }

    fn passed(request: &Request) -> Vec<u8> {
        let mut response = infrastructure(request, "");
        response.outcome = TestOutcome::Passed;
        response.errors.clear();
        response.tests.passed = 1;
        serde_json::to_vec(&response).unwrap()
    }

    #[test]
    fn empty_dispatch_leaves_background_warm_busy() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let mut spare = worker("time.sleep(60)", &[]);
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

    #[test]
    fn lanes_sharing_one_resident_set_travel_with_it_once() {
        let module = |name: &str| Mutation {
            id: "__oximut_resident__".into(),
            file: format!("/p/{name}.ts"),
            code: "c".repeat(10_000),
            map: serde_json::json!({"version": 3, "sources": [], "mappings": ""}),
        };
        let set = Arc::new(vec![module("a"), module("b")]);
        let lane = |id: &str, resident: Option<Arc<Vec<Mutation>>>| {
            let mut job = request();
            job.job_id = id.into();
            job.kind = "mutant".into();
            job.resident = resident;
            job
        };
        let batch = |lanes: Vec<Request>| {
            let mut top = lanes[0].clone();
            top.lanes = Some(lanes);
            top
        };
        let shared = batch(vec![
            lane("1", Some(set.clone())),
            lane("2", Some(set.clone())),
        ]);
        let (tag, payload) = encode_request(0, &shared).unwrap();
        assert_eq!(tag, RUN_LANES);
        let full = serde_json::to_vec(&shared).unwrap();
        assert!(
            payload.len() * 2 < full.len() + 30_000,
            "{} {}",
            payload.len(),
            full.len()
        );
        // The slot restores exactly what the scheduler had.
        let mut received: Request = serde_json::from_slice(&payload).unwrap();
        assert!(
            received
                .lanes
                .as_ref()
                .unwrap()
                .iter()
                .all(|l| l.resident.is_none())
        );
        restore_lane_resident(&mut received).unwrap();
        assert_eq!(serde_json::to_vec(&received).unwrap(), full);
        assert!(received.lanes.iter().flatten().all(|l| Arc::ptr_eq(
            l.resident.as_ref().unwrap(),
            received.resident.as_ref().unwrap()
        )));
        // Equal content under another allocation is never assumed identical,
        // so the adapter keeps validating such a batch itself.
        let copy = Arc::new(set.as_ref().clone());
        for lanes in [
            vec![lane("1", Some(set.clone())), lane("2", Some(copy))],
            vec![lane("1", Some(set.clone())), lane("2", None)],
        ] {
            let mut mixed = batch(lanes);
            mixed.resident = Some(set.clone());
            let (tag, payload) = encode_request(0, &mixed).unwrap();
            assert_eq!(tag, RUN);
            assert_eq!(payload, serde_json::to_vec(&mixed).unwrap());
        }
        // No lanes, no resident modules, or a warm job: unchanged frames.
        let mut plain = lane("1", Some(set.clone()));
        assert_eq!(encode_request(0, &plain).unwrap().0, RUN);
        assert_eq!(encode_request(WARM_JOB, &shared).unwrap().0, WARM);
        plain.resident = None;
        let mut none = batch(vec![lane("1", None)]);
        none.resident = None;
        assert_eq!(encode_request(0, &none).unwrap().0, RUN);
        // A frame whose lanes already hold modules, or that has none to
        // share, is malformed.
        let mut bad: Request =
            serde_json::from_slice(&serde_json::to_vec(&shared).unwrap()).unwrap();
        assert!(restore_lane_resident(&mut bad).is_err());
        let mut bad = batch(vec![lane("1", None)]);
        assert!(restore_lane_resident(&mut bad).is_err());
        assert!(restore_lane_resident(&mut request()).is_err());
    }

    #[test]
    fn cached_resident_sizes_equal_the_encoded_sizes() {
        let module = |name: &str, code: &str| Mutation {
            id: "__oximut_resident__".into(),
            file: format!("/p/{name}.ts"),
            code: code.into(),
            map: serde_json::json!({"version": 3, "sources": ["é\"x"], "mappings": ""}),
        };
        let set = Arc::new(vec![module("a", "a = 1"), module("b", &"é\n".repeat(500))]);
        let mut sizes = RequestSizes::default();
        for resident in [
            None,
            Some(set.clone()),
            Some(Arc::new(vec![module("c", "")])),
        ] {
            for mutation in [None, Some(module("m", "x"))] {
                let mut job = request();
                job.resident = resident.clone();
                job.mutation = mutation;
                assert_eq!(sizes.request_len(&mut job), encoded_len(&job));
                assert_eq!(job.resident.is_some(), resident.is_some(), "restored");
            }
        }
        // Only the very same allocation counts: a batch sends such a set once.
        let (mut a, mut b) = (request(), request());
        a.resident = Some(set.clone());
        b.resident = Some(set.clone());
        assert!(sizes.same_resident(&a, &b));
        b.resident = Some(Arc::new(set.as_ref().clone()));
        assert!(
            !sizes.same_resident(&a, &b),
            "equal content, other allocation"
        );
        b.resident = None;
        assert!(!sizes.same_resident(&a, &b));
        assert!(sizes.same_resident(&b, &b.clone()));
    }

    #[test]
    fn a_mutant_without_a_resident_module_is_never_a_lane() {
        let set = Arc::new(vec![]);
        let job = |id: usize, resident: Option<Arc<Vec<Mutation>>>| {
            let mut job = request();
            job.job_id = id.to_string();
            job.resident = resident;
            (id, job)
        };
        let (lanes, unshared) = split_unshared(vec![
            job(0, Some(set.clone())),
            job(1, None),
            job(2, Some(set)),
        ]);
        assert_eq!(lanes.iter().map(|(i, _)| *i).collect::<Vec<_>>(), [0, 2]);
        assert_eq!(unshared.iter().map(|(i, _)| *i).collect::<Vec<_>>(), [1]);
    }

    #[test]
    fn lanes_are_priced_as_they_travel_in_a_batch() {
        let code = "export const n = 1;\n".repeat(5000);
        let module = |name: &str| Mutation {
            id: "__oximut_resident__".into(),
            file: format!("/p/{name}.ts"),
            code: code.clone(),
            map: serde_json::json!({"version": 3, "sources": [], "mappings": "AAAA"}),
        };
        let set = Arc::new(vec![module("a"), module("b")]);
        let lane = |id: &str, file: &str| {
            let mut job = request();
            job.job_id = id.into();
            job.kind = "mutant".into();
            job.resident = Some(set.clone());
            job.mutation = Some(Mutation {
                id: id.into(),
                ..module(file)
            });
            job
        };
        let mut sizes = RequestSizes::default();
        let mut jobs: Vec<_> = ["1", "2", "3"]
            .iter()
            .map(|id| (0, lane(id, if *id == "2" { "b" } else { "a" })))
            .collect();
        // The encoded batch carries the resident set once and each lane as a reference.
        let mut batch_len = sizes.batch_len(&mut jobs[0].1) + 1;
        for (_, job) in &mut jobs[1..] {
            batch_len += sizes.lane_len(job, Some(&set)) + 1;
        }
        let wire = encoded_len(&without_duplicate_lane_residents(&lane_batch(&jobs, 4)).unwrap());
        // Within the batch's own, slightly longer, deadline and worker fields.
        assert!(
            batch_len <= wire + 64 && wire <= batch_len + 64,
            "{batch_len} {wire}"
        );
        assert!(wire < 2 * encoded_len(&*set) + 2 * code.len() / 2);
        // A mutation that is not its file's resident module keeps its own code.
        let mut other = lane("4", "a");
        other.mutation.as_mut().unwrap().code.push('!');
        assert!(sizes.lane_len(&mut other, Some(&set)) > code.len());
        assert!(sizes.lane_len(&mut jobs[1].1, Some(&set)) < 1000);
        // A lane that does not share the batch's set is priced whole.
        let mut own = lane("5", "a");
        own.resident = Some(Arc::new(set.as_ref().clone()));
        assert!(sizes.lane_len(&mut own, Some(&set)) > encoded_len(&*set));
        // The slot restores what the scheduler had, and refuses a dangling reference.
        let (tag, payload) = encode_request(0, &lane_batch(&jobs, 4)).unwrap();
        assert_eq!(tag, RUN_LANES);
        let mut received: Request = serde_json::from_slice(&payload).unwrap();
        assert!(
            received
                .lanes
                .iter()
                .flatten()
                .all(|l| is_resident_reference(l.mutation.as_ref().unwrap()))
        );
        restore_lane_resident(&mut received).unwrap();
        assert_eq!(
            serde_json::to_vec(&received).unwrap(),
            serde_json::to_vec(&lane_batch(&jobs, 4)).unwrap()
        );
        let mut dangling: Request = serde_json::from_slice(&payload).unwrap();
        dangling.lanes.as_mut().unwrap()[0]
            .mutation
            .as_mut()
            .unwrap()
            .file = "/p/z.ts".into();
        assert!(restore_lane_resident(&mut dangling).is_err());
    }

    #[test]
    fn oversized_requests_do_not_become_lane_batches() {
        let resident = |megabytes: usize| {
            let mut job = request();
            job.resident = Some(Arc::new(vec![Mutation {
                id: "__oximut_resident__".into(),
                file: "/p/module.ts".into(),
                code: "x".repeat(megabytes * 1024 * 1024),
                map: serde_json::json!({"version": 3, "sources": [], "mappings": ""}),
            }]));
            job
        };
        let mut sizes = RequestSizes::default();
        assert!(sizes.batch_len(&mut request()) <= MAX_LANE_BATCH);
        // The batch sends the resident set once, so a set of more than half the
        // limit still travels as a one-lane batch.
        let mut big = resident(5);
        assert!(sizes.request_len(&mut big) <= MAX_REQUEST);
        assert!(sizes.batch_len(&mut big) <= MAX_LANE_BATCH);
        let batch = lane_batch(&[(0, big)], 1);
        assert!(encoded_len(&without_duplicate_lane_residents(&batch).unwrap()) < MAX_LANE_BATCH);
        // One that cannot travel in a frame is not a lane.
        assert!(sizes.batch_len(&mut resident(8)) > MAX_LANE_BATCH);
    }

    #[test]
    fn a_lane_batch_can_report_after_its_lanes_reach_the_job_deadline() {
        let mut job = request();
        job.deadline_ms = 5000;
        let batch = lane_batch(&[(0, job.clone()), (1, job)], 4);
        assert_eq!(batch.workers, Some(4));
        let huge = vec![(0, request()); 1025];
        assert_eq!(lane_batch(&huge, 2000).workers, Some(1024));
        // More lanes than slots is a rolling batch, which keeps its slot count.
        assert_eq!(lane_batch(&huge, 2).workers, Some(2));
        assert_eq!(batch.deadline_ms, 5000 + LANE_REPORT_MS);
        assert!(
            batch
                .lanes
                .unwrap()
                .iter()
                .all(|lane| lane.deadline_ms == 5000)
        );
    }

    #[test]
    fn a_rolling_lane_batch_keeps_its_width_and_cannot_time_out_before_its_last_lane() {
        let mut job = request();
        job.deadline_ms = 5000;
        let lanes: Vec<_> = (0..9).map(|index| (index, job.clone())).collect();
        let rolling = lane_batch(&lanes, 4);
        // The adapter reads more lanes than workers as a rolling batch.
        assert_eq!(rolling.workers, Some(4));
        assert_eq!(rolling.lanes.as_ref().unwrap().len(), 9);
        // Nine lanes through four slots take three rounds, each of which may use a
        // lane's whole deadline and its report time.
        assert_eq!(rolling.deadline_ms, 3 * (5000 + LANE_REPORT_MS));
        assert!(Worker::limit(&rolling) > Duration::from_millis(3 * 5000));
        // A batch within the width is the barrier batch it always was.
        let barrier = lane_batch(&lanes[..4], 4);
        assert_eq!(barrier.deadline_ms, 5000 + LANE_REPORT_MS);
        assert_eq!(barrier.workers, Some(4));
        // Per-lane deadlines stay per lane; only the batch's grows.
        assert!(
            rolling
                .lanes
                .unwrap()
                .iter()
                .all(|lane| lane.deadline_ms == 5000)
        );
        // The adapter bounds deadlines.
        job.deadline_ms = u64::MAX / 4;
        let huge = lane_batch(&[(0, job.clone()), (1, job)], 1);
        assert_eq!(huge.deadline_ms, MAX_DEADLINE_MS);
    }

    #[test]
    fn lanes_with_their_own_deadlines_bound_the_batch_by_each_rounds_slowest_lane() {
        let lane = |deadline_ms| {
            let mut job = request();
            job.deadline_ms = deadline_ms;
            (0, job)
        };
        let lanes = [lane(1_000), lane(9_000), lane(2_000), lane(3_000)];
        // Two rounds of two lanes: the 9 s lane bounds the first, the 3 s lane the second.
        let rolling = lane_batch(&lanes, 2);
        assert_eq!(rolling.deadline_ms, 9_000 + 3_000 + 2 * LANE_REPORT_MS);
        // Every lane keeps its own deadline for the adapter to enforce.
        let each: Vec<_> = rolling
            .lanes
            .as_ref()
            .unwrap()
            .iter()
            .map(|lane| lane.deadline_ms)
            .collect();
        assert_eq!(each, [1_000, 9_000, 2_000, 3_000]);
        // One round is bounded by its slowest lane alone.
        assert_eq!(lane_batch(&lanes, 4).deadline_ms, 9_000 + LANE_REPORT_MS);
        // A rolling batch never allows less than any lane's own deadline and report time.
        assert!(rolling.deadline_ms >= 9_000 + LANE_REPORT_MS);
    }

    fn request() -> Request {
        serde_json::from_value(serde_json::json!({
            "protocol": 1, "runId": "test", "jobId": "baseline", "generation": 0,
            "kind": "baseline", "root": ".", "config": "", "deadlineMs": 60000,
            "mutation": null
        }))
        .unwrap()
    }

    /// Force a partial request even on hosts with large default socket buffers.
    fn stalled() -> (Worker, UnixStream, Vec<u8>) {
        let (mut job, peer) = detached();
        let size: libc::c_int = 4096;
        assert_eq!(
            unsafe {
                libc::setsockopt(
                    job.channel.as_raw_fd(),
                    libc::SOL_SOCKET,
                    libc::SO_SNDBUF,
                    (&size as *const libc::c_int).cast(),
                    std::mem::size_of_val(&size) as libc::socklen_t,
                )
            },
            0
        );
        let mut request = request();
        request.root = "x".repeat(1024 * 1024);
        let expected = frame(RUN, &serde_json::to_vec(&request).unwrap());
        let started = Instant::now();
        job.send(0, request).unwrap();
        assert!(started.elapsed() < Duration::from_secs(2), "send blocked");
        assert!(!job.pending.is_empty());
        assert!(job.busy.as_ref().unwrap().2 >= started);
        (job, peer, expected)
    }

    #[test]
    fn partial_request_is_flushed_by_dispatch_without_blocking_send() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let (job, mut peer, expected) = stalled();
        peer.set_nonblocking(true).unwrap();
        scheduler.slots = vec![Some(job)];
        let mut received = vec![];
        let deadline = Instant::now() + Duration::from_secs(10);
        while !scheduler.slots[0].as_ref().unwrap().pending.is_empty() {
            assert!(Instant::now() < deadline, "pending request never drained");
            let (fds, until) = scheduler.events();
            assert_ne!(fds[0].events & libc::POLLOUT, 0);
            assert!(until <= scheduler.budget.next_sample_at());
            assert_eq!(
                peer.read_to_end(&mut received).unwrap_err().kind(),
                std::io::ErrorKind::WouldBlock
            );
            scheduler.wait_for_events();
            assert!(scheduler.slots[0].as_mut().unwrap().ready().is_none());
        }
        assert_eq!(
            peer.read_to_end(&mut received).unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
        assert_eq!(received, expected);
        assert_eq!(scheduler.events().0[0].events, libc::POLLIN);
    }

    #[test]
    fn shutdown_skips_stop_when_request_bytes_are_pending() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let (job, mut peer, expected) = stalled();
        let written = expected.len() - job.pending.len();
        scheduler.slots = vec![Some(job)];
        let started = Instant::now();
        scheduler.close_slots(&[0]);
        assert!(
            started.elapsed() < Duration::from_secs(3),
            "shutdown waited for STOP"
        );
        let mut received = vec![];
        peer.read_to_end(&mut received).unwrap();
        assert_eq!(
            received,
            expected[..written],
            "STOP must not follow a partial frame"
        );
        assert!(scheduler.slots[0].is_none());
        assert!(scheduler.cleanup_ok, "{:?}", scheduler.diagnostics);
    }

    #[test]
    fn shutdown_abandons_warm_before_stop_and_reaps_descendants() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        enable_subreaper().unwrap();
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let evidence = tempfile::tempdir().unwrap();
        let child_pid = evidence.path().join("child");
        let stop_seen = evidence.path().join("stop-seen");
        let script = "child=subprocess.Popen(['python3','-c','import time; time.sleep(60)'], start_new_session=True)\npathlib.Path(args[0]).write_text(str(child.pid))\nwhile True:\n m=recv()\n if m is None: time.sleep(60); continue\n if m[0]==b'S': pathlib.Path(args[1]).touch()";
        let mut warm = worker(
            script,
            &[child_pid.to_str().unwrap(), stop_seen.to_str().unwrap()],
        );
        let pid: u32 = wait_for_file(&child_pid).parse().unwrap();
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
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        scheduler.budget.effective_workers = 2;
        let request = request();
        // The baseline's response is already there when dispatch starts.
        let (mut baseline, mut peer) = detached();
        peer.write_all(&frame(RESPONSE, &passed(&request))).unwrap();
        baseline.busy = Some((0, request.clone(), Instant::now()));
        scheduler.slots = vec![Some(baseline), None];
        scheduler.budget.active = 1;
        scheduler.spares.push_back((WARM_JOB, request));
        let completed = scheduler.dispatch(VecDeque::new(), true);
        assert_eq!(completed.len(), 1);
        assert_eq!(completed[0].1.outcome, TestOutcome::Passed);
        assert!(scheduler.spares.is_empty());
        let spare = scheduler.slots[1].as_ref().expect("queued spare started");
        assert_eq!(spare.busy.as_ref().unwrap().0, WARM_JOB);
    }

    #[test]
    fn only_controllers_that_ran_lanes_are_retired() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let script = "while (m:=recv()) is not None and m[0]!=b'S': pass";
        scheduler.slots = vec![Some(worker(script, &[]))];
        scheduler.retire_lane_controllers();
        assert!(scheduler.slots[0].is_some());
        scheduler.lanes_ran = true;
        scheduler.retire_lane_controllers();
        assert!(scheduler.slots[0].is_none());
        assert!(!scheduler.lanes_ran);
        assert!(scheduler.cleanup_ok, "{:?}", scheduler.diagnostics);
    }

    #[test]
    fn shutdown_signals_all_slots_before_waiting() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let barrier = tempfile::tempdir().unwrap();
        let first = barrier.path().join("first");
        let second = barrier.path().join("second");
        let (first, second) = (first.to_str().unwrap(), second.to_str().unwrap());
        // Each exits only once the other has seen its stop as well.
        let script = "while (m:=recv()) is not None and m[0]!=b'S': pass\npathlib.Path(args[0]).touch()\nwhile not pathlib.Path(args[1]).exists(): time.sleep(.01)";
        let a = worker(script, &[first, second]);
        let b = worker(script, &[second, first]);
        scheduler.slots = vec![Some(a), Some(b)];
        let mut diagnostics = vec![];
        assert!(scheduler.finish(&mut diagnostics), "{diagnostics:?}");
        assert!(scheduler.slots.iter().all(Option::is_none));
    }

    #[test]
    fn a_response_ends_the_dispatch_wait_without_a_poll_interval() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let (mut job, mut peer) = detached();
        let request = request();
        job.busy = Some((0, request.clone(), Instant::now()));
        scheduler.slots = vec![Some(job)];
        scheduler.budget.active = 1;
        // The loop's own wait ends no later than the next resource sample...
        let (fds, until) = scheduler.events();
        assert_eq!(fds.len(), 1);
        assert!(until <= scheduler.budget.next_sample_at());
        // ...but nothing else bounds it: the response alone ends an hour's wait.
        let writer = thread::spawn(move || {
            peer.write_all(&frame(RESPONSE, &passed(&request))).unwrap();
            peer
        });
        assert_ne!(
            woken(fds.iter().map(|fd| fd.fd).collect())[0] & libc::POLLIN,
            0
        );
        let _peer = writer.join().unwrap();
        // The response is read as soon as it is there.
        let completed = scheduler.dispatch(VecDeque::new(), true);
        assert_eq!(completed.len(), 1);
        assert_eq!(completed[0].1.outcome, TestOutcome::Passed);
        assert!(scheduler.slots[0].as_ref().unwrap().busy.is_none());
    }

    #[test]
    fn worker_death_mid_job_is_reported_at_once() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let mut job = worker("recv()\nsys.exit(5)", &[]);
        job.send(0, request()).unwrap();
        // The worker's exit hangs up its channel, which ends the wait.
        assert_ne!(woken(vec![job.channel.as_raw_fd()])[0], 0);
        let error = job.ready().expect("reported").unwrap_err();
        assert!(
            error.starts_with("scheduler worker exited before response"),
            "{error}"
        );
        assert!(error.contains("exit status: 5"), "{error}");
    }

    #[test]
    fn deadline_expiry_aborts_the_slot_and_reaps_its_descendants() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        enable_subreaper().unwrap();
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let evidence = tempfile::tempdir().unwrap();
        let child_pid = evidence.path().join("child");
        let script = "child=subprocess.Popen(['python3','-c','import time; time.sleep(60)'], start_new_session=True)\npathlib.Path(args[0]).write_text(str(child.pid))\ntime.sleep(60)";
        let mut job = worker(script, &[child_pid.to_str().unwrap()]);
        let pid: u32 = wait_for_file(&child_pid).parse().unwrap();
        let (_, start) = process_identity(pid).unwrap();
        let mut late = request();
        late.deadline_ms = 0;
        job.busy = Some((0, late, Instant::now() - Duration::from_secs(17)));
        scheduler.slots = vec![Some(job)];
        scheduler.budget.active = 1;
        let completed = scheduler.dispatch(VecDeque::new(), true);
        assert_eq!(completed.len(), 1);
        assert_eq!(completed[0].1.outcome, TestOutcome::InfrastructureError);
        assert!(completed[0].1.errors[0].contains("exceeded controller deadline"));
        assert!(scheduler.slots[0].is_none(), "the slot must be aborted");
        let mut diagnostics = vec![];
        scheduler.finish(&mut diagnostics);
        assert!(!still_owned(pid, start), "descendant survived the abort");
    }

    #[test]
    fn graceful_shutdown_reports_and_reaps_a_surviving_descendant() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        enable_subreaper().unwrap();
        let options = options();
        let mut scheduler = Scheduler::new(&options).unwrap();
        let evidence = tempfile::tempdir().unwrap();
        let child_pid = evidence.path().join("child");
        let script = "child=subprocess.Popen(['python3','-c','import time; time.sleep(60)'], start_new_session=True)\npathlib.Path(args[0]).write_text(str(child.pid))\nwhile (m:=recv()) is not None and m[0]!=b'S': pass\nsend(b'D', json.dumps(['from worker']).encode())";
        let job = worker(script, &[child_pid.to_str().unwrap()]);
        let pid: u32 = wait_for_file(&child_pid).parse().unwrap();
        let (_, start) = process_identity(pid).unwrap();
        scheduler.slots = vec![Some(job)];
        let mut diagnostics = vec![];
        assert!(!scheduler.finish(&mut diagnostics), "{diagnostics:?}");
        assert!(
            diagnostics.iter().any(|d| d == "from worker"),
            "{diagnostics:?}"
        );
        assert!(
            diagnostics
                .iter()
                .any(|d| d == "scheduler shutdown left owned descendants"),
            "{diagnostics:?}"
        );
        assert!(!still_owned(pid, start), "descendant survived shutdown");
    }

    fn serving() -> (UnixStream, thread::JoinHandle<Result<(), String>>) {
        let (ours, theirs) = UnixStream::pair().unwrap();
        let options = options();
        (ours, thread::spawn(move || serve(theirs, &options)))
    }

    #[test]
    fn serve_answers_stop_with_its_shutdown_diagnostics() {
        let (mut ours, served) = serving();
        ours.write_all(&frame(STOP, b"")).unwrap();
        let mut received = vec![];
        ours.read_to_end(&mut received).unwrap();
        assert_eq!(
            take_frame(&mut received, RESPONSE_LIMIT).unwrap(),
            Some((SHUTDOWN, b"[]".to_vec()))
        );
        assert!(received.is_empty());
        served.join().unwrap().unwrap();
    }

    #[test]
    fn serve_shuts_down_when_the_scheduler_hangs_up() {
        let (ours, served) = serving();
        drop(ours);
        served.join().unwrap().unwrap();
    }

    #[test]
    fn requests_and_responses_keep_their_size_limits() {
        let (mut ours, served) = serving();
        ours.write_all(&((MAX_REQUEST + 2) as u32).to_be_bytes())
            .unwrap();
        assert_eq!(
            served.join().unwrap().unwrap_err(),
            "internal request exceeds 8 MiB"
        );
        // A reply longer than 1 MiB is refused from its header alone.
        let mut oversized = ((RESPONSE_LIMIT + 2) as u32).to_be_bytes().to_vec();
        oversized.push(RESPONSE);
        assert_eq!(
            take_frame(&mut oversized, RESPONSE_LIMIT).unwrap_err(),
            "adapter response exceeds 1 MiB limit"
        );
        let mut exact = frame(RESPONSE, &vec![b' '; RESPONSE_LIMIT]);
        assert!(take_frame(&mut exact, RESPONSE_LIMIT).unwrap().is_some());
        let mut partial = frame(RESPONSE, b"{}");
        partial.pop();
        assert_eq!(take_frame(&mut partial, RESPONSE_LIMIT).unwrap(), None);
    }
}
