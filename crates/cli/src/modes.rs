//! Resolution of the execution modes from what the command line asked for.
//!
//! Every mode is a request (`Choice`: explicit on, explicit off, or automatic)
//! that `resolve` turns, once, into a plain decision. Automatic modes turn on
//! whenever their prerequisites hold and are skipped silently otherwise; an
//! explicit positive request whose prerequisites are missing is an error. The
//! rest of the program only sees the resolved booleans.

use super::{Execution, Isolation};

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
    pub workers: usize,
    pub no_shared_controller: bool,
    pub test_selection: Choice,
    pub zygote: Choice,
    pub zygote_precollect: Choice,
    pub rolling_lanes: Choice,
}

/// Facts about the host the modes depend on, injected so they can be tested
/// without the operating system.
#[derive(Debug)]
pub struct Host {
    /// The platform supports the zygote (Linux with glibc is checked later by
    /// the addon itself).
    pub zygote_platform: bool,
    /// Whether the adapter is the reference persistent adapter, or why that
    /// could not be determined.
    pub reference_adapter: Result<bool, String>,
}

/// The modes a run uses.
#[derive(Debug, PartialEq, Eq)]
pub struct Modes {
    pub execution: Execution,
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

pub fn resolve(requested: &Requested, host: &Host) -> Result<Modes, String> {
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
            host.zygote_platform,
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
        && requested.workers >= 2
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
            workers: 4,
            no_shared_controller: false,
            test_selection: Choice::Auto,
            zygote: Choice::Auto,
            zygote_precollect: Choice::Auto,
            rolling_lanes: Choice::Auto,
        }
    }

    fn linux() -> Host {
        Host {
            zygote_platform: true,
            reference_adapter: Ok(true),
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
            "modes: execution=persistent (default), test-selection=on (default), zygote=on (auto), zygote-precollect=on (auto), rolling-lanes=on (auto)"
        );
    }

    #[test]
    fn missing_prerequisites_skip_automatic_modes_without_error() {
        // One worker has no shared controller: neither rolling lanes nor precollect.
        let mut one = requested();
        one.workers = 1;
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
        let other = Host {
            zygote_platform: false,
            reference_adapter: Ok(true),
        };
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
        let custom = Host {
            zygote_platform: true,
            reference_adapter: Ok(false),
        };
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
                &Host {
                    zygote_platform: false,
                    reference_adapter: Ok(true)
                }
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
                r.workers = 1;
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
                &Host {
                    zygote_platform: true,
                    reference_adapter: Ok(false)
                }
            )
            .contains("--zygote requires the reference persistent adapter")
        );
        assert_eq!(
            error(
                &|r| {
                    r.execution = Some(Execution::Persistent);
                    r.zygote = Choice::On;
                },
                &Host {
                    zygote_platform: true,
                    reference_adapter: Err("adapter: missing".into())
                }
            ),
            "adapter: missing"
        );
    }

    #[test]
    fn explicit_on_overrides_a_platform_the_auto_mode_would_skip() {
        let other = Host {
            zygote_platform: false,
            reference_adapter: Ok(true),
        };
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
}
