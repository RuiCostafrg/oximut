"""Copied with an absolute real-interpreter shebang as diagnostic PATH/python3."""
import cProfile
import json
import os
from pathlib import Path
import runpy
import sys
import time


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
    record = {'pid': os.getpid(), 'ppid': os.getppid(), 'argv': sys.argv[1:],
              'cwd': os.getcwd(), 'timer': 'time.process_time (self CPU only)',
              'startTime': Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19],
              'completed': False}
    identity = stem.with_suffix('.json')
    identity.write_text(json.dumps(record, indent=2))
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
            record['completed'] = True  # Dump completed, not a workload success claim.
            identity.write_text(json.dumps(record, indent=2))
        except Exception as error:
            try:
                print(f'Diagnostic Python profile write failed: {error}', file=sys.stderr)
            except OSError:
                pass


if __name__ == '__main__':
    main()
