#!/usr/bin/env python3
"""Linux-only process-cold Vitest/Stryker measurements; Python standard library."""
from __future__ import annotations

import argparse
import collections
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import signal
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PAGE_SIZE = os.sysconf('SC_PAGE_SIZE')
SCHEMA_VERSION = 1
LINUX_KERNEL = sys.platform.startswith('linux') or sys.platform == 'android'
FIXTURE_TEST_SCOPE = {
    'pure': (7, {'fixtures/pure/test/logic.test.ts'}),
    'stateful': (4, {'fixtures/stateful/test/dependency.test.ts',
                     'fixtures/stateful/test/service.test.ts'}),
}


def parse_proc_stat(value: str) -> dict:
    """Parse /proc/PID/stat, including command names containing spaces or ')'."""
    head, tail = value.rsplit(')', 1)
    fields = tail.split()
    return {
        'pid': int(head.split('(', 1)[0]),
        'state': fields[0], 'ppid': int(fields[1]), 'pgrp': int(fields[2]),
        'starttime': int(fields[19]), 'rss_bytes': max(0, int(fields[21])) * PAGE_SIZE,
    }


def process_snapshot() -> dict[int, dict]:
    result = {}
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                stat = parse_proc_stat((entry / 'stat').read_text())
                result[stat['pid']] = stat
            except (OSError, ValueError, IndexError):
                pass  # A process can disappear during a sample.
    return result


def process_tree(snapshot: dict[int, dict], root: int, known: dict[int, int],
                 controller: int) -> dict[int, dict]:
    selected = {
        pid: stat for pid, stat in snapshot.items()
        if (pid == root or stat['pgrp'] == root or stat['ppid'] == controller
            or known.get(pid) == stat['starttime'])
    }
    while True:
        children = {pid: stat for pid, stat in snapshot.items()
                    if stat['ppid'] in selected and pid not in selected}
        if not children:
            break
        selected.update(children)
    known.update({pid: stat['starttime'] for pid, stat in selected.items()})
    return selected


def parse_rollup_rss(value: str) -> int:
    """Use the kernel's page-table RSS sum, not approximate stat counters."""
    rows = [line.split() for line in value.splitlines() if line.startswith('Rss:')]
    if (len(rows) != 1 or len(rows[0]) != 3 or rows[0][2] != 'kB'
            or not rows[0][1].isascii() or not rows[0][1].isdigit()):
        raise ValueError('missing, duplicate, or malformed smaps_rollup Rss')
    return int(rows[0][1]) * 1024


def aggregate_rollup_rss(tree: dict[int, dict]) -> tuple[int, list[str]]:
    """Sample owned live processes; reject unavailable live-process accounting.

    PID identity is checked after reading so a reused PID cannot contribute.
    Exits between discovery and sampling are normal. No approximate fallback is
    used: a restricted kernel cannot establish a memory performance claim.
    """
    total, errors = 0, []
    for pid, expected in tree.items():
        path = Path(f'/proc/{pid}')
        failure = None
        try:
            rss = parse_rollup_rss((path / 'smaps_rollup').read_text())
        except ProcessLookupError:
            # The kernel drops mm before marking a task zombie. smaps_rollup
            # returns ESRCH in that exit window even if stat still says live.
            continue
        except (OSError, ValueError) as error:
            failure = error
        try:
            current = parse_proc_stat((path / 'stat').read_text())
        except FileNotFoundError:
            continue
        except (OSError, ValueError, IndexError) as error:
            errors.append(f'{pid}: identity unavailable: {error}')
            continue
        if current['starttime'] != expected['starttime'] or current['state'] in ('Z', 'X'):
            continue
        if failure is not None:
            errors.append(f'{pid}: RSS unavailable: {failure}')
        else:
            total += rss
    return total, errors


def enable_subreaper() -> None:
    # PR_SET_CHILD_SUBREAPER: reap orphaned grandchildren, including timeout jobs.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'PR_SET_CHILD_SUBREAPER failed')


def signal_tree(root: int, tree: dict[int, dict], sig: int) -> None:
    try:
        os.killpg(root, sig)
    except ProcessLookupError:
        pass
    for pid, stat in tree.items():
        try:
            # Avoid signaling a reused PID between sampling and termination.
            current = parse_proc_stat(Path(f'/proc/{pid}/stat').read_text())
            if current['starttime'] == stat['starttime']:
                os.kill(pid, sig)
        except (ProcessLookupError, FileNotFoundError):
            pass


def run_command(command: list[str], cwd: Path, log_path: Path,
                timeout: float = 180, sample_interval: float = .02,
                output_limit: int = 1_048_576, env: dict | None = None) -> dict:
    """Run one workload. The harness owns/reaps all its children while active."""
    enable_subreaper()
    started = time.monotonic()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        'command': command, 'cwd': str(cwd), 'timeout_seconds': timeout,
        'sample_interval_seconds': sample_interval, 'exit_code': None,
        'timed_out': False, 'descendant_cleanup_required': False,
        'cpu_user_seconds': 0., 'cpu_system_seconds': 0.,
        'peak_aggregate_rss_bytes': 0, 'peak_process_count': 0,
        'observed_process_count': 0, 'samples': 0,
        'memory_method': 'sum-live-smaps_rollup-rss',
        'memory_errors': [],
        'max_sample_gap_seconds': 0., 'max_memory_sample_seconds': 0.,
        'output_bytes': 0, 'captured_output_bytes': 0, 'output_truncated': False,
        'log_file': str(log_path), 'spawn_error': None,
    }
    try:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as error:
        log_path.write_text(str(error))
        result.update(spawn_error=str(error), wall_seconds=time.monotonic() - started,
                      cpu_seconds=0., success=False)
        return result
    assert proc.stdout is not None
    os.set_blocking(proc.stdout.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    known: dict[int, int] = {}
    root_reaped_at = None
    terminating_at = None
    root_done = False
    eof = False
    previous_sample = None
    with log_path.open('wb') as output:
        while True:
            now = time.monotonic()
            if previous_sample is not None:
                result['max_sample_gap_seconds'] = max(result['max_sample_gap_seconds'], now - previous_sample)
            previous_sample = now
            tree = process_tree(process_snapshot(), proc.pid, known, os.getpid())
            live = {pid: stat for pid, stat in tree.items() if stat['state'] != 'Z'}
            result['samples'] += 1
            memory_started = time.monotonic()
            rss, memory_errors = aggregate_rollup_rss(live)
            result['max_memory_sample_seconds'] = max(
                result['max_memory_sample_seconds'], time.monotonic() - memory_started)
            # Bound diagnostics without accepting an incomplete memory sample.
            result['memory_errors'] = (result['memory_errors'] + memory_errors)[:20]
            result['peak_aggregate_rss_bytes'] = max(
                result['peak_aggregate_rss_bytes'], rss)
            result['peak_process_count'] = max(result['peak_process_count'], len(live))
            # wait4 returns each child once. Its rusage includes descendants it reaped;
            # adopted children contribute their own subtree once, with no RSS/CPU sampling sum.
            while True:
                try:
                    pid, status, usage = os.wait4(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if pid == 0:
                    break
                result['cpu_user_seconds'] += usage.ru_utime
                result['cpu_system_seconds'] += usage.ru_stime
                if pid == proc.pid:
                    result['exit_code'] = os.waitstatus_to_exitcode(status)
                    proc.returncode = result['exit_code']
                    root_done = True
                    root_reaped_at = now
            if not terminating_at and now - started >= timeout:
                result['timed_out'] = True
                terminating_at = now
                signal_tree(proc.pid, tree, signal.SIGTERM)
            if (root_done and live and root_reaped_at is not None
                    and now - root_reaped_at >= .2 and not terminating_at):
                result['descendant_cleanup_required'] = True
                terminating_at = now
                signal_tree(proc.pid, tree, signal.SIGTERM)
            if terminating_at and now - terminating_at >= .2:
                signal_tree(proc.pid, tree, signal.SIGKILL)
            # Limit per-iteration reads so an infinite writer cannot starve deadlines.
            if not eof:
                for _ in range(16):
                    try:
                        chunk = os.read(proc.stdout.fileno(), 65536)
                    except BlockingIOError:
                        break
                    if not chunk:
                        eof = True
                        selector.unregister(proc.stdout)
                        break
                    result['output_bytes'] += len(chunk)
                    remaining = max(0, output_limit - result['captured_output_bytes'])
                    output.write(chunk[:remaining])
                    result['captured_output_bytes'] += min(len(chunk), remaining)
            if root_done and not live and eof:
                break
            if terminating_at and now - terminating_at > 5:
                # An uninterruptible kernel task cannot be synchronously cleaned up.
                result['cleanup_error'] = 'Processes or inherited pipes remained after SIGKILL'
                break
            selector.select(sample_interval)
    selector.close()
    proc.stdout.close()
    result.update(
        wall_seconds=time.monotonic() - started,
        cpu_seconds=result['cpu_user_seconds'] + result['cpu_system_seconds'],
        observed_process_count=len(known),
        output_truncated=result['output_bytes'] > result['captured_output_bytes'],
    )
    result['success'] = (result['exit_code'] == 0 and not result['timed_out']
                         and not result['descendant_cleanup_required']
                         and not result['memory_errors']
                         and 'cleanup_error' not in result)
    return result


def read_json(path: Path):
    return json.loads(path.read_text())


def validate_artifact(tool: str, path: Path, run_success: bool, fixture: str | None = None) -> dict:
    """Exit code alone is insufficient: require a fresh, successful tool report."""
    if not run_success:
        raise ValueError('Command failed; its report cannot establish success')
    report = read_json(path)
    if tool == 'vitest':
        if (report.get('success') is not True or report.get('numTotalTests', 0) < 1
                or report.get('numFailedTests', 0) != 0
                or report.get('numPassedTests') != report.get('numTotalTests')):
            raise ValueError('Vitest report is unsuccessful, empty, or contains unpassed tests')
        identities = []
        durations = []
        for suite in report.get('testResults', []):
            for test in suite.get('assertionResults', []):
                identities.append((suite.get('name', '').removeprefix(str(ROOT) + '/'),
                                   test.get('fullName'), test.get('status')))
                if isinstance(test.get('duration'), (int, float)):
                    durations.append(test['duration'])
        if len(identities) != report['numTotalTests'] or any(x[2] != 'passed' for x in identities):
            raise ValueError('Vitest assertion identities do not match the passed test count')
        if fixture:
            expected_count, expected_files = FIXTURE_TEST_SCOPE[fixture]
            if len(identities) != expected_count or {x[0] for x in identities} != expected_files:
                raise ValueError(f'Vitest did not run the expected {fixture} test files/count')
        return {'test_counts': {key: report.get(key) for key in
                ('numTotalTests', 'numPassedTests', 'numFailedTests', 'numPendingTests')},
                'test_identity_sha256': hashlib.sha256(json.dumps(sorted(identities)).encode()).hexdigest(),
                'artifact_phase_metrics': {'test_execution_sum_ms': sum(durations),
                                          'source': 'sum of Vitest assertion duration; excludes discovery/transform/startup'}}
    files = report.get('files')
    if not isinstance(files, dict) or not report.get('schemaVersion'):
        raise ValueError('Stryker mutation report lacks schemaVersion/files')
    counts: collections.Counter = collections.Counter()
    outcomes = []
    allowed = {'Killed', 'Survived', 'NoCoverage', 'Timeout', 'CompileError', 'RuntimeError', 'Ignored'}
    for filename, source in files.items():
        for mutant in source.get('mutants', []):
            status = mutant.get('status')
            if status not in allowed:
                raise ValueError('Stryker report has unfinished or unknown mutant statuses')
            if status in {'Killed', 'Survived'} and (not isinstance(mutant.get('testsCompleted'), int)
                                                     or mutant['testsCompleted'] <= 0):
                raise ValueError('Stryker classified a mutant without completing any tests')
            counts[status] += 1
            outcomes.append({'file': filename, **{key: mutant.get(key) for key in
                             ['id', 'mutatorName', 'replacement', 'location', 'status']}})
    if not counts:
        raise ValueError('Stryker report contains no mutants')
    if counts.get('RuntimeError', 0):
        raise ValueError('Stryker report contains runtime/infrastructure errors')
    test_identities = [(filename, test.get('name'))
                       for filename, source in report.get('testFiles', {}).items()
                       for test in source.get('tests', [])]
    if fixture:
        expected_count, expected_files = FIXTURE_TEST_SCOPE[fixture]
        if (len(test_identities) != expected_count
                or {x[0] for x in test_identities} != expected_files):
            raise ValueError(f'Stryker did not discover the expected {fixture} test files/count')
        # These exact fixture functions have direct return-value assertions. Removing
        # the entire body must fail those assertions: a successful report alone
        # does not prove a compatible runner actually activated a mutant.
        sentinel_file, sentinel_line = {
            'pure': ('fixtures/pure/src/logic.ts', 1),
            'stateful': ('fixtures/stateful/src/service.ts', 9),
        }[fixture]
        sentinels = [m for m in files.get(sentinel_file, {}).get('mutants', [])
                     if m.get('mutatorName') == 'BlockStatement' and m.get('replacement') == '{}'
                     and m.get('location', {}).get('start', {}).get('line') == sentinel_line]
        if len(sentinels) != 1 or sentinels[0]['status'] != 'Killed':
            raise ValueError(f'Known function-body-removal sentinel was not killed: {sentinel_file}:{sentinel_line}')
    return {'mutation_status_counts': dict(sorted(counts.items())),
            'mutant_count': sum(counts.values()), 'report_schema_version': report['schemaVersion'],
            'test_counts': {'numTotalTests': len(test_identities)},
            'test_identity_sha256': hashlib.sha256(json.dumps(sorted(test_identities)).encode()).hexdigest(),
            'mutation_outcome_sha256': hashlib.sha256(json.dumps(
                sorted(outcomes, key=lambda x: (x['file'], str(x['id']))), sort_keys=True).encode()).hexdigest()}


def parse_phase_metrics(output: str) -> dict:
    """Retain only explicit tool timing lines; do not infer phases from timestamps."""
    import re
    phases = []
    values = {}
    for line in output.splitlines():
        plain = re.sub(r'\x1b\[[0-9;]*m', '', line)
        baseline = re.search(r'Initial test run succeeded\. Ran (\d+) tests in (.*?) '
                             r'\(net ([\d.]+) ms, overhead ([\d.]+) ms\)', plain)
        if baseline:
            values['baseline_test_count'] = int(baseline[1])
            values['baseline_net_test_ms'] = float(baseline[3])
            values['baseline_overhead_ms'] = float(baseline[4])
            values['baseline_gross_ms'] = float(baseline[3]) + float(baseline[4])
            phases.append(plain[:1000])
        if re.search(r'(?:time spent|duration|took |in \d+(?:\.\d+)?\s*(?:ms|s|seconds))',
                     plain, re.I):
            phases.append(plain[:1000])
    return {'source': 'explicit tool output lines (not inferred phase durations)',
            'values': values, 'lines': phases[:50],
            'unavailable': ['discovery', 'instrumentation', 'transform', 'cache', 'reporting']}


def capture(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, cwd=ROOT, stderr=subprocess.STDOUT,
                                       text=True, timeout=10).strip()[:32768]
    except (OSError, subprocess.SubprocessError) as error:
        return f'unavailable: {error}'


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metadata() -> dict:
    tracked = [ROOT / name for name in ['package.json', 'package-lock.json', '.node-version',
                                      'Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml']]
    tracked += sorted((ROOT / 'fixtures').rglob('*.ts'))
    tracked += sorted((ROOT / 'benchmarks/config').glob('*.mjs'))
    tracked += [Path(__file__).resolve()]
    versions = {'node': capture(['node', '--version']),
                'npm': capture(['npm', '--version']), 'python': sys.version,
                'rustc': capture(['rustc', '--version']), 'cargo': capture(['cargo', '--version']),
                'python_executable': sys.executable, 'python_platform': sys.platform,
                'node_runtime': capture(['node', '-p', 'JSON.stringify({execPath:process.execPath,platform:process.platform,arch:process.arch})'])}
    for package in ['vitest', '@vitest/coverage-v8', '@stryker-mutator/core',
                    '@stryker-mutator/vitest-runner', 'typescript']:
        path = ROOT / 'node_modules' / package / 'package.json'
        versions[package] = read_json(path).get('version') if path.exists() else 'missing'
    kernel = platform.uname()
    machine = {'system': kernel.system, 'release': kernel.release,
               'machine': kernel.machine, 'node': kernel.node,
               'logical_cpu_count': os.cpu_count(),
               'cpu_affinity': sorted(os.sched_getaffinity(0)),
               'load_average': os.getloadavg()}
    for name, path in {'cpuinfo': '/proc/cpuinfo', 'meminfo': '/proc/meminfo',
                       'cgroup': '/proc/self/cgroup',
                       'cgroup_cpu_max': '/sys/fs/cgroup/cpu.max',
                       'cgroup_memory_max': '/sys/fs/cgroup/memory.max',
                       'cgroup_cpuset': '/sys/fs/cgroup/cpuset.cpus.effective'}.items():
        try:
            content = Path(path).read_text()
            machine[name] = content.split('\n\n', 1)[0] if name == 'cpuinfo' else content
        except OSError:
            machine[name] = 'unavailable'
    return {'versions': versions, 'machine': machine,
            'git_revision': capture(['git', 'rev-parse', 'HEAD']),
            'git_status': capture(['git', 'status', '--porcelain']),
            'input_sha256': {str(p.relative_to(ROOT)): file_hash(p) for p in tracked if p.exists()}}


def make_command(tool: str, fixture: str, report: Path) -> list[str]:
    if tool == 'vitest':
        return ['node', str(ROOT / 'node_modules/vitest/vitest.mjs'), 'run',
                '--config', f'benchmarks/config/vitest-{fixture}.mjs', '--maxWorkers=1',
                '--no-file-parallelism', '--reporter=json', f'--outputFile={report}']
    return [str(ROOT / 'node_modules/.bin/stryker'), 'run', f'benchmarks/config/{fixture}.mjs']


def summarize(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, list] = collections.defaultdict(list)
    for row in rows:
        if not row['warmup'] and row['success']:
            grouped[(row['fixture'], row['tool'])].append(row)
    summary = []
    for (fixture, tool), group in sorted(grouped.items()):
        item = {'fixture': fixture, 'tool': tool, 'measured_successes': len(group)}
        for metric in ['wall_seconds', 'cpu_seconds', 'peak_aggregate_rss_bytes', 'peak_process_count']:
            values = [row[metric] for row in group]
            item[metric] = {'median': statistics.median(values),
                            'min': min(values), 'max': max(values)}
        counts = [row.get('mutation_status_counts') for row in group]
        item['mutation_status_counts_agree'] = all(x == counts[0] for x in counts)
        outcomes = [row.get('mutation_outcome_sha256') for row in group]
        item['mutation_outcomes_agree'] = all(x == outcomes[0] for x in outcomes)
        tests = [(row.get('test_counts'), row.get('test_identity_sha256')) for row in group]
        item['test_identities_and_counts_agree'] = all(x == tests[0] for x in tests)
        item['outcomes_agree'] = (item['mutation_outcomes_agree']
                                 and item['test_identities_and_counts_agree'])
        summary.append(item)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--sample-interval', type=float, default=.02)
    parser.add_argument('--output-limit', type=int, default=1_048_576)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fixtures', choices=['pure', 'stateful'], nargs='+', default=['pure', 'stateful'])
    parser.add_argument('--tools', choices=['vitest', 'stryker'], nargs='+', default=['vitest', 'stryker'])
    args = parser.parse_args(argv)
    if not LINUX_KERNEL:
        parser.error('Linux /proc and wait4 are required')
    if args.repetitions < 1 or args.warmups < 0 or args.timeout <= 0 or args.sample_interval <= 0 or args.output_limit < 1:
        parser.error('Counts/limits must be positive (warmups may be zero)')
    output = args.output.resolve()
    # Refuse to reuse a directory; reports can never be stale from earlier invocations.
    output.mkdir(parents=True, exist_ok=False)
    meta = metadata()
    meta.update(schema_version=SCHEMA_VERSION, created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                policy={'repetitions': args.repetitions, 'warmups_per_tool_and_fixture': args.warmups,
                        'process_cold': True, 'os_caches': 'left untouched; warmups may warm filesystem caches',
                        'ordering': 'fixture order and tool order reverse each round; warmups precede measured rounds',
                        'concurrency': 1, 'incremental': False, 'typescript_checker': False,
                        'vitest_pool': 'threads', 'vitest_max_workers': 1,
                        'vitest_file_parallelism': False,
                        'coverage_analysis': 'perTest for Stryker',
                        'declared_environment': {'CI': 'true', 'NO_COLOR': '1', 'OXIMUT_FIXTURE_MODE': 'fixture'},
                        'cpu_accounting': 'sum wait4 rusage for direct child and adopted children; includes descendants reaped by them; un-reaped/lost descendants may be missing',
                        'memory_accounting': 'sampled sum /proc/PID/stat RSS of observed process tree; shared pages double counted; short-lived processes/peaks between samples can be missed; excludes harness',
                        'process_count': 'observed non-zombie process count; includes controller and test workers, not thread count',
                        'phase_metrics': 'only explicit lines emitted by installed tool; unavailable phases remain unavailable',
                        'background_load': 'recorded, not controlled; affinity/cgroup are recorded, not configured'})
    (output / 'metadata.json').write_text(json.dumps(meta, indent=2) + '\n')
    rows = []
    total_rounds = args.warmups + args.repetitions
    with (output / 'measurements.jsonl').open('w') as stream:
        for round_index in range(total_rounds):
            warmup = round_index < args.warmups
            repetition = round_index + 1 if warmup else round_index - args.warmups + 1
            fixtures = args.fixtures if round_index % 2 == 0 else list(reversed(args.fixtures))
            tools = args.tools if round_index % 2 == 0 else list(reversed(args.tools))
            for fixture in fixtures:
                for tool in tools:
                    label = f'{"warmup" if warmup else "measured"}-{repetition:02d}-{fixture}-{tool}'
                    run_dir = output / label
                    run_dir.mkdir()
                    report = run_dir / 'report.json'
                    env = dict(os.environ, OXIMUT_BENCH_REPORT=str(report), CI='true', NO_COLOR='1',
                               OXIMUT_FIXTURE_MODE='fixture')
                    command = make_command(tool, fixture, report)
                    print(f'{label}: {" ".join(command)}', flush=True)
                    row = run_command(command, ROOT, run_dir / 'output.log', args.timeout,
                                      args.sample_interval, args.output_limit, env)
                    row.update(schema_version=SCHEMA_VERSION, fixture=fixture, tool=tool,
                               scenario='ordinary' if tool == 'vitest' else 'clean-full-perTest',
                               repetition=repetition, warmup=warmup, report_file=str(report),
                               configuration_sha256=hashlib.sha256(json.dumps(meta['input_sha256'], sort_keys=True).encode()).hexdigest())
                    try:
                        row.update(validate_artifact(tool, report, row['success'], fixture))
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        row.update(success=False, validation_error=str(error))
                    row['phase_metrics'] = parse_phase_metrics((run_dir / 'output.log').read_text(errors='replace'))
                    rows.append(row)
                    stream.write(json.dumps(row) + '\n')
                    stream.flush()
                    print(f'  success={row["success"]}, wall={row["wall_seconds"]:.3f}s, '
                          f'CPU={row["cpu_seconds"]:.3f}s, RSS={row["peak_aggregate_rss_bytes"]}', flush=True)
    summary = summarize(rows)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    # Partial results remain available, but a failed/wrong artifact cannot pass the run.
    return 0 if all(row['success'] for row in rows) and all(item['outcomes_agree'] for item in summary) else 1


if __name__ == '__main__':
    raise SystemExit(main())
