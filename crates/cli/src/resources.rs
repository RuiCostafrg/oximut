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
// Discovery is shared; identity and RSS observations are deliberately fresh for
// every aggregate/slot observation, including roots and the final CLI floor.
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
        total = total.saturating_add(checked_rss(pid, Some(start), read_identity, read_rss)?);
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
        self.sample_with_readers(
            workers,
            force,
            || ProcessTable::read_with(identity),
            identity,
            rss,
        );
    }
    fn sample_with_readers(
        &mut self,
        workers: &[u32],
        force: bool,
        discover: impl FnOnce() -> Result<ProcessTable, String>,
        mut read_identity: impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
        mut read_rss: impl FnMut(u32) -> Result<Option<u64>, String>,
    ) {
        if !force && self.last_sample.elapsed() < Duration::from_millis(SAMPLE_MS) {
            return;
        }
        self.last_sample = Instant::now();
        self.samples += 1;
        let observed = (|| -> Result<(), String> {
            // Exactly one fresh broad discovery per admitted sample. This also
            // includes adopted descendants outside any live worker branch.
            let table = discover()?;
            self.current = tree_rss(
                std::process::id(),
                &table,
                &mut read_identity,
                &mut read_rss,
            )?;
            self.peak_aggregate_rss_bytes = self.peak_aggregate_rss_bytes.max(self.current);
            self.observed_workers.clear();
            for &pid in workers {
                let bytes = tree_rss(pid, &table, &mut read_identity, &mut read_rss)?;
                self.measured_worker_peak_bytes = self.measured_worker_peak_bytes.max(bytes);
                self.admission_worker_cost_bytes = self
                    .measured_worker_peak_bytes
                    .saturating_add(self.measured_worker_peak_bytes.div_ceil(4));
                self.observed_workers.insert(pid, bytes);
            }
            // RSS can grow between the aggregate and per-slot observations.
            // Never credit a slot's RSS against reservations unless aggregate
            // accounting includes at least those same slot totals. Nested slots
            // intentionally overcount; duplicate PIDs retain one observed total.
            let accounted_slots = self.observed_workers.values().fold(
                checked_rss(
                    std::process::id(),
                    table.start(std::process::id()),
                    &mut read_identity,
                    &mut read_rss,
                )?,
                |total, bytes| total.saturating_add(*bytes),
            );
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
            exhaustion: None,
            last_sample: Instant::now() - Duration::from_secs(1),
            current: 0,
            active: 0,
            host_available: u64::MAX,
            observed_workers: BTreeMap::new(),
            base_budget_bytes: u64::MAX,
        }
    }

    #[test]
    fn samples_discover_once_and_read_rss_fresh_for_every_tree() {
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
            assert_eq!(
                observations.len(),
                2 * (6 + workers
                    .iter()
                    .map(|pid| if *pid == worker { 2 } else { 1 })
                    .sum::<usize>())
            );
            for pair in observations.chunks_exact(2) {
                assert_eq!(pair[0], ("identity", pair[1].1));
                assert_eq!(pair[1].0, "rss");
                assert_ne!(pair[1].1, unrelated);
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
        assert_eq!(reads, 4); // Aggregate CLI/worker, slot worker, CLI floor.
        assert_eq!(state.observed_workers[&worker], 30);
        assert_eq!(state.current, 70);
        assert_eq!(state.peak_aggregate_rss_bytes, 70);
        assert_eq!(state.measured_worker_peak_bytes, 30);
        assert_eq!(state.admission_worker_cost_bytes, 38);
        state.budget_bytes = 200;
        state.host_available = 200;
        state.reserve_bytes = 40;
        state.active = 1;
        assert_eq!(state.capacity(&[worker]), 2); // (200 - 70 - 40 - 8) / 38
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
}
