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


def check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise RuntimeError('Owned descendants did not exit within cleanup bound')


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
                if process_identity(pid)[1] == stamp:
                    os.kill(pid, signal.SIGKILL)
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
    ipc_fd = int(os.environ['NODE_CHANNEL_FD'])
    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    controller = subprocess.Popen([node, worker], pass_fds=(ipc_fd,))
    owned = {}
    cleanup_started = None
    try:
        # Pin the direct child before broad discovery, including startup failures.
        try:
            owned[controller.pid] = process_identity(controller.pid)[1]
        except (FileNotFoundError, ProcessLookupError):
            pass
        os.close(ipc_fd)
        with open(report_path + '.ready', 'w', encoding='utf8') as stream:
            stream.write('ready')
        reaped = []
        controller_status = None
        next_snapshot = 0.0
        while True:
            if stop and cleanup_started is None:
                cleanup_started = time.monotonic()
            deadline = cleanup_started + 2 if cleanup_started is not None else None
            check_deadline(deadline)
            # Historical identity discovery is sampled; reaping remains responsive.
            if stop or time.monotonic() >= next_snapshot:
                table = snapshot(deadline)
                for pid in descendants(table, os.getpid(), deadline):
                    owned[pid] = table[pid][1]
                next_snapshot = time.monotonic() + 0.05
            no_children = False
            while True:
                check_deadline(deadline)
                try:
                    pid, status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    no_children = True
                    pid = 0
                if not pid:
                    break
                reaped.append(pid)
                if pid == controller.pid:
                    controller_status = os.waitstatus_to_exitcode(status)
                    stop = True
                    if cleanup_started is None:
                        cleanup_started = time.monotonic()
                        deadline = cleanup_started + 2
            if stop:
                # Never signal from a cached active snapshot: adoption may have changed.
                table = snapshot(deadline)
                check_deadline(deadline)
                for pid in descendants(table, os.getpid(), deadline):
                    owned[pid] = table[pid][1]
                # Re-read identity immediately before signalling: even a fresh
                # discovery snapshot cannot rule out subsequent PID reuse.
                for pid in descendants(table, os.getpid(), deadline):
                    check_deadline(deadline)
                    try:
                        current_start = process_identity(pid)[1]
                    except (OSError, ValueError, IndexError):
                        check_deadline(deadline)
                        continue
                    check_deadline(deadline)
                    if current_start == table[pid][1]:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                if no_children:
                    final = snapshot(deadline)
                    if not descendants(final, os.getpid(), deadline):
                        check_deadline(deadline)
                        break
                check_deadline(deadline)
            time.sleep(0.01)

    except Exception:
        # Discovery failure must not strand independently verifiable descendants.
        # Preserve the original error and never publish successful cleanup evidence.
        try:
            cleanup_known(owned, (cleanup_started if cleanup_started is not None
                                  else time.monotonic()) + 2)
        except Exception:
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
