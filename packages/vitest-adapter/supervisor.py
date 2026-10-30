"""Linux subreaper: own and reap descendants even when tests detach them.

The inherited Node IPC descriptor connects the parent directly to worker.mjs;
test output uses stdout/stderr, and cleanup evidence uses a private JSON file.
"""
import ctypes
import json
import os
import signal
import subprocess
import sys
import time


def process_identity(pid):
    with open(f'/proc/{pid}/stat', encoding='utf8') as stream:
        text = stream.read()
    fields = text[text.rfind(')') + 2:].split()
    return int(fields[1]), fields[19], fields[0]


class CleanupDeadline(RuntimeError):
    pass


def check_deadline(deadline):
    # A callable deadline lets a scan observe a budget started mid-enumeration.
    deadline = deadline() if callable(deadline) else deadline
    if deadline is not None and time.monotonic() >= deadline:
        raise CleanupDeadline('Owned descendants did not exit within cleanup bound')


def _syscall(number, *args):
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(ctypes.c_long(number), *args)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def pidfd_open(pid):
    """Pin one process generation; some Python builds lack os.pidfd_open."""
    if hasattr(os, 'pidfd_open'):
        return os.pidfd_open(pid)
    return _syscall(434, ctypes.c_long(pid), ctypes.c_long(0))  # SYS_pidfd_open


def pidfd_kill(fd):
    if hasattr(signal, 'pidfd_send_signal'):
        signal.pidfd_send_signal(fd, signal.SIGKILL)
    else:
        _syscall(424, ctypes.c_long(fd), ctypes.c_long(signal.SIGKILL), None,
                 ctypes.c_long(0))  # SYS_pidfd_send_signal


def signal_verified(pid, stamp, deadline):
    """Signal only the generation pinned before its identity was re-read.

    A numeric kill after an identity check can hit a reused PID. The pidfd is
    opened first, so a matching re-read proves it pins that generation; if the
    generation exits afterwards, the signal fails with ESRCH and cannot reach a
    replacement. Missing pidfd support raises OSError: never fall back to kill.
    """
    check_deadline(deadline)
    fd = pidfd_open(pid)
    try:
        current = process_identity(pid)[1]
        check_deadline(deadline)
        if current != stamp:
            return False
        try:
            pidfd_kill(fd)
        except ProcessLookupError:
            return False
        return True
    finally:
        os.close(fd)


def snapshot(deadline=None):
    check_deadline(deadline)
    result = {}
    for item in os.listdir('/proc'):
        check_deadline(deadline)
        if not item.isdigit():
            continue
        try:
            result[int(item)] = process_identity(int(item))
        except (FileNotFoundError, ProcessLookupError):
            pass
        check_deadline(deadline)
    check_deadline(deadline)
    return result


def descendants(table, parent, deadline=None):
    check_deadline(deadline)
    children = []
    for pid, value in table.items():
        check_deadline(deadline)
        if value[0] == parent:
            children.append(pid)
    result = []
    for child in children:
        check_deadline(deadline)
        result.extend(descendants(table, child, deadline))
        result.append(child)
    check_deadline(deadline)
    return result


def cleanup_known(owned, deadline):
    """Bounded error-path cleanup; never use a cached identity to authorize a kill."""
    owned = dict(owned)
    while True:
        if time.monotonic() >= deadline:
            return
        for pid, stamp in owned.items():
            if time.monotonic() >= deadline:
                return
            try:
                signal_verified(pid, stamp, deadline)
            except CleanupDeadline:
                return
            except (OSError, ValueError, IndexError):
                # Gone, reused or independently unverifiable: do not signal it.
                pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            except OSError:
                break
            if not pid:
                break
            if time.monotonic() >= deadline:
                return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def main():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'PR_SET_CHILD_SUBREAPER failed')

    node, worker, report_path = sys.argv[1:]
    # Fail before spawning anything if identity-bound signalling is unavailable.
    os.close(pidfd_open(os.getpid()))
    ipc_fd = int(os.environ['NODE_CHANNEL_FD'])
    stop = False
    cleanup_started = None

    def request_stop(_signum, _frame):
        nonlocal stop, cleanup_started
        if cleanup_started is None:
            cleanup_started = time.monotonic()
        stop = True

    def active_deadline():
        return cleanup_started + 2 if cleanup_started is not None else None

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    controller = subprocess.Popen([node, worker], pass_fds=(ipc_fd,))
    owned = {}
    unexpected_identity_failure = False
    try:
        # Pin the direct child before broad discovery, including startup failures.
        try:
            owned[controller.pid] = process_identity(controller.pid)[1]
        except (FileNotFoundError, ProcessLookupError):
            pass
        except Exception:
            unexpected_identity_failure = True
            raise
        os.close(ipc_fd)
        with open(report_path + '.ready', 'w', encoding='utf8') as stream:
            stream.write('ready')
        reaped = []
        controller_status = None

        def record(pid, status):
            nonlocal controller_status, stop, cleanup_started
            reaped.append(pid)
            if pid == controller.pid:
                controller_status = os.waitstatus_to_exitcode(status)
                stop = True
                if cleanup_started is None:
                    cleanup_started = time.monotonic()

        next_snapshot = 0.0
        while True:
            if stop and cleanup_started is None:
                cleanup_started = time.monotonic()
            deadline = cleanup_started + 2 if cleanup_started is not None else None
            check_deadline(deadline)
            # Historical identity discovery is sampled; reaping remains responsive.
            if stop or time.monotonic() >= next_snapshot:
                # A stop signal during this scan starts a budget it must observe.
                table = snapshot(active_deadline)
                for pid in descendants(table, os.getpid(), active_deadline):
                    owned[pid] = table[pid][1]
                next_snapshot = time.monotonic() + 0.05
            no_children = False
            while True:
                check_deadline(active_deadline)
                try:
                    pid, status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    no_children = True
                    pid = 0
                if not pid:
                    break
                record(pid, status)
            if stop:
                # A signal can arrive after this iteration cached its deadline.
                if cleanup_started is None:
                    cleanup_started = time.monotonic()
                deadline = cleanup_started + 2
                check_deadline(deadline)
                # Never signal from a cached active snapshot: adoption may have changed.
                table = snapshot(deadline)
                check_deadline(deadline)
                for pid in descendants(table, os.getpid(), deadline):
                    owned[pid] = table[pid][1]
                # Even a fresh discovery snapshot cannot rule out PID reuse.
                for pid in descendants(table, os.getpid(), deadline):
                    try:
                        signal_verified(pid, table[pid][1], deadline)
                    except (OSError, ValueError, IndexError):
                        check_deadline(deadline)
                if no_children:
                    final = snapshot(deadline)
                    if not descendants(final, os.getpid(), deadline):
                        check_deadline(deadline)
                        # ECHILD predates the scan: a child adopted during it is
                        # missed by the scan but visible to a fresh waitpid.
                        try:
                            pid, status = os.waitpid(-1, os.WNOHANG)
                        except ChildProcessError:
                            break
                        if pid:
                            record(pid, status)
                check_deadline(deadline)
            time.sleep(0.01)

    except Exception as error:
        # Discovery failure must not strand independently verifiable descendants.
        # Preserve the original error and never publish successful cleanup evidence.
        deadline = (cleanup_started if cleanup_started is not None else time.monotonic()) + 2
        if unexpected_identity_failure:
            # Popen owns this unreaped direct child, independently of /proc.
            # Do not use this exception path for cached descendant identities.
            try:
                os.close(ipc_fd)
            except OSError:
                pass
            if time.monotonic() < deadline:
                try:
                    controller.kill()
                except Exception:
                    pass
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    try:
                        controller.wait(timeout=remaining)
                    except Exception:
                        pass
        try:
            cleanup_known(owned, deadline)
        except Exception:
            pass
        # The Node parent cannot see this exception; leave a bounded diagnostic.
        try:
            with open(report_path + '.error', 'w', encoding='utf8') as stream:
                json.dump({'error': f'{type(error).__name__}: {error}'[:2048]}, stream)
        except OSError:
            pass
        raise
    states = [{'pid': pid, 'startTime': stamp,
               'state': final[pid][2] if pid in final and final[pid][1] == stamp else 'absent'}
              for pid, stamp in owned.items()]
    report = {'supervisorPid': os.getpid(), 'controllerPid': controller.pid,
              'tracked': list(owned), 'states': states, 'reaped': reaped,
              'allExited': all(item['state'] in ('absent', 'Z') for item in states),
              'allReaped': all(item['state'] == 'absent' for item in states),
              'controllerStatus': controller_status,
              'method': 'Linux PR_SET_CHILD_SUBREAPER + waitpid ECHILD; observed PID/start-time identities'}
    with open(report_path, 'w', encoding='utf8') as stream:
        json.dump(report, stream)
    return 0


if __name__ == '__main__':
    sys.exit(main())
