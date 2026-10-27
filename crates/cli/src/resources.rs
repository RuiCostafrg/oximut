//! Linux sampled admission control. Never claims to install an OS hard limit.
use super::{Options, descendants, process_identity};
use serde::Serialize;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

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
fn tree_rss(pid: u32, tree: &BTreeMap<u32, u64>) -> Result<u64, String> {
    let mut total = rss(pid)?.unwrap_or(0);
    for (&pid, &start) in tree {
        if process_identity(pid).is_some_and(|(_, actual)| actual == start) {
            total = total.saturating_add(rss(pid)?.unwrap_or(0));
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
    pub exhaustion: Option<String>,
    #[serde(skip)]
    last_sample: Instant,
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
            measurement: "Linux /proc smaps_rollup Rss sum, approximate stat RSS fallback on older/restricted kernels: main CLI and all worker/adapter/controller/test descendants; shared pages counted per process",
            enforcement: "best-effort sampled admission and process-tree cancellation; existing cgroup limits are OS-enforced; no new hard cap installed",
            sample_interval_ms: SAMPLE_MS,
            peak_aggregate_rss_bytes: 0,
            measured_worker_peak_bytes: 0,
            admission_worker_cost_bytes: 0,
            max_active_jobs: 0,
            admission_pauses: 0,
            recycled_idle_workers: 0,
            samples: 0,
            exhaustion: None,
            last_sample: Instant::now() - Duration::from_secs(1),
            current: 0,
            active: 0,
            host_available: 0,
            observed_workers: BTreeMap::new(),
            base_budget_bytes,
        };
        state.sample(&[], true);
        Ok(state)
    }
    pub fn sample(&mut self, workers: &[u32], force: bool) {
        if !force && self.last_sample.elapsed() < Duration::from_millis(SAMPLE_MS) {
            return;
        }
        self.last_sample = Instant::now();
        self.samples += 1;
        let observed = (|| -> Result<(), String> {
            // Detect an unreadable process directory rather than silently using zero.
            fs::read_dir("/proc").map_err(|e| format!("process accounting unavailable: {e}"))?;
            self.current = tree_rss(std::process::id(), &descendants(std::process::id()))?;
            self.peak_aggregate_rss_bytes = self.peak_aggregate_rss_bytes.max(self.current);
            self.observed_workers.clear();
            for &pid in workers {
                let bytes = tree_rss(pid, &descendants(pid))?;
                self.measured_worker_peak_bytes = self.measured_worker_peak_bytes.max(bytes);
                self.admission_worker_cost_bytes = self
                    .measured_worker_peak_bytes
                    .saturating_add(self.measured_worker_peak_bytes.div_ceil(4));
                self.observed_workers.insert(pid, bytes);
            }
            // Processes can spawn between the aggregate and per-slot scans.
            // Never credit a slot's RSS against reservations unless aggregate
            // accounting includes at least those same disjoint slot totals.
            let accounted_slots = self
                .observed_workers
                .values()
                .fold(rss(std::process::id())?.unwrap_or(0), |total, bytes| {
                    total.saturating_add(*bytes)
                });
            self.current = self.current.max(accounted_slots);
            self.peak_aggregate_rss_bytes = self.peak_aggregate_rss_bytes.max(self.current);
            self.host_available =
                meminfo("MemAvailable").ok_or("host available memory accounting unavailable")?;
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
            Ok(())
        })();
        if let Err(error) = observed
            && self.exhaustion.is_none()
        {
            self.exhaustion = Some(error);
        }
    }
    pub fn capacity(&self, active_pids: &[u32]) -> usize {
        if self.exhaustion.is_some() {
            return 0;
        }
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
        self.effective_workers
            .saturating_sub(self.active)
            .min((headroom / cost).min(usize::MAX as u64) as usize)
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
}
