"""The native coordinator keeps the relay's deadline, cleanup and reuse contract."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'fixtures/m1'
# A fake controller speaking the reference controller's IPC protocol. The CLI
# drives it natively because it sits beside a file named m2-runner.mjs.
CONTROLLER = r"""
import { appendFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
const mode = process.env.OXIMUT_FAKE_MODE;
const log = (event) => appendFileSync(process.env.OXIMUT_FAKE_LOG,
  JSON.stringify({ ...event, pid: process.pid }) + '\n');
log({ event: 'start' });
let jobs = 0;
process.on('message', (message) => {
  const { id, command, args } = message;
  const reply = (body) => process.send({ protocol: 1, id, ...body });
  if (command === 'initialize') {
    log({ event: 'initialize', job: args.jobId });
    if (mode === 'warm-start-fail' && args.jobId === 'spare-warm') process.exit(3);
    if (mode === 'bad-init') return reply({ result: {} });
    return reply({ result: { controllerPid: process.pid,
      pool: args.pool === 'threads' ? 'threads' : 'forks',
      ...(mode === 'bad-pool' ? { pool: 'forks' } : {}),
      reuse: mode === 'fresh' ? 'fresh: test' : 'controller' } });
  }
  if (command === 'close') {
    log({ event: 'close' });
    if (mode === 'parallel-close') {
      const path = process.env.OXIMUT_FAKE_LOG + '.closing';
      appendFileSync(path, 'close\n');
      const wait = () => {
        if (readFileSync(path, 'utf8').trim().split('\n').length < 2) return setTimeout(wait, 10);
        log({ event: 'close-reply' });
        reply({ result: { closed: true } });
      };
      wait(); return;
    }
    if (mode === 'no-close') return;
    return reply({ result: { closed: true } });
  }
  if (command === 'warm') {
    log({ event: 'warm', job: args.request.jobId });
    writeFileSync(process.env.OXIMUT_FAKE_LOG + '.warm', '');
    if (mode === 'warm-fail') {
      const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'],
        { detached: true, stdio: 'ignore' });
      child.unref(); log({ event: 'leaked', child: child.pid });
      return reply({ error: 'warm deliberately failed' });
    }
    const finish = () => {
      log({ event: 'warm-ready' });
      return reply({ result: { warmed: true, outcome: 'skipped',
        tests: { passed: 0, failed: 0, skipped: 0 }, errors: [],
        ...(args.request.resident ? { resident: args.request.resident.length } : {}) } });
    };
    if (mode.startsWith('nonblocking')) {
      const wait = () => {
        const workerDirectory = readFileSync(`/proc/${process.ppid}/cmdline`, 'utf8').split('\0')[2];
        const stopping = existsSync(workerDirectory + '/stop');
        if (!stopping && !existsSync(process.env.OXIMUT_FAKE_LOG + '.release')) return setTimeout(wait, 10);
        if (stopping) log({ event: 'warm-stopping' });
        finish();
      };
      wait(); return;
    }
    return finish();
  }
  const request = args.request;
  jobs++;
  log({ event: 'run', job: request.jobId, workers: request.workers });
  if (mode.startsWith('nonblocking') && request.jobId === 'instrumented-baseline') {
    log({ event: 'instrumented-start', workers: request.workers });
  }
  if (mode === 'nonblocking' && request.kind === 'mutant') {
    writeFileSync(process.env.OXIMUT_FAKE_LOG + '.release', '');
    const path = process.env.OXIMUT_FAKE_LOG + '.mutants';
    appendFileSync(path, request.jobId + '\n');
    const wait = () => {
      if (readFileSync(path, 'utf8').trim().split('\n').length < 2) return setTimeout(wait, 10);
      reply({ result: { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 },
        errors: [], ...(request.resident ? { resident: request.resident.length } : {}) } });
    };
    wait(); return;
  }
  if ((mode.startsWith('nonblocking') || mode === 'warm-order' || mode === 'baseline-fail-warm') && request.jobId === 'baseline') {
    const wait = () => {
      if (!existsSync(process.env.OXIMUT_FAKE_LOG + '.warm')) return setTimeout(wait, 10);
      log({ event: 'baseline-end' });
      reply({ result: { outcome: (mode === 'baseline-fail-warm' || mode === 'nonblocking-fail') ? 'failed' : 'passed',
        tests: { passed: (mode === 'baseline-fail-warm' || mode === 'nonblocking-fail') ? 0 : 1,
          failed: (mode === 'baseline-fail-warm' || mode === 'nonblocking-fail') ? 1 : 0, skipped: 0 }, errors: [] } });
    };
    wait(); return;
  }
  if (mode === 'warm-order' && request.kind === 'mutant') {
    const path = process.env.OXIMUT_FAKE_LOG + '.mutants';
    appendFileSync(path, request.jobId + '\n');
    const wait = () => {
      if (readFileSync(path, 'utf8').trim().split('\n').length < 2) return setTimeout(wait, 10);
      reply({ result: { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 },
        errors: [], ...(request.resident ? { resident: request.resident.length } : {}) } });
    };
    wait(); return;
  }
  if (mode === 'hang') return;
  if (mode === 'crash') process.exit(3);
  if (mode === 'error') return reply({ error: 'boom from controller' });
  if (mode === 'oversized') return reply({ result: { padding: 'x'.repeat(2 * 1024 * 1024) } });
  if (mode === 'lingering') {
    // Like a Vitest fork still exiting when the run resolves.
    const child = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 150)'], { stdio: 'ignore' });
    log({ event: 'lingering', child: child.pid });
  }
  if (mode === 'leak' && !existsSync(process.env.OXIMUT_FAKE_LOG + '.leaked')) {
    writeFileSync(process.env.OXIMUT_FAKE_LOG + '.leaked', '');
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'],
      { detached: true, stdio: 'ignore' });
    child.unref();
    log({ event: 'leaked', child: child.pid });
  }
  reply({ result: { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 },
    errors: [], ...(request.resident ? { resident: request.resident.length } : {}) } });
});
process.once('disconnect', () => process.exit(0));
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f'/proc/{pid}/stat')
    try:
        return stat.read_text().rsplit(')', 1)[1].split()[0] not in ('Z', 'X')
    except (FileNotFoundError, IndexError):
        return False


class NativeCoordinator(unittest.TestCase):
    def test_fresh_thread_checks_canonical_adapter(self):
        with tempfile.TemporaryDirectory(prefix='oximut-native-symlink-') as directory:
            directory = Path(directory)
            target = directory / 'target'
            target.mkdir()
            (target / 'm2-runner.mjs').write_text('throw new Error("must not execute");')
            adapter = directory / 'm2-runner.mjs'
            adapter.symlink_to(target / 'm2-runner.mjs')
            (directory / 'm2-worker.ts').write_text(CONTROLLER)
            args = [str(REPO / 'target/debug/oximut'), '--root', str(ROOT),
                    '--source', str(ROOT / 'src/subject.ts'), '--baseline-only',
                    '--execution', 'persistent', '--isolation', 'fresh-thread',
                    '--adapter', str(adapter), '--report', '-']
            rejected = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=60)
            self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
            self.assertIn('requires the reference persistent adapter', rejected.stderr)
            adapter.unlink()
            (directory / 'm2-worker.ts').unlink()
            adapter.symlink_to(REPO / 'packages/vitest-adapter/m2-runner.mjs')
            accepted = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=60)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertEqual(json.loads(accepted.stdout)['baseline']['outcome'], 'passed')

    def run_cli(self, mode, timeout_ms=5000, extra_args=(), full=False):
        with tempfile.TemporaryDirectory(prefix='oximut-native-') as directory:
            adapter = Path(directory) / 'm2-runner.mjs'
            adapter.write_text('throw new Error("the native coordinator must not run this");\n')
            (Path(directory) / 'm2-worker.ts').write_text(CONTROLLER)
            log = Path(directory) / 'log.jsonl'
            result = subprocess.run([
                str(REPO / 'target/debug/oximut'), '--root', str(ROOT),
                '--source', str(ROOT / 'src/subject.ts'), *([] if full else ['--baseline-only']),
                '--execution', 'persistent', '--adapter', str(adapter), '--report', '-',
                '--timeout-ms', str(timeout_ms), '--no-cache', '--no-shared-controller', *extra_args,
            ], env={**os.environ, 'OXIMUT_FAKE_MODE': mode, 'OXIMUT_FAKE_LOG': str(log)},
                text=True, capture_output=True, timeout=120)
            events = [json.loads(line) for line in log.read_text().splitlines()]
            return result, json.loads(result.stdout), events

    def controllers(self, events):
        return {event['pid'] for event in events if event['event'] == 'start'}

    def require_two_workers(self):
        """Spares only start with at least two admitted workers."""
        cpus = len(os.sched_getaffinity(0))
        try:
            quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
            if quota != 'max':
                cpus = min(cpus, max(1, int(quota) // int(period)))
        except (OSError, ValueError):
            pass
        try:
            quota = int(Path('/sys/fs/cgroup/cpu/cpu.cfs_quota_us').read_text())
            period = int(Path('/sys/fs/cgroup/cpu/cpu.cfs_period_us').read_text())
            if quota > 0:
                cpus = min(cpus, max(1, quota // period))
        except (OSError, ValueError):
            pass
        if cpus < 2:
            self.skipTest('needs two usable CPUs for a spare controller')

    def assert_reaped(self, events):
        for pid in self.controllers(events):
            self.assertFalse(alive(pid), f'controller {pid} survived')

    def require_spare_capacity(self, report):
        # Only a host CPU limit may lower workers; anything else is a regression.
        if report['resources']['cpuLimitWorkers'] < 2:
            self.skipTest('spare warming requires at least two usable CPUs')
        self.assertGreaterEqual(report['resources']['effectiveWorkers'], 2)

    def test_nonblocking_warm_preserves_baseline_capacity_and_reuses_slot(self):
        self.require_two_workers()
        result, report, events = self.run_cli('nonblocking', extra_args=('--workers', '2'), full=True)
        self.require_spare_capacity(report)
        self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
        names = [e['event'] for e in events]
        self.assertIn('warm-ready', names)
        self.assertLess(names.index('instrumented-start'), names.index('warm-ready'))
        instrumented = next(e for e in events if e['event'] == 'instrumented-start')
        self.assertEqual(instrumented['workers'], report['resources']['effectiveWorkers'])
        first_mutant = next(i for i, e in enumerate(events)
                            if e['event'] == 'run' and e['job'] not in ('baseline', 'instrumented-baseline'))
        self.assertLess(first_mutant, names.index('warm-ready'))
        warm_pid = next(e['pid'] for e in events if e['event'] == 'warm-ready')
        self.assertTrue(any(e['event'] == 'run' and e['pid'] == warm_pid for e in events))
        self.assertEqual(len(self.controllers(events)), 2)
        self.assert_reaped(events)

    def test_ending_and_failing_with_warm_in_flight_cleans_up(self):
        self.require_two_workers()
        for mode in ('nonblocking-end', 'nonblocking-fail'):
            with self.subTest(mode=mode):
                result, report, events = self.run_cli(
                    mode, extra_args=('--workers', '2'), full=True)
                self.require_spare_capacity(report)
                self.assertEqual(report['baseline']['outcome'],
                                 'failed' if mode.endswith('fail') else 'passed', result.stderr)
                self.assertEqual(report['summary']['complete'], mode == 'nonblocking-end')
                self.assertTrue(any('spare warm abandoned' in d for d in report['diagnostics']))
                self.assertFalse(any(e['event'] == 'warm-ready' for e in events))
                self.assert_reaped(events)

    def test_baseline_only_and_empty_mutant_inventory_never_warm(self):
        for full, extra in ((False, ()), (True, ('--mutators', 'arithmetic'))):
            with self.subTest(full=full):
                # subject.ts has no arithmetic expressions: filtering leaves no jobs.
                result, report, events = self.run_cli(
                    'normal', extra_args=('--workers', '2', *extra), full=full)
                self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
                self.assertEqual(report['mutants'], [])
                self.assertFalse(any(e['event'] == 'warm' for e in events))
                self.assertFalse(any('spare warm' in d for d in report['diagnostics']))
                self.assert_reaped(events)

    def test_parallel_close_signals_all_slots_before_waiting(self):
        self.require_two_workers()
        result, report, events = self.run_cli('parallel-close', extra_args=('--workers', '2'), full=True)
        self.require_spare_capacity(report)
        self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
        names = [e['event'] for e in events]
        self.assertEqual(names[:names.index('close-reply')].count('close'), 2)
        self.assert_reaped(events)

    def test_spares_overlap_baseline_and_are_ready_before_mutants(self):
        self.require_two_workers()
        result, report, events = self.run_cli('warm-order', extra_args=('--workers', '2'), full=True)
        self.require_spare_capacity(report)
        self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
        names = [e['event'] for e in events]
        self.assertLess(names.index('warm'), names.index('baseline-end'))
        first_mutant = next(i for i, e in enumerate(events)
                            if e['event'] == 'run' and e['job'] not in ('baseline', 'instrumented-baseline'))
        self.assertLess(names.index('warm-ready'), first_mutant)
        self.assertEqual(len(self.controllers(events)), 2)
        warm_pid = next(e['pid'] for e in events if e['event'] == 'warm-ready')
        self.assertTrue(any(e['event'] == 'run' and e['pid'] == warm_pid for e in events))
        self.assert_reaped(events)

    def test_failed_warm_and_spare_start_fall_back_without_leaks(self):
        self.require_two_workers()
        normal, expected, _ = self.run_cli('normal', extra_args=('--workers', '2'), full=True)
        self.assertTrue(expected['summary']['complete'], normal.stdout)
        for mode in ('warm-fail', 'warm-start-fail'):
            with self.subTest(mode=mode):
                result, report, events = self.run_cli(mode, extra_args=('--workers', '2'), full=True)
                self.require_spare_capacity(report)
                self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
                self.assertEqual([m['outcome'] for m in report['mutants']],
                                 [m['outcome'] for m in expected['mutants']])
                self.assertTrue(any('spare warm failed' in d for d in report['diagnostics']))
                self.assert_reaped(events)
                for event in events:
                    if event['event'] == 'leaked':
                        self.assertFalse(alive(event['child']))

    def test_one_worker_never_warms(self):
        for workers in (1, 2):
            result, report, events = self.run_cli(
                'normal', extra_args=('--workers', str(workers)), full=True)
            self.assertIn(result.returncode, (0, 1), result.stderr)
            self.assertTrue(report["summary"]["complete"], report)
            if report['resources']['effectiveWorkers'] >= 2:
                self.assertTrue(any(any(f'spare warm {outcome}' in d
                                        for outcome in ('ready', 'skipped', 'abandoned'))
                                    for d in report['diagnostics']), report)
            else:
                self.assertFalse(any(e['event'] == 'warm' for e in events))
            self.assert_reaped(events)

    def test_baseline_failure_cleans_warm_spares(self):
        self.require_two_workers()
        result, report, events = self.run_cli('baseline-fail-warm', extra_args=('--workers', '2'), full=True)
        self.require_spare_capacity(report)
        self.assertEqual(report['baseline']['outcome'], 'failed', result.stderr)
        self.assertTrue(any(e['event'] == 'warm' for e in events))
        self.assertFalse(any(e['event'] == 'run' and e['job'] != 'baseline' for e in events))
        self.assert_reaped(events)

    def test_baseline_worker_limit_is_admitted_and_forwarded(self):
        for workers in (1, 2):
            with self.subTest(workers=workers):
                result, report, events = self.run_cli('normal', extra_args=('--workers', str(workers)))
                self.assertEqual(result.returncode, 0, result.stderr)
                runs = [e for e in events if e['event'] == 'run']
                self.assertEqual(len(runs), 2)
                # CPU quota or memory admission can lower the requested limit.
                expected = report['resources']['effectiveWorkers']
                self.assertLessEqual(expected, workers)
                forwarded = sorted(run['workers'] for run in runs)
                if expected >= 2:
                    # The two baselines overlap on separate slots and split the
                    # admitted forks, so together they never exceed the limit.
                    self.assertEqual(forwarded, [expected // 2, expected - expected // 2])
                else:
                    self.assertEqual(forwarded, [expected, expected])
                self.assert_reaped(events)

    def test_healthy_controller_is_reused_and_closed(self):
        result, report, events = self.run_cli('normal')
        self.assertEqual(report['baseline']['outcome'], 'passed', result.stderr)
        self.assertTrue(report['summary']['complete'])
        # Original and instrumented baselines share one controller.
        self.assertEqual([e['job'] for e in events if e['event'] == 'run'],
                         ['baseline', 'instrumented-baseline'])
        self.assertEqual(len(self.controllers(events)), 1)
        self.assertEqual([e['event'] for e in events][-1], 'close')
        self.assertIn('adapter observed reuse boundary: controller', report['diagnostics'])
        self.assertIn('native coordinator: 1 controllers for 2 jobs', report['diagnostics'])
        self.assert_reaped(events)

    def test_malformed_initialize_is_rejected_before_run_and_reaped(self):
        result, report, events = self.run_cli('bad-init')
        self.assertEqual(report['baseline']['outcome'], 'infrastructure-error', result.stderr)
        self.assertIn('string reuse', json.dumps(report['baseline']['errors']))
        self.assertFalse(any(e['event'] == 'run' for e in events))
        self.assertEqual(len(self.controllers(events)), 1)
        self.assert_reaped(events)

    def test_pool_mismatch_is_rejected_before_run_and_reaped(self):
        result, report, events = self.run_cli('bad-pool', extra_args=('--isolation', 'fresh-thread'))
        self.assertEqual(report['baseline']['outcome'], 'infrastructure-error', result.stderr)
        self.assertIn('pool', json.dumps(report['baseline']['errors']))
        self.assertFalse(any(e['event'] == 'run' for e in events))
        self.assert_reaped(events)

    def test_thread_pool_is_verified(self):
        result, report, events = self.run_cli('normal', extra_args=('--isolation', 'fresh-thread'))
        self.assertEqual(report['baseline']['outcome'], 'passed', result.stderr)
        self.assert_reaped(events)

    def test_hung_job_times_out_after_verified_cleanup(self):
        started = time.monotonic()
        result, report, events = self.run_cli('hang', timeout_ms=1500)
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual(report['baseline']['outcome'], 'timeout', result.stderr)
        self.assertIn('owned processes reaped', json.dumps(report['baseline']['errors']))
        self.assertFalse(report['summary']['complete'])
        self.assert_reaped(events)

    def test_crash_and_error_replies_are_infrastructure_errors(self):
        for mode, message in [('crash', 'Controller exited'),
                              ('error', 'boom from controller'),
                              ('oversized', 'exceeds the response limit')]:
            with self.subTest(mode=mode):
                result, report, events = self.run_cli(mode)
                self.assertEqual(report['baseline']['outcome'], 'infrastructure-error',
                                 result.stderr)
                self.assertIn(message, json.dumps(report['baseline']['errors']))
                self.assertFalse(report['summary']['complete'])
                self.assert_reaped(events)

    def test_leaked_descendant_is_reaped_and_the_controller_recycled(self):
        result, report, events = self.run_cli('leak')
        self.assertEqual(report['baseline']['outcome'], 'passed', result.stderr)
        [leaked] = [e['child'] for e in events if e['event'] == 'leaked']
        self.assertFalse(alive(leaked), 'detached descendant survived')
        # The next job could not reuse a controller whose job leaked a process.
        self.assertEqual(len(self.controllers(events)), 2)
        self.assert_reaped(events)

    def test_exiting_children_do_not_retire_a_healthy_controller(self):
        result, report, events = self.run_cli('lingering')
        self.assertEqual(report['baseline']['outcome'], 'passed', result.stderr)
        self.assertEqual(len([e for e in events if e['event'] == 'lingering']), 2)
        self.assertEqual(len(self.controllers(events)), 1)
        self.assertIn('native coordinator: 1 controllers for 2 jobs', report['diagnostics'])
        self.assert_reaped(events)

    def test_fresh_reuse_decisions_open_a_controller_per_job(self):
        result, report, events = self.run_cli('fresh')
        self.assertEqual(report['baseline']['outcome'], 'passed', result.stderr)
        self.assertEqual(len(self.controllers(events)), 2)
        self.assertIn('adapter observed reuse boundary: fresh: test', report['diagnostics'])
        self.assert_reaped(events)

    def test_controller_that_ignores_close_invalidates_successful_evidence(self):
        result, report, events = self.run_cli('no-close')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(report['summary']['complete'])
        self.assertTrue(any('shutdown failed' in d for d in report['diagnostics']),
                        report['diagnostics'])
        self.assert_reaped(events)


if __name__ == '__main__':
    unittest.main()
