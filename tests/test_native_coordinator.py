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
import { appendFileSync, existsSync, writeFileSync } from 'node:fs';
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
    if (mode === 'bad-init') return reply({ result: {} });
    return reply({ result: { controllerPid: process.pid,
      pool: args.pool === 'threads' ? 'threads' : 'forks',
      ...(mode === 'bad-pool' ? { pool: 'forks' } : {}),
      reuse: mode === 'fresh' ? 'fresh: test' : 'controller' } });
  }
  if (command === 'close') {
    log({ event: 'close' });
    if (mode === 'no-close') return;
    return reply({ result: { closed: true } });
  }
  const request = args.request;
  jobs++;
  log({ event: 'run', job: request.jobId });
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
    def run_cli(self, mode, timeout_ms=5000, extra_args=()):
        with tempfile.TemporaryDirectory(prefix='oximut-native-') as directory:
            adapter = Path(directory) / 'm2-runner.mjs'
            adapter.write_text('throw new Error("the native coordinator must not run this");\n')
            (Path(directory) / 'm2-worker.ts').write_text(CONTROLLER)
            log = Path(directory) / 'log.jsonl'
            result = subprocess.run([
                str(REPO / 'target/debug/oximut'), '--root', str(ROOT),
                '--source', str(ROOT / 'src/subject.ts'), '--baseline-only',
                '--execution', 'persistent', '--adapter', str(adapter), '--report', '-',
                '--timeout-ms', str(timeout_ms), '--no-cache', *extra_args,
            ], env={**os.environ, 'OXIMUT_FAKE_MODE': mode, 'OXIMUT_FAKE_LOG': str(log)},
                text=True, capture_output=True, timeout=120)
            events = [json.loads(line) for line in log.read_text().splitlines()]
            return result, json.loads(result.stdout), events

    def controllers(self, events):
        return {event['pid'] for event in events if event['event'] == 'start'}

    def assert_reaped(self, events):
        for pid in self.controllers(events):
            self.assertFalse(alive(pid), f'controller {pid} survived')

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
