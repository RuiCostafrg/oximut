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


def snapshot():
    result = {}
    for item in os.listdir('/proc'):
        if not item.isdigit():
            continue
        try:
            with open(f'/proc/{item}/stat', encoding='utf8') as stream:
                text = stream.read()
            fields = text[text.rfind(')') + 2:].split()
            result[int(item)] = (int(fields[1]), fields[19], fields[0])
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


def descendants(table, parent):
    children = [pid for pid, value in table.items() if value[0] == parent]
    return [descendant for child in children for descendant in [*descendants(table, child), child]]


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
    os.close(ipc_fd)
    with open(report_path + '.ready', 'w', encoding='utf8') as stream:
        stream.write('ready')
    owned = {}
    reaped = []
    controller_status = None
    cleanup_started = None
    next_snapshot = 0.0
    while True:
        # Historical identity discovery is sampled; reaping remains responsive.
        if stop or time.monotonic() >= next_snapshot:
            table = snapshot()
            for pid in descendants(table, os.getpid()):
                owned[pid] = table[pid][1]
            next_snapshot = time.monotonic() + 0.05
        no_children = False
        while True:
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
        if stop:
            cleanup_started = cleanup_started or time.monotonic()
            # Never signal from a cached active snapshot: adoption may have changed.
            table = snapshot()
            for pid in descendants(table, os.getpid()):
                owned[pid] = table[pid][1]
            # All adopted children are ours. Identity checks use this fresh snapshot.
            for pid in descendants(table, os.getpid()):
                if table[pid][1] == owned.get(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if no_children and not descendants(snapshot(), os.getpid()):
                break
            if time.monotonic() - cleanup_started > 2:
                raise RuntimeError('Owned descendants did not exit within cleanup bound')
        time.sleep(0.01)

    final = snapshot()
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
