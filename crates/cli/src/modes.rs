//! Resolution of the execution modes from what the command line asked for.
//!
//! Every mode is a request (`Choice`: explicit on, explicit off, or automatic)
//! that `resolve` turns, once, into a plain decision. Automatic modes turn on
//! whenever their prerequisites hold and are skipped silently otherwise; an
//! explicit positive request whose prerequisites are missing is an error. The
//! rest of the program only sees the resolved booleans.

use super::{Execution, Isolation};

/// The most simultaneous jobs `--workers` accepts.
pub const MAX_WORKERS: usize = 1024;

/// Memory an automatically chosen worker is assumed to need before any worker
/// has been measured: one test fork plus its share of the controller. Runtime
/// memory admission replaces the assumption with measurements.
const AUTO_WORKER_MEMORY_MIB: u64 = 512;

const MIB: u64 = 1024 * 1024;

/// What `--workers` asked for.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Workers {
    /// No `--workers`, or `--workers auto`: derived from the host.
    #[default]
    Auto,
    Count(usize),
}

impl Workers {
    pub fn parse(value: &str) -> Result<Self, String> {
        if value == "auto" {
            return Ok(Workers::Auto);
        }
        let count: usize = value
            .parse()
            .map_err(|_| "--workers requires an integer or auto")?;
        if !(1..=MAX_WORKERS).contains(&count) {
            return Err(format!("--workers must be 1..{MAX_WORKERS}"));
        }
        Ok(Workers::Count(count))
    }
}

/// StrykerJS's default concurrency applied to the CPUs this process may use:
/// all of them up to four, and one fewer above that so the machine stays
/// responsive. The result is at least one and at most `MAX_WORKERS`.
pub fn auto_workers(cpus: usize) -> usize {
    let cpus = cpus.max(1);
    (if cpus <= 4 { cpus } else { cpus - 1 }).min(MAX_WORKERS)
}

/// How many automatically chosen workers the memory budget can hold at their
/// assumed cost; always at least one.
fn memory_workers(budget_bytes: u64, reserve_bytes: u64) -> usize {
    let affordable = budget_bytes.saturating_sub(reserve_bytes) / (AUTO_WORKER_MEMORY_MIB * MIB);
    affordable.clamp(1, MAX_WORKERS as u64) as usize
}

/// What a mode flag pair asked for.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Choice {
    /// Neither `--name` nor `--no-name` was given.
    #[default]
    Auto,
    On,
    Off,
}

impl Choice {
    /// Record `--name` (`on`) or `--no-name`; asking for both is an error.
    pub fn request(&mut self, name: &str, on: bool) -> Result<(), String> {
        let requested = if on { Choice::On } else { Choice::Off };
        if *self != Choice::Auto && *self != requested {
            return Err(format!("--{name} and --no-{name} cannot be combined"));
        }
        *self = requested;
        Ok(())
    }

    fn source(self, automatic: &'static str) -> &'static str {
        match self {
            Choice::Auto => automatic,
            Choice::On | Choice::Off => "explicit",
        }
    }
}

/// The command line's requests, before any prerequisite is checked.
#[derive(Debug)]
pub struct Requested {
    /// `None` when `--execution` was not given.
    pub execution: Option<Execution>,
    pub isolation: Isolation,
    pub workers: Workers,
    pub memory_budget_mib: Option<u64>,
    pub memory_reserve_mib: u64,
    pub no_shared_controller: bool,
    pub test_selection: Choice,
    pub zygote: Choice,
    pub zygote_precollect: Choice,
    pub rolling_lanes: Choice,
}

/// Facts about the machine, injected so the modes can be tested without it.
#[derive(Clone, Debug)]
pub struct Platform {
    /// The platform supports the zygote (Linux with glibc is checked later by
    /// the addon itself).
    pub zygote_platform: bool,
    /// CPUs this process may use: the scheduler's affinity mask and any cgroup
    /// CPU quota, not the host's core count.
    pub cpu_limit: usize,
    /// Physical memory, when it can be read.
    pub physical_memory_bytes: Option<u64>,
    /// The tightest cgroup memory limit above this process, if any.
    pub cgroup_memory_limit_bytes: Option<u64>,
}

/// Facts about the host the modes depend on.
#[derive(Debug)]
pub struct Host {
    pub platform: Platform,
    /// Whether the adapter is the reference persistent adapter, or why that
    /// could not be determined.
    pub reference_adapter: Result<bool, String>,
}

/// The modes a run uses.
#[derive(Debug, PartialEq, Eq)]
pub struct Modes {
    pub execution: Execution,
    /// The maximum simultaneous jobs; memory admission may lower it at run time.
    pub workers: usize,
    /// How the count was chosen: `explicit`, or `auto: N CPUs` with any memory limit.
    pub workers_source: String,
    pub test_selection: bool,
    pub shared_controller: bool,
    pub zygote: bool,
    /// Why the zygote is off when that is an observable choice or limit.
    pub zygote_off_reason: Option<String>,
    pub zygote_precollect: bool,
    pub rolling_lanes: bool,
    /// One line naming each mode and whether it was asked for or resolved.
    pub summary: String,
}

/// The default execution: a persistent controller with per-mutant isolation
/// for the reference adapter. A custom adapter keeps the fresh protocol every
/// adapter speaks, so it runs unchanged without `--execution fresh`.
pub fn default_execution(reference_adapter: bool) -> Execution {
    if reference_adapter {
        Execution::Persistent
    } else {
        Execution::Fresh
    }
}

/// Auto mode for the zygote: on exactly for persistent, fresh-process runs of
/// the reference adapter on a supported platform; otherwise the reason it is off.
fn auto_zygote(
    platform: bool,
    execution: Execution,
    isolation: Isolation,
    reference_adapter: bool,
) -> Result<(), String> {
    if !platform {
        return Err("not Linux".into());
    }
    if execution != Execution::Persistent {
        return Err("execution fresh".into());
    }
    if isolation != Isolation::FreshProcess {
        return Err("isolation fresh-thread".into());
    }
    if !reference_adapter {
        return Err("custom adapter".into());
    }
    Ok(())
}

/// The worker count for a request: an explicit count as given, otherwise the
/// automatic rule on the CPU limit, held to what the memory budget can carry.
fn resolve_workers(requested: &Requested, platform: &Platform) -> (usize, String) {
    match requested.workers {
        Workers::Count(count) => (count, "explicit".into()),
        Workers::Auto => {
            let by_cpu = auto_workers(platform.cpu_limit);
            let configured = requested
                .memory_budget_mib
                .map(|mib| mib.saturating_mul(MIB));
            let budget = configured
                .or(platform.physical_memory_bytes)
                .into_iter()
                .chain(platform.cgroup_memory_limit_bytes)
                .min();
            let cpus = platform.cpu_limit.max(1);
            match budget {
                Some(budget) => {
                    let by_memory =
                        memory_workers(budget, requested.memory_reserve_mib.saturating_mul(MIB));
                    if by_memory < by_cpu {
                        let mib = budget / MIB;
                        return (
                            by_memory,
                            format!("auto: {cpus} CPUs, limited by {mib} MiB of memory"),
                        );
                    }
                    (by_cpu, format!("auto: {cpus} CPUs"))
                }
                None => (by_cpu, format!("auto: {cpus} CPUs")),
            }
        }
    }
}

pub fn resolve(requested: &Requested, host: &Host) -> Result<Modes, String> {
    let (workers, workers_source) = resolve_workers(requested, &host.platform);
    let reference_adapter = host
        .reference_adapter
        .as_ref()
        .is_ok_and(|reference| *reference);
    let execution = requested
        .execution
        .unwrap_or_else(|| default_execution(reference_adapter));

    let (zygote, zygote_off_reason) = match requested.zygote {
        Choice::On => {
            if execution != Execution::Persistent {
                return Err("--zygote requires --execution persistent".into());
            }
            if requested.isolation != Isolation::FreshProcess {
                return Err("--zygote requires --isolation fresh-process".into());
            }
            match &host.reference_adapter {
                Err(error) => return Err(error.clone()),
                Ok(false) => return Err("--zygote requires the reference persistent adapter (m2-runner.mjs beside m2-worker.ts)".into()),
                Ok(true) => {}
            }
            (true, None)
        }
        Choice::Off => (false, Some("--no-zygote".to_string())),
        Choice::Auto => match auto_zygote(
            host.platform.zygote_platform,
            execution,
            requested.isolation,
            reference_adapter,
        ) {
            Ok(()) => (true, None),
            Err(reason) => (false, Some(reason)),
        },
    };

    let test_selection = requested.test_selection != Choice::Off;

    // Default for parallel persistent runs; unsupported projects fall back at run time.
    let shared_controller = !requested.no_shared_controller
        && execution == Execution::Persistent
        && workers >= 2
        && reference_adapter;

    let mut precollect_off_reason = None;
    let zygote_precollect = match requested.zygote_precollect {
        Choice::On => {
            if !zygote {
                return Err(match &zygote_off_reason {
                    Some(reason) => {
                        format!("--zygote-precollect requires the zygote, which is off ({reason})")
                    }
                    None => "--zygote-precollect requires --zygote".into(),
                });
            }
            if !test_selection {
                return Err("--zygote-precollect requires --test-selection".into());
            }
            true
        }
        Choice::Off => false,
        Choice::Auto => {
            precollect_off_reason = if !zygote {
                Some(format!(
                    "zygote off: {}",
                    zygote_off_reason.as_deref().unwrap_or("unavailable")
                ))
            } else if !test_selection {
                Some("--no-test-selection".to_string())
            } else if !shared_controller {
                Some("no shared controller".to_string())
            } else {
                None
            };
            precollect_off_reason.is_none()
        }
    };

    let rolling_lanes = match requested.rolling_lanes {
        Choice::On => {
            if !shared_controller {
                return Err("--rolling-lanes requires the shared controller (persistent execution, --workers 2 or more, no --no-shared-controller)".into());
            }
            true
        }
        Choice::Off => false,
        Choice::Auto => shared_controller,
    };

    let state = |on: bool| if on { "on" } else { "off" };
    let mut parts = vec![
        format!(
            "execution={} ({})",
            match execution {
                Execution::Fresh => "fresh",
                Execution::Persistent => "persistent",
            },
            if requested.execution.is_some() {
                "explicit"
            } else {
                "default"
            }
        ),
        format!(
            "test-selection={} ({})",
            state(test_selection),
            requested.test_selection.source("default")
        ),
        format!("workers={workers} ({workers_source})"),
        match (&zygote_off_reason, requested.zygote) {
            (Some(reason), Choice::Auto) => format!("zygote=off (auto: {reason})"),
            _ => format!(
                "zygote={} ({})",
                state(zygote),
                requested.zygote.source("auto")
            ),
        },
    ];
    parts.push(
        match (&precollect_off_reason, requested.zygote_precollect) {
            (Some(reason), Choice::Auto) => format!("zygote-precollect=off (auto: {reason})"),
            _ => format!(
                "zygote-precollect={} ({})",
                state(zygote_precollect),
                requested.zygote_precollect.source("auto")
            ),
        },
    );
    parts.push(match (shared_controller, requested.rolling_lanes) {
        (false, Choice::Auto) => "rolling-lanes=off (auto: no shared controller)".to_string(),
        _ => format!(
            "rolling-lanes={} ({})",
            state(rolling_lanes),
            requested.rolling_lanes.source("auto")
        ),
    });

    Ok(Modes {
        execution,
        workers,
        workers_source,
        test_selection,
        shared_controller,
        zygote,
        zygote_off_reason,
        zygote_precollect,
        rolling_lanes,
        summary: format!("modes: {}", parts.join(", ")),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn requested() -> Requested {
        Requested {
            execution: None,
            isolation: Isolation::FreshProcess,
            workers: Workers::Count(4),
            memory_budget_mib: None,
            memory_reserve_mib: 64,
            no_shared_controller: false,
            test_selection: Choice::Auto,
            zygote: Choice::Auto,
            zygote_precollect: Choice::Auto,
            rolling_lanes: Choice::Auto,
        }
    }

    fn host(zygote_platform: bool, reference_adapter: Result<bool, String>) -> Host {
        Host {
            platform: Platform {
                zygote_platform,
                cpu_limit: 8,
                physical_memory_bytes: Some(64 * 1024 * MIB),
                cgroup_memory_limit_bytes: None,
            },
            reference_adapter,
        }
    }

    fn linux() -> Host {
        host(true, Ok(true))
    }

    fn platform(cpu_limit: usize, memory_mib: u64) -> Host {
        let mut host = linux();
        host.platform.cpu_limit = cpu_limit;
        host.platform.physical_memory_bytes = Some(memory_mib * MIB);
        host
    }

    fn auto() -> Requested {
        Requested {
            workers: Workers::Auto,
            ..requested()
        }
    }

    #[test]
    fn defaults_turn_every_fast_mode_on_when_prerequisites_hold() {
        let modes = resolve(&requested(), &linux()).unwrap();
        assert_eq!(modes.execution, Execution::Persistent);
        assert!(modes.test_selection);
        assert!(modes.shared_controller);
        assert!(modes.zygote && modes.zygote_precollect && modes.rolling_lanes);
        assert_eq!(
            modes.summary,
            "modes: execution=persistent (default), test-selection=on (default), workers=4 (explicit), zygote=on (auto), zygote-precollect=on (auto), rolling-lanes=on (auto)"
        );
    }

    #[test]
    fn missing_prerequisites_skip_automatic_modes_without_error() {
        // One worker has no shared controller: neither rolling lanes nor precollect.
        let mut one = requested();
        one.workers = Workers::Count(1);
        let modes = resolve(&one, &linux()).unwrap();
        assert!(modes.zygote && modes.test_selection);
        assert!(!modes.zygote_precollect && !modes.rolling_lanes);
        assert!(
            modes
                .summary
                .contains("zygote-precollect=off (auto: no shared controller)")
        );
        assert!(
            modes
                .summary
                .contains("rolling-lanes=off (auto: no shared controller)")
        );
        // Off Linux the zygote and, with it, precollect stay off; rolling lanes need no zygote.
        let other = host(false, Ok(true));
        let modes = resolve(&requested(), &other).unwrap();
        assert!(!modes.zygote && !modes.zygote_precollect && modes.rolling_lanes);
        assert_eq!(modes.zygote_off_reason.as_deref(), Some("not Linux"));
        assert!(modes.summary.contains("zygote=off (auto: not Linux)"));
        assert!(
            modes
                .summary
                .contains("zygote-precollect=off (auto: zygote off: not Linux)")
        );
        // A custom adapter has no native zygote or shared controller.
        let custom = host(true, Ok(false));
        let modes = resolve(&requested(), &custom).unwrap();
        assert!(!modes.zygote && !modes.shared_controller);
        assert!(!modes.zygote_precollect && !modes.rolling_lanes);
        // ...and keeps the fresh protocol every adapter speaks unless asked otherwise.
        assert_eq!(modes.execution, Execution::Fresh);
        assert!(
            modes
                .summary
                .starts_with("modes: execution=fresh (default)")
        );
        let mut persistent = requested();
        persistent.execution = Some(Execution::Persistent);
        let modes = resolve(&persistent, &custom).unwrap();
        assert_eq!(modes.execution, Execution::Persistent);
        // Fresh execution has none of the persistent modes, silently.
        let mut fresh = requested();
        fresh.execution = Some(Execution::Fresh);
        let modes = resolve(&fresh, &linux()).unwrap();
        assert!(!modes.zygote && !modes.shared_controller);
        assert!(!modes.zygote_precollect && !modes.rolling_lanes);
        assert!(modes.test_selection);
        assert!(
            modes
                .summary
                .starts_with("modes: execution=fresh (explicit)")
        );
        // Fresh-thread isolation has no zygote.
        let mut thread = requested();
        thread.isolation = Isolation::FreshThread;
        let modes = resolve(&thread, &linux()).unwrap();
        assert!(!modes.zygote && !modes.zygote_precollect);
        assert_eq!(
            modes.zygote_off_reason.as_deref(),
            Some("isolation fresh-thread")
        );
    }

    #[test]
    fn each_opt_out_turns_only_its_mode_off() {
        let mut no_selection = requested();
        no_selection.test_selection = Choice::Off;
        let modes = resolve(&no_selection, &linux()).unwrap();
        assert!(!modes.test_selection && !modes.zygote_precollect);
        assert!(modes.zygote && modes.rolling_lanes);
        assert!(modes.summary.contains("test-selection=off (explicit)"));
        assert!(
            modes
                .summary
                .contains("zygote-precollect=off (auto: --no-test-selection)")
        );

        let mut no_precollect = requested();
        no_precollect.zygote_precollect = Choice::Off;
        let modes = resolve(&no_precollect, &linux()).unwrap();
        assert!(!modes.zygote_precollect && modes.zygote && modes.rolling_lanes);
        assert!(modes.summary.contains("zygote-precollect=off (explicit)"));

        let mut no_rolling = requested();
        no_rolling.rolling_lanes = Choice::Off;
        let modes = resolve(&no_rolling, &linux()).unwrap();
        assert!(!modes.rolling_lanes && modes.zygote_precollect && modes.shared_controller);

        let mut no_zygote = requested();
        no_zygote.zygote = Choice::Off;
        let modes = resolve(&no_zygote, &linux()).unwrap();
        assert!(!modes.zygote && !modes.zygote_precollect && modes.rolling_lanes);
        assert_eq!(modes.zygote_off_reason.as_deref(), Some("--no-zygote"));
    }

    #[test]
    fn explicit_modes_with_missing_prerequisites_are_errors() {
        let error = |edit: &dyn Fn(&mut Requested), host: &Host| {
            let mut request = requested();
            edit(&mut request);
            resolve(&request, host).unwrap_err()
        };
        let linux = linux();
        assert!(
            error(
                &|r| r.zygote_precollect = Choice::On,
                &host(false, Ok(true))
            )
            .contains("--zygote-precollect requires the zygote, which is off (not Linux)")
        );
        assert!(
            error(
                &|r| {
                    r.zygote = Choice::Off;
                    r.zygote_precollect = Choice::On;
                },
                &linux
            )
            .contains("--zygote-precollect requires the zygote")
        );
        assert!(
            error(
                &|r| {
                    r.test_selection = Choice::Off;
                    r.zygote_precollect = Choice::On;
                },
                &linux
            )
            .contains("--zygote-precollect requires --test-selection")
        );
        for edit in [
            (&|r: &mut Requested| {
                r.workers = Workers::Count(1);
                r.rolling_lanes = Choice::On;
            }) as &dyn Fn(&mut Requested),
            &|r| {
                r.no_shared_controller = true;
                r.rolling_lanes = Choice::On;
            },
            &|r| {
                r.execution = Some(Execution::Fresh);
                r.rolling_lanes = Choice::On;
            },
        ] {
            assert!(error(edit, &linux).contains("--rolling-lanes requires the shared controller"));
        }
        assert!(
            error(
                &|r| {
                    r.execution = Some(Execution::Fresh);
                    r.zygote = Choice::On;
                },
                &linux
            )
            .contains("--zygote requires --execution persistent")
        );
        assert!(
            error(
                &|r| {
                    r.isolation = Isolation::FreshThread;
                    r.zygote = Choice::On;
                },
                &linux
            )
            .contains("--zygote requires --isolation fresh-process")
        );
        assert!(
            error(
                &|r| {
                    r.execution = Some(Execution::Persistent);
                    r.zygote = Choice::On;
                },
                &host(true, Ok(false))
            )
            .contains("--zygote requires the reference persistent adapter")
        );
        assert_eq!(
            error(
                &|r| {
                    r.execution = Some(Execution::Persistent);
                    r.zygote = Choice::On;
                },
                &host(true, Err("adapter: missing".into()))
            ),
            "adapter: missing"
        );
    }

    #[test]
    fn explicit_on_overrides_a_platform_the_auto_mode_would_skip() {
        let other = host(false, Ok(true));
        let mut request = requested();
        request.zygote = Choice::On;
        let modes = resolve(&request, &other).unwrap();
        assert!(modes.zygote && modes.zygote_precollect);
        assert!(modes.summary.contains("zygote=on (explicit)"));
    }

    #[test]
    fn a_flag_pair_cannot_name_both_sides() {
        let mut choice = Choice::Auto;
        choice.request("rolling-lanes", true).unwrap();
        choice.request("rolling-lanes", true).unwrap();
        assert_eq!(choice, Choice::On);
        assert_eq!(
            choice.request("rolling-lanes", false).unwrap_err(),
            "--rolling-lanes and --no-rolling-lanes cannot be combined"
        );
    }

    #[test]
    fn the_automatic_count_is_strykers_rule() {
        // Up to four CPUs every CPU is used; above four, one is left free.
        for (cpus, expected) in [
            (0, 1),
            (1, 1),
            (2, 2),
            (3, 3),
            (4, 4),
            (5, 4),
            (8, 7),
            (16, 15),
            (1025, 1024),
            (4096, 1024),
        ] {
            assert_eq!(auto_workers(cpus), expected, "{cpus} CPUs");
        }
    }

    #[test]
    fn the_default_resolves_to_auto_on_the_effective_cpu_limit() {
        for (cpus, expected) in [(1, 1), (2, 2), (4, 4), (5, 4), (8, 7), (16, 15)] {
            let modes = resolve(&auto(), &platform(cpus, 64 * 1024)).unwrap();
            assert_eq!(modes.workers, expected, "{cpus} CPUs");
            assert!(
                modes
                    .summary
                    .contains(&format!("workers={expected} (auto: {cpus} CPUs)")),
                "{}",
                modes.summary
            );
        }
        // The fast modes need two workers: a one-CPU host (a cgroup quota of
        // one core, say) quietly keeps its automatic modes off.
        let one = resolve(&auto(), &platform(1, 64 * 1024)).unwrap();
        assert!(!one.shared_controller && !one.rolling_lanes && !one.zygote_precollect);
        let two = resolve(&auto(), &platform(2, 64 * 1024)).unwrap();
        assert!(two.shared_controller && two.rolling_lanes && two.zygote_precollect);
        assert_eq!(Workers::default(), Workers::Auto);
    }

    #[test]
    fn an_explicit_count_ignores_the_host() {
        let mut request = requested();
        request.workers = Workers::Count(12);
        let modes = resolve(&request, &platform(2, 1024)).unwrap();
        assert_eq!(modes.workers, 12);
        assert!(modes.summary.contains("workers=12 (explicit)"));
    }

    #[test]
    fn automatic_workers_do_not_oversubscribe_a_small_memory_budget() {
        // 2 vCPU / 4 GiB: two workers fit.
        let modes = resolve(&auto(), &platform(2, 4096)).unwrap();
        assert_eq!(modes.workers, 2);
        // 16 CPUs on 2 GiB: 2048 - 64 MiB reserve holds three 512 MiB workers.
        let modes = resolve(&auto(), &platform(16, 2048)).unwrap();
        assert_eq!(modes.workers, 3);
        assert!(
            modes
                .summary
                .contains("workers=3 (auto: 16 CPUs, limited by 2048 MiB of memory)")
        );
        // A cgroup limit below physical memory counts; so does --memory-budget-mib.
        let mut limited = platform(16, 64 * 1024);
        limited.platform.cgroup_memory_limit_bytes = Some(1024 * MIB);
        assert_eq!(resolve(&auto(), &limited).unwrap().workers, 1);
        let mut budget = auto();
        budget.memory_budget_mib = Some(2048);
        assert_eq!(
            resolve(&budget, &platform(16, 64 * 1024)).unwrap().workers,
            3
        );
        // Never below one worker, however little memory there is.
        assert_eq!(resolve(&auto(), &platform(8, 256)).unwrap().workers, 1);
        // An unreadable memory budget leaves the CPU rule alone; admission still runs.
        let mut unknown = platform(8, 0);
        unknown.platform.physical_memory_bytes = None;
        assert_eq!(resolve(&auto(), &unknown).unwrap().workers, 7);
        // An explicit count is never lowered here: admission does that at run time.
        let mut explicit = requested();
        explicit.workers = Workers::Count(8);
        assert_eq!(resolve(&explicit, &platform(16, 1024)).unwrap().workers, 8);
    }

    #[test]
    fn workers_parse_as_auto_or_a_bounded_integer() {
        assert_eq!(Workers::parse("auto"), Ok(Workers::Auto));
        assert_eq!(Workers::parse("1"), Ok(Workers::Count(1)));
        assert_eq!(Workers::parse("1024"), Ok(Workers::Count(1024)));
        for bad in ["0", "1025", "-1", "", "Auto", "2.5", "two"] {
            assert!(Workers::parse(bad).is_err(), "{bad:?}");
        }
        assert_eq!(
            Workers::parse("1025").unwrap_err(),
            "--workers must be 1..1024"
        );
    }
}
