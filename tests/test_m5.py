"""Linux M5 scheduler stress/failure contract; fake adapters isolate accounting."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / 'target/debug/oximut'
MIB = 1024 * 1024
ADAPTER = r"""
import { appendFileSync, existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
const args = process.argv.slice(2);
const persistent = args.length === 1;
const mode = process.env.OXIMUT_M5_MODE;
const log = process.env.OXIMUT_M5_LOG;
const steady = mode === 'steady' ? Buffer.alloc(64 * 1024 * 1024, 17) : null;
if (steady) setInterval(() => { steady[0]++; }, 100).unref();
const sleep = ms => new Promise(r => setTimeout(r, ms));
const event = value => appendFileSync(log, JSON.stringify({ ...value, pid: process.pid, at: Date.now() }) + '\n');
async function run(request, output) {
  const mutant = request.kind === 'mutant' && !request.jobId.startsWith('__oximut_disabled__');
  event({ event: 'start', job: request.jobId, mutant });
  if (mutant && (['hog', 'interrupt', 'root-crash'].includes(mode) || (mode === 'leak' && request.generation === 1))) {
    const child = spawn(process.execPath, ['--eval', `const b=Buffer.alloc(${mode === 'hog' ? 180 : 1}*1024*1024, 7);setInterval(()=>{b[0]++},1000)`], { detached: true, stdio: 'ignore' });
    event({ event: 'child', child: child.pid, job: request.jobId });
    child.unref();
    if (mode === 'root-crash') { process.kill(process.ppid, 'SIGKILL'); await sleep(100000); }
    if (mode === 'interrupt') await sleep(100000);
    if (mode === 'leak' && request.generation === 1) await sleep(100000);
    if (mode === 'hog') await sleep(100000);
  }
  // Deterministic overlap: the parent must cap parallel jobs centrally.
  await sleep(mutant ? 250 : 180);
  const failed = mutant && request.generation % 2 === 1;
  const response = { protocol: request.protocol, runId: request.runId, jobId: request.jobId,
    generation: request.generation, outcome: failed ? 'failed' : 'passed',
    tests: { passed: failed ? 0 : 1, failed: failed ? 1 : 0, skipped: 0 }, errors: [] };
  event({ event: 'end', job: request.jobId });
  writeFileSync(output + '.tmp', JSON.stringify(response)); renameSync(output + '.tmp', output);
}
if (persistent) {
  for (let sequence=0;;sequence++) {
    const input = join(args[0], `request-${sequence}.json`);
    while (!existsSync(input)) {
      if (existsSync(join(args[0], 'stop'))) process.exit(0);
      await sleep(5);
    }
    await run(JSON.parse(readFileSync(input, 'utf8')), join(args[0], `response-${sequence}.json`));
  }
} else await run(JSON.parse(readFileSync(args[0], 'utf8')), args[1]);
"""


class ResourceBudget(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='oximut-m5-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / 'project'
        self.root.mkdir()
        (self.root / 'vitest.config.ts').write_text('export default {};')
        (self.root / 'subject.ts').write_text('\n'.join(f'export const b{i} = true;' for i in range(8)))
        self.adapter = self.directory / 'adapter.mjs'
        self.adapter.write_text(ADAPTER)
        self.log = self.directory / 'events.jsonl'

    def command(self, *extra):
        return [str(BIN), '--root', str(self.root), '--source', 'subject.ts',
                '--adapter', str(self.adapter), '--report', '-', '--timeout-ms', '500',
                '--memory-reserve-mib', '8', *extra]

    def environment(self, mode='normal'):
        return {**os.environ, 'OXIMUT_M5_MODE': mode, 'OXIMUT_M5_LOG': str(self.log)}

    def run_cli(self, *extra, mode='normal'):
        result = subprocess.run(self.command(*extra), env=self.environment(mode),
                                capture_output=True, text=True, timeout=120)
        self.assertIn(result.returncode, (0, 1, 2), result.stderr)
        self.assertTrue(result.stdout.startswith('{'), result.stderr + result.stdout)
        return result, json.loads(result.stdout)

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_reaped(self, events):
        pids = {event['pid'] for event in events}
        pids.update(event['child'] for event in events if 'child' in event)
        for pid in pids:
            self.assertFalse(Path(f'/proc/{pid}').exists(), f'owned PID {pid} leaked')

    def test_parallel_slots_have_stable_results_and_explicit_measurements(self):
        for execution in ('fresh', 'persistent'):
            _, serial = self.run_cli('--execution', execution, '--workers', '1', '--memory-budget-mib', '512')
            _, parallel = self.run_cli('--execution', execution, '--workers', '2', '--memory-budget-mib', '512')
            self.assertEqual([(m['id'], m['outcome']) for m in serial['mutants']],
                             [(m['id'], m['outcome']) for m in parallel['mutants']])
            resources = parallel['resources']
            self.assertEqual(resources['configuredWorkers'], 2)
            self.assertLessEqual(resources['maxActiveJobs'], resources['effectiveWorkers'])
            if resources['effectiveWorkers'] >= 2:
                self.assertEqual(resources['maxActiveJobs'], 2)
            self.assertEqual(resources['budgetBytes'], 512 * MIB)
            self.assertEqual(resources['sampleIntervalMs'], 50)
            self.assertGreater(resources['measuredWorkerPeakBytes'], 0)
            self.assertGreater(resources['peakAggregateRssBytes'], resources['measuredWorkerPeakBytes'])
            self.assertIn('best-effort', resources['enforcement'])
            self.assertTrue(parallel['summary']['complete'])
        self.assert_reaped(self.events())

    def test_measured_cost_limits_admission_and_recycles_idle_workers(self):
        _, calibration = self.run_cli('--baseline-only', '--execution', 'persistent', '--memory-budget-mib', '512', mode='steady')
        cost = calibration['resources']['measuredWorkerPeakBytes']
        budget = math.ceil((cost * 1.7) / MIB) + 16
        _, report = self.run_cli('--execution', 'persistent', '--workers', '4', '--memory-budget-mib', str(budget), mode='steady')
        self.assertTrue(report['summary']['complete'], report)
        self.assertEqual(report['resources']['maxActiveJobs'], 1)
        self.assertGreater(report['resources']['admissionPauses'], 0)
        self.assertGreater(report['resources']['recycledIdleWorkers'], 0)
        self.assertLessEqual(report['resources']['peakAggregateRssBytes'], budget * MIB)
        self.assert_reaped(self.events())

    def test_descendant_hog_exhausts_once_without_kills_or_cache_publication(self):
        result, report = self.run_cli('--execution', 'persistent', '--workers', '1',
                                      '--memory-budget-mib', '128', '--cache', '--cache-inputs-complete', mode='hog')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report['baseline']['outcome'], 'passed')
        self.assertFalse(report['summary']['complete'])
        self.assertEqual(report['summary']['killed'], 0)
        self.assertTrue(all(m['outcome'] == 'resource-exhausted' for m in report['mutants']))
        starts = [e for e in self.events() if e['event'] == 'start' and e.get('mutant')]
        self.assertEqual(len(starts), 1, self.events())
        self.assertIn('not be retried', report['mutants'][0]['errors'][0])
        self.assertGreater(report['resources']['peakAggregateRssBytes'], 128 * MIB)
        self.assertNotEqual(report['cache']['persistence'], 'complete-run-persisted')
        self.assertFalse(list((self.root / '.oximut/cache').rglob('outcomes/*.json')))
        self.assert_reaped(self.events())

    def test_tiny_budget_rejects_baseline_without_deadlock(self):
        result, report = self.run_cli('--memory-budget-mib', '1', '--memory-reserve-mib', '0')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report['baseline']['outcome'], 'resource-exhausted')
        self.assertFalse(report['summary']['complete'])
        self.assertEqual(self.events(), [])

    def test_timeout_cleanup_cannot_kill_a_parallel_sibling(self):
        result, report = self.run_cli('--workers', '2', '--memory-budget-mib', '512', mode='leak')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report['mutants'][0]['outcome'], 'infrastructure-error')
        self.assertEqual(report['mutants'][1]['outcome'], 'survived', report)
        self.assertEqual(report['summary']['infrastructureError'], 1, report)
        self.assert_reaped(self.events())

    def test_unexpected_slot_death_sweeps_unobserved_adopted_children(self):
        result, report = self.run_cli('--workers', '2', '--memory-budget-mib', '512', mode='root-crash')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(report['summary']['complete'])
        self.assertEqual(report['summary']['killed'], 0)
        self.assert_reaped(self.events())

    def test_sigterm_reaps_detached_processes_and_reports_incomplete(self):
        process = subprocess.Popen(self.command('--workers', '2', '--memory-budget-mib', '512'),
                                   env=self.environment('interrupt'), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if any(event['event'] == 'child' for event in self.events()):
                break
            time.sleep(0.02)
        else:
            self.fail('mutant child did not start')
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 2, stderr)
        report = json.loads(stdout)
        self.assertFalse(report['summary']['complete'])
        self.assert_reaped(self.events())

    def test_invalid_units_and_worker_counts_are_rejected(self):
        for args in [('--workers', '0'), ('--workers', '1.5'), ('--memory-budget-mib', '10MB'),
                     ('--memory-budget-mib', '0'), ('--memory-budget-mib', '8', '--memory-reserve-mib', '8')]:
            result = subprocess.run(self.command(*args), capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
