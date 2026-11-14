//! Event waits that replace fixed sleeps on the job path: readiness of channel
//! descriptors (`poll(2)`) and process exits (pidfd). Every wait is bounded by
//! the caller's deadline, and callers re-check their own state after waking,
//! so an early or spurious wake-up is always safe.
use std::os::fd::{AsRawFd, OwnedFd, RawFd};
use std::process::{Child, ExitStatus};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(any(target_os = "linux", target_os = "android"))]
pub fn pidfd_open(pid: u32) -> std::io::Result<OwnedFd> {
    use std::os::fd::FromRawFd;
    let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::c_int, 0) };
    if fd < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(unsafe { OwnedFd::from_raw_fd(fd as i32) })
}
#[cfg(not(any(target_os = "linux", target_os = "android")))]
pub fn pidfd_open(_: u32) -> std::io::Result<OwnedFd> {
    Err(std::io::ErrorKind::Unsupported.into())
}

fn remaining(until: Instant) -> Duration {
    until.saturating_duration_since(Instant::now())
}

/// `poll(2)` for input on `fds` (hang-ups and errors always count) until one
/// is ready or `until`. Returns each descriptor's `revents`. An interrupted
/// wait returns early with nothing ready, so signal flags are seen promptly.
/// With no descriptors this is an interruptible sleep until `until`.
pub fn readable(fds: &[RawFd], until: Instant) -> std::io::Result<Vec<libc::c_short>> {
    events(fds, libc::POLLIN, until)
}

/// Waits until `fd` accepts output (or hangs up), at most until `until`.
pub fn writable(fd: RawFd, until: Instant) -> std::io::Result<()> {
    events(&[fd], libc::POLLOUT, until).map(drop)
}

fn events(
    fds: &[RawFd],
    events: libc::c_short,
    until: Instant,
) -> std::io::Result<Vec<libc::c_short>> {
    let mut items: Vec<_> = fds
        .iter()
        .map(|&fd| libc::pollfd {
            fd,
            events,
            revents: 0,
        })
        .collect();
    poll(&mut items, until)?;
    Ok(items.iter().map(|item| item.revents).collect())
}

/// Waits on per-descriptor interests, returning early on interruption.
pub fn poll(items: &mut [libc::pollfd], until: Instant) -> std::io::Result<()> {
    // Round up so a sub-millisecond remainder does not become a busy loop.
    let wait = remaining(until);
    let millis = wait
        .as_millis()
        .saturating_add(u128::from(!wait.subsec_nanos().is_multiple_of(1_000_000)))
        .min(i32::MAX as u128) as i32;
    let ready = unsafe { libc::poll(items.as_mut_ptr(), items.len() as libc::nfds_t, millis) };
    if ready < 0 {
        let error = std::io::Error::last_os_error();
        if error.kind() != std::io::ErrorKind::Interrupted {
            return Err(error);
        }
        return Ok(());
    }
    Ok(())
}

/// Waits until one of `pids` exits, at most until `until`.
///
/// Returns at once when a process is already gone. A process that has exited
/// but is not yet reaped (a zombie) wakes the wait at once only when
/// `reaping_ours` is set and this process is its parent, because the caller
/// will reap it; a zombie that another process must reap cannot signal
/// anything new, so when only such processes remain the wait lasts at most
/// `idle`. Without pidfd support the wait also lasts at most `idle`, which is
/// the fixed sleep this replaces.
pub fn any_exit(
    pids: impl IntoIterator<Item = u32>,
    until: Instant,
    idle: Duration,
    reaping_ours: bool,
) {
    let idle_until = (Instant::now() + idle).min(until);
    let mut fds = vec![];
    for pid in pids {
        match pidfd_open(pid) {
            Ok(fd) => fds.push((pid, fd)),
            // Already gone: the caller re-checks now.
            Err(error) if error.raw_os_error() == Some(libc::ESRCH) => return,
            Err(_) => {
                thread::sleep(remaining(idle_until));
                return;
            }
        }
    }
    if fds.is_empty() {
        thread::sleep(remaining(idle_until));
        return;
    }
    let raw: Vec<_> = fds.iter().map(|(_, fd)| fd.as_raw_fd()).collect();
    let Ok(now) = readable(&raw, Instant::now()) else {
        thread::sleep(remaining(idle_until));
        return;
    };
    let me = std::process::id();
    let mut live = vec![];
    for ((pid, fd), revents) in fds.iter().zip(now) {
        if revents == 0 {
            live.push(fd.as_raw_fd());
        } else if reaping_ours
            && crate::processes::identity(*pid)
                .ok()
                .flatten()
                .is_none_or(|(parent, _)| parent == me)
        {
            return;
        }
    }
    if live.is_empty() {
        thread::sleep(remaining(idle_until));
        return;
    }
    if readable(&live, until).is_err() {
        thread::sleep(remaining(idle_until));
    }
}

/// Waits for an unreaped child of this process to exit, at most until
/// `until`, and reaps it. The unreaped child pins its PID, so its pidfd is
/// the right process. `Ok(None)` means it is still running at `until`.
pub fn child_exit(child: &mut Child, until: Instant) -> std::io::Result<Option<ExitStatus>> {
    let pidfd = pidfd_open(child.id()).ok();
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(Some(status));
        }
        if Instant::now() >= until {
            return Ok(None);
        }
        match &pidfd {
            Some(fd) => {
                readable(&[fd.as_raw_fd()], until)?;
            }
            None => thread::sleep(remaining(until).min(Duration::from_millis(5))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::PROCESS_TEST;
    use std::process::Command;

    #[test]
    fn child_exit_wakes_on_exit_not_on_the_deadline() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let mut child = Command::new("sh").args(["-c", "exit 3"]).spawn().unwrap();
        // A deadline an hour away: returning at all shows the exit woke it.
        let status = child_exit(&mut child, Instant::now() + Duration::from_secs(3600))
            .unwrap()
            .expect("exited");
        assert_eq!(status.code(), Some(3));
    }

    #[test]
    fn child_exit_reports_a_running_child_at_the_deadline() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let mut child = Command::new("sleep").arg("60").spawn().unwrap();
        assert!(child_exit(&mut child, Instant::now()).unwrap().is_none());
        child.kill().unwrap();
        child.wait().unwrap();
    }

    #[test]
    fn any_exit_wakes_when_a_child_becomes_reapable() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let mut child = Command::new("sh").args(["-c", "exit 0"]).spawn().unwrap();
        // Our own exited child returns at once, however long `idle` is.
        any_exit(
            [child.id()],
            Instant::now() + Duration::from_secs(3600),
            Duration::from_secs(3600),
            true,
        );
        // It has exited (a zombie until reaped), so a non-blocking wait reaps it.
        let watchdog = Instant::now() + Duration::from_secs(30);
        loop {
            if child.try_wait().unwrap().is_some() {
                break;
            }
            assert!(Instant::now() < watchdog, "child did not exit");
            any_exit(
                [child.id()],
                Instant::now() + Duration::from_secs(3600),
                Duration::from_secs(3600),
                true,
            );
        }
    }

    #[test]
    fn any_exit_returns_at_once_for_a_process_that_is_gone() {
        let _guard = PROCESS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        let mut child = Command::new("sh").args(["-c", "exit 0"]).spawn().unwrap();
        let pid = child.id();
        child.wait().unwrap();
        any_exit(
            [pid],
            Instant::now() + Duration::from_secs(3600),
            Duration::from_secs(3600),
            false,
        );
    }

    #[test]
    fn readable_wakes_on_input_and_on_hang_up() {
        use std::io::Write;
        use std::os::unix::net::UnixStream;
        let (ours, mut theirs) = UnixStream::pair().unwrap();
        theirs.write_all(b"x").unwrap();
        let ready = readable(
            &[ours.as_raw_fd()],
            Instant::now() + Duration::from_secs(3600),
        )
        .unwrap();
        assert_ne!(ready[0] & libc::POLLIN, 0);
        let (ours, theirs) = UnixStream::pair().unwrap();
        drop(theirs);
        let ready = readable(
            &[ours.as_raw_fd()],
            Instant::now() + Duration::from_secs(3600),
        )
        .unwrap();
        assert_ne!(ready[0], 0);
    }
}
