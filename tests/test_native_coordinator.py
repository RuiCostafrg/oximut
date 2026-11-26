"""The native coordinator keeps the relay's deadline, cleanup and reuse contract."""
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
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
let helper;
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
      ...(mode === 'lanes-compact' ? { laneResidentEncoding: 'run-shared-lane-resident-v2' } : {}),
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
      // Held until a mutant releases it; a warm still held at shutdown is
      // abandoned (aborted) before its slot is asked to stop.
      const wait = () => {
        if (!existsSync(process.env.OXIMUT_FAKE_LOG + '.release')) return setTimeout(wait, 10);
        finish();
      };
      wait(); return;
    }
    return finish();
  }
  const request = args.request;
  jobs++;
  log({ event: 'run', job: request.jobId, workers: request.workers,
    bypass: request.bypassZygote, kind: request.kind, deadline: request.deadlineMs });
  if (mode.startsWith('lanes-') && request.lanes) {
    const compact = mode === 'lanes-compact';
    const expected = compact ? 'run-shared-lane-resident-v2' : 'run';
    if (command !== expected || !request.resident?.length ||
        !request.lanes.every(lane => compact ? !Object.hasOwn(lane, 'resident')
          : JSON.stringify(lane.resident) === JSON.stringify(request.resident)))
      return reply({ error: 'unexpected resident encoding' });
    log({ event: 'lanes', command, ids: request.lanes.map(lane => lane.jobId),
      deadline: request.deadlineMs, jobs: request.lanes.map(lane => lane.deadlineMs),
      workers: request.workers });
    // 'lanes-die-first' loses the controller in its first batch only;
    // 'lanes-die-always' in every batch.
    if (mode === 'lanes-die-always' || (mode === 'lanes-die-first' &&
        !existsSync(process.env.OXIMUT_FAKE_LOG + '.died'))) {
      writeFileSync(process.env.OXIMUT_FAKE_LOG + '.died', '');
      process.exit(3);
    }
    const passed = { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 },
      errors: [], resident: request.resident.length };
    // 'lanes-short' reports only the first half of each batch.
    const reported = mode === 'lanes-short'
      ? request.lanes.slice(0, Math.floor(request.lanes.length / 2)) : request.lanes;
    // Under the watchdog mode the first lane of every batch deadlocks in a zygote
    // worker and passes in a real fork.
    // The -busy and -idle variants say what the adapter saw of the stopped worker.
    const laneKind = { 'lanes-rolling-watchdog-busy': 'busy', 'lanes-rolling-watchdog-idle': 'idle',
      'lanes-rolling-watchdog-test': 'test-timeout' }[mode];
    const stuck = (lane, index) => (mode === 'lanes-rolling-watchdog' || laneKind !== undefined)
      && index === 0 && !lane.bypassZygote;
    // 'lanes-ineligible': a controller reporting precollect templates it did not use.
    const ineligible = mode === 'lanes-ineligible' ? { precollectIneligible: [
      { template: '["/a.test.ts"]', reasons: { handles: ['Timeout x2'],
        fsWrites: ['mkdtempSync /tmp/a', 'writeFileSync /tmp/b'], timers: [], children: [] } },
      { template: '["/b.test.ts"]', reasons: { handles: ['TCPServerWrap'],
        fsWrites: [], timers: [], children: ['spawn node'] } }] } : {};
    return reply({ result: { ...passed, ...ineligible, outcome: 'skipped',
      tests: { passed: 0, failed: 0, skipped: 0 }, lanes: reported.map((lane, index) => ({
      ...passed, protocol: lane.protocol, jobId: lane.jobId, runId: lane.runId,
      generation: lane.generation,
      ...(stuck(lane, index) ? { outcome: 'timeout', tests: { passed: 0, failed: 0, skipped: 0 },
        errors: ['Job deadline exceeded'], ...(laneKind ? { timeoutKind: laneKind } : {}) } : {}),
    })) } });
  }
  if (mode.startsWith('lanes-') && command !== 'run')
    return reply({ error: 'unexpected command for a non-lane job' });
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
  if ((mode === 'warm-fail' || mode === 'warm-start-fail') && request.kind === 'mutant') {
    // A failed spare warm is only reported as failed once its reply is
    // consumed; the scheduler then kills the spare. Hold every mutant until
    // the spare controller is gone so the run cannot end with the warm still
    // in flight (reported as "abandoned at shutdown").
    const spare = () => readFileSync(process.env.OXIMUT_FAKE_LOG, 'utf8').trim().split('\n')
      .map((line) => JSON.parse(line))
      .find((e) => e.event === 'initialize' && e.job === 'spare-warm')?.pid;
    const gone = (pid) => {
      try { process.kill(pid, 0); } catch { return true; }
      try { return /^[ZX]/.test(readFileSync(`/proc/${pid}/stat`, 'utf8').split(') ')[1]); }
      catch { return true; }
    };
    const wait = () => {
      const pid = spare();
      if (pid === undefined || !gone(pid)) return setTimeout(wait, 10);
      reply({ result: { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 },
        errors: [], ...(request.resident ? { resident: request.resident.length } : {}) } });
    };
    wait(); return;
  }
  if (mode.startsWith('zygote-') && request.kind === 'mutant') {
    // A zygote worker that deadlocks looks like a timeout; a real fork does not.
    // The -busy, -idle and -test variants say what the adapter saw of the stopped worker.
    const kind = { 'zygote-always-timeout-busy': 'busy', 'zygote-always-timeout-idle': 'idle',
      'zygote-always-timeout-test': 'test-timeout' }[mode];
    const timesOut = mode.startsWith('zygote-always-timeout') || !request.bypassZygote;
    if (timesOut) return reply({ result: { outcome: 'timeout',
      tests: { passed: 0, failed: 0, skipped: 0 }, errors: ['Test timed out in 5ms'],
      ...(kind ? { timeoutKind: kind } : {}) } });
  }
  if (mode.startsWith('helper') && !helper) {
    // A long-lived direct child the controller declares, like a worker zygote.
    helper = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
    log({ event: 'helper', child: helper.pid });
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
  // 'helper' declares its own child; 'helper-stranger' a live process that is
  // not the controller's child (the slot worker above it).
  const declared = mode === 'helper' ? [helper.pid] : mode === 'helper-stranger' ? [process.ppid] : undefined;
  // Baselines report how long each test file took when the test says so.
  const timings = request.kind === 'baseline' && process.env.OXIMUT_FAKE_TIMINGS
    ? { fileTimings: JSON.parse(process.env.OXIMUT_FAKE_TIMINGS) } : {};
  reply({ result: { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 },
    errors: [], ...(request.resident ? { resident: request.resident.length } : {}),
    ...(declared ? { residentHelpers: declared } : {}), ...timings } });
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



def pinned_modes(extra):
    """Selection and rolling lanes are on by default; these tests name the modes they cover."""
    return ['--no-test-selection',
            *([] if '--rolling-lanes' in extra else ['--no-rolling-lanes'])]

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

    def test_zygote_checks_canonical_adapter(self):
        with tempfile.TemporaryDirectory(prefix='oximut-native-zygote-symlink-') as directory:
            directory = Path(directory)
            target = directory / 'target'
            target.mkdir()
            (target / 'm2-runner.mjs').write_text('throw new Error("must not execute");')
            adapter = directory / 'm2-runner.mjs'
            adapter.symlink_to(target / 'm2-runner.mjs')
            (directory / 'm2-worker.ts').write_text(CONTROLLER)
            args = [str(REPO / 'target/debug/oximut'), '--root', str(ROOT),
                    '--source', str(ROOT / 'src/subject.ts'), '--baseline-only',
                    '--execution', 'persistent', '--zygote',
                    '--adapter', str(adapter), '--report', '-']
            rejected = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=60)
            self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
            self.assertIn('--zygote requires the reference persistent adapter', rejected.stderr)
            adapter.unlink()
            (directory / 'm2-worker.ts').unlink()
            adapter.symlink_to(REPO / 'packages/vitest-adapter/m2-runner.mjs')
            accepted = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=120)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            report = json.loads(accepted.stdout)
            self.assertEqual(report['baseline']['outcome'], 'passed')
            self.assertIn('zygote=on', json.dumps(report['diagnostics']))

    # Rust reports target_os=android under PRoot, where auto stays off.
    @unittest.skipUnless(sys.platform == 'linux' and 'proot' not in platform.release().lower(),
                         'auto zygote needs a real Linux target')
    def test_zygote_is_on_by_default_for_persistent_runs_on_linux(self):
        result, report, events = self.run_cli('helper', full=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(any('zygote=on' in d for d in report['diagnostics']), report['diagnostics'])

    def test_declared_helpers_are_admitted_only_with_zygote_and_reaped(self):
        result, report, events = self.run_cli('helper', full=True, extra_args=('--zygote',))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
        self.assertEqual(report['baseline']['outcome'], 'passed')
        self.assertTrue(any('zygote=on' in d for d in report['diagnostics']), report['diagnostics'])
        [helper] = {e['child'] for e in events if e['event'] == 'helper'}
        self.assertFalse(alive(helper), 'resident helper survived shutdown')
        # The helper never retired its controller: one stayed for every job.
        self.assertEqual(len(self.controllers(events)), 1, events)
        self.assertTrue(report['mutants'])
        self.assertNotIn('residentHelpers', result.stdout)
        self.assert_reaped(events)

        result, report, events = self.run_cli('helper', full=True, extra_args=('--no-zygote',))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report['baseline']['outcome'], 'infrastructure-error', result.stderr)
        self.assertIn('declared resident helpers without opting in',
                      json.dumps(report['baseline']['errors']))
        self.assertFalse(report['summary']['complete'])
        self.assertFalse(any('zygote=on' in d for d in report['diagnostics']))
        self.assertTrue(any('zygote=off (--no-zygote)' in d for d in report['diagnostics']))
        for helper in {e['child'] for e in events if e['event'] == 'helper'}:
            self.assertFalse(alive(helper), 'retired controller left its helper behind')
        self.assert_reaped(events)

    def test_declared_helper_that_is_not_a_controller_child_is_rejected(self):
        result, report, events = self.run_cli(
            'helper-stranger', full=True, extra_args=('--zygote',))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report['baseline']['outcome'], 'infrastructure-error', result.stderr)
        self.assertRegex(json.dumps(report['baseline']['errors']),
                         r'resident helper \d+ is not a live child of the controller')
        self.assertFalse(report['summary']['complete'])
        self.assert_reaped(events)

    def mutant_runs(self, events):
        runs = {}
        for event in events:
            if event['event'] == 'run' and event['kind'] == 'mutant':
                runs.setdefault(event['job'], []).append(event.get('bypass'))
        return runs

    TIMINGS = json.dumps([{'file': str(ROOT / 'test/subject.test.ts'), 'durationMs': 2000}])

    def job_deadlines(self, events, kind):
        return [e['deadline'] for e in events if e['event'] == 'run' and e['kind'] == kind]

    def test_deadlines_are_derived_from_the_baseline_unless_a_timeout_is_given(self):
        # Derived: the baseline's 2 s for the one test file, times 1.5, plus 10 s; the
        # baseline itself gets the generous default.
        result, report, events = self.run_cli(
            'ok', timeout_ms=None, full=True, env={'OXIMUT_FAKE_TIMINGS': self.TIMINGS})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(set(self.job_deadlines(events, 'baseline')), {600_000})
        self.assertEqual(set(self.job_deadlines(events, 'mutant')), {13_000})
        self.assertEqual({m['deadlineMs'] for m in report['mutants']}, {13_000})
        self.assertEqual(report['baseline']['fileTimings'],
                         [{'file': str(ROOT / 'test/subject.test.ts'), 'durationMs': 2000.0}])
        # The factor, the constant and the baseline deadline are flags of their own.
        result, report, events = self.run_cli(
            'ok', timeout_ms=None, full=True, env={'OXIMUT_FAKE_TIMINGS': self.TIMINGS},
            extra_args=('--timeout-factor', '3', '--timeout-constant-ms', '500',
                        '--baseline-timeout-ms', '90000'))
        self.assertEqual(set(self.job_deadlines(events, 'baseline')), {90_000})
        self.assertEqual({m['deadlineMs'] for m in report['mutants']}, {6_500})
        # Explicit: one deadline for every job, baselines included, as always.
        result, report, events = self.run_cli(
            'ok', timeout_ms=7_000, full=True, env={'OXIMUT_FAKE_TIMINGS': self.TIMINGS})
        self.assertEqual(set(self.job_deadlines(events, 'baseline')), {7_000})
        self.assertEqual({m['deadlineMs'] for m in report['mutants']}, {7_000})
        # ...unless the baseline has its own.
        result, report, events = self.run_cli(
            'ok', timeout_ms=7_000, full=True, env={'OXIMUT_FAKE_TIMINGS': self.TIMINGS},
            extra_args=('--baseline-timeout-ms', '70000'))
        self.assertEqual(set(self.job_deadlines(events, 'baseline')), {70_000})
        self.assertEqual({m['deadlineMs'] for m in report['mutants']}, {7_000})

    def test_without_baseline_timings_the_wall_time_of_the_baseline_stands_in(self):
        result, report, events = self.run_cli('ok', timeout_ms=None, full=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        deadlines = {m['deadlineMs'] for m in report['mutants']}
        self.assertEqual(len(deadlines), 1, deadlines)
        # Seconds of fake baseline at most, scaled, plus the constant.
        self.assertTrue(10_000 <= deadlines.pop() < 10_000 + 1.5 * 60_000)
        self.assertNotIn('fileTimings', report['baseline'])

    def test_a_zygote_watchdog_rerun_keeps_the_scaled_deadline(self):
        result, report, events = self.run_cli(
            'zygote-watchdog', timeout_ms=None, full=True, extra_args=('--zygote',),
            env={'OXIMUT_FAKE_TIMINGS': self.TIMINGS})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        first = report['mutants'][0]['id']
        attempts = [e for e in events if e['event'] == 'run' and e['job'] == first]
        self.assertEqual([e.get('bypass') for e in attempts], [None, True])
        self.assertEqual([e['deadline'] for e in attempts], [13_000, 13_000])
        self.assertEqual(report['mutants'][0]['deadlineMs'], 13_000)

    def test_a_zygote_watchdog_rerun_of_a_lane_keeps_its_own_deadline(self):
        self.require_two_workers()
        result, report, events = self.run_cli(
            'lanes-rolling-watchdog', timeout_ms=None, full=True, shared=True,
            extra_args=('--workers', '2', '--zygote', '--rolling-lanes'),
            env={'OXIMUT_FAKE_TIMINGS': self.TIMINGS})
        self.require_spare_capacity(report)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        first = report['mutants'][0]['id']
        batch = next(e for e in events if e['event'] == 'lanes')
        # Every lane keeps the deadline derived for it; the batch allows each round of
        # two lanes that long again, and a moment to report.
        self.assertEqual(set(batch['jobs']), {13_000})
        rounds = -(-len(batch['jobs']) // 2)
        self.assertEqual(batch['deadline'], rounds * 14_000)
        # The first 'run' is the batch itself (named for its first lane); the last is the
        # lane's rerun, a job of its own.
        rerun = [e for e in events if e['event'] == 'run' and e['job'] == first]
        self.assertEqual(rerun[0]['deadline'], batch['deadline'])
        self.assertEqual(rerun[-1].get('bypass'), True)
        self.assertEqual(rerun[-1]['deadline'], 13_000)

    def test_zygote_watchdog_reruns_a_timeout_without_the_zygote(self):
        result, report, events = self.run_cli('zygote-watchdog', full=True,
                                              extra_args=('--zygote',))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
        mutants = report['mutants']
        self.assertGreaterEqual(len(mutants), 2)
        self.assertEqual({m['outcome'] for m in mutants}, {'survived'})
        runs = self.mutant_runs(events)
        first, *rest = [m['id'] for m in mutants]
        # The first mutant ran under the zygote, timed out, and was rerun once.
        self.assertEqual(runs[first], [None, True])
        # From then on every request carries bypassZygote and nothing is rerun.
        for mutant in rest:
            self.assertEqual(runs[mutant], [True])
        diagnostics = report['diagnostics']
        self.assertEqual(sum('zygote worker timed out where a normal worker did not; '
                             'zygote disabled for the rest of the run' == d for d in diagnostics), 1,
                         diagnostics)
        self.assertIn('zygote watchdog: 1 timeout reruns', diagnostics)
        self.assert_reaped(events)

    def test_zygote_watchdog_reports_a_timeout_that_the_rerun_repeats(self):
        result, report, events = self.run_cli('zygote-always-timeout', full=True,
                                              extra_args=('--zygote',))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(report['summary']['complete'])
        mutants = report['mutants']
        self.assertGreaterEqual(len(mutants), 2)
        self.assertEqual({m['outcome'] for m in mutants}, {'timeout'})
        runs = self.mutant_runs(events)
        for mutant in mutants:
            # Exactly one rerun, never more.
            self.assertEqual(runs[mutant['id']], [None, True], mutant['id'])
        diagnostics = report['diagnostics']
        self.assertIn(f'zygote watchdog: {len(mutants)} timeout reruns', diagnostics)
        self.assertFalse(any('zygote disabled' in d for d in diagnostics), diagnostics)
        self.assert_reaped(events)

    def test_a_timeout_from_a_worker_that_was_running_is_not_rerun(self):
        for mode in ('zygote-always-timeout-busy', 'zygote-always-timeout-test'):
            with self.subTest(mode=mode):
                result, report, events = self.run_cli(mode, full=True, extra_args=('--zygote',))
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                mutants = report['mutants']
                self.assertGreaterEqual(len(mutants), 2)
                self.assertEqual({m['outcome'] for m in mutants}, {'timeout'})
                for mutant in mutants:
                    # Run once under the zygote and never again: the verdict stands.
                    self.assertEqual(self.mutant_runs(events)[mutant['id']], [None], mutant['id'])
                diagnostics = report['diagnostics']
                self.assertFalse(any('timeout reruns' in d for d in diagnostics), diagnostics)
                self.assertIn(f'zygote watchdog: {len(mutants)} timeouts kept without a rerun '
                              '(worker was running, not stalled)', diagnostics)
                self.assertFalse(any('zygote disabled' in d for d in diagnostics), diagnostics)
                self.assert_reaped(events)

    def test_a_timeout_from_a_stalled_worker_is_still_rerun(self):
        result, report, events = self.run_cli('zygote-always-timeout-idle', full=True,
                                              extra_args=('--zygote',))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        mutants = report['mutants']
        for mutant in mutants:
            self.assertEqual(self.mutant_runs(events)[mutant['id']], [None, True], mutant['id'])
        diagnostics = report['diagnostics']
        self.assertIn(f'zygote watchdog: {len(mutants)} timeout reruns', diagnostics)
        self.assertFalse(any('kept without a rerun' in d for d in diagnostics), diagnostics)
        self.assert_reaped(events)

    def test_timeouts_are_never_rerun_without_zygote(self):
        result, report, events = self.run_cli('zygote-always-timeout', full=True,
                                              extra_args=('--no-zygote',))
        mutants = report['mutants']
        self.assertEqual({m['outcome'] for m in mutants}, {'timeout'})
        for mutant in mutants:
            self.assertEqual(self.mutant_runs(events)[mutant['id']], [None])
        self.assertFalse(any('zygote watchdog' in d for d in report['diagnostics']))
        self.assert_reaped(events)

    def run_cli_unchecked(self, mode, extra_args=()):
        """The CLI refused its options: there is no log or report to read."""
        with tempfile.TemporaryDirectory(prefix='oximut-native-') as directory:
            adapter = Path(directory) / 'm2-runner.mjs'
            adapter.write_text('throw new Error("the native coordinator must not run this");\n')
            (Path(directory) / 'm2-worker.ts').write_text(CONTROLLER)
            result = subprocess.run([
                str(REPO / 'target/debug/oximut'), '--root', str(ROOT),
                '--source', str(ROOT / 'src/subject.ts'), '--execution', 'persistent',
                *pinned_modes(extra_args),
                '--adapter', str(adapter), '--report', '-', '--no-cache', *extra_args,
            ], env={**os.environ, 'OXIMUT_FAKE_MODE': mode, 'OXIMUT_FAKE_LOG': str(Path(directory) / 'log')},
                text=True, capture_output=True, timeout=120)
            return result, None, None

    def run_cli(self, mode, timeout_ms=5000, extra_args=(), full=False, shared=False,
                fixture=None, env=None):
        root = REPO / fixture if fixture else ROOT
        source = root / ('src/arithmetic.ts' if fixture else 'src/subject.ts')
        with tempfile.TemporaryDirectory(prefix='oximut-native-') as directory:
            adapter = Path(directory) / 'm2-runner.mjs'
            adapter.write_text('throw new Error("the native coordinator must not run this");\n')
            (Path(directory) / 'm2-worker.ts').write_text(CONTROLLER)
            log = Path(directory) / 'log.jsonl'
            result = subprocess.run([
                str(REPO / 'target/debug/oximut'), '--root', str(root),
                '--source', str(source), *([] if full else ['--baseline-only']),
                '--execution', 'persistent', '--adapter', str(adapter), '--report', '-',
                *([] if timeout_ms is None else ['--timeout-ms', str(timeout_ms)]), '--no-cache',
                *([] if shared else ['--no-shared-controller']),
                *pinned_modes(extra_args), *extra_args,
            ], env={**os.environ, 'OXIMUT_FAKE_MODE': mode, 'OXIMUT_FAKE_LOG': str(log), **(env or {})},
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

    def test_native_lane_encoding_negotiates_with_the_controller(self):
        self.require_two_workers()
        for mode, command in (('lanes-legacy', 'run'),
                              ('lanes-compact', 'run-shared-lane-resident-v2')):
            with self.subTest(mode=mode):
                result, report, events = self.run_cli(
                    mode, extra_args=('--workers', '2'), full=True, shared=True)
                self.require_spare_capacity(report)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
                batches = [event for event in events if event['event'] == 'lanes']
                self.assertTrue(batches, events)
                self.assertEqual(
                    [job for batch in batches for job in batch['ids']],
                    [m['id'] for m in report['mutants']])
                for batch in batches:
                    self.assertEqual(batch['command'], command)
                    self.assertEqual(batch['deadline'], 6000)
                    self.assertTrue(all(1 <= ms <= 5000 for ms in batch['jobs']))
                self.assert_reaped(events)

    def test_rolling_lanes_send_one_large_batch_with_a_deadline_that_covers_every_round(self):
        self.require_two_workers()
        timeout_ms = 5000
        extra = ('--workers', '2')
        result, report, events = self.run_cli(
            'lanes-legacy', timeout_ms=timeout_ms, extra_args=extra, full=True, shared=True)
        self.require_spare_capacity(report)
        stock = [event for event in events if event['event'] == 'lanes']
        count = len(report['mutants'])
        self.assertGreater(count, 2, 'the fixture must have more mutants than workers')
        self.assertTrue(all(len(batch['ids']) <= 2 for batch in stock), stock)
        self.assertGreater(len(stock), 1)
        result, report, events = self.run_cli(
            'lanes-legacy', timeout_ms=timeout_ms, extra_args=(*extra, '--rolling-lanes'),
            full=True, shared=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
        self.assertTrue(any('rolling-lanes=on' in d for d in report['diagnostics']))
        batches = [event for event in events if event['event'] == 'lanes']
        # Every mutant in one batch, attributed in order, run two at a time.
        self.assertEqual(len(batches), 1, batches)
        [batch] = batches
        self.assertEqual(batch['ids'], [m['id'] for m in report['mutants']])
        self.assertEqual(batch['workers'], 2)
        # Each lane has its own deadline; the batch one covers every round of lanes.
        rounds = -(-count // 2)
        self.assertEqual(batch['deadline'], rounds * (timeout_ms + 1000))
        self.assertTrue(all(1 <= ms <= timeout_ms for ms in batch['jobs']))
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'survived'})
        self.assert_reaped(events)

    def test_rolling_lanes_need_the_shared_controller(self):
        result, report, events = self.run_cli_unchecked(
            'normal', extra_args=('--workers', '2', '--no-shared-controller', '--rolling-lanes'))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('--rolling-lanes requires the shared controller', result.stderr)

    def test_zygote_watchdog_reruns_a_timed_out_rolling_lane_without_the_zygote(self):
        self.require_two_workers()
        result, report, events = self.run_cli(
            'lanes-rolling-watchdog', full=True, shared=True,
            extra_args=('--workers', '2', '--zygote', '--rolling-lanes'))
        self.require_spare_capacity(report)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
        first = report['mutants'][0]['id']
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'survived'})
        batches = [event for event in events if event['event'] == 'lanes']
        self.assertEqual(batches[0]['ids'], [m['id'] for m in report['mutants']])
        # The timed-out lane was rerun once on its own with the zygote bypassed; the
        # rerun passed, so the zygote is off for what comes after.
        reruns = [e for e in events if e['event'] == 'run' and e['job'] == first]
        self.assertEqual([e.get('bypass') for e in reruns][-1], True, events)
        self.assertEqual(len(reruns), 2, reruns)
        diagnostics = report['diagnostics']
        self.assertIn('zygote watchdog: 1 timeout reruns', diagnostics)
        self.assertEqual(sum('zygote worker timed out where a normal worker did not; '
                             'zygote disabled for the rest of the run' == d for d in diagnostics), 1,
                         diagnostics)
        self.assert_reaped(events)

    def test_a_rolling_lane_that_timed_out_while_running_is_not_rerun(self):
        self.require_two_workers()
        for mode in ('lanes-rolling-watchdog-busy', 'lanes-rolling-watchdog-test'):
            with self.subTest(mode=mode):
                result, report, events = self.run_cli(
                    mode, full=True, shared=True,
                    extra_args=('--workers', '2', '--zygote', '--rolling-lanes'))
                self.require_spare_capacity(report)
                first = report['mutants'][0]['id']
                outcomes = [m['outcome'] for m in report['mutants']]
                self.assertEqual(outcomes[0], 'timeout', outcomes)
                self.assertEqual(set(outcomes[1:]), {'survived'}, outcomes)
                # Only the batch itself ran the lane: no job of its own, zygote still on.
                runs = [e for e in events if e['event'] == 'run' and e['job'] == first]
                self.assertEqual(len(runs), 1, runs)
                self.assertNotEqual(runs[0].get('bypass'), True)
                diagnostics = report['diagnostics']
                self.assertFalse(any('timeout reruns' in d for d in diagnostics), diagnostics)
                self.assertIn('zygote watchdog: 1 timeouts kept without a rerun '
                              '(worker was running, not stalled)', diagnostics)
                self.assertFalse(any('zygote disabled' in d for d in diagnostics), diagnostics)
                self.assert_reaped(events)

    def test_a_rolling_lane_that_stalled_is_still_rerun(self):
        self.require_two_workers()
        result, report, events = self.run_cli(
            'lanes-rolling-watchdog-idle', full=True, shared=True,
            extra_args=('--workers', '2', '--zygote', '--rolling-lanes'))
        self.require_spare_capacity(report)
        first = report['mutants'][0]['id']
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'survived'})
        runs = [e for e in events if e['event'] == 'run' and e['job'] == first]
        self.assertEqual(runs[-1].get('bypass'), True, runs)
        diagnostics = report['diagnostics']
        self.assertIn('zygote watchdog: 1 timeout reruns', diagnostics)
        self.assert_reaped(events)

    def test_ineligible_precollect_templates_are_reported_and_never_part_of_a_result(self):
        self.require_two_workers()
        result, report, events = self.run_cli(
            'lanes-ineligible', full=True, shared=True, extra_args=('--workers', '2'))
        self.require_spare_capacity(report)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'survived'})
        lines = [d for d in report['diagnostics'] if d.startswith('zygote precollect:')]
        # Counted per template, named by kind only (never by path), one line for the run.
        self.assertEqual(len(lines), 1, report['diagnostics'])
        batches = len([e for e in events if e['event'] == 'lanes'])
        self.assertEqual(lines[0], (
            f'zygote precollect: {2 * batches} templates ineligible (active handles: TCPServerWrap, '
            'Timeout; child processes: spawn; fs writes: mkdtempSync, writeFileSync); '
            'their mutants used the ordinary zygote path'))
        self.assert_reaped(events)

    def test_a_controller_lost_mid_batch_re_dispatches_every_lane_it_did_not_report(self):
        self.require_two_workers()
        result, report, events = self.run_cli(
            'lanes-die-first', full=True, shared=True,
            extra_args=('--workers', '2', '--rolling-lanes'))
        self.require_spare_capacity(report)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(report['summary']['complete'], result.stdout + result.stderr)
        count = len(report['mutants'])
        # No mutant is reported as an infrastructure failure: each has its real outcome.
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'survived'},
                         report['mutants'])
        batches = [event for event in events if event['event'] == 'lanes']
        self.assertEqual(batches[0]['ids'], [m['id'] for m in report['mutants']])
        self.assertIn(f'shared lanes: {count} lanes re-dispatched after a failed batch',
                      report['diagnostics'])
        self.assert_reaped(events)

    def test_a_malformed_short_lane_report_re_dispatches_every_lane_once(self):
        self.require_two_workers()
        result, report, events = self.run_cli(
            'lanes-short', full=True, shared=True,
            extra_args=('--workers', '2', '--rolling-lanes'))
        self.require_spare_capacity(report)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'survived'},
                         report['mutants'])
        position = next(i for i, e in enumerate(events) if e['event'] == 'lanes')
        batch = events[position]
        # The batch report is validated as a whole, so none of its lanes counts as a
        # result; each ran again, once, on an ordinary worker.
        again = [e['job'] for e in events[position:]
                 if e['event'] == 'run' and e['kind'] == 'mutant']
        self.assertEqual(sorted(again), sorted(batch['ids']))
        self.assertIn(f"shared lanes: {len(batch['ids'])} lanes re-dispatched after a failed batch",
                      report['diagnostics'])
        self.assert_reaped(events)

    def test_a_controller_that_keeps_failing_is_given_up_after_two_batches(self):
        self.require_two_workers()
        result, report, events = self.run_cli(
            'lanes-die-always', full=True, shared=True, fixture='fixtures/matched-small',
            extra_args=('--workers', '2'))
        self.require_spare_capacity(report)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'survived'},
                         report['mutants'])
        batches = [event for event in events if event['event'] == 'lanes']
        self.assertEqual(len(batches), 2, batches)
        self.assertIn('shared controller failed twice in a row, using one controller per '
                      'worker for the rest', report['diagnostics'])
        self.assert_reaped(events)

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
                if any('spare warm skipped' in d for d in report['diagnostics']):
                    self.skipTest('resource admission refused the spare warm')
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
                self.assertEqual([run['workers'] for run in runs], [expected, expected])
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
