"""Baselines report per-file timings and coverage that fits the response limit.

A fake persistent adapter plays the reference adapter's side of the protocol: it reports
file timings, hands over a coverage payload as large as a big suite's (as index runs,
the compact form the reference adapter uses when explicit ids would not fit) or drops
coverage that cannot fit, and logs every request so the deadlines of the mutant jobs
can be read back.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'fixtures/matched-scale'
FILES = 8
SOURCES = [ROOT / f'src/arithmetic-{index}.ts' for index in range(FILES)]
TEST_FILES = [str(ROOT / f'test/arithmetic-{index}.test.ts') for index in range(FILES)]
# A suite far larger than the fixture's, so that explicit hit lists would not fit 1 MiB.
TESTS_PER_FILE = 700
ADAPTER = r"""
import { appendFileSync, existsSync, readFileSync, renameSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const session = process.argv[2];
const mode = process.env.OXIMUT_FAKE_MODE;
const files = JSON.parse(process.env.OXIMUT_FAKE_FILES);
const perFile = Number(process.env.OXIMUT_FAKE_TESTS_PER_FILE);
const timings = JSON.parse(process.env.OXIMUT_FAKE_TIMINGS);
const tests = files.flatMap((file, f) => Array.from({ length: perFile }, (_, t) => ({
  id: `${f}-${String(t).padStart(5, '0')}`, file, name: `file ${f} case ${t} behaves as documented` })));
const total = tests.length;
let sequence = 0;
for (;;) {
  if (existsSync(join(session, 'stop'))) process.exit(0);
  const input = join(session, `request-${sequence}.json`);
  if (!existsSync(input)) { await new Promise(r => setTimeout(r, 5)); continue; }
  const request = JSON.parse(readFileSync(input, 'utf8'));
  appendFileSync(process.env.OXIMUT_FAKE_LOG, JSON.stringify({ jobId: request.jobId, kind: request.kind,
    deadlineMs: request.deadlineMs, selection: request.selection ?? null,
    coverageDir: request.coverageDir ?? null,
    coverageDirExists: request.coverageDir ? existsSync(request.coverageDir) : null }) + '\n');
  const response = { protocol: request.protocol, runId: request.runId, jobId: request.jobId,
    generation: request.generation, outcome: 'passed',
    tests: { passed: total, failed: 0, skipped: 0 }, errors: [] };
  if (request.resident) response.resident = request.resident.length;
  if (request.kind === 'baseline') response.fileTimings = timings;
  if (request.kind === 'mutant') {
    const selected = request.selection?.mode === 'files' ? request.selection.selectedTests : total;
    response.tests.passed = selected;
  }
  if (request.collectCoverage) {
    const ids = request.collectCoverage.flatMap((probe) =>
      [...probe.code.matchAll(/__oximut_m3_hit__\?\.\("([a-f0-9]{64})"\)/g)].map((m) => m[1]));
    if (mode === 'file' || mode === 'file-symlink') {
      // A suite whose inventory alone exceeds a response: the coverage goes to a file in
      // the directory the CLI offered. Mutant i is hit by every test of the files from
      // i % 8 on, as runs.
      const coverage = { complete: true, tests, reasons: [], hits: ids.map((mutantId, i) => {
        const first = (i % files.length) * perFile;
        return { mutantId, tests: [], outsideTest: false, testRanges: [[first, total - 1]] };
      }) };
      const name = 'coverage-fake.json';
      if (mode === 'file') {
        writeFileSync(join(request.coverageDir, name), JSON.stringify(coverage), { flag: 'wx', mode: 0o600 });
      } else {
        const target = join(session, 'elsewhere.json');
        writeFileSync(target, JSON.stringify(coverage), { mode: 0o600 });
        symlinkSync(target, join(request.coverageDir, name));
      }
      response.coverageFile = name;
    } else if (mode === 'too-large') {
      response.coverage = { complete: false, tests: [], hits: [], reasons: ['coverage-payload-too-large'] };
    } else {
      // Mutant i is hit by every test of the files from i % 8 on (all files for 0),
      // written as runs over the inventory: a few ranges, however many tests.
      response.coverage = { complete: true, tests, reasons: [], hits: ids.map((mutantId, i) => {
        const first = (i % files.length) * perFile;
        return { mutantId, tests: [], outsideTest: false, testRanges: [[first, total - 1]] };
      }) };
    }
  }
  const output = join(session, `response-${sequence++}.json`);
  writeFileSync(output + '.tmp', JSON.stringify(response));
  renameSync(output + '.tmp', output);
}
"""


class BaselineEvidence(unittest.TestCase):
    def run_cli(self, mode='', extra=(), per_file=TESTS_PER_FILE):
        timings = [{'file': file, 'durationMs': 1000 * (index + 1)}
                   for index, file in enumerate(TEST_FILES)]
        with tempfile.TemporaryDirectory(prefix='oximut-evidence-') as directory:
            adapter = Path(directory) / 'adapter.mjs'
            adapter.write_text(ADAPTER)
            log = Path(directory) / 'requests.jsonl'
            command = [
                str(REPO / 'target/debug/oximut'), '--root', str(ROOT),
                '--execution', 'persistent', '--adapter', str(adapter), '--report', '-',
                '--no-cache', '--test-selection', '--workers', '1', *extra,
            ]
            for source in SOURCES:
                command += ['--source', str(source)]
            env = {**os.environ, 'OXIMUT_FAKE_MODE': mode, 'OXIMUT_FAKE_LOG': str(log),
                   'OXIMUT_FAKE_FILES': json.dumps(TEST_FILES),
                   'OXIMUT_FAKE_TESTS_PER_FILE': str(per_file),
                   'OXIMUT_FAKE_TIMINGS': json.dumps(timings)}
            result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=180)
            self.assertIn(result.returncode, (0, 1, 2), result.stderr)
            requests = [json.loads(line) for line in log.read_text().splitlines()]
            return json.loads(result.stdout), requests, result

    def test_a_large_suites_coverage_travels_as_runs_and_drives_selection_and_deadlines(self):
        report, requests, result = self.run_cli()
        self.assertEqual(report['baseline']['outcome'], 'passed', report['baseline'])
        coverage = report['baseline']['coverage']
        self.assertTrue(coverage['complete'], coverage['reasons'])
        total = TESTS_PER_FILE * FILES
        self.assertEqual(len(coverage['tests']), total)
        # The CLI expands runs, so reports hold explicit ids as ever.
        self.assertTrue(all('testRanges' not in hit for hit in coverage['hits']))
        wide = [hit for hit in coverage['hits'] if len(hit['tests']) == total]
        narrow = [hit for hit in coverage['hits'] if len(hit['tests']) == TESTS_PER_FILE]
        self.assertTrue(wide and narrow, sorted({len(hit['tests']) for hit in coverage['hits']}))
        # Explicit ids for every hit would be many times the 1 MiB response limit.
        self.assertGreater(len(json.dumps(coverage['hits'])), 2 * 1024 * 1024)
        self.assertEqual({m['selection']['mode'] for m in report['mutants']}, {'files'})
        self.assertEqual({m['selection']['selectedTests'] for m in report['mutants']},
                         {TESTS_PER_FILE * n for n in range(1, FILES + 1)})

        # Deadlines follow the timings of the files each mutant runs: the files take 1 to
        # 8 s, so a mutant's deadline is 1.5 times the sum of its files' seconds plus 10 s.
        seconds = {file: 1000 * (index + 1) for index, file in enumerate(TEST_FILES)}
        seen = set()
        for mutant in report['mutants']:
            files = mutant['selection']['files']
            expected = int(sum(seconds[file] for file in files) * 1.5) + 10_000
            self.assertEqual(mutant['deadlineMs'], expected, (files, mutant['deadlineMs']))
            seen.add(expected)
        self.assertEqual(seen, {int(sum(range(first, FILES + 1)) * 1000 * 1.5) + 10_000
                                for first in range(1, FILES + 1)}, seen)
        logged = {r['jobId']: r['deadlineMs'] for r in requests if r['kind'] == 'mutant'}
        self.assertEqual(logged, {m['id']: m['deadlineMs'] for m in report['mutants']})
        baselines = [r['deadlineMs'] for r in requests if r['kind'] == 'baseline']
        self.assertTrue(baselines and set(baselines) == {600_000}, baselines)
        # The merged timings are evidence in the report.
        self.assertEqual({t['file']: t['durationMs'] for t in report['baseline']['fileTimings']},
                         {file: float(ms) for file, ms in seconds.items()})

    def test_a_suite_past_the_response_limit_sends_coverage_by_file_and_stays_narrow(self):
        # 8 files of 3,200 tests: the inventory alone is megabytes, over any response.
        per_file = 3200
        report, requests, result = self.run_cli('file', per_file=per_file)
        self.assertEqual(report['baseline']['outcome'], 'passed', report['baseline'])
        coverage = report['baseline']['coverage']
        self.assertTrue(coverage['complete'], coverage['reasons'])
        total = per_file * FILES
        self.assertEqual(len(coverage['tests']), total)
        self.assertGreater(len(json.dumps(coverage['tests'])), 2 * 1024 * 1024)
        self.assertEqual(coverage['reasons'], [])
        # The CLI offered a private directory to the coverage run only, and removed it.
        offered = [r for r in requests if r['coverageDir']]
        self.assertEqual(len(offered), 1, requests)
        self.assertTrue(os.path.isabs(offered[0]['coverageDir']))
        self.assertTrue(offered[0]['coverageDirExists'])
        self.assertFalse(os.path.exists(offered[0]['coverageDir']))
        # Selection stays narrow: a mutant runs the files it reaches, not the whole suite.
        modes = {m['selection']['mode'] for m in report['mutants']}
        self.assertEqual(modes, {'files'})
        self.assertEqual({m['selection']['selectedTests'] for m in report['mutants']},
                         {per_file * n for n in range(1, FILES + 1)})
        self.assertLess(min(m['selection']['selectedTests'] for m in report['mutants']), total)
        self.assertNotIn('coverage exceeded', ' '.join(report['diagnostics']))

    def test_a_coverage_file_that_is_a_link_is_refused_and_falls_back_to_the_whole_suite(self):
        report, requests, result = self.run_cli('file-symlink', per_file=10)
        self.assertEqual(report['baseline']['outcome'], 'passed', report['baseline'])
        coverage = report['baseline']['coverage']
        self.assertFalse(coverage['complete'])
        self.assertTrue(any(reason.startswith('invalid-coverage-file') for reason in coverage['reasons']),
                        coverage['reasons'])
        self.assertEqual({m['selection']['mode'] for m in report['mutants']}, {'full'})

    def test_coverage_dropped_for_size_falls_back_to_the_whole_suite_and_its_time(self):
        report, requests, result = self.run_cli('too-large', per_file=10)
        self.assertEqual(report['baseline']['outcome'], 'passed', report['baseline'])
        self.assertTrue(report['summary']['complete'], result.stdout[-2000:])
        coverage = report['baseline']['coverage']
        self.assertFalse(coverage['complete'])
        self.assertIn('coverage-payload-too-large', coverage['reasons'])
        # Nothing to validate, so no misleading second reason.
        self.assertNotIn('invalid-coverage-inventory', coverage['reasons'])
        self.assertIn('coverage exceeded the transport limit (1 MiB response, or the coverage file '
                      'limit); test selection falls back to full suites', report['diagnostics'])
        # Every mutant runs the whole suite, which the eight files make 36 s: 36 * 1.5 + 10.
        self.assertEqual({m['selection']['mode'] for m in report['mutants']}, {'full'})
        self.assertEqual({m['deadlineMs'] for m in report['mutants']}, {64_000})

    def test_an_explicit_timeout_fixes_every_deadline(self):
        report, requests, _ = self.run_cli(extra=('--timeout-ms', '9000'), per_file=10)
        self.assertEqual({m['deadlineMs'] for m in report['mutants']}, {9_000})
        self.assertEqual({r['deadlineMs'] for r in requests}, {9_000})


if __name__ == '__main__':
    unittest.main()
