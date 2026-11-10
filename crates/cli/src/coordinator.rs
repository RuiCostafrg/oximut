//! Native persistent execution coordinator for the reference Vitest adapter.
//!
//! The Rust slot (a subreaper) drives `m2-worker.ts` over a dedicated Node IPC
//! channel, replacing the `m2-runner.mjs` relay and the `supervisor.py`
//! subreaper. It keeps their contract: per-job deadlines that include
//! controller start-up, input-change controller recycling, at most 50 jobs per
//! controller, retirement after timeouts and infrastructure errors, post-job
//! leak detection from fresh broad discovery, and identity-verified cleanup
//! (pidfd) that must prove every owned process exited and was reaped.
use super::*;
use sha2::{Digest, Sha256};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::os::unix::process::CommandExt;
use std::process::Child;
use std::sync::Mutex;

const MAX_JOBS_PER_CONTROLLER: usize = 50;
/// Controller replies carry at most a 1 MiB result plus a small envelope.
const MAX_MESSAGE_BYTES: usize = 1024 * 1024 + 16 * 1024;
const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
const OUTPUT_BYTES: usize = 32 * 1024;
/// Shutdown must finish within the scheduler's 7 s slot bound: a graceful
/// close reply, the controller's exit, then verified cleanup.
const CLOSE_MS: u64 = 3_000;
const EXIT_MS: u64 = 1_000;
const CLEANUP_MS: u64 = 2_000;
/// Vitest resolves a run before its pool has awaited fork termination, so the
/// last test fork may still be exiting when the reply arrives. Owned
/// processes get this long (within the job deadline) to exit before leak
/// verification; any that remain are leaks and retire the controller.
const EXIT_GRACE_MS: u64 = 500;
/// Timestamps on coarse filesystems (FAT: 2 s) can hide a same-tick rewrite.
const TIMESTAMP_ALLOWANCE: Duration = Duration::from_secs(2);

/// The reference persistent adapter is executed natively by driving its
/// sibling controller; any other adapter keeps the file-protocol relay.
pub fn native_worker(adapter: &Path) -> Option<PathBuf> {
    let worker = adapter.with_file_name("m2-worker.ts");
    (adapter.file_name()? == "m2-runner.mjs" && worker.is_file()).then_some(worker)
}

// ---------------------------------------------------------------------------
// Identity-verified signalling.

#[cfg(any(target_os = "linux", target_os = "android"))]
fn pidfd_open(pid: u32) -> std::io::Result<std::os::fd::OwnedFd> {
    use std::os::fd::FromRawFd;
    let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::c_int, 0) };
    if fd < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(unsafe { std::os::fd::OwnedFd::from_raw_fd(fd as i32) })
}

#[cfg(any(target_os = "linux", target_os = "android"))]
fn pidfd_signal(fd: &std::os::fd::OwnedFd, signal: libc::c_int) -> std::io::Result<()> {
    let result = unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            fd.as_raw_fd(),
            signal,
            std::ptr::null::<libc::siginfo_t>(),
            0,
        )
    };
    if result < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

/// Both syscalls can be denied independently (e.g. by seccomp); test each.
fn probe_pidfd() -> Result<(), String> {
    let fd = pidfd_open(std::process::id())
        .map_err(|error| format!("pidfd identity-bound signalling unavailable: {error}"))?;
    pidfd_signal(&fd, 0)
        .map_err(|error| format!("pidfd identity-bound signalling unavailable: {error}"))
}

/// Signal only the generation pinned before its identity was re-read. A
/// numeric kill after an identity check can hit a reused PID; a pidfd opened
/// first pins the generation, and a signal after its exit fails with ESRCH.
fn signal_verified(pid: u32, start: u64) {
    let Ok(fd) = pidfd_open(pid) else {
        return; // Gone, or unverifiable: never fall back to kill(2).
    };
    if processes::identity(pid)
        .ok()
        .flatten()
        .is_some_and(|(_, current)| current == start)
    {
        let _ = pidfd_signal(&fd, libc::SIGKILL);
    }
}

/// Every descendant of this slot. Only the controller tree lives under it, and
/// as a subreaper it adopts every orphan, so this is the complete owned set.
/// Strict: an unreadable process table is a verification failure, never an
/// empty (clean-looking) tree.
fn owned() -> Result<BTreeMap<u32, u64>, String> {
    let discover = || {
        let entries = fs::read_dir("/proc").map_err(|error| error.to_string())?;
        let pids = entries.map(|entry| {
            entry
                .map(|entry| entry.file_name().to_string_lossy().parse::<u32>().ok())
                .map_err(|error| error.to_string())
        });
        strict_owned(
            pids,
            |pid| fs::read_to_string(format!("/proc/{pid}/stat")),
            std::process::id(),
        )
    };
    discover().map_err(|error| format!("owned-process verification unavailable: {error}"))
}

/// Unlike accounting's best-effort discovery, verification must not lose a
/// descendant because its identity could not be read. Permission-denied stats
/// belong to foreign processes on hardened hosts; owned stats are readable.
fn strict_owned(
    pids: impl IntoIterator<Item = Result<Option<u32>, String>>,
    mut read: impl FnMut(u32) -> std::io::Result<String>,
    root: u32,
) -> Result<BTreeMap<u32, u64>, String> {
    let mut table = BTreeMap::new();
    for pid in pids {
        let Some(pid) = pid? else { continue };
        let stat = match read(pid) {
            Ok(stat) => stat,
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
                ) || error.raw_os_error() == Some(libc::ESRCH) =>
            {
                continue;
            }
            Err(error) => {
                return Err(format!(
                    "process identity unavailable for PID {pid}: {error}"
                ));
            }
        };
        // Same field positions and final ')' handling as processes::identity.
        let identity = || {
            let mut fields = stat.rsplit_once(')')?.1.split_whitespace();
            let parent = fields.nth(1)?.parse::<u32>().ok()?;
            let start = fields.nth(17)?.parse::<u64>().ok()?;
            Some((parent, start))
        };
        table.insert(
            pid,
            identity().ok_or_else(|| format!("invalid process identity for PID {pid}"))?,
        );
    }
    // Reuse the shared pure projection only after every identity was verified.
    processes::ProcessTable::collect(table.keys().map(|&pid| Ok(Some(pid))), |pid| {
        Ok(table.get(&pid).copied())
    })
    .map(|table| table.descendants(root))
}

/// Cheap polling view of the owned tree for exit waits; never used to
/// conclude cleanup or the absence of leaks.
fn owned_hint() -> BTreeMap<u32, u64> {
    let root = std::process::id();
    match processes::ProcessTable::read_tree(root) {
        Ok(Some(table)) => table.descendants(root),
        _ => descendants(root),
    }
}

// ---------------------------------------------------------------------------
// Controller input fingerprint.

#[derive(Clone, Debug, PartialEq)]
struct Entry {
    path: PathBuf,
    size: u64,
    modified: Option<SystemTime>,
    changed: (i64, i64),
    /// Content hash, taken while timestamps could hide a same-tick write
    /// and once more in the following snapshot for comparison.
    digest: Option<[u8; 32]>,
    /// Timestamps not strictly older than this snapshot's start minus the
    /// precision allowance: the next snapshot must compare content.
    racy: bool,
}

#[derive(Debug)]
enum TakeError {
    Deadline,
    Io(String),
}

impl From<String> for TakeError {
    fn from(error: String) -> Self {
        TakeError::Io(error)
    }
}

/// Streams a file through SHA-256, checking the job deadline between chunks.
fn hash_file(path: &Path, deadline: Instant) -> Result<[u8; 32], TakeError> {
    let mut file = fs::File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut chunk = vec![0u8; 1 << 16];
    loop {
        if Instant::now() >= deadline {
            return Err(TakeError::Deadline);
        }
        let read = file
            .read(&mut chunk)
            .map_err(|e| format!("{}: {e}", path.display()))?;
        if read == 0 {
            return Ok(hasher.finalize().into());
        }
        hasher.update(&chunk[..read]);
    }
}

#[derive(Clone, Debug)]
struct Fingerprint {
    root: PathBuf,
    config: PathBuf,
    entries: Vec<Entry>,
    symlink: bool,
}

impl Fingerprint {
    /// Same comparison as the JS relay (path, size, mtime, ctime for every
    /// project input plus the config; any symlink forces a fresh controller),
    /// plus git's racily-clean rule: entries whose timestamps are not strictly
    /// older than a snapshot's start (minus a precision allowance) are hashed
    /// then and in the next snapshot, so an equal-size same-tick rewrite with
    /// restored timestamps still changes the fingerprint.
    fn take(
        root: &Path,
        config: &Path,
        previous: Option<&Fingerprint>,
        deadline: Instant,
    ) -> Result<Self, TakeError> {
        let started = SystemTime::now();
        let mut entries = vec![];
        let mut symlink = false;
        let hashed: BTreeSet<&Path> = previous.map_or_else(BTreeSet::new, |previous| {
            previous
                .entries
                .iter()
                .filter(|entry| entry.racy)
                .map(|entry| entry.path.as_path())
                .collect()
        });
        let cutoff = started
            .checked_sub(TIMESTAMP_ALLOWANCE)
            .unwrap_or(UNIX_EPOCH);
        let mut record = |path: PathBuf, metadata: &fs::Metadata| -> Result<(), TakeError> {
            if Instant::now() >= deadline {
                return Err(TakeError::Deadline);
            }
            use std::os::unix::fs::MetadataExt;
            let modified = metadata.modified().ok();
            let changed = (metadata.ctime(), metadata.ctime_nsec());
            let changed_time = UNIX_EPOCH
                .checked_add(Duration::new(
                    changed.0.max(0) as u64,
                    changed.1.clamp(0, 999_999_999) as u32,
                ))
                .unwrap_or(UNIX_EPOCH);
            let racy = modified.is_none_or(|time| time >= cutoff) || changed_time >= cutoff;
            let digest = if metadata.is_file() && (racy || hashed.contains(path.as_path())) {
                Some(hash_file(&path, deadline)?)
            } else {
                None
            };
            entries.push(Entry {
                path,
                size: metadata.len(),
                modified,
                changed,
                digest,
                racy,
            });
            Ok(())
        };
        let mut pending = vec![root.to_path_buf()];
        while let Some(directory) = pending.pop() {
            let listing =
                fs::read_dir(&directory).map_err(|e| format!("{}: {e}", directory.display()))?;
            for item in listing {
                let item = item.map_err(|e| e.to_string())?;
                let name = item.file_name();
                if [".git", "node_modules", ".oximut"]
                    .iter()
                    .any(|skipped| name == *skipped)
                {
                    continue;
                }
                let path = item.path();
                let metadata =
                    fs::symlink_metadata(&path).map_err(|e| format!("{}: {e}", path.display()))?;
                if metadata.file_type().is_symlink() {
                    symlink = true;
                    record(path, &metadata)?;
                } else if metadata.is_dir() {
                    pending.push(path);
                } else {
                    record(path, &metadata)?;
                }
            }
        }
        let metadata = fs::metadata(config).map_err(|e| format!("{}: {e}", config.display()))?;
        record(config.to_path_buf(), &metadata)?;
        entries.sort_by(|a, b| a.path.cmp(&b.path));
        Ok(Self {
            root: root.to_path_buf(),
            config: config.to_path_buf(),
            entries,
            symlink,
        })
    }

    /// Content hashes taken only by one side do not differ by themselves;
    /// metadata still must match, and a hash present on both sides must match.
    fn same(&self, other: &Fingerprint) -> bool {
        self.root == other.root
            && self.config == other.config
            && self.entries.len() == other.entries.len()
            && self.entries.iter().zip(&other.entries).all(|(a, b)| {
                a.path == b.path
                    && a.size == b.size
                    && a.modified == b.modified
                    && a.changed == b.changed
                    && match (a.digest, b.digest) {
                        (Some(a), Some(b)) => a == b,
                        _ => true,
                    }
            })
    }
}

// ---------------------------------------------------------------------------
// Node IPC channel (JSON serialization: one JSON document per line).

/// Splits complete lines out of `buffer`, bounding each message.
fn take_messages(buffer: &mut Vec<u8>) -> Result<Vec<serde_json::Value>, String> {
    let mut messages = vec![];
    while let Some(end) = buffer.iter().position(|&byte| byte == b'\n') {
        let line: Vec<u8> = buffer.drain(..=end).collect();
        if line.len() > MAX_MESSAGE_BYTES {
            return Err("controller message exceeds the response limit".into());
        }
        messages.push(
            serde_json::from_slice(&line[..line.len() - 1])
                .map_err(|e| format!("malformed controller message: {e}"))?,
        );
    }
    if buffer.len() > MAX_MESSAGE_BYTES {
        return Err("controller message exceeds the response limit".into());
    }
    Ok(messages)
}

enum Reply {
    Result(serde_json::Value),
    Error(String),
}

enum Wait {
    Deadline,
    Failed(String),
}

#[derive(Default)]
struct Output {
    text: Vec<u8>,
    truncated: bool,
}

struct Controller {
    child: Child,
    channel: UnixStream,
    buffer: Vec<u8>,
    next_id: u64,
    reuse: String,
    baseline: BTreeMap<u32, u64>,
    output: Arc<Mutex<Output>>,
    reaped: bool,
}

fn drain(mut stream: impl Read + Send + 'static, output: Arc<Mutex<Output>>) {
    thread::spawn(move || {
        let mut chunk = [0u8; 8192];
        while let Ok(read) = stream.read(&mut chunk) {
            if read == 0 {
                break;
            }
            let mut output = output.lock().expect("controller output");
            let room = OUTPUT_BYTES.saturating_sub(output.text.len());
            output.text.extend_from_slice(&chunk[..read.min(room)]);
            output.truncated |= read > room;
        }
    });
}

fn poll(fd: i32, events: libc::c_short, timeout: Duration) -> Result<libc::c_short, String> {
    let mut item = libc::pollfd {
        fd,
        events,
        revents: 0,
    };
    let millis = timeout.as_millis().min(i32::MAX as u128) as i32;
    let ready = unsafe { libc::poll(&mut item, 1, millis) };
    if ready < 0 {
        let error = std::io::Error::last_os_error();
        if error.kind() == std::io::ErrorKind::Interrupted {
            return Ok(0);
        }
        return Err(format!("controller channel poll failed: {error}"));
    }
    Ok(if ready == 0 { 0 } else { item.revents })
}

impl Controller {
    fn spawn(options: &Options, worker: &Path) -> Result<Self, String> {
        probe_pidfd()?;
        let (ours, theirs) = UnixStream::pair().map_err(|e| format!("controller channel: {e}"))?;
        let fd = theirs.as_raw_fd();
        let mut command = Command::new("node");
        command
            .arg(worker)
            .current_dir(&options.root)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("NODE_CHANNEL_FD", fd.to_string())
            .env("NODE_CHANNEL_SERIALIZATION_MODE", "json")
            .env("OXIMUT_BUDGETED", "1")
            .env("RAYON_NUM_THREADS", "1")
            .env("UV_THREADPOOL_SIZE", "1")
            .process_group(0);
        // The child keeps its channel end at the same descriptor number.
        unsafe {
            command.pre_exec(move || {
                if libc::fcntl(fd, libc::F_SETFD, 0) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("could not start Vitest controller: {e}"))?;
        drop(theirs);
        ours.set_nonblocking(true)
            .map_err(|e| format!("controller channel: {e}"))?;
        let output = Arc::new(Mutex::new(Output::default()));
        if let Some(stdout) = child.stdout.take() {
            drain(stdout, output.clone());
        }
        if let Some(stderr) = child.stderr.take() {
            drain(stderr, output.clone());
        }
        Ok(Self {
            child,
            channel: ours,
            buffer: vec![],
            next_id: 0,
            reuse: String::new(),
            baseline: BTreeMap::new(),
            output,
            reaped: false,
        })
    }

    fn diagnostics(&self) -> String {
        let output = self.output.lock().expect("controller output");
        let mut text = String::from_utf8_lossy(&output.text).into_owned();
        if output.truncated {
            text.push_str("\n[controller output truncated]");
        }
        text
    }

    fn send(
        &mut self,
        id: u64,
        command: &str,
        args: &serde_json::Value,
        deadline: Instant,
    ) -> Result<(), Wait> {
        let mut bytes = serde_json::to_vec(&serde_json::json!({
            "protocol": 1, "id": id, "command": command, "args": args,
        }))
        .map_err(|e| Wait::Failed(e.to_string()))?;
        bytes.push(b'\n');
        let mut written = 0;
        while written < bytes.len() {
            match self.channel.write(&bytes[written..]) {
                Ok(0) => return Err(Wait::Failed("Controller channel closed".into())),
                Ok(count) => written += count,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    let now = Instant::now();
                    if now >= deadline {
                        return Err(Wait::Deadline);
                    }
                    poll(
                        self.channel.as_raw_fd(),
                        libc::POLLOUT,
                        (deadline - now).min(Duration::from_millis(50)),
                    )
                    .map_err(Wait::Failed)?;
                }
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                Err(error) => {
                    return Err(Wait::Failed(format!("Controller channel closed: {error}")));
                }
            }
        }
        Ok(())
    }

    /// One request, bounded by `deadline`; only the matching reply counts.
    fn request(
        &mut self,
        command: &str,
        args: &serde_json::Value,
        deadline: Instant,
    ) -> Result<Reply, Wait> {
        self.next_id += 1;
        let id = self.next_id;
        self.send(id, command, args, deadline)?;
        let mut chunk = [0u8; 65536];
        loop {
            for message in take_messages(&mut self.buffer).map_err(Wait::Failed)? {
                if message.get("protocol") != Some(&serde_json::json!(1))
                    || message.get("id").and_then(serde_json::Value::as_u64) != Some(id)
                {
                    continue;
                }
                // A reply that arrives after the deadline is never accepted.
                if Instant::now() >= deadline {
                    return Err(Wait::Deadline);
                }
                if let Some(error) = message.get("error") {
                    return Ok(Reply::Error(
                        error.as_str().unwrap_or("controller error").into(),
                    ));
                }
                return Ok(Reply::Result(
                    message
                        .get("result")
                        .cloned()
                        .unwrap_or(serde_json::Value::Null),
                ));
            }
            match self.channel.read(&mut chunk) {
                Ok(0) => {
                    // Never block on the child here; cleanup reaps it.
                    let status = self.child.try_wait().ok().flatten();
                    self.reaped |= status.is_some();
                    return Err(Wait::Failed(format!(
                        "Controller exited ({})",
                        status.map_or_else(|| "channel closed".into(), |s| s.to_string())
                    )));
                }
                Ok(count) => self.buffer.extend_from_slice(&chunk[..count]),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    let now = Instant::now();
                    if now >= deadline {
                        return Err(Wait::Deadline);
                    }
                    poll(
                        self.channel.as_raw_fd(),
                        libc::POLLIN,
                        (deadline - now).min(Duration::from_millis(100)),
                    )
                    .map_err(Wait::Failed)?;
                }
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                Err(error) => {
                    return Err(Wait::Failed(format!("Controller channel closed: {error}")));
                }
            }
        }
    }

    /// Verified cleanup of the whole controller tree within its own bound.
    /// Success requires every owned process to have exited and been reaped.
    fn terminate(&mut self) -> Result<(), String> {
        let deadline = Instant::now() + Duration::from_millis(CLEANUP_MS);
        // The unreaped direct child pins its PID; descendants are signalled
        // individually through verified identities below.
        if !self.reaped {
            let _ = self.child.kill();
        }
        loop {
            if !self.reaped && self.child.try_wait().ok().flatten().is_some() {
                self.reaped = true;
            }
            // Reap adopted orphans; only owned processes are our children.
            loop {
                let pid = unsafe { libc::waitpid(-1, std::ptr::null_mut(), libc::WNOHANG) };
                if pid <= 0 {
                    break;
                }
                if pid as u32 == self.child.id() {
                    self.reaped = true;
                }
            }
            let remaining = owned()?;
            if remaining.is_empty() && self.reaped {
                return Ok(());
            }
            for (&pid, &start) in &remaining {
                signal_verified(pid, start);
            }
            if Instant::now() >= deadline {
                return Err(format!(
                    "Incomplete owned-process cleanup: {} processes survived",
                    remaining.len()
                ));
            }
            thread::sleep(Duration::from_millis(5));
        }
    }

    /// Graceful close, falling back to verified cleanup. Either way, success
    /// requires an empty owned tree afterwards; `Ok(false)` reports that the
    /// controller had to be killed (it did not close gracefully).
    fn close(mut self) -> Result<bool, String> {
        let deadline = Instant::now() + Duration::from_millis(CLOSE_MS);
        let graceful = match self.request("close", &serde_json::json!({}), deadline) {
            Ok(Reply::Result(_)) => {
                let _ = self.channel.shutdown(std::net::Shutdown::Both);
                let exit_by = Instant::now() + Duration::from_millis(EXIT_MS);
                loop {
                    if self.child.try_wait().ok().flatten().is_some() {
                        self.reaped = true;
                        break true;
                    }
                    if Instant::now() >= exit_by {
                        break false;
                    }
                    thread::sleep(Duration::from_millis(5));
                }
            }
            _ => false,
        };
        self.terminate().map(|()| graceful)
    }
}

// ---------------------------------------------------------------------------
// Jobs.

fn empty(request: &Request, outcome: TestOutcome, error: impl Into<String>) -> Response {
    let mut response = infrastructure(request, error);
    response.outcome = outcome;
    response
}

/// The JS relay's envelope: request identity plus the controller's result,
/// with at most eight errors of at most 2048 characters, within 1 MiB.
fn envelope(request: &Request, result: serde_json::Value) -> Result<Response, String> {
    let serde_json::Value::Object(mut fields) = result else {
        return Err("controller result is not an object".into());
    };
    if let Some(serde_json::Value::Array(errors)) = fields.get_mut("errors") {
        errors.truncate(8);
        for error in errors.iter_mut() {
            if let serde_json::Value::String(text) = error {
                *text = text.chars().take(2048).collect();
            }
        }
    }
    fields.insert("protocol".into(), request.protocol.into());
    fields.insert("runId".into(), request.run_id.clone().into());
    fields.insert("jobId".into(), request.job_id.clone().into());
    fields.insert("generation".into(), request.generation.into());
    let value = serde_json::Value::Object(fields);
    if encoded_len(&value) > MAX_RESPONSE_BYTES {
        return Err("Response exceeds 1 MiB".into());
    }
    let response: Response =
        serde_json::from_value(value).map_err(|e| format!("malformed controller result: {e}"))?;
    // The CLI validates every slot response again; checking here retires an
    // untrusted controller before its result leaves the slot.
    response.validate(request)?;
    Ok(response)
}

pub struct Coordinator {
    worker: PathBuf,
    controller: Option<Controller>,
    fingerprint: Option<Fingerprint>,
    jobs: usize,
    pub reuse_modes: BTreeSet<String>,
    /// Controllers started and jobs run, reported as reuse evidence.
    pub controllers_started: usize,
    pub jobs_run: usize,
    /// Set when cleanup could not prove the slot owns nothing: no later job
    /// may run beside processes that may have survived.
    poisoned: Option<String>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Run,
    Warm,
    PrepareLanes,
}

impl Coordinator {
    pub fn new(worker: PathBuf) -> Self {
        Self {
            worker,
            controller: None,
            fingerprint: None,
            jobs: 0,
            reuse_modes: BTreeSet::new(),
            controllers_started: 0,
            jobs_run: 0,
            poisoned: None,
        }
    }

    /// Retires the controller. Only a failed verified cleanup is an error
    /// (and poisons the slot); `Ok(false)` means it had to be killed.
    fn close(&mut self) -> Result<bool, String> {
        let closed = self.controller.take().map_or(Ok(true), Controller::close);
        if let Err(error) = &closed {
            self.poisoned.get_or_insert_with(|| error.clone());
        }
        closed
    }

    /// Verified cleanup of a controller that never joined the coordinator.
    fn discard(&mut self, mut controller: Controller) -> Result<(), String> {
        let cleaned = controller.terminate();
        if let Err(error) = &cleaned {
            self.poisoned.get_or_insert_with(|| error.clone());
        }
        cleaned
    }

    /// Retire the controller and report `response`, unless cleanup fails:
    /// then ownership is uncertain and the job is an infrastructure error.
    fn retire_with(&mut self, request: &Request, response: Response) -> Response {
        match self.close() {
            Ok(_) => response,
            Err(error) => infrastructure(request, error),
        }
    }

    /// Processes owned now that were not owned when the controller started.
    /// A test fork can exit during enumeration: an empty second scan alone
    /// cannot prove disappearance, so every observed PID is checked too.
    fn leaked(&self, deadline: Instant) -> Result<bool, Wait> {
        let baseline = &self.controller.as_ref().expect("controller").baseline;
        let check = || {
            if Instant::now() >= deadline {
                Err(Wait::Deadline)
            } else {
                Ok(())
            }
        };
        // Let exiting test forks finish (bounded); a process still present
        // afterwards is judged by the strict verification below.
        let grace = (Instant::now() + Duration::from_millis(EXIT_GRACE_MS)).min(deadline);
        while owned_hint()
            .iter()
            .any(|(pid, start)| baseline.get(pid) != Some(start))
            && Instant::now() < grace
        {
            thread::sleep(Duration::from_millis(2));
        }
        let unexpected = || -> Result<Vec<(u32, u64)>, Wait> {
            Ok(owned()
                .map_err(Wait::Failed)?
                .into_iter()
                .filter(|(pid, start)| baseline.get(pid) != Some(start))
                .collect())
        };
        check()?;
        let observed = unexpected()?;
        check()?;
        if observed.is_empty() {
            return Ok(false);
        }
        if !unexpected()?.is_empty() {
            return Ok(true);
        }
        for &(pid, start) in &observed {
            check()?;
            if still_owned(pid, start) {
                return Ok(true);
            }
        }
        check()?;
        // A fork by an exiting process can be missed while it is reparented
        // mid-scan; every observed process is now gone, so rescan once more.
        Ok(!unexpected()?.is_empty())
    }

    pub fn run(&mut self, options: &Options, request: &Request) -> Response {
        self.run_inner(options, request, Mode::Run)
    }

    pub fn warm(&mut self, options: &Options, request: &Request) -> Response {
        self.run_inner(options, request, Mode::Warm)
    }

    /// Builds the shared Vitest lane context ahead of the first batch. The
    /// request's `workers` is the lane count; no test, module or fork runs.
    pub fn prepare_lanes(&mut self, options: &Options, request: &Request) -> Response {
        self.run_inner(options, request, Mode::PrepareLanes)
    }

    fn run_inner(&mut self, options: &Options, request: &Request, mode: Mode) -> Response {
        let warm = mode != Mode::Run;
        let started = Instant::now();
        let deadline = started + Duration::from_millis(request.deadline_ms);
        let timeout = |message: &str| empty(request, TestOutcome::Timeout, message);
        if let Some(error) = &self.poisoned {
            return infrastructure(
                request,
                format!("slot cleanup could not be verified earlier: {error}"),
            );
        }
        let snapshot = match Fingerprint::take(
            &options.root,
            &options.config,
            self.fingerprint.as_ref(),
            deadline,
        ) {
            Ok(snapshot) => snapshot,
            // A timed-out job always retires its controller.
            Err(TakeError::Deadline) => {
                return self.retire_with(request, timeout("Job deadline exceeded"));
            }
            Err(TakeError::Io(error)) => {
                let failure = infrastructure(request, error);
                return self.retire_with(request, failure);
            }
        };
        let changed = self
            .fingerprint
            .as_ref()
            .is_none_or(|previous| !previous.same(&snapshot));
        if (snapshot.symlink || changed || self.jobs >= MAX_JOBS_PER_CONTROLLER)
            && let Err(error) = self.close()
        {
            return infrastructure(request, error);
        }
        let symlink = snapshot.symlink;
        self.fingerprint = Some(snapshot);
        if Instant::now() >= deadline {
            return self.retire_with(request, timeout("Job deadline exceeded"));
        }
        let mut args = match serde_json::to_value(request) {
            Ok(args) => args,
            Err(error) => return infrastructure(request, error.to_string()),
        };
        if self.controller.is_none() {
            let mut controller = match Controller::spawn(options, &self.worker) {
                Ok(controller) => controller,
                Err(error) => return infrastructure(request, error),
            };
            self.controllers_started += 1;
            let failure = match controller.request("initialize", &args, deadline) {
                Ok(Reply::Result(initialized)) => {
                    let pool_ok = match request.pool.as_deref() {
                        Some("threads") => {
                            initialized.get("pool").and_then(serde_json::Value::as_str)
                                == Some("threads")
                        }
                        _ => initialized
                            .get("pool")
                            .and_then(serde_json::Value::as_str)
                            .is_none_or(|pool| pool == "forks"),
                    };
                    if !pool_ok {
                        Some(infrastructure(
                            request,
                            "controller initialize result pool does not match requested pool",
                        ))
                    } else {
                        match initialized.get("reuse").and_then(serde_json::Value::as_str) {
                            Some(reuse) => {
                                controller.reuse = if symlink {
                                    "fresh: symlink inputs require fresh evaluation".into()
                                } else {
                                    reuse.into()
                                };
                                None
                            }
                            None => Some(infrastructure(
                                request,
                                "controller initialize result must contain a string reuse",
                            )),
                        }
                    }
                }
                Ok(Reply::Error(error)) | Err(Wait::Failed(error)) => Some(infrastructure(
                    request,
                    format!("{error}\n{}", controller.diagnostics()),
                )),
                Err(Wait::Deadline) => Some(timeout(
                    "Controller initialization timed out after verified cleanup",
                )),
            };
            if let Some(failure) = failure {
                return match self.discard(controller) {
                    Ok(()) => failure,
                    Err(error) => infrastructure(request, error),
                };
            }
            controller.baseline = match owned() {
                Ok(baseline) => baseline,
                Err(error) => {
                    let _ = self.discard(controller);
                    return infrastructure(request, error);
                }
            };
            self.reuse_modes.insert(controller.reuse.clone());
            self.jobs = 0;
            self.controller = Some(controller);
        }
        let controller = self.controller.as_mut().expect("controller");
        if warm && controller.reuse != "controller" {
            return self.retire_with(
                request,
                infrastructure(request, "spare controller cannot be reused"),
            );
        }
        charge_lane_startup(&mut args, started.elapsed());
        let reply = if Instant::now() >= deadline {
            Err(Wait::Deadline)
        } else {
            controller.request(
                match mode {
                    Mode::Run => "run",
                    Mode::Warm => "warm",
                    Mode::PrepareLanes => "prepare-lanes",
                },
                &serde_json::json!({ "request": args }),
                deadline,
            )
        };
        if !warm {
            self.jobs += 1;
            self.jobs_run += 1;
        }
        let response = match reply {
            Ok(Reply::Result(mut result)) => {
                if warm && result.get("warmed").and_then(serde_json::Value::as_bool) != Some(true) {
                    Err("controller did not acknowledge warm".into())
                } else {
                    if warm && let Some(fields) = result.as_object_mut() {
                        fields.remove("warmed");
                        fields.remove("transformed");
                    }
                    envelope(request, result)
                }
            }
            Ok(Reply::Error(error)) | Err(Wait::Failed(error)) => Err(error),
            Err(Wait::Deadline) => {
                return self.retire_with(
                    request,
                    timeout("Job deadline exceeded; owned processes reaped"),
                );
            }
        };
        let response = match response {
            Ok(response) => response,
            // Never accept another job on a controller with untrusted state.
            Err(error) => {
                let failure = infrastructure(request, error);
                return self.retire_with(request, failure);
            }
        };
        let lane_failure = response.lanes.as_ref().is_some_and(|lanes| {
            lanes.iter().any(|lane| {
                matches!(
                    lane.outcome,
                    TestOutcome::Timeout | TestOutcome::InfrastructureError
                )
            })
        });
        let retire = lane_failure
            || matches!(
                response.outcome,
                TestOutcome::Timeout | TestOutcome::InfrastructureError
            )
            || self
                .controller
                .as_ref()
                .is_some_and(|c| c.reuse != "controller");
        let leaked = if retire {
            false
        } else {
            match self.leaked(deadline) {
                Ok(leaked) => leaked,
                // Verification may cross the deadline; never publish late.
                Err(Wait::Deadline) => {
                    let late = timeout("Job deadline exceeded during descendant verification");
                    return self.retire_with(request, late);
                }
                Err(Wait::Failed(error)) => {
                    let failure = infrastructure(request, error);
                    return self.retire_with(request, failure);
                }
            }
        };
        if warm && leaked {
            return self.retire_with(
                request,
                infrastructure(request, "warm left owned descendants"),
            );
        }
        // Never publish a result after the deadline, retiring or not.
        if Instant::now() >= deadline {
            let late = timeout("Job deadline exceeded during descendant verification");
            return self.retire_with(request, late);
        }
        // Retiring is not job time: cleanup has its own bound.
        if retire || leaked {
            return self.retire_with(request, response);
        }
        response
    }

    /// Final shutdown: close the controller and prove the slot owns nothing.
    pub fn finish(&mut self) -> Result<(), String> {
        // A controller that must be killed at shutdown invalidates evidence.
        let closed = self.close().and_then(|graceful| {
            graceful
                .then_some(())
                .ok_or_else(|| "Controller did not close; owned processes reaped".to_string())
        });
        let deadline = Instant::now() + Duration::from_millis(CLEANUP_MS);
        loop {
            loop {
                let pid = unsafe { libc::waitpid(-1, std::ptr::null_mut(), libc::WNOHANG) };
                if pid <= 0 {
                    break;
                }
            }
            let remaining = owned()?;
            if remaining.is_empty() {
                break;
            }
            for (&pid, &start) in &remaining {
                signal_verified(pid, start);
            }
            if Instant::now() >= deadline {
                return Err("persistent adapter shutdown left owned descendants".into());
            }
            thread::sleep(Duration::from_millis(5));
        }
        closed.map_err(|error| format!("persistent adapter shutdown failed: {error}"))
    }
}

impl Drop for Coordinator {
    fn drop(&mut self) {
        let _ = self.finish();
    }
}

/// Job deadlines include controller start-up. Lanes start counting when the
/// adapter receives them, so they only get what is left of the job's budget.
fn charge_lane_startup(args: &mut serde_json::Value, spent: Duration) {
    let spent = u64::try_from(spent.as_millis()).unwrap_or(u64::MAX);
    let lanes = args
        .get_mut("lanes")
        .and_then(serde_json::Value::as_array_mut);
    for lane in lanes.into_iter().flatten() {
        if let Some(budget) = lane.get("deadlineMs").and_then(serde_json::Value::as_u64) {
            lane["deadlineMs"] = budget.saturating_sub(spent).max(1).into();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stat(parent: u32, start: u64) -> String {
        // Include spaces and ')' in comm to exercise the final-delimiter rule.
        let mut fields = vec!["0".to_string(); 20];
        fields[0] = "S".into();
        fields[1] = parent.to_string();
        fields[19] = start.to_string();
        format!("42 (name ) with spaces) {}", fields.join(" "))
    }

    #[test]
    fn strict_scan_classifies_errors_and_projects_descendants() {
        use std::io::{Error, ErrorKind};
        let table = strict_owned(
            [Ok(None), Ok(Some(1)), Ok(Some(2)), Ok(Some(3)), Ok(Some(4))],
            |pid| match pid {
                1 => Err(Error::from(ErrorKind::NotFound)),
                2 => Err(Error::from(ErrorKind::PermissionDenied)),
                3 => Err(Error::from_raw_os_error(libc::ESRCH)),
                4 => Ok(stat(10, 40)),
                _ => unreachable!(),
            },
            10,
        )
        .unwrap();
        assert_eq!(table, BTreeMap::from([(4, 40)]));
        for kind in [
            ErrorKind::Interrupted,
            ErrorKind::InvalidData,
            ErrorKind::Other,
        ] {
            let error = strict_owned([Ok(Some(4))], |_| Err(Error::from(kind)), 10).unwrap_err();
            assert!(error.contains("process identity unavailable for PID 4"));
        }
        for malformed in ["invalid".to_string(), stat(10, 40).replace("40", "bad")] {
            assert!(
                strict_owned([Ok(Some(4))], |_| Ok(malformed.clone()), 10)
                    .unwrap_err()
                    .contains("invalid process identity for PID 4")
            );
        }
        assert_eq!(
            strict_owned([Err("directory entry".into())], |_| unreachable!(), 10).unwrap_err(),
            "directory entry"
        );
        let data = BTreeMap::from([
            (10, (1, 100)),
            (20, (10, 200)),
            (15, (20, 300)),
            (30, (10, 400)),
            (40, (10, 500)),
            (90, (1, 600)),
            (91, (90, 700)),
        ]);
        let project = |root| {
            strict_owned(
                data.keys().map(|&pid| Ok(Some(pid))),
                |pid| {
                    let (parent, start) = data[&pid];
                    Ok(stat(parent, start))
                },
                root,
            )
            .unwrap()
        };
        assert_eq!(
            project(10),
            BTreeMap::from([(15, 300), (20, 200), (30, 400), (40, 500)])
        );
        assert_eq!(project(20), BTreeMap::from([(15, 300)]));
        assert!(project(15).is_empty());
    }

    #[test]
    fn messages_split_on_lines_and_stay_bounded() {
        let mut buffer = b"{\"id\":1}\n{\"id\":2}\n{\"id\":".to_vec();
        let messages = take_messages(&mut buffer).unwrap();
        assert_eq!(
            messages,
            vec![serde_json::json!({"id": 1}), serde_json::json!({"id": 2})]
        );
        assert_eq!(buffer, b"{\"id\":");
        buffer.extend_from_slice(b"3}\n");
        assert_eq!(
            take_messages(&mut buffer).unwrap(),
            vec![serde_json::json!({"id": 3})]
        );
        assert!(take_messages(&mut b"{bad\n".to_vec()).is_err());
        let mut oversized = vec![b' '; MAX_MESSAGE_BYTES + 1];
        assert!(take_messages(&mut oversized).is_err());
        let mut long_line = vec![b' '; MAX_MESSAGE_BYTES];
        long_line.extend_from_slice(b"1\n");
        assert!(take_messages(&mut long_line).is_err());
    }

    #[test]
    fn lanes_only_get_what_is_left_of_the_job_budget() {
        let mut args = serde_json::json!({
            "deadlineMs": 6000,
            "lanes": [{ "deadlineMs": 5000 }, { "deadlineMs": 5000 }],
        });
        charge_lane_startup(&mut args, Duration::from_millis(1500));
        assert_eq!(args["deadlineMs"], 6000);
        assert_eq!(args["lanes"][0]["deadlineMs"], 3500);
        assert_eq!(args["lanes"][1]["deadlineMs"], 3500);
        charge_lane_startup(&mut args, Duration::from_secs(60));
        assert_eq!(args["lanes"][1]["deadlineMs"], 1);
    }

    fn request() -> Request {
        serde_json::from_value(serde_json::json!({
            "protocol": 1, "runId": "r", "jobId": "j", "generation": 3,
            "kind": "baseline", "root": "/p", "config": "/p/c", "deadlineMs": 10,
            "mutation": null
        }))
        .unwrap()
    }

    #[test]
    fn envelope_adds_identity_and_truncates_errors() {
        let errors: Vec<_> = (0..10)
            .map(|i| format!("{i}{}", "x".repeat(3000)))
            .collect();
        let response = envelope(
            &request(),
            serde_json::json!({
                "outcome": "failed", "tests": {"passed": 0, "failed": 1, "skipped": 0},
                "errors": errors,
            }),
        )
        .unwrap();
        assert_eq!((response.job_id.as_str(), response.generation), ("j", 3));
        assert_eq!(response.errors.len(), 8);
        assert!(response.errors.iter().all(|e| e.chars().count() == 2048));
        assert!(envelope(&request(), serde_json::json!([])).is_err());
        // Semantic validation: a pass without passing tests is not evidence.
        let empty_pass = serde_json::json!({
            "outcome": "passed", "tests": {"passed": 0, "failed": 0, "skipped": 0}, "errors": [],
        });
        assert!(
            envelope(&request(), empty_pass)
                .unwrap_err()
                .contains("passing response")
        );
        assert!(
            envelope(
                &request(),
                serde_json::json!({"outcome": "passed", "unknown": 1})
            )
            .is_err()
        );
        let huge = serde_json::json!({
            "outcome": "passed", "tests": {"passed": 1, "failed": 0, "skipped": 0},
            "errors": [], "coverage": {"complete": false, "tests": [], "hits": [],
            "reasons": ["x".repeat(MAX_RESPONSE_BYTES)]},
        });
        assert!(envelope(&request(), huge).unwrap_err().contains("1 MiB"));
    }

    #[test]
    fn reference_adapter_runs_natively_and_others_keep_the_relay() {
        let directory = tempfile::tempdir().unwrap();
        let runner = directory.path().join("m2-runner.mjs");
        fs::write(&runner, "").unwrap();
        assert_eq!(native_worker(&runner), None, "the controller must exist");
        fs::write(directory.path().join("m2-worker.ts"), "").unwrap();
        assert_eq!(
            native_worker(&runner),
            Some(directory.path().join("m2-worker.ts"))
        );
        assert_eq!(native_worker(&directory.path().join("adapter.mjs")), None);
    }

    fn far() -> Instant {
        Instant::now() + Duration::from_secs(60)
    }

    #[test]
    fn fingerprint_stops_at_the_job_deadline() {
        let root = tempfile::tempdir().unwrap();
        let config = root.path().join("vitest.config.ts");
        fs::write(&config, "export default {}").unwrap();
        assert!(matches!(
            Fingerprint::take(root.path(), &config, None, Instant::now()),
            Err(TakeError::Deadline)
        ));
        let big = root.path().join("big.bin");
        fs::write(&big, vec![7u8; 3 << 16]).unwrap();
        assert_eq!(
            hash_file(&big, far()).ok(),
            Some(Sha256::digest(fs::read(&big).unwrap()).into())
        );
        assert!(matches!(
            hash_file(&big, Instant::now()),
            Err(TakeError::Deadline)
        ));
    }

    #[test]
    fn fingerprint_detects_same_size_rewrites_with_restored_timestamps() {
        let root = tempfile::tempdir().unwrap();
        let config = root.path().join("vitest.config.ts");
        fs::write(&config, "export default {}").unwrap();
        let source = root.path().join("source.ts");
        fs::write(&source, "export const value = 1;").unwrap();
        fs::create_dir(root.path().join("node_modules")).unwrap();
        fs::write(root.path().join("node_modules/ignored.js"), "a").unwrap();
        let first = Fingerprint::take(root.path(), &config, None, far()).unwrap();
        // Freshly written files are racy and therefore hashed.
        assert!(first.entries.iter().all(|e| e.racy && e.digest.is_some()));
        assert!(
            first
                .entries
                .iter()
                .all(|e| !e.path.starts_with(root.path().join("node_modules")))
        );
        let unchanged = Fingerprint::take(root.path(), &config, Some(&first), far()).unwrap();
        assert!(first.same(&unchanged));
        // Equal size, restored mtime: only the content hash can tell. ctime
        // cannot be restored by user code, so compare against a snapshot whose
        // recorded ctime is copied over to model a coarse-timestamp filesystem.
        let modified = fs::metadata(&source).unwrap().modified().unwrap();
        fs::write(&source, "export const value = 2;").unwrap();
        fs::File::options()
            .write(true)
            .open(&source)
            .unwrap()
            .set_modified(modified)
            .unwrap();
        let mut rewritten =
            Fingerprint::take(root.path(), &config, Some(&unchanged), far()).unwrap();
        for (entry, before) in rewritten.entries.iter_mut().zip(&unchanged.entries) {
            entry.changed = before.changed;
        }
        assert!(
            !unchanged.same(&rewritten),
            "same-tick rewrite must change the fingerprint"
        );
        // Without a content hash on both sides the metadata alone decides.
        let mut stale = rewritten.clone();
        for entry in &mut stale.entries {
            entry.digest = None;
        }
        assert!(unchanged.same(&stale));
        std::os::unix::fs::symlink(&source, root.path().join("link.ts")).unwrap();
        assert!(
            Fingerprint::take(root.path(), &config, None, far())
                .unwrap()
                .symlink
        );
    }
}
