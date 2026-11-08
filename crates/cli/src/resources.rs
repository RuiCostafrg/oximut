//! Linux sampled admission control. Never claims to install an OS hard limit.
use super::{
    Options,
    processes::{ProcessTable, identity},
};
use serde::Serialize;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

// Profiling is process-wide configuration and is read once. In the default
// path no diagnostic clock or getrusage reads occur.
static ACCOUNTING_PROFILE: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
fn profile_enabled() -> bool {
    *ACCOUNTING_PROFILE.get_or_init(|| {
        std::env::var_os("OXIMUT_PROFILE_ACCOUNTING").as_deref() == Some(std::ffi::OsStr::new("1"))
    })
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AccountingDiagnostics {
    pub clock_version: u8,
    pub thread_cpu_since_budget_start_ns: Option<u64>,
    pub thread_cpu_failures: u64,
    pub scope: &'static str,
    pub evidence: &'static str,
    pub sampling: StageTotals,
    pub discovery: StageTotals,
    pub identity_reads: StageTotals,
    pub rss_reads: StageTotals,
    pub timing_note: &'static str,
}
#[derive(Clone, Debug, Default, Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StageTotals {
    pub calls: u64,
    pub wall_ns: Option<u64>,
    pub self_user_ns: Option<u64>,
    pub self_system_ns: Option<u64>,
    pub self_cpu_ns: Option<u64>,
    pub self_cpu_failures: u64,
    pub cpu_failures: u64,
    pub timing_failures: u64,
}
fn totals() -> StageTotals {
    StageTotals {
        wall_ns: Some(0),
        self_user_ns: Some(0),
        self_system_ns: Some(0),
        self_cpu_ns: Some(0),
        ..StageTotals::default()
    }
}
impl StageTotals {
    fn merge(&mut self, previous: Self) {
        self.cpu_failures = self.cpu_failures.saturating_add(previous.cpu_failures);
        self.timing_failures = self
            .timing_failures
            .saturating_add(previous.timing_failures);
        match self.calls.checked_add(previous.calls) {
            Some(calls) => self.calls = calls,
            None => {
                self.calls = u64::MAX;
                self.timing_failures = self.timing_failures.saturating_add(1);
                self.wall_ns = None;
            }
        }
        fn merge_total(current: &mut Option<u64>, previous: Option<u64>) -> bool {
            let overflow = current
                .zip(previous)
                .is_some_and(|(a, b)| a.checked_add(b).is_none());
            *current = current.zip(previous).and_then(|(a, b)| a.checked_add(b));
            overflow
        }
        if merge_total(&mut self.wall_ns, previous.wall_ns) {
            self.timing_failures = self.timing_failures.saturating_add(1);
        }
        let user_overflow = merge_total(&mut self.self_user_ns, previous.self_user_ns);
        let system_overflow = merge_total(&mut self.self_system_ns, previous.self_system_ns);
        if user_overflow || system_overflow {
            self.cpu_failures = self.cpu_failures.saturating_add(1);
        }
        let precise_overflow = merge_total(&mut self.self_cpu_ns, previous.self_cpu_ns);
        self.self_cpu_failures = self
            .self_cpu_failures
            .saturating_add(previous.self_cpu_failures);
        if precise_overflow || (self.self_cpu_ns.is_none() && self.self_cpu_failures == 0) {
            self.self_cpu_failures = self.self_cpu_failures.saturating_add(1);
        }
        if self.self_user_ns.is_none() || self.self_system_ns.is_none() {
            self.self_user_ns = None;
            self.self_system_ns = None;
        }
    }
    fn start(&mut self) -> StageMark {
        match self.calls.checked_add(1) {
            Some(calls) => self.calls = calls,
            None => {
                self.timing_failures = self.timing_failures.saturating_add(1);
                self.wall_ns = None;
            }
        }
        StageMark {
            raw: thread_cpu_ns(),
            precise: precise_thread_cpu_ns(),
        }
    }
    fn end(&mut self, mark: StageMark, wall_start: Instant) {
        let precise_end = precise_thread_cpu_ns();
        let raw_end = thread_cpu_ns();
        self.record_elapsed(
            u64::try_from(wall_start.elapsed().as_nanos()).ok(),
            mark.raw,
            raw_end,
        );
        let delta = mark
            .precise
            .zip(precise_end)
            .and_then(|(a, b)| b.checked_sub(a));
        self.record_precise_delta(delta);
    }
    fn record_precise_delta(&mut self, delta: Option<u64>) {
        if !add_delta(&mut self.self_cpu_ns, delta) {
            self.self_cpu_failures = self.self_cpu_failures.saturating_add(1);
        }
    }
    fn record_elapsed(
        &mut self,
        wall_ns: Option<u64>,
        before: Option<(u64, u64)>,
        after: Option<(u64, u64)>,
    ) {
        // Once a total is incomplete it stays null. Count new failed frames or
        // overflows, without treating an already invalid total as another fault.
        fn add(total: &mut Option<u64>, delta: Option<u64>) -> bool {
            let Some(delta) = delta else {
                *total = None;
                return false;
            };
            let Some(previous) = *total else {
                return true;
            };
            *total = previous.checked_add(delta);
            total.is_some()
        }
        if !add(&mut self.wall_ns, wall_ns) {
            self.timing_failures = self.timing_failures.saturating_add(1);
        }
        let delta = before
            .zip(after)
            .and_then(|((u0, s0), (u1, s1))| Some((u1.checked_sub(u0)?, s1.checked_sub(s0)?)));
        let user_valid = add(&mut self.self_user_ns, delta.map(|v| v.0));
        let system_valid = add(&mut self.self_system_ns, delta.map(|v| v.1));
        if !user_valid || !system_valid {
            self.cpu_failures = self.cpu_failures.saturating_add(1);
            self.self_user_ns = None;
            self.self_system_ns = None;
        }
    }
}
#[derive(Clone, Copy)]
struct StageMark {
    raw: Option<(u64, u64)>,
    precise: Option<u64>,
}
fn add_delta(total: &mut Option<u64>, delta: Option<u64>) -> bool {
    let Some(delta) = delta else {
        *total = None;
        return false;
    };
    let Some(old) = *total else {
        return true;
    };
    *total = old.checked_add(delta);
    total.is_some()
}

/// Combine both attempts' observations while retaining final configuration.
pub fn merge_retry_resources(current: &mut serde_json::Value, previous: &serde_json::Value) {
    for field in [
        "peakAggregateRssBytes",
        "measuredWorkerPeakBytes",
        "admissionWorkerCostBytes",
        "maxActiveJobs",
    ] {
        if let (Some(old), Some(now)) = (previous[field].as_u64(), current[field].as_u64()) {
            current[field] = old.max(now).into();
        }
    }
    for field in [
        "samples",
        "exactSamples",
        "counterSamples",
        "treeDiscoveries",
        "broadDiscoveries",
        "admissionPauses",
        "recycledIdleWorkers",
    ] {
        if let (Some(old), Some(now)) = (previous[field].as_u64(), current[field].as_u64()) {
            current[field] = old.saturating_add(now).into();
        }
    }
    if let (Some(now), Some(old)) = (
        current.get_mut("accountingDiagnostics"),
        previous.get("accountingDiagnostics"),
    ) {
        for name in ["sampling", "discovery", "identityReads", "rssReads"] {
            // Both values were serialized from StageTotals by this process.
            let mut merged: StageTotals =
                serde_json::from_value(now[name].clone()).expect("serialized current stage");
            let old: StageTotals =
                serde_json::from_value(old[name].clone()).expect("serialized previous stage");
            merged.merge(old);
            now[name] = serde_json::to_value(merged).expect("serializable stage");
        }
        let mut thread_merge_failure = false;
        {
            let field = "threadCpuSinceBudgetStartNs";
            let a = now[field].as_u64();
            let b = old[field].as_u64();
            now[field] = match (a, b) {
                (Some(a), Some(b)) => match a.checked_add(b) {
                    Some(sum) => sum.into(),
                    None => {
                        thread_merge_failure = true;
                        serde_json::Value::Null
                    }
                },
                _ => serde_json::Value::Null,
            };
        }
        if let (Some(a), Some(b)) = (
            now["threadCpuFailures"].as_u64(),
            old["threadCpuFailures"].as_u64(),
        ) {
            now["threadCpuFailures"] = a
                .saturating_add(b)
                .saturating_add(u64::from(
                    thread_merge_failure
                        || (now["threadCpuSinceBudgetStartNs"].is_null()
                            && a.saturating_add(b) == 0),
                ))
                .into();
        }
    }
}
fn thread_cpu_ns() -> Option<(u64, u64)> {
    let mut usage = std::mem::MaybeUninit::<libc::rusage>::zeroed();
    // RUSAGE_THREAD measures only this Linux thread, excluding workers/forks.
    // Linux UAPI defines RUSAGE_THREAD as 1 (some libc targets omit the name).
    if unsafe { libc::getrusage(1, usage.as_mut_ptr()) } != 0 {
        return None;
    }
    let usage = unsafe { usage.assume_init() };
    Some((timeval_ns(usage.ru_utime)?, timeval_ns(usage.ru_stime)?))
}
fn timeval_ns(t: libc::timeval) -> Option<u64> {
    let sec: u64 = t.tv_sec.try_into().ok()?;
    let usec: u64 = t.tv_usec.try_into().ok()?;
    if usec >= 1_000_000 {
        return None;
    }
    sec.checked_mul(1_000_000_000)?
        .checked_add(usec.checked_mul(1_000)?)
}
fn precise_thread_cpu_ns() -> Option<u64> {
    let mut ts = std::mem::MaybeUninit::<libc::timespec>::uninit();
    if unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, ts.as_mut_ptr()) } != 0 {
        return None;
    }
    timespec_ns(unsafe { ts.assume_init() })
}
fn timespec_ns(ts: libc::timespec) -> Option<u64> {
    let sec: u64 = ts.tv_sec.try_into().ok()?;
    let nsec: u64 = ts.tv_nsec.try_into().ok()?;
    if nsec >= 1_000_000_000 {
        return None;
    }
    sec.checked_mul(1_000_000_000)?.checked_add(nsec)
}

pub const MIB: u64 = 1024 * 1024;
pub const SAMPLE_MS: u64 = 50;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Cgroup {
    pub limit_path: PathBuf,
    pub usage_path: PathBuf,
    pub limit_bytes: Option<u64>,
    pub oom_events_path: PathBuf,
    pub initial_oom_events: Option<u64>,
}
fn number(path: &Path) -> Option<u64> {
    fs::read_to_string(path).ok()?.trim().parse().ok()
}
fn unescape(value: &str) -> String {
    value
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\134", "\\")
}
// Resolve the process membership relative to each mount root, then inspect all
// ancestors exposed by that mount. This includes limits on shared parent groups.
fn hierarchy_paths(controller: &str) -> Vec<(PathBuf, bool)> {
    let memberships = fs::read_to_string("/proc/self/cgroup").unwrap_or_default();
    let mounts = fs::read_to_string("/proc/self/mountinfo").unwrap_or_default();
    parse_hierarchy_paths(&memberships, &mounts, controller)
}
fn parse_hierarchy_paths(
    memberships: &str,
    mounts: &str,
    controller: &str,
) -> Vec<(PathBuf, bool)> {
    let mut result = vec![];
    for mount in mounts.lines() {
        let Some((left, right)) = mount.split_once(" - ") else {
            continue;
        };
        let fields: Vec<_> = left.split_whitespace().collect();
        let fsfields: Vec<_> = right.split_whitespace().collect();
        if fields.len() < 5 || fsfields.len() < 3 {
            continue;
        }
        let unified = fsfields[0] == "cgroup2";
        if !(unified || fsfields[0] == "cgroup" && fsfields[2].split(',').any(|c| c == controller))
        {
            continue;
        }
        for membership in memberships.lines() {
            let parts: Vec<_> = membership.splitn(3, ':').collect();
            if parts.len() != 3
                || if unified {
                    !parts[1].is_empty()
                } else {
                    !parts[1].split(',').any(|c| c == controller)
                }
            {
                continue;
            }
            let mount_root = PathBuf::from(unescape(fields[3]));
            let mount_point = PathBuf::from(unescape(fields[4]));
            let member = Path::new(parts[2]);
            // Namespaced memberships may be relative to the visible mount root.
            let relative = member
                .strip_prefix(&mount_root)
                .unwrap_or_else(|_| member.strip_prefix("/").unwrap_or(member));
            if relative
                .components()
                .any(|c| matches!(c, std::path::Component::ParentDir))
            {
                continue;
            }
            let mut path = mount_point.join(relative);
            while path.starts_with(&mount_point) {
                result.push((path.clone(), unified));
                if path == mount_point || !path.pop() {
                    break;
                }
            }
        }
    }
    result.sort();
    result.dedup();
    result
}
fn oom_events(path: &Path) -> Option<u64> {
    let value = fs::read_to_string(path).ok()?;
    if let Ok(number) = value.trim().parse() {
        return Some(number);
    }
    value.lines().find_map(|line| {
        let (key, value) = line.split_once(' ')?;
        (key == "oom_kill")
            .then(|| value.trim().parse().ok())
            .flatten()
    })
}
fn memory_limit(path: &Path) -> Result<Option<u64>, String> {
    let value = fs::read_to_string(path)
        .map_err(|e| format!("cgroup memory limit {}: {e}", path.display()))?;
    if value.trim() == "max" {
        return Ok(None);
    }
    let value: u64 = value
        .trim()
        .parse()
        .map_err(|_| "invalid cgroup memory limit")?;
    Ok((value < 1u64 << 60).then_some(value))
}
pub fn memory_cgroups() -> Result<Vec<Cgroup>, String> {
    let mut groups = vec![];
    for (path, unified) in hierarchy_paths("memory") {
        let limit_path = path.join(if unified {
            "memory.max"
        } else {
            "memory.limit_in_bytes"
        });
        if !limit_path.exists() {
            continue;
        }
        let usage_path = path.join(if unified {
            "memory.current"
        } else {
            "memory.usage_in_bytes"
        });
        let limit_bytes = memory_limit(&limit_path)?;
        let oom_events_path = path.join(if unified {
            "memory.events"
        } else {
            "memory.failcnt"
        });
        let initial_oom_events = oom_events(&oom_events_path);
        groups.push(Cgroup {
            limit_path,
            usage_path,
            limit_bytes,
            oom_events_path,
            initial_oom_events,
        });
    }
    Ok(groups)
}
fn effective_budget(base: u64, limits: impl IntoIterator<Item = Option<u64>>) -> u64 {
    limits.into_iter().flatten().fold(base, u64::min)
}
pub fn cache_policy(options: &Options) -> Result<serde_json::Value, String> {
    let physical = meminfo("MemTotal").ok_or("host memory accounting unavailable")?;
    let groups = memory_cgroups()?;
    let mut limits: Vec<_> = groups.iter().map(|group| group.limit_bytes).collect();
    limits.sort();
    Ok(serde_json::json!({
        "effectiveMemoryBudgetBytes": effective_budget(options.memory_budget_mib.map_or(physical, |n| n * MIB), limits.clone()),
        "ancestorMemoryLimitsBytes": limits,
        "cpuLimitWorkers": cpu_limit(),
        "samplingMs": SAMPLE_MS,
        "policy": "sampled-rss-admission-subreeper-v1"
    }))
}
pub fn cpu_limit() -> usize {
    let mut limit = std::thread::available_parallelism().map_or(1, usize::from);
    for (path, unified) in hierarchy_paths("cpu") {
        let quota_period = if unified {
            fs::read_to_string(path.join("cpu.max"))
                .ok()
                .and_then(|value| {
                    let mut parts = value.split_whitespace();
                    Some((
                        parts.next()?.parse::<u64>().ok()?,
                        parts.next()?.parse::<u64>().ok()?,
                    ))
                })
        } else {
            number(&path.join("cpu.cfs_quota_us")).zip(number(&path.join("cpu.cfs_period_us")))
        };
        if let Some((quota, period)) = quota_period
            && period > 0
        {
            limit = limit.min((quota / period).max(1).min(usize::MAX as u64) as usize);
        }
    }
    limit.max(1)
}
fn meminfo(key: &str) -> Option<u64> {
    fs::read_to_string("/proc/meminfo")
        .ok()?
        .lines()
        .find_map(|line| {
            let (name, rest) = line.split_once(':')?;
            (name == key)
                .then(|| {
                    rest.split_whitespace()
                        .next()?
                        .parse::<u64>()
                        .ok()?
                        .checked_mul(1024)
                })
                .flatten()
        })
}
fn rss(pid: u32) -> Result<Option<u64>, String> {
    // stat's RSS counter can lag resident allocations by tens of MiB. A rollup
    // walks the mappings and gives admission a current resident working set.
    // Older/restricted kernels retain the documented approximate fallback.
    match fs::read_to_string(format!("/proc/{pid}/smaps_rollup")) {
        Ok(rollup) => {
            return match rollup_rss(&rollup) {
                Ok(bytes) => Ok(Some(bytes)),
                Err(error) => {
                    // A task can lose its memory map between open and read.
                    // An empty rollup is safe only after confirming its exit.
                    let stat = fs::read_to_string(format!("/proc/{pid}/stat"));
                    let exited = match &stat {
                        Ok(stat) => stat
                            .rsplit_once(')')
                            .and_then(|(_, fields)| fields.split_whitespace().next())
                            .is_some_and(|state| matches!(state, "Z" | "X" | "x")),
                        Err(error) => {
                            error.kind() == std::io::ErrorKind::NotFound
                                || error.raw_os_error() == Some(libc::ESRCH)
                        }
                    };
                    if exited {
                        Ok(None)
                    } else {
                        Err(format!("RSS rollup invalid for PID {pid}: {error}"))
                    }
                }
            };
        }
        Err(error) if error.raw_os_error() == Some(libc::ESRCH) => return Ok(None),
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
            ) => {}
        Err(error) => return Err(format!("RSS rollup unavailable for PID {pid}: {error}")),
    }
    let stat = match fs::read_to_string(format!("/proc/{pid}/stat")) {
        Ok(stat) => stat,
        Err(error)
            if error.kind() == std::io::ErrorKind::NotFound
                || error.raw_os_error() == Some(libc::ESRCH) =>
        {
            return Ok(None);
        }
        Err(error) => return Err(format!("RSS accounting unavailable for PID {pid}: {error}")),
    };
    let pages: u64 = stat
        .rsplit_once(')')
        .and_then(|(_, fields)| fields.split_whitespace().nth(21))
        .and_then(|n| n.parse().ok())
        .ok_or_else(|| format!("invalid RSS stat for PID {pid}"))?;
    let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if page_size <= 0 {
        return Err("could not determine RSS page size".into());
    }
    Ok(Some(pages.saturating_mul(page_size as u64)))
}
// Kernel counters are an escalation hint, never admission or peak evidence.
fn rss_counter(pid: u32) -> Result<Option<u64>, String> {
    let statm = match fs::read_to_string(format!("/proc/{pid}/statm")) {
        Ok(statm) => statm,
        Err(error)
            if error.kind() == std::io::ErrorKind::NotFound
                || error.raw_os_error() == Some(libc::ESRCH) =>
        {
            return Ok(None);
        }
        Err(error) => return Err(format!("RSS counter unavailable for PID {pid}: {error}")),
    };
    let pages: u64 = statm
        .split_whitespace()
        .nth(1)
        .and_then(|n| n.parse().ok())
        .ok_or_else(|| format!("invalid RSS statm for PID {pid}"))?;
    let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if page_size <= 0 {
        return Err("could not determine RSS page size".into());
    }
    pages
        .checked_mul(page_size as u64)
        .map(Some)
        .ok_or_else(|| format!("RSS statm overflow for PID {pid}"))
}
fn rollup_rss(rollup: &str) -> Result<u64, String> {
    let mut fields = rollup
        .lines()
        .find_map(|line| line.strip_prefix("Rss:"))
        .ok_or("RSS rollup is missing Rss")?
        .split_whitespace();
    let kib: u64 = fields
        .next()
        .and_then(|value| value.parse().ok())
        .ok_or("RSS rollup has invalid Rss")?;
    if fields.next() != Some("kB") || fields.next().is_some() {
        return Err("RSS rollup has invalid Rss units".into());
    }
    kib.checked_mul(1024)
        .ok_or_else(|| "RSS rollup Rss overflow".into())
}
// Discovery is shared and identities are re-checked fresh for every aggregate/
// slot observation, including roots and the final CLI floor. Each verified PID's
// RSS is read once per sample (see `sample_with_readers`).
fn checked_rss(
    pid: u32,
    start: Option<u64>,
    read_identity: &mut impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
    read_rss: &mut impl FnMut(u32) -> Result<Option<u64>, String>,
) -> Result<u64, String> {
    match read_identity(pid)? {
        None => Ok(0), // Confirmed exit since discovery.
        Some((_, actual)) if start == Some(actual) => Ok(read_rss(pid)?.unwrap_or(0)),
        Some(_) => Err(format!(
            "process identity changed since discovery for PID {pid}"
        )),
    }
}
fn tree_rss(
    pid: u32,
    table: &ProcessTable,
    read_identity: &mut impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
    read_rss: &mut impl FnMut(u32) -> Result<Option<u64>, String>,
) -> Result<u64, String> {
    let mut total = checked_rss(pid, table.start(pid), read_identity, read_rss)?;
    for (pid, start) in table.descendants(pid) {
        // A changed start confirms that this discovered identity has retired.
        // Preserve the old descendant skip policy without measuring its
        // replacement. Read failures still propagate, and roots remain strict.
        if let Some((_, actual)) = read_identity(pid)?
            && actual == start
        {
            total = total.saturating_add(read_rss(pid)?.unwrap_or(0));
        }
    }
    Ok(total)
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Budget {
    pub configured_workers: usize,
    pub effective_workers: usize,
    pub cpu_limit_workers: usize,
    pub configured_budget_bytes: Option<u64>,
    pub budget_bytes: u64,
    pub reserve_bytes: u64,
    pub cgroups: Vec<Cgroup>,
    pub measurement: &'static str,
    pub enforcement: &'static str,
    pub sample_interval_ms: u64,
    pub peak_aggregate_rss_bytes: u64,
    pub measured_worker_peak_bytes: u64,
    pub admission_worker_cost_bytes: u64,
    pub max_active_jobs: usize,
    pub admission_pauses: u64,
    pub recycled_idle_workers: u64,
    pub samples: u64,
    pub exact_samples: u64,
    pub counter_samples: u64,
    #[serde(skip)]
    last_exact_current: u64,
    #[serde(skip)]
    last_exact_counter_total: Option<u64>,
    #[serde(skip)]
    last_exact_at: Instant,
    /// Host MemAvailable source; injectable so tests don't depend on host memory.
    #[serde(skip)]
    available_reader: fn() -> Option<u64>,
    /// Samples discovered by walking the owned tree's `children` links, and
    /// by broad `/proc` enumeration (kernels without those links).
    pub tree_discoveries: u64,
    pub broad_discoveries: u64,
    pub exhaustion: Option<String>,
    #[serde(skip)]
    budget_cpu_start: Option<u64>,
    #[serde(skip)]
    budget_cpu_failures: u64,
    #[serde(skip)]
    budget_thread: std::thread::ThreadId,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub accounting_diagnostics: Option<AccountingDiagnostics>,
    /// The latest sample's discovery, so slot ownership needs no extra scan.
    #[serde(skip)]
    pub table: Option<ProcessTable>,
    #[serde(skip)]
    last_sample: Instant,
    #[serde(skip)]
    last_broad: Instant,
    #[serde(skip)]
    pub current: u64,
    #[serde(skip)]
    pub active: usize,
    #[serde(skip)]
    host_available: u64,
    #[serde(skip)]
    observed_workers: BTreeMap<u32, u64>,
    #[serde(skip)]
    base_budget_bytes: u64,
}
impl Budget {
    pub fn new(options: &Options) -> Result<Self, String> {
        let profiling = profile_enabled();
        let budget_cpu_start = profiling.then(precise_thread_cpu_ns).flatten();
        let budget_cpu_failures = u64::from(profiling && budget_cpu_start.is_none());
        fs::read_dir("/proc").map_err(|e| format!("Linux /proc accounting required: {e}"))?;
        let cgroups = memory_cgroups()?;
        let physical = meminfo("MemTotal").ok_or("host memory accounting unavailable")?;
        let configured = options.memory_budget_mib.map(|n| n * MIB);
        let base_budget_bytes = configured.unwrap_or(physical);
        let budget = effective_budget(base_budget_bytes, cgroups.iter().map(|c| c.limit_bytes));
        let reserve = options.memory_reserve_mib * MIB;
        let cpu = cpu_limit();
        let mut state = Self {
            configured_workers: options.workers,
            effective_workers: options.workers.min(cpu),
            cpu_limit_workers: cpu,
            configured_budget_bytes: configured,
            budget_bytes: budget,
            reserve_bytes: reserve,
            cgroups,
            measurement: "Peaks and decisions use exact Linux /proc smaps_rollup Rss samples (forced and periodic escalations at least once per second), approximate stat RSS fallback on older/restricted kernels; other periodic samples use kernel counters only to decide escalation; main CLI and all descendants, shared pages counted per process",
            enforcement: "best-effort sampled admission and process-tree cancellation; existing cgroup limits are OS-enforced; no new hard cap installed",
            sample_interval_ms: SAMPLE_MS,
            peak_aggregate_rss_bytes: 0,
            measured_worker_peak_bytes: 0,
            admission_worker_cost_bytes: 0,
            max_active_jobs: 0,
            admission_pauses: 0,
            recycled_idle_workers: 0,
            samples: 0,
            exact_samples: 0,
            counter_samples: 0,
            last_exact_current: 0,
            last_exact_counter_total: None,
            last_exact_at: Instant::now(),
            available_reader: || meminfo("MemAvailable"),
            tree_discoveries: 0,
            broad_discoveries: 0,
            exhaustion: None,
            budget_cpu_start,
            budget_cpu_failures,
            budget_thread: std::thread::current().id(),
            accounting_diagnostics: profiling.then(|| AccountingDiagnostics {
                clock_version: 2,
                thread_cpu_since_budget_start_ns: None,
                thread_cpu_failures: 0,
                scope: "CLI sampling thread only; excludes Node/controllers/test forks and external benchmark sampler",
                evidence: "incomplete until each CPU and wall measurement succeeds",
                sampling: totals(), discovery: totals(), identity_reads: totals(), rss_reads: totals(),
                timing_note: "stage totals are nested within inclusive sampling totals; getrusage/clock/framework overhead is not separately measured, so categories are not a process-tree CPU partition",
            }),
            table: None,
            last_sample: Instant::now() - Duration::from_secs(1),
            last_broad: Instant::now() - Duration::from_secs(1),
            current: 0,
            active: 0,
            host_available: 0,
            observed_workers: BTreeMap::new(),
            base_budget_bytes,
        };
        state.sample(&[], true);
        Ok(state)
    }
    /// Admission under memory pressure: a broad sample at most once per
    /// sampling interval, so waiting never rescans the host on every poll.
    pub fn sample_admission(&mut self, workers: &[u32]) {
        if self.last_broad.elapsed() >= Duration::from_millis(SAMPLE_MS) {
            self.sample(workers, true);
        }
    }

    pub fn sample(&mut self, workers: &[u32], force: bool) {
        let (mut tree, mut broad) = (0, 0);
        self.sample_with_counter_readers(
            workers,
            force,
            || {
                // Every owned process descends from this subreaper CLI.
                // Periodic samples walk the owned tree; forced samples (job
                // completion, admission pressure, final accounting) and any
                // walk that observed the tree changing use broad discovery,
                // whose strict errors are unchanged.
                if !force && let Ok(Some(table)) = ProcessTable::read_tree(std::process::id()) {
                    tree += 1;
                    return Ok(table);
                }
                broad += 1;
                ProcessTable::read_with(identity)
            },
            identity,
            rss,
            rss_counter,
        );
        self.tree_discoveries += tree;
        self.broad_discoveries += broad;
        // Every broad scan, forced or a walk's fallback, satisfies admission.
        if broad > 0 {
            self.last_broad = Instant::now();
        }
    }
    #[cfg(test)]
    fn sample_with_readers(
        &mut self,
        workers: &[u32],
        force: bool,
        discover: impl FnOnce() -> Result<ProcessTable, String>,
        read_identity: impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
        read_rss: impl FnMut(u32) -> Result<Option<u64>, String>,
    ) {
        self.sample_with_counter_readers(
            workers,
            force,
            discover,
            read_identity,
            read_rss,
            rss_counter,
        );
    }
    fn sample_with_counter_readers(
        &mut self,
        workers: &[u32],
        force: bool,
        discover: impl FnOnce() -> Result<ProcessTable, String>,
        mut read_identity: impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
        mut read_rss: impl FnMut(u32) -> Result<Option<u64>, String>,
        mut read_counter: impl FnMut(u32) -> Result<Option<u64>, String>,
    ) {
        if !force && self.last_sample.elapsed() < Duration::from_millis(SAMPLE_MS) {
            return;
        }
        self.last_sample = Instant::now();
        self.samples += 1;
        self.table = None;
        let mut profile = std::cell::RefCell::new(self.accounting_diagnostics.take());
        let profiling = profile.borrow().is_some();
        let sample_start = profiling.then(|| {
            let wall = Instant::now();
            let cpu = profile.borrow_mut().as_mut().unwrap().sampling.start();
            (wall, cpu)
        });
        let profiled_discover = || {
            if !profiling {
                return discover();
            }
            let mut profile = profile.borrow_mut();
            let d = profile.as_mut().unwrap();
            let wall = Instant::now();
            let cpu = d.discovery.start();
            let result = discover();
            d.discovery.end(cpu, wall);
            result
        };
        let mut profiled_identity = |pid| {
            if !profiling {
                return read_identity(pid);
            }
            let mut profile = profile.borrow_mut();
            let d = profile.as_mut().unwrap();
            let wall = Instant::now();
            let cpu = d.identity_reads.start();
            let result = read_identity(pid);
            d.identity_reads.end(cpu, wall);
            result
        };
        let mut profiled_rss = |pid| {
            if !profiling {
                return read_rss(pid);
            }
            let mut profile = profile.borrow_mut();
            let d = profile.as_mut().unwrap();
            let wall = Instant::now();
            let cpu = d.rss_reads.start();
            let result = read_rss(pid);
            d.rss_reads.end(cpu, wall);
            result
        };
        // One page-table walk per verified PID per sample: smaps_rollup costs
        // ~1 ms of kernel CPU per Node process on this thread, and aggregate,
        // slot and CLI-floor trees overlap. Identities stay fresh per
        // observation and are verified against this sample's discovery before
        // any lookup, so a cached value always belongs to the same process.
        // Errors are never cached; they exhaust the sample.
        let mut rss_snapshot = std::collections::HashMap::new();
        let mut profiled_rss = |pid| {
            if let Some(&bytes) = rss_snapshot.get(&pid) {
                return Ok(bytes);
            }
            let bytes = profiled_rss(pid)?;
            rss_snapshot.insert(pid, bytes);
            Ok(bytes)
        };
        // Counter reads share rssReads timing to retain the diagnostics schema.
        let mut profiled_counter = |pid| {
            if !profiling {
                return read_counter(pid);
            }
            let mut profile = profile.borrow_mut();
            let d = profile.as_mut().unwrap();
            let wall = Instant::now();
            let cpu = d.rss_reads.start();
            let result = read_counter(pid);
            d.rss_reads.end(cpu, wall);
            result
        };
        let observed = (|| -> Result<(), String> {
            // Exactly one fresh broad discovery per admitted sample. This also
            // includes adopted descendants outside any live worker branch.
            let table = profiled_discover()?;
            // Counters are only a hint: any counter or identity error falls through to
            // the exact path, which repeats every check strictly.
            let cheap_total = if force {
                None
            } else {
                tree_rss(
                    std::process::id(),
                    &table,
                    &mut profiled_identity,
                    &mut profiled_counter,
                )
                .ok()
            };
            if let Some(cheap) = cheap_total
                && let Some(baseline) = self.last_exact_counter_total
                && self.exact_samples > 0
                && self.last_exact_at.elapsed() <= Duration::from_secs(1)
                && cheap <= baseline.saturating_add((32 * MIB).max(self.last_exact_current / 8))
            {
                let projected = self
                    .last_exact_current
                    .saturating_add(cheap.saturating_sub(baseline));
                let margin = (256 * MIB).max(self.budget_bytes / 4);
                // Re-read external availability/limits: pressure or uncertain
                // accounting must never be hidden by accepting a counter hint.
                let mut limit = self.base_budget_bytes;
                let mut uncertain = false;
                let available = (self.available_reader)();
                match available {
                    Some(available) => limit = limit.min(available),
                    None => uncertain = true,
                }
                let mut group_limits = Vec::with_capacity(self.cgroups.len());
                for group in &self.cgroups {
                    match (memory_limit(&group.limit_path), number(&group.usage_path)) {
                        (Ok(group_limit), Some(usage)) => {
                            if let Some(cap) = group_limit {
                                limit = limit.min(cap).min(cap.saturating_sub(usage));
                            }
                            group_limits.push(group_limit);
                        }
                        _ => uncertain = true,
                    }
                    if let Some(initial) = group.initial_oom_events
                        && oom_events(&group.oom_events_path) != Some(initial)
                    {
                        uncertain = true;
                    }
                }
                if !uncertain
                    && self.exhaustion.is_none()
                    && self.memory_capacity(workers) > 0
                    && projected.saturating_add(margin) < limit
                {
                    // Admission between exact samples must see fresh headroom, exactly
                    // as the exact path stores it.
                    self.host_available = available.unwrap_or(0);
                    self.budget_bytes = self.base_budget_bytes;
                    for (group, group_limit) in self.cgroups.iter_mut().zip(group_limits) {
                        group.limit_bytes = group_limit;
                        if let Some(cap) = group_limit {
                            self.budget_bytes = self.budget_bytes.min(cap);
                        }
                    }
                    self.counter_samples += 1;
                    self.table = Some(table);
                    return Ok(());
                }
            }
            self.exact_samples += 1;
            self.current = tree_rss(
                std::process::id(),
                &table,
                &mut profiled_identity,
                &mut profiled_rss,
            )?;
            self.peak_aggregate_rss_bytes = self.peak_aggregate_rss_bytes.max(self.current);
            self.observed_workers.clear();
            for &pid in workers {
                let bytes = tree_rss(pid, &table, &mut profiled_identity, &mut profiled_rss)?;
                self.measured_worker_peak_bytes = self.measured_worker_peak_bytes.max(bytes);
                self.admission_worker_cost_bytes = self
                    .measured_worker_peak_bytes
                    .saturating_add(self.measured_worker_peak_bytes.div_ceil(4));
                self.observed_workers.insert(pid, bytes);
            }
            // Slot trees reuse the aggregate's per-PID snapshot. Never credit a slot's RSS against reservations unless aggregate
            // accounting includes at least those same slot totals. Nested slots
            // intentionally overcount; duplicate PIDs retain one observed total.
            let accounted_slots = self.observed_workers.values().fold(
                checked_rss(
                    std::process::id(),
                    table.start(std::process::id()),
                    &mut profiled_identity,
                    &mut profiled_rss,
                )?,
                |total, bytes| total.saturating_add(*bytes),
            );
            self.current = self.current.max(accounted_slots);
            self.peak_aggregate_rss_bytes = self.peak_aggregate_rss_bytes.max(self.current);
            self.host_available =
                (self.available_reader)().ok_or("host available memory accounting unavailable")?;
            self.budget_bytes = self.base_budget_bytes;
            for cgroup in &mut self.cgroups {
                cgroup.limit_bytes = memory_limit(&cgroup.limit_path)?;
                number(&cgroup.usage_path).ok_or("cgroup memory usage accounting unavailable")?;
                if let Some(limit) = cgroup.limit_bytes {
                    self.budget_bytes = self.budget_bytes.min(limit);
                }
                if let Some(initial) = cgroup.initial_oom_events {
                    let events = oom_events(&cgroup.oom_events_path)
                        .ok_or("cgroup OOM accounting became unavailable")?;
                    if events > initial {
                        return Err(format!(
                            "cgroup memory failure/OOM counter increased at {}",
                            cgroup.oom_events_path.display()
                        ));
                    }
                }
            }
            if self.current > self.budget_bytes {
                return Err(format!(
                    "aggregate RSS {} bytes exceeded budget {} bytes",
                    self.current, self.budget_bytes
                ));
            }
            self.last_exact_current = self.current;
            self.last_exact_counter_total = cheap_total;
            self.last_exact_at = Instant::now();
            self.table = Some(table);
            Ok(())
        })();
        if let Some((wall, cpu)) = sample_start {
            let d = profile.get_mut().as_mut().unwrap();
            d.sampling.end(cpu, wall);
            d.evidence =
                "review wallNs and CPU failure counters; null CPU totals mean incomplete evidence";
        }
        self.accounting_diagnostics = profile.into_inner();
        if let Err(error) = observed
            && self.exhaustion.is_none()
        {
            self.exhaustion = Some(error);
        }
    }
    pub fn report_snapshot(&self) -> serde_json::Value {
        let mut value = serde_json::to_value(self).expect("resource report");
        if let (Some(diag), Some(start)) = (
            value.get_mut("accountingDiagnostics"),
            self.budget_cpu_start,
        ) {
            let same_thread = std::thread::current().id() == self.budget_thread;
            let end = same_thread.then(precise_thread_cpu_ns).flatten();
            let delta = end.and_then(|v| v.checked_sub(start));
            diag["threadCpuSinceBudgetStartNs"] =
                delta.map_or(serde_json::Value::Null, |v| v.into());
            diag["threadCpuFailures"] = self
                .budget_cpu_failures
                .saturating_add(u64::from(delta.is_none()))
                .into();
        } else if let Some(diag) = value.get_mut("accountingDiagnostics") {
            diag["threadCpuFailures"] = self.budget_cpu_failures.into();
        }
        value
    }
    pub fn capacity(&self, active_pids: &[u32]) -> usize {
        if self.exhaustion.is_some() {
            return 0;
        }
        self.effective_workers
            .saturating_sub(self.active)
            .min(self.memory_capacity(active_pids))
    }
    fn memory_capacity(&self, active_pids: &[u32]) -> usize {
        let mut available = self
            .budget_bytes
            .saturating_sub(self.current)
            .min(self.host_available);
        for group in &self.cgroups {
            let Some(current) = number(&group.usage_path) else {
                return 0;
            };
            if let Some(limit) = group.limit_bytes {
                available = available.min(limit.saturating_sub(current));
            }
        }
        let growth = active_pids.iter().fold(0u64, |total, pid| {
            total.saturating_add(
                self.admission_worker_cost_bytes
                    .saturating_sub(self.observed_workers.get(pid).copied().unwrap_or(0)),
            )
        });
        let headroom = available
            .saturating_sub(self.reserve_bytes)
            .saturating_sub(growth);
        let cost = self.admission_worker_cost_bytes.max(1);
        (headroom / cost).min(usize::MAX as u64) as usize
    }
    pub fn exhausted(&mut self, reason: impl Into<String>) {
        if self.exhaustion.is_none() {
            self.exhaustion = Some(reason.into());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    fn budget() -> Budget {
        Budget {
            configured_workers: 4,
            effective_workers: 4,
            cpu_limit_workers: 4,
            configured_budget_bytes: Some(u64::MAX),
            budget_bytes: u64::MAX,
            reserve_bytes: 0,
            cgroups: vec![],
            measurement: "test",
            enforcement: "test",
            sample_interval_ms: SAMPLE_MS,
            peak_aggregate_rss_bytes: 0,
            measured_worker_peak_bytes: 0,
            admission_worker_cost_bytes: 0,
            max_active_jobs: 0,
            admission_pauses: 0,
            recycled_idle_workers: 0,
            samples: 0,
            exact_samples: 0,
            counter_samples: 0,
            last_exact_current: 0,
            last_exact_counter_total: None,
            last_exact_at: Instant::now(),
            available_reader: || meminfo("MemAvailable"),
            tree_discoveries: 0,
            broad_discoveries: 0,
            exhaustion: None,
            budget_cpu_start: None,
            budget_cpu_failures: 0,
            budget_thread: std::thread::current().id(),
            accounting_diagnostics: None,
            table: None,
            last_sample: Instant::now() - Duration::from_secs(1),
            last_broad: Instant::now() - Duration::from_secs(1),
            current: 0,
            active: 0,
            host_available: u64::MAX,
            observed_workers: BTreeMap::new(),
            base_budget_bytes: u64::MAX,
        }
    }

    fn counter_sample(state: &mut Budget, force: bool, exact: u64, counter: u64) -> (usize, usize) {
        let cli = std::process::id();
        let worker = cli + 1;
        let mut exact_calls = 0;
        let mut counter_calls = 0;
        state.last_sample = Instant::now() - Duration::from_millis(SAMPLE_MS);
        state.sample_with_counter_readers(
            &[worker],
            force,
            || {
                ProcessTable::collect([Ok(Some(cli)), Ok(Some(worker))], |pid| {
                    Ok(Some((if pid == cli { 0 } else { cli }, u64::from(pid))))
                })
            },
            |pid| Ok(Some((if pid == cli { 0 } else { cli }, u64::from(pid)))),
            |_| {
                exact_calls += 1;
                Ok(Some(exact))
            },
            |_| {
                counter_calls += 1;
                Ok(Some(counter))
            },
        );
        (exact_calls, counter_calls)
    }

    fn seeded_counter_budget() -> Budget {
        let mut state = budget();
        // Deterministic thresholds, independent of the host's free memory.
        state.available_reader = || Some(64 * 1024 * MIB);
        state.base_budget_bytes = 4 * 1024 * MIB;
        state.budget_bytes = state.base_budget_bytes;
        assert_eq!(counter_sample(&mut state, false, 10 * MIB, 8 * MIB), (2, 2));
        assert_eq!(state.exact_samples, 1);
        state
    }

    #[test]
    fn forced_samples_read_every_pid_exactly_without_counters() {
        let mut state = seeded_counter_budget();
        assert_eq!(counter_sample(&mut state, true, 20 * MIB, 0), (2, 0));
        assert_eq!(state.exact_samples, 2);
        assert_eq!(state.counter_samples, 0);
        assert_eq!(state.last_exact_counter_total, None);
        assert_eq!(state.peak_aggregate_rss_bytes, 40 * MIB);
    }

    #[test]
    fn periodic_counters_preserve_all_exact_decision_values_and_refresh_table() {
        let mut state = seeded_counter_budget();
        let workers = state.observed_workers.clone();
        let cost = state.admission_worker_cost_bytes;
        state.table = None;
        state.active = state.effective_workers; // Busy slots alone are not memory pressure.
        state.last_exact_at = Instant::now(); // Independent of scheduling delays.
        state.available_reader = || Some(48 * 1024 * MIB);
        assert_eq!(
            counter_sample(&mut state, false, 999 * MIB, 9 * MIB),
            (0, 2)
        );
        let cli = std::process::id();
        let table = state.table.as_ref().expect("refreshed ownership table");
        assert!(table.start(cli).is_some() && table.start(cli + 1).is_some());
        // Admission sees fresh host headroom from the accepted counter sample.
        assert_eq!(state.host_available, 48 * 1024 * MIB);
        assert_eq!(state.current, 20 * MIB);
        assert_eq!(state.peak_aggregate_rss_bytes, 20 * MIB);
        assert_eq!(state.measured_worker_peak_bytes, 10 * MIB);
        assert_eq!(state.admission_worker_cost_bytes, cost);
        assert_eq!(state.observed_workers, workers);
        assert_eq!(
            (state.samples, state.exact_samples, state.counter_samples),
            (2, 1, 1)
        );
        let report = state.report_snapshot();
        assert_eq!(report["exactSamples"], 1);
        assert_eq!(report["counterSamples"], 1);
    }

    #[test]
    fn counter_errors_fall_back_to_an_exact_sample() {
        let mut state = seeded_counter_budget();
        state.last_exact_at = Instant::now();
        let cli = std::process::id();
        let worker = cli + 1;
        let (mut exact_calls, mut counter_calls) = (0, 0);
        state.last_sample = Instant::now() - Duration::from_millis(SAMPLE_MS);
        state.sample_with_counter_readers(
            &[worker],
            false,
            || {
                ProcessTable::collect([Ok(Some(cli)), Ok(Some(worker))], |pid| {
                    Ok(Some((if pid == cli { 0 } else { cli }, u64::from(pid))))
                })
            },
            |pid| Ok(Some((if pid == cli { 0 } else { cli }, u64::from(pid)))),
            |_| {
                exact_calls += 1;
                Ok(Some(10 * MIB))
            },
            |_| {
                counter_calls += 1;
                Err("statm unreadable".into())
            },
        );
        assert_eq!(
            state.exhaustion, None,
            "a counter error is only a missing hint"
        );
        assert_eq!((exact_calls, counter_calls), (2, 1));
        assert_eq!((state.exact_samples, state.counter_samples), (2, 0));
    }

    fn check_periodic_escalation(reason: &str) {
        let mut state = seeded_counter_budget();
        state.last_exact_at = Instant::now();
        assert_eq!(
            counter_sample(&mut state, false, 999 * MIB, 8 * MIB),
            (0, 2)
        );
        let mut counter = 8 * MIB;
        match reason {
            "elapsed" => state.last_exact_at = Instant::now() - Duration::from_millis(1001),
            "growth" => counter = 25 * MIB,
            "limit" | "exhaustion" => {
                state.base_budget_bytes = 280 * MIB;
                state.budget_bytes = 280 * MIB;
                counter = 11 * MIB;
            }
            "pressure" => state.reserve_bytes = state.budget_bytes,
            _ => unreachable!(),
        }
        let exact = if reason == "exhaustion" {
            200 * MIB
        } else {
            30 * MIB
        };
        assert_eq!(counter_sample(&mut state, false, exact, counter), (2, 2));
        assert_eq!(state.exact_samples, 2);
        assert_eq!(state.counter_samples, 1);
        assert_eq!(state.peak_aggregate_rss_bytes, 2 * exact);
        assert_eq!(state.measured_worker_peak_bytes, exact);
        if reason == "exhaustion" {
            assert!(
                state
                    .exhaustion
                    .as_deref()
                    .unwrap()
                    .contains("exceeded budget")
            );
            assert_eq!(state.capacity(&[]), 0);
        } else {
            assert_eq!(state.exhaustion, None);
        }
    }

    #[test]
    fn periodic_elapsed_escalates_to_exact() {
        check_periodic_escalation("elapsed");
    }
    #[test]
    fn periodic_growth_escalates_to_exact() {
        check_periodic_escalation("growth");
    }
    #[test]
    fn periodic_limit_proximity_escalates_to_exact() {
        check_periodic_escalation("limit");
    }
    #[test]
    fn periodic_pressure_escalates_to_exact() {
        check_periodic_escalation("pressure");
    }
    #[test]
    fn periodic_escalation_detects_exhaustion() {
        check_periodic_escalation("exhaustion");
    }

    #[test]
    fn samples_discover_once_check_identity_per_tree_and_read_rss_once_per_pid() {
        let cli = std::process::id();
        let (worker, nested, sibling, adopted, unrelated) =
            (cli + 1, cli + 2, cli + 3, cli + 4, cli + 5);
        let identities = BTreeMap::from([
            (cli, (0, 1)),
            (worker, (cli, 2)),
            (nested, (worker, 3)),
            (sibling, (cli, 4)),
            (adopted, (cli, 5)),
            (unrelated, (0, 6)),
        ]);
        let values = BTreeMap::from([
            (cli, 10),
            (worker, 20),
            (nested, 30),
            (sibling, 40),
            (adopted, 100),
            (unrelated, 10_000),
        ]);
        for workers in [
            vec![],
            vec![worker, sibling],
            vec![worker, nested],
            vec![worker, worker, sibling],
        ] {
            let mut state = budget();
            let mut discoveries = 0;
            let mut discovery_reads = 0;
            let observations = RefCell::new(vec![]);
            state.sample_with_readers(
                &workers,
                true,
                || {
                    discoveries += 1;
                    ProcessTable::collect(identities.keys().map(|&pid| Ok(Some(pid))), |pid| {
                        discovery_reads += 1;
                        Ok(identities.get(&pid).copied())
                    })
                },
                |pid| {
                    observations.borrow_mut().push(("identity", pid));
                    Ok(identities.get(&pid).copied())
                },
                |pid| {
                    observations.borrow_mut().push(("rss", pid));
                    Ok(values.get(&pid).copied())
                },
            );
            assert_eq!(state.exhaustion, None);
            assert_eq!(discoveries, 1);
            assert_eq!(discovery_reads, identities.len());
            assert_eq!(
                state.current, 200,
                "aggregate includes adopted children only once"
            );
            assert_eq!(state.peak_aggregate_rss_bytes, 200);
            let expected_slots: BTreeMap<_, _> = workers
                .iter()
                .map(|&pid| (pid, if pid == worker { 50 } else { values[&pid] }))
                .collect();
            assert_eq!(state.observed_workers, expected_slots);
            let peak = expected_slots.values().copied().max().unwrap_or(0);
            assert_eq!(state.measured_worker_peak_bytes, peak);
            assert_eq!(state.admission_worker_cost_bytes, peak + peak.div_ceil(4));
            let observations = observations.into_inner();
            // Exact per-PID identity checks, cache hits included: the aggregate
            // tree checks every owned PID once, each slot tree (duplicates
            // included) checks its own members, and the CLI floor checks the CLI.
            let mut expected_identity: BTreeMap<u32, usize> =
                [cli, worker, nested, sibling, adopted]
                    .into_iter()
                    .map(|pid| (pid, 1))
                    .collect();
            *expected_identity.get_mut(&cli).unwrap() += 1;
            for &slot in &workers {
                *expected_identity.get_mut(&slot).unwrap() += 1;
                if slot == worker {
                    *expected_identity.get_mut(&nested).unwrap() += 1;
                }
            }
            let mut identity_reads = BTreeMap::new();
            for (kind, pid) in &observations {
                if *kind == "identity" {
                    *identity_reads.entry(*pid).or_insert(0) += 1;
                }
            }
            assert_eq!(
                identity_reads, expected_identity,
                "identities stay fresh for every tree observation"
            );
            let rss_reads: Vec<_> = observations
                .iter()
                .filter(|(kind, _)| *kind == "rss")
                .map(|(_, pid)| *pid)
                .collect();
            assert_eq!(rss_reads, vec![cli, worker, nested, sibling, adopted]);
            for (index, observation) in observations.iter().enumerate() {
                assert_ne!(observation.1, unrelated);
                if observation.0 == "rss" {
                    assert_eq!(observations[index - 1], ("identity", observation.1));
                }
            }
        }
    }

    #[test]
    fn samples_ignore_unrelated_discovery_identity_failures() {
        let cli = std::process::id();
        let (worker, child, unrelated) = (cli + 1, cli + 2, cli + 3);
        let identities = BTreeMap::from([(cli, (0, 1)), (worker, (cli, 2)), (child, (worker, 3))]);
        for error in [
            "permission denied",
            "invalid process identity",
            "stream did not contain valid UTF-8",
        ] {
            let mut state = budget();
            let mut discovery_reads = 0;
            state.sample_with_readers(
                &[worker],
                true,
                || {
                    ProcessTable::collect(
                        [cli, worker, child, unrelated].map(|pid| Ok(Some(pid))),
                        |pid| {
                            discovery_reads += 1;
                            if pid == unrelated {
                                Err(error.into())
                            } else {
                                Ok(identities.get(&pid).copied())
                            }
                        },
                    )
                },
                |pid| {
                    assert_ne!(pid, unrelated);
                    Ok(identities.get(&pid).copied())
                },
                |pid| Ok(Some(if pid == cli { 10 } else { 20 })),
            );
            assert_eq!(discovery_reads, 4);
            assert_eq!(state.exhaustion, None, "{error}");
            assert_eq!(state.current, 50);
            assert_eq!(state.observed_workers, BTreeMap::from([(worker, 40)]));
            assert_eq!(state.measured_worker_peak_bytes, 40);
            assert_eq!(state.admission_worker_cost_bytes, 50);
            assert!(state.capacity(&[worker]) > 0);
        }
    }

    #[test]
    fn nested_slot_sum_raises_aggregate_floor_and_preserves_reservations() {
        let cli = std::process::id();
        let (worker, nested) = (cli + 1, cli + 2);
        let identities = BTreeMap::from([(cli, (0, 1)), (worker, (cli, 2)), (nested, (worker, 3))]);
        let values = BTreeMap::from([(cli, 10), (worker, 20), (nested, 30)]);
        let mut state = budget();
        let mut rss_reads = BTreeMap::new();
        state.sample_with_readers(
            &[worker, nested],
            true,
            || {
                ProcessTable::collect(identities.keys().map(|&pid| Ok(Some(pid))), |pid| {
                    Ok(identities.get(&pid).copied())
                })
            },
            |pid| Ok(identities.get(&pid).copied()),
            |pid| {
                *rss_reads.entry(pid).or_insert(0) += 1;
                Ok(Some(values[&pid]))
            },
        );
        assert_eq!(state.exhaustion, None);
        assert_eq!(
            rss_reads,
            BTreeMap::from([(cli, 1), (worker, 1), (nested, 1)]),
            "nested slot trees reuse the sample's per-PID snapshot"
        );
        assert_eq!(
            state.observed_workers,
            BTreeMap::from([(worker, 50), (nested, 30)])
        );
        let aggregate: u64 = values.values().sum();
        let slots: u64 = state.observed_workers.values().sum();
        assert!(slots > aggregate);
        assert_eq!(state.current, 90); // CLI + nested slot totals, versus aggregate 60.
        assert_eq!(state.peak_aggregate_rss_bytes, 90);
        assert_eq!(state.measured_worker_peak_bytes, 50);
        assert_eq!(state.admission_worker_cost_bytes, 63);
        state.budget_bytes = 300;
        state.host_available = 300;
        state.reserve_bytes = 40;
        state.active = 2;
        assert_eq!(state.capacity(&[worker, nested]), 1); // (300 - 90 - 40 - 13 - 33) / 63
    }

    #[test]
    fn retired_descendants_are_skipped_before_aggregate_or_slot_measurement() {
        let cli = std::process::id();
        let (worker, child) = (cli + 1, cli + 2);
        let identities = BTreeMap::from([(cli, (0, 1)), (worker, (cli, 2)), (child, (worker, 3))]);
        for after_aggregate in [false, true] {
            let mut state = budget();
            let mut child_checks = 0;
            let mut child_rss_reads = 0;
            state.sample_with_readers(
                &[worker],
                true,
                || {
                    ProcessTable::collect(identities.keys().map(|&pid| Ok(Some(pid))), |pid| {
                        Ok(identities.get(&pid).copied())
                    })
                },
                |pid| {
                    if pid == child {
                        child_checks += 1;
                        Ok(Some((
                            worker,
                            if after_aggregate && child_checks == 1 {
                                3
                            } else {
                                4
                            },
                        )))
                    } else {
                        Ok(identities.get(&pid).copied())
                    }
                },
                |pid| {
                    if pid == child {
                        child_rss_reads += 1;
                        assert!(after_aggregate && child_rss_reads == 1, "replacement RSS");
                        Ok(Some(30))
                    } else {
                        Ok(Some(if pid == cli { 10 } else { 20 }))
                    }
                },
            );
            assert_eq!(state.exhaustion, None);
            assert_eq!(child_checks, 2);
            assert_eq!(child_rss_reads, u32::from(after_aggregate));
            assert_eq!(state.observed_workers, BTreeMap::from([(worker, 20)]));
            assert_eq!(state.current, if after_aggregate { 60 } else { 30 });
            assert_eq!(state.peak_aggregate_rss_bytes, state.current);
        }
    }

    #[test]
    fn root_identity_changes_after_aggregate_still_exhaust_without_replacement_rss() {
        let cli = std::process::id();
        let worker = cli + 1;
        let identities = BTreeMap::from([(cli, (0, 1)), (worker, (cli, 2))]);
        for changed in [cli, worker] {
            let mut state = budget();
            let mut checks = 0;
            let mut changed_rss_reads = 0;
            state.sample_with_readers(
                &[worker],
                true,
                || {
                    ProcessTable::collect(identities.keys().map(|&pid| Ok(Some(pid))), |pid| {
                        Ok(identities.get(&pid).copied())
                    })
                },
                |pid| {
                    let (parent, start) = identities[&pid];
                    if pid == changed {
                        checks += 1;
                        Ok(Some((
                            parent,
                            if checks == 1 { start } else { start + 100 },
                        )))
                    } else {
                        Ok(Some((parent, start)))
                    }
                },
                |pid| {
                    if pid == changed {
                        changed_rss_reads += 1;
                        assert_eq!(changed_rss_reads, 1, "replacement RSS");
                    }
                    Ok(Some(10))
                },
            );
            assert_eq!(checks, 2);
            assert_eq!(changed_rss_reads, 1);
            assert_eq!(
                state.exhaustion,
                Some(format!(
                    "process identity changed since discovery for PID {changed}"
                ))
            );
            assert_eq!(state.capacity(&[]), 0);
        }
    }

    #[test]
    fn fresh_accounting_errors_for_roots_and_discovered_members_still_exhaust() {
        let cli = std::process::id();
        let (worker, child) = (cli + 1, cli + 2);
        let identities = BTreeMap::from([(cli, (0, 1)), (worker, (cli, 2)), (child, (worker, 3))]);
        for failed_pid in [cli, worker, child] {
            for identity_error in [false, true] {
                let mut state = budget();
                state.sample_with_readers(
                    &[worker],
                    true,
                    || {
                        ProcessTable::collect(identities.keys().map(|&pid| Ok(Some(pid))), |pid| {
                            Ok(identities.get(&pid).copied())
                        })
                    },
                    |pid| {
                        if pid == failed_pid && identity_error {
                            Err("fresh identity unavailable".into())
                        } else {
                            Ok(identities.get(&pid).copied())
                        }
                    },
                    |pid| {
                        if pid == failed_pid {
                            assert!(!identity_error, "RSS read after identity failure");
                            Err("fresh RSS unavailable".into())
                        } else {
                            Ok(Some(10))
                        }
                    },
                );
                assert_eq!(
                    state.exhaustion.as_deref(),
                    Some(if identity_error {
                        "fresh identity unavailable"
                    } else {
                        "fresh RSS unavailable"
                    })
                );
                assert_eq!(state.capacity(&[]), 0);
            }
        }
    }

    #[test]
    fn growing_slot_observations_raise_aggregate_floor_and_preserve_admission_reserve() {
        let cli = std::process::id();
        let worker = cli + 1;
        let identities = BTreeMap::from([(cli, (0, 1)), (worker, (cli, 2))]);
        let mut state = budget();
        let mut reads = 0;
        state.sample_with_readers(
            &[worker],
            true,
            || {
                ProcessTable::collect(identities.keys().map(|&pid| Ok(Some(pid))), |pid| {
                    Ok(identities.get(&pid).copied())
                })
            },
            |pid| Ok(identities.get(&pid).copied()),
            |_| {
                reads += 1;
                Ok(Some(reads * 10))
            },
        );
        // One snapshot: aggregate reads CLI and worker; the slot tree and CLI
        // floor reuse them, so growth cannot appear between observations.
        assert_eq!(reads, 2);
        assert_eq!(state.observed_workers[&worker], 20);
        assert_eq!(state.current, 30);
        assert_eq!(state.peak_aggregate_rss_bytes, 30);
        assert_eq!(state.measured_worker_peak_bytes, 20);
        assert_eq!(state.admission_worker_cost_bytes, 25);
        // A later sample reads afresh and still raises the peaks.
        state.sample_with_readers(
            &[worker],
            true,
            || {
                ProcessTable::collect(identities.keys().map(|&pid| Ok(Some(pid))), |pid| {
                    Ok(identities.get(&pid).copied())
                })
            },
            |pid| Ok(identities.get(&pid).copied()),
            |_| {
                reads += 1;
                Ok(Some(reads * 10))
            },
        );
        assert_eq!(reads, 4);
        assert_eq!(state.observed_workers[&worker], 40);
        assert_eq!(state.current, 70);
        assert_eq!(state.peak_aggregate_rss_bytes, 70);
        assert_eq!(state.measured_worker_peak_bytes, 40);
        assert_eq!(state.admission_worker_cost_bytes, 50);
        state.budget_bytes = 200;
        state.host_available = 200;
        state.reserve_bytes = 40;
        state.active = 1;
        assert_eq!(state.capacity(&[worker]), 1); // (200 - 70 - 40 - 10) / 50
        state.reserve_bytes = 100;
        assert_eq!(state.capacity(&[worker]), 0);
    }

    #[test]
    fn rate_limited_samples_do_not_discover_but_forced_samples_do() {
        let mut state = budget();
        state.last_sample = Instant::now();
        state.sample_with_readers(
            &[],
            false,
            || panic!("unexpected discovery"),
            |_| panic!("unexpected identity"),
            |_| panic!("unexpected RSS"),
        );
        assert_eq!(state.samples, 0);
        for force in [true, true] {
            state.sample_with_readers(
                &[],
                force,
                || Err("discovery failed".into()),
                |_| panic!("unexpected identity"),
                |_| panic!("unexpected RSS"),
            );
        }
        assert_eq!(state.samples, 2);
        assert_eq!(state.exhaustion.as_deref(), Some("discovery failed"));
        assert_eq!(state.capacity(&[]), 0);
    }

    #[test]
    fn retry_resources_keep_samples_and_diagnostics_aligned() {
        let mut state = budget();
        state.samples = 2;
        state.accounting_diagnostics = Some(AccountingDiagnostics {
            clock_version: 2,
            thread_cpu_since_budget_start_ns: None,
            thread_cpu_failures: 0,
            sampling: StageTotals {
                calls: 2,
                wall_ns: Some(40),
                self_user_ns: Some(10),
                self_system_ns: Some(20),
                ..StageTotals::default()
            },
            discovery: totals(),
            identity_reads: totals(),
            rss_reads: totals(),
            ..AccountingDiagnostics::default()
        });
        let previous = serde_json::to_value(&state).unwrap();
        let mut current = previous.clone();
        merge_retry_resources(&mut current, &previous);
        assert_eq!(current["samples"], 4);
        assert_eq!(current["accountingDiagnostics"]["sampling"]["calls"], 4);
        assert_eq!(current["accountingDiagnostics"]["sampling"]["wallNs"], 80);
        assert_eq!(
            current["accountingDiagnostics"]["sampling"]["selfSystemNs"],
            40
        );
        let mut invalid = previous.clone();
        invalid["accountingDiagnostics"]["sampling"]["selfUserNs"] = serde_json::Value::Null;
        invalid["accountingDiagnostics"]["sampling"]["cpuFailures"] = 1.into();
        merge_retry_resources(&mut current, &invalid);
        assert!(current["accountingDiagnostics"]["sampling"]["selfUserNs"].is_null());
        assert!(current["accountingDiagnostics"]["sampling"]["selfSystemNs"].is_null());
        assert_eq!(
            current["accountingDiagnostics"]["sampling"]["cpuFailures"],
            1
        );
        let mut overflowing = totals();
        overflowing.wall_ns = Some(u64::MAX);
        overflowing.self_user_ns = Some(u64::MAX);
        overflowing.merge(StageTotals {
            wall_ns: Some(1),
            self_user_ns: Some(1),
            ..totals()
        });
        assert_eq!(
            (
                overflowing.wall_ns,
                overflowing.self_user_ns,
                overflowing.self_system_ns
            ),
            (None, None, None)
        );
        assert_eq!(
            (overflowing.timing_failures, overflowing.cpu_failures),
            (1, 1)
        );
        let mut disabled = serde_json::to_value(budget()).unwrap();
        merge_retry_resources(&mut disabled, &serde_json::to_value(budget()).unwrap());
        assert!(disabled.get("accountingDiagnostics").is_none());
    }

    #[test]
    fn accounting_profile_switch_initializes_budget() {
        const CHILD: &str = "OXIMUT_ACCOUNTING_SWITCH_CHILD";
        if let Some(expected) = std::env::var_os(CHILD) {
            let options = super::super::parse_options(
                [
                    "--root".into(),
                    Path::new(env!("CARGO_MANIFEST_DIR"))
                        .join("../../fixtures/m1")
                        .to_string_lossy()
                        .into_owned(),
                    "--baseline-only".into(),
                ]
                .into_iter(),
            )
            .unwrap()
            .unwrap();
            let state = Budget::new(&options).unwrap();
            let enabled = expected == "1";
            assert_eq!(state.accounting_diagnostics.is_some(), enabled);
            assert_eq!(
                serde_json::to_value(&state)
                    .unwrap()
                    .get("accountingDiagnostics")
                    .is_some(),
                enabled
            );
            if enabled {
                assert_eq!(
                    state.accounting_diagnostics.as_ref().unwrap().clock_version,
                    2
                );
                assert_eq!(
                    state
                        .accounting_diagnostics
                        .as_ref()
                        .unwrap()
                        .sampling
                        .calls,
                    state.samples
                );
                let report = state.report_snapshot();
                assert!(report["accountingDiagnostics"]["threadCpuSinceBudgetStartNs"].is_number());
                assert!(report["accountingDiagnostics"]["threadCpuFailures"].is_number());
            }
            return;
        }
        for flag in [None, Some("0"), Some("1"), Some("true")] {
            let mut command = std::process::Command::new(std::env::current_exe().unwrap());
            command
                .args([
                    "--exact",
                    "resources::tests::accounting_profile_switch_initializes_budget",
                ])
                .env(CHILD, if flag == Some("1") { "1" } else { "0" })
                .env_remove("OXIMUT_PROFILE_ACCOUNTING");
            if let Some(flag) = flag {
                command.env("OXIMUT_PROFILE_ACCOUNTING", flag);
            }
            let output = command.output().unwrap();
            assert!(
                output.status.success(),
                "{flag:?}: {}",
                String::from_utf8_lossy(&output.stdout)
            );
        }
    }

    #[test]
    fn accounting_profile_is_optional_bounded_and_counts_admitted_failed_reads() {
        let mut state = budget();
        let disabled = serde_json::to_value(&state).unwrap();
        assert!(disabled.get("accountingDiagnostics").is_none());
        state.accounting_diagnostics = Some(AccountingDiagnostics {
            clock_version: 2,
            thread_cpu_since_budget_start_ns: None,
            thread_cpu_failures: 0,
            scope: "CLI sampling thread only",
            evidence: "incomplete",
            sampling: totals(),
            discovery: totals(),
            identity_reads: totals(),
            rss_reads: totals(),
            timing_note: "nested",
        });
        let cli = std::process::id();
        let mut discovers = 0;
        let mut identities = 0;
        let mut rss_reads = 0;
        state.sample_with_readers(
            &[],
            false,
            || {
                discovers += 1;
                ProcessTable::collect([Ok(Some(cli))], |_| Ok(Some((0, 1))))
            },
            |_| {
                identities += 1;
                Err("injected identity error".into())
            },
            |_| {
                rss_reads += 1;
                Ok(Some(1))
            },
        );
        let prof = state.accounting_diagnostics.as_ref().unwrap();
        // The periodic counter pass and then the strict exact pass each check the
        // identity once; the exact pass still fails closed.
        assert_eq!((discovers, identities, rss_reads), (1, 2, 0));
        assert_eq!(
            (
                prof.sampling.calls,
                prof.discovery.calls,
                prof.identity_reads.calls,
                prof.rss_reads.calls
            ),
            (1, 1, 2, 0)
        );
        assert!(
            state
                .exhaustion
                .as_deref()
                .unwrap()
                .contains("injected identity error")
        );
        let encoded = serde_json::to_value(&state).unwrap();
        assert_eq!(
            encoded["accountingDiagnostics"]["identityReads"]["calls"],
            2
        );
        assert!(
            encoded["accountingDiagnostics"]["identityReads"]["wallNs"]
                .as_u64()
                .is_some()
        );

        state.last_sample = Instant::now();
        state.sample_with_readers(
            &[],
            false,
            || panic!("rate-limited discovery"),
            |_| panic!("rate-limited identity"),
            |_| panic!("rate-limited RSS"),
        );
        assert_eq!(
            state
                .accounting_diagnostics
                .as_ref()
                .unwrap()
                .sampling
                .calls,
            1
        );
    }

    #[test]
    fn accounting_profile_measures_fresh_rss_calls_including_errors() {
        let mut state = budget();
        state.accounting_diagnostics = Some(AccountingDiagnostics {
            sampling: totals(),
            discovery: totals(),
            identity_reads: totals(),
            rss_reads: totals(),
            ..AccountingDiagnostics::default()
        });
        let cli = std::process::id();
        let mut rss_calls = 0;
        for fail in [false, true] {
            state.sample_with_readers(
                &[],
                true,
                || ProcessTable::collect([Ok(Some(cli))], |_| Ok(Some((0, 1)))),
                |_| Ok(Some((0, 1))),
                |_| {
                    rss_calls += 1;
                    if fail {
                        Err("injected RSS error".into())
                    } else {
                        Ok(Some(10))
                    }
                },
            );
        }
        // Each sample reads the CLI once: the final floor reuses the snapshot,
        // and the next sample (including its error) reads afresh.
        assert_eq!(rss_calls, 2);
        let profile = state.accounting_diagnostics.as_ref().unwrap();
        assert_eq!(profile.rss_reads.calls, rss_calls);
        assert_eq!(profile.sampling.calls, state.samples);
        assert_eq!(profile.discovery.calls, 2);
        assert_eq!(profile.identity_reads.calls, 3);
        assert!(profile.rss_reads.wall_ns.is_some());
        assert_eq!(state.exhaustion.as_deref(), Some("injected RSS error"));
    }

    #[test]
    fn accounting_clock_faults_and_overflows_never_become_zero_evidence() {
        let mut stage = totals();
        stage.record_elapsed(Some(7), Some((10, 20)), Some((13, 25)));
        assert_eq!(
            (stage.wall_ns, stage.self_user_ns, stage.self_system_ns),
            (Some(7), Some(3), Some(5))
        );
        stage.record_elapsed(Some(1), None, Some((14, 26)));
        assert_eq!(
            (stage.self_user_ns, stage.self_system_ns, stage.cpu_failures),
            (None, None, 1)
        );
        stage.record_elapsed(None, Some((14, 26)), Some((13, 26)));
        assert_eq!(
            (stage.wall_ns, stage.timing_failures, stage.cpu_failures),
            (None, 1, 2)
        );
        stage.record_elapsed(Some(1), Some((14, 26)), Some((15, 27)));
        assert_eq!(
            (stage.wall_ns, stage.self_user_ns, stage.self_system_ns),
            (None, None, None)
        );

        let mut overflow = totals();
        overflow.wall_ns = Some(u64::MAX);
        overflow.self_user_ns = Some(u64::MAX);
        overflow.record_elapsed(Some(1), Some((0, 0)), Some((1, 0)));
        assert_eq!(
            (
                overflow.wall_ns,
                overflow.self_user_ns,
                overflow.self_system_ns
            ),
            (None, None, None)
        );
        assert_eq!((overflow.timing_failures, overflow.cpu_failures), (1, 1));
        let mut counter = totals();
        counter.calls = u64::MAX;
        counter.start();
        assert_eq!(counter.calls, u64::MAX);
        assert_eq!((counter.wall_ns, counter.timing_failures), (None, 1));
        assert!(
            timeval_ns(libc::timeval {
                tv_sec: 0,
                tv_usec: 1_000_000
            })
            .is_none()
        );
        assert!(
            timeval_ns(libc::timeval {
                tv_sec: -1,
                tv_usec: 0
            })
            .is_none()
        );
    }

    #[test]
    fn next_sample_rediscovers_births_and_reparented_children() {
        let cli = std::process::id();
        let worker = cli + 1;
        let child = cli + 2;
        let mut state = budget();
        for (identities, workers, expected) in [
            (
                BTreeMap::from([(cli, (0, 1)), (worker, (cli, 2))]),
                vec![worker],
                20,
            ),
            (
                BTreeMap::from([(cli, (0, 1)), (worker, (cli, 2)), (child, (worker, 3))]),
                vec![worker],
                30,
            ),
            (
                BTreeMap::from([(cli, (0, 1)), (child, (cli, 3))]),
                vec![],
                20,
            ),
        ] {
            state.sample_with_readers(
                &workers,
                true,
                || {
                    ProcessTable::collect(identities.keys().map(|&pid| Ok(Some(pid))), |pid| {
                        Ok(identities.get(&pid).copied())
                    })
                },
                |pid| Ok(identities.get(&pid).copied()),
                |_| Ok(Some(10)),
            );
            assert_eq!(state.current, expected);
            assert_eq!(state.exhaustion, None);
        }
        assert_eq!(state.samples, 3);
        assert!(state.observed_workers.is_empty());
        assert_eq!(state.measured_worker_peak_bytes, 20);
        assert_eq!(state.admission_worker_cost_bytes, 25);
        assert_eq!(state.peak_aggregate_rss_bytes, 30);
    }

    #[test]
    fn samples_keep_dynamic_cgroup_limit_and_oom_gates() {
        let directory = tempfile::tempdir().unwrap();
        let limit = directory.path().join("memory.max");
        let usage = directory.path().join("memory.current");
        let events = directory.path().join("memory.events");
        fs::write(&limit, "max").unwrap();
        fs::write(&usage, "10").unwrap();
        fs::write(&events, "oom_kill 0").unwrap();
        let mut state = budget();
        state.cgroups.push(Cgroup {
            limit_path: limit.clone(),
            usage_path: usage.clone(),
            limit_bytes: None,
            oom_events_path: events.clone(),
            initial_oom_events: Some(0),
        });
        let sample = |state: &mut Budget| {
            let cli = std::process::id();
            state.sample_with_readers(
                &[],
                true,
                || ProcessTable::collect([Ok(Some(cli))], |_| Ok(Some((0, 1)))),
                |_| Ok(Some((0, 1))),
                |_| Ok(Some(100)),
            );
        };
        sample(&mut state);
        assert_eq!(state.exhaustion, None);
        fs::write(&limit, "50").unwrap();
        sample(&mut state);
        assert_eq!(state.budget_bytes, 50);
        assert!(
            state
                .exhaustion
                .as_deref()
                .unwrap()
                .contains("exceeded budget")
        );
        assert_eq!(state.capacity(&[]), 0);
        state.exhaustion = None;
        fs::write(&limit, "max").unwrap();
        fs::write(&events, "oom_kill 1").unwrap();
        sample(&mut state);
        assert!(
            state
                .exhaustion
                .as_deref()
                .unwrap()
                .contains("OOM counter increased")
        );
        assert_eq!(state.budget_bytes, u64::MAX);
        state.exhaustion = None;
        fs::write(&usage, "invalid").unwrap();
        sample(&mut state);
        assert_eq!(
            state.exhaustion.as_deref(),
            Some("cgroup memory usage accounting unavailable")
        );
        assert_eq!(state.capacity(&[]), 0);
    }

    #[test]
    fn missing_and_reused_identities_never_read_replacement_rss() {
        for (start, actual, expected) in [
            (Some(1), None, Some(0)),      // Exited after discovery.
            (None, None, Some(0)),         // Exited before discovery.
            (Some(1), Some((0, 2)), None), // PID reuse.
            (None, Some((0, 2)), None),    // Born after discovery; no safe tree identity.
        ] {
            let result = checked_rss(10, start, &mut |_| Ok(actual), &mut |_| {
                panic!("must not read unknown/replacement RSS")
            });
            if let Some(expected) = expected {
                assert_eq!(result.unwrap(), expected);
            } else {
                assert!(result.is_err());
            }
        }
        let mut state = budget();
        let cli = std::process::id();
        let child = cli + 1;
        let table = ProcessTable::collect([Ok(Some(child))], |_| Ok(Some((cli, 3)))).unwrap();
        // Even a missing root must not erase the already discovered children.
        assert_eq!(
            tree_rss(
                cli,
                &table,
                &mut |pid| Ok((pid == child).then_some((cli, 3))),
                &mut |_| Ok(Some(100))
            )
            .unwrap(),
            100
        );
        state.sample_with_readers(
            &[],
            true,
            || Ok(table),
            |_| Err("identity permission denied".into()),
            |_| panic!("unexpected RSS"),
        );
        assert_eq!(
            state.exhaustion.as_deref(),
            Some("identity permission denied")
        );
        assert_eq!(state.capacity(&[]), 0);
        let table = ProcessTable::collect([Ok(Some(cli))], |_| Ok(Some((0, 1)))).unwrap();
        state.exhaustion = None;
        state.sample_with_readers(
            &[],
            true,
            || Ok(table),
            |_| Ok(Some((0, 1))),
            |_| Err("RSS unavailable".into()),
        );
        assert_eq!(state.exhaustion.as_deref(), Some("RSS unavailable"));
        assert_eq!(state.capacity(&[]), 0);
    }

    #[test]
    fn rollup_uses_resident_rss_and_rejects_invalid_accounting() {
        assert_eq!(
            rollup_rss("Pss: 1 kB\nRss: 65536 kB\nAnonymous: 2 kB\n").unwrap(),
            64 * MIB
        );
        for invalid in [
            "Pss: 10 kB\n",
            "Rss: not-a-number kB\n",
            "Rss: 10 MB\n",
            "Rss: 10 kB extra\n",
            "Rss: 18446744073709551615 kB\n",
        ] {
            assert!(rollup_rss(invalid).is_err(), "{invalid}");
        }
    }
    #[test]
    fn exited_process_without_memory_map_has_no_resident_working_set() {
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .arg("--list")
            .stdout(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let mut exited = false;
        for _ in 0..200 {
            let stat = fs::read_to_string(format!("/proc/{}/stat", child.id())).unwrap();
            if stat
                .rsplit_once(')')
                .and_then(|(_, fields)| fields.split_whitespace().next())
                == Some("Z")
            {
                exited = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        let observed = rss(child.id());
        child.wait().unwrap();
        assert!(exited, "child must exit before checking its memory map");
        assert_eq!(observed.unwrap().unwrap_or(0), 0);
    }
    #[test]
    fn resolves_namespaced_cgroup_mounts_and_every_ancestor() {
        let unified = parse_hierarchy_paths(
            "0::/tenant/job\n",
            "1 0 0:1 /tenant /sys/fs/cgroup rw - cgroup2 cgroup rw\n",
            "memory",
        );
        assert_eq!(
            unified,
            vec![
                (PathBuf::from("/sys/fs/cgroup"), true),
                (PathBuf::from("/sys/fs/cgroup/job"), true)
            ]
        );
        let legacy = parse_hierarchy_paths(
            "2:memory:/parent/child\n",
            "2 0 0:2 / /cg rw - cgroup cgroup rw,memory\n",
            "memory",
        );
        assert_eq!(legacy.len(), 3);
        assert_eq!(legacy[2].0, PathBuf::from("/cg/parent/child"));
        assert!(
            parse_hierarchy_paths(
                "2:cpu:/job\n",
                "2 0 0:2 / /cg rw - cgroup cgroup rw,cpu\n",
                "memory"
            )
            .is_empty()
        );
    }
    #[test]
    fn memory_limits_change_between_unlimited_and_finite_without_parse_failure() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("memory.max");
        fs::write(&path, "max\n").unwrap();
        assert_eq!(memory_limit(&path).unwrap(), None);
        fs::write(&path, "104857600\n").unwrap();
        assert_eq!(memory_limit(&path).unwrap(), Some(100 * MIB));
        fs::write(&path, "9223372036854771712\n").unwrap();
        assert_eq!(memory_limit(&path).unwrap(), None);
        assert_eq!(
            effective_budget(1024 * MIB, [None, Some(256 * MIB), Some(512 * MIB)]),
            256 * MIB
        );
        fs::write(&path, "oom 2\noom_kill 1\n").unwrap();
        assert_eq!(oom_events(&path), Some(1));
    }
    #[test]
    fn own_rss_and_descendants_are_measurable() {
        assert!(rss(std::process::id()).unwrap().unwrap() > 0);
        assert!(cpu_limit() >= 1);
        assert!(meminfo("MemTotal").unwrap() > 0);
    }
    #[test]
    fn unified_and_v1_limits_have_explicit_units() {
        for group in memory_cgroups().unwrap() {
            assert!(group.limit_bytes.is_none_or(|n| n < 1 << 60));
            assert!(group.usage_path.is_absolute());
        }
    }
    #[test]
    fn precise_cpu_delta_failure_backward_and_overflow_are_sticky() {
        let mut s = StageTotals {
            self_cpu_ns: Some(7),
            ..StageTotals::default()
        };
        s.record_precise_delta(Some(5));
        assert_eq!(s.self_cpu_ns, Some(12));
        s.record_precise_delta(None);
        assert_eq!(s.self_cpu_ns, None);
        assert_eq!(s.self_cpu_failures, 1);
        let mut backward = StageTotals::default();
        backward.record_precise_delta(9u64.checked_sub(10));
        assert_eq!(backward.self_cpu_ns, None);
        assert_eq!(backward.self_cpu_failures, 1);
        let mut overflow = StageTotals {
            self_cpu_ns: Some(u64::MAX),
            ..StageTotals::default()
        };
        overflow.record_precise_delta(Some(1));
        assert_eq!(overflow.self_cpu_ns, None);
        assert_eq!(overflow.self_cpu_failures, 1);
    }
    #[test]
    fn precise_retry_failures_are_not_counted_again_when_nulls_are_preserved() {
        let mut failed = totals();
        failed.record_precise_delta(None);
        let mut merged = totals();
        merged.merge(failed);
        assert_eq!(merged.self_cpu_failures, 1);
        merged.merge(totals());
        assert_eq!((merged.self_cpu_ns, merged.self_cpu_failures), (None, 1));
        let mut current = serde_json::json!({"accountingDiagnostics": {
            "sampling": totals(), "discovery": totals(), "identityReads": totals(), "rssReads": totals(),
            "threadCpuSinceBudgetStartNs": 3, "threadCpuFailures": 0}});
        let valid = current.clone();
        let mut previous = valid.clone();
        previous["accountingDiagnostics"]["threadCpuSinceBudgetStartNs"] = serde_json::Value::Null;
        previous["accountingDiagnostics"]["threadCpuFailures"] = 1.into();
        merge_retry_resources(&mut current, &previous);
        assert_eq!(current["accountingDiagnostics"]["threadCpuFailures"], 1);
        merge_retry_resources(&mut current, &valid);
        assert_eq!(current["accountingDiagnostics"]["threadCpuFailures"], 1);
        assert!(current["accountingDiagnostics"]["threadCpuSinceBudgetStartNs"].is_null());
    }

    #[test]
    fn precise_stage_clock_records_work_inside_its_wall_interval() {
        let mut stage = totals();
        let wall = Instant::now();
        let mark = stage.start();
        let mut value = 0u64;
        for i in 0..std::hint::black_box(100_000u64) {
            value = std::hint::black_box(value.wrapping_add(i));
        }
        std::hint::black_box(value);
        stage.end(mark, wall);
        assert!(stage.self_cpu_ns.unwrap() > 0);
        assert!(stage.self_cpu_ns.unwrap() <= stage.wall_ns.unwrap());
        assert_eq!(stage.self_cpu_failures, 0);
    }

    #[test]
    fn precise_timespec_rejects_negative_noncanonical_and_overflowed_values() {
        assert_eq!(
            timespec_ns(libc::timespec {
                tv_sec: 2,
                tv_nsec: 3
            }),
            Some(2_000_000_003)
        );
        for (sec, nsec) in [(-1, 0), (0, -1), (0, 1_000_000_000), (libc::time_t::MAX, 0)] {
            assert_eq!(
                timespec_ns(libc::timespec {
                    tv_sec: sec,
                    tv_nsec: nsec
                }),
                None
            );
        }
    }

    #[test]
    fn budget_snapshot_rejects_another_thread_and_preserves_start_failure() {
        let mut state = budget();
        state.accounting_diagnostics = Some(AccountingDiagnostics {
            clock_version: 2,
            ..AccountingDiagnostics::default()
        });
        state.budget_cpu_failures = 1;
        let missing = state.report_snapshot();
        assert!(missing["accountingDiagnostics"]["threadCpuSinceBudgetStartNs"].is_null());
        assert_eq!(missing["accountingDiagnostics"]["threadCpuFailures"], 1);
        state.budget_cpu_start = Some(precise_thread_cpu_ns().unwrap());
        state.budget_cpu_failures = 0;
        let wrong_thread = std::thread::spawn(move || state.report_snapshot())
            .join()
            .unwrap();
        assert!(wrong_thread["accountingDiagnostics"]["threadCpuSinceBudgetStartNs"].is_null());
        assert_eq!(
            wrong_thread["accountingDiagnostics"]["threadCpuFailures"],
            1
        );
    }
    #[test]
    fn retry_merge_sums_precise_and_budget_thread_cpu() {
        let mut current = serde_json::json!({"accountingDiagnostics":{"sampling":{"calls":1,"wallNs":3,"selfUserNs":1,"selfSystemNs":1,"selfCpuNs":2,"selfCpuFailures":0,"cpuFailures":0,"timingFailures":0},"discovery":{"calls":0,"wallNs":0,"selfUserNs":0,"selfSystemNs":0,"selfCpuNs":0,"selfCpuFailures":0,"cpuFailures":0,"timingFailures":0},"identityReads":{"calls":0,"wallNs":0,"selfUserNs":0,"selfSystemNs":0,"selfCpuNs":0,"selfCpuFailures":0,"cpuFailures":0,"timingFailures":0},"rssReads":{"calls":0,"wallNs":0,"selfUserNs":0,"selfSystemNs":0,"selfCpuNs":0,"selfCpuFailures":0,"cpuFailures":0,"timingFailures":0},"threadCpuSinceBudgetStartNs":5,"threadCpuFailures":0}});
        let previous = current.clone();
        merge_retry_resources(&mut current, &previous);
        assert_eq!(current["accountingDiagnostics"]["sampling"]["selfCpuNs"], 4);
        assert_eq!(
            current["accountingDiagnostics"]["threadCpuSinceBudgetStartNs"],
            10
        );
    }
}
