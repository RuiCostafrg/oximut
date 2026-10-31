"""Copied with an absolute real-interpreter shebang as diagnostic PATH/python3."""
import cProfile
import json
import os
from pathlib import Path
import runpy
import sys
import time


def report_failure(action, error):
    """Report diagnostic failures without replacing the supervisor's exit behavior."""
    try:
        print(f'Diagnostic Python {action} failed: {error}', file=sys.stderr)
    except Exception:
        pass


def main():
    """Profile only the selected supervisor, preserving argv, IPC and exit behavior."""
    actual = os.environ['OXIMUT_PROFILE_PYTHON']
    target = Path(os.environ['OXIMUT_PROFILE_SUPERVISOR'])
    # Delegate every other invocation by exec: preserve descriptors, signals/status,
    # and argv, with no PATH lookup or recursive python3 launch.
    if len(sys.argv) < 2 or Path(sys.argv[1]).resolve() != target:
        os.execv(actual, [actual, *sys.argv[1:]])
    directory = Path(os.environ['OXIMUT_PROFILE_DIR'])
    stem = directory / f'python-{os.getpid()}-{time.time_ns()}'
    start_time = None
    try:
        start_time = Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19]
        if not (start_time.isascii() and start_time.isdecimal()):
            raise ValueError('Invalid process start time')
    except Exception as error:
        start_time = None
        report_failure('identity read', error)
    record = {'pid': os.getpid(), 'ppid': os.getppid(), 'argv': sys.argv[1:],
              'cwd': os.getcwd(), 'timer': 'time.process_time (self CPU only)',
              'startTime': start_time,
              'completed': False}
    identity = stem.with_suffix('.json')
    initial_identity_written = False
    try:
        identity.write_text(json.dumps(record, indent=2))
        initial_identity_written = True
    except Exception as error:
        record['initialIdentityWriteFailed'] = True
        report_failure('identity write', error)
    sys.argv = sys.argv[1:]
    sys.path[0] = str(target.parent)
    profiler = cProfile.Profile(timer=time.process_time)
    try:
        profiler.enable()
        runpy.run_path(str(target), run_name='__main__')
    finally:
        profiler.disable()
        # Never replace the supervisor's exception/exit code with a profiler error.
        try:
            profiler.dump_stats(str(stem.with_suffix('.pstats')))
            if initial_identity_written:
                record['completed'] = True  # Dump completed, not a workload success claim.
        except Exception as error:
            report_failure('profile write', error)
        try:
            # Retry metadata even after a transient initial failure, keeping it incomplete.
            identity.write_text(json.dumps(record, indent=2))
        except Exception as error:
            report_failure('identity write', error)


if __name__ == '__main__':
    main()
