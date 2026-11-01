"""Instrumented baselines validate the exact modules every mutant job serves."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'fixtures/matched-scale'
SOURCES = [ROOT / 'src/arithmetic-0.ts', ROOT / 'src/arithmetic-1.ts']
FILES = sorted(map(str, SOURCES))
ADAPTER = r"""
import { appendFileSync, existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const session = process.argv[2];
const mode = process.env.OXIMUT_FAKE_MODE;
let sequence = 0;
for (;;) {
  if (existsSync(join(session, 'stop'))) process.exit(0);
  const input = join(session, `request-${sequence}.json`);
  if (!existsSync(input)) { await new Promise(r => setTimeout(r, 5)); continue; }
  const request = JSON.parse(readFileSync(input, 'utf8'));
  appendFileSync(process.env.OXIMUT_FAKE_LOG, JSON.stringify(request) + '\n');
  const instrumented = request.jobId.startsWith('instrumented-baseline');
  const failed = instrumented && mode === 'fail';
  const passed = instrumented && mode === 'count' ? 2 : failed ? 0 : 1;
  const acknowledged = request.resident && mode !== 'no-ack';
  const response = { protocol: request.protocol, runId: request.runId, jobId: request.jobId,
    generation: request.generation, ...(acknowledged ? { resident: request.resident.length } : {}),
    outcome: failed ? 'failed' : 'passed',
    tests: { passed, failed: failed ? 1 : 0, skipped: 0 }, errors: [] };
  const output = join(session, `response-${sequence++}.json`);
  writeFileSync(output + '.tmp', JSON.stringify(response));
  renameSync(output + '.tmp', output);
}
"""


def encoded(value):
    """Byte length of serde_json's compact encoding of the logged request part."""
    return len(json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode())


class InstrumentedBaseline(unittest.TestCase):
    def run_cli(self, mode='', selection=False, budget=None):
        with tempfile.TemporaryDirectory(prefix='oximut-instrumented-') as directory:
            adapter = Path(directory) / 'adapter.mjs'
            adapter.write_text(ADAPTER)
            log = Path(directory) / 'requests.jsonl'
            command = [
                str(REPO / 'target/debug/oximut'), '--root', str(ROOT),
                '--execution', 'persistent', '--adapter', str(adapter), '--report', '-',
                '--timeout-ms', '5000', '--no-cache',
            ]
            for source in SOURCES:
                command += ['--source', str(source)]
            if selection:
                command.append('--test-selection')
            env = {**os.environ, 'OXIMUT_FAKE_MODE': mode, 'OXIMUT_FAKE_LOG': str(log)}
            env.pop('OXIMUT_DEBUG_REQUEST_BUDGET', None)
            if budget is not None:
                env['OXIMUT_DEBUG_REQUEST_BUDGET'] = str(budget)
            result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=120)
            self.assertIn(result.returncode, (0, 1, 2), result.stderr)
            requests = [json.loads(line) for line in log.read_text().splitlines()]
            return json.loads(result.stdout), requests

    def modules(self, selection):
        """The resident modules the CLI plans, from an unconstrained run."""
        _, requests = self.run_cli(selection=selection)
        first = next(r for r in requests if r['kind'] == 'mutant')
        return {module['file']: module for module in first['resident']}

    def assert_mutants_serve(self, requests, modules, grouped):
        mutants = [r for r in requests if r['kind'] == 'mutant']
        self.assertGreater(len(mutants), 2)
        self.assertEqual({r['mutation']['file'] for r in mutants}, set(FILES))
        for request in mutants:
            self.assertNotIn('collectCoverage', request)
            mutation = request['mutation']
            # Code and source map: attribution depends on both.
            for field in ('code', 'map'):
                self.assertEqual(mutation[field], modules[mutation['file']][field])
            if grouped:
                # Every module of the validated group, never a partial set.
                self.assertEqual({m['file'] for m in request['resident']}, set(FILES))
                for module in request['resident']:
                    for field in ('code', 'map'):
                        self.assertEqual(module[field], modules[module['file']][field])
            else:
                # A lone module is served through `mutation` alone.
                self.assertNotIn('resident', request)

    def assert_records(self, report, groups, coverage):
        resources = report['resources']
        self.assertIn('instrumentedBaseline', resources['phaseWallMs'])
        self.assertNotIn('disabledSwitchValidation', resources['phaseWallMs'])
        self.assertNotIn('coverageBaseline', resources['phaseWallMs'])
        runs = resources['disabledValidationRuns']
        self.assertEqual([sorted(run['files']) for run in runs], groups)
        for run in runs:
            self.assertEqual((run['run'], run['coverage']), ('instrumentedBaseline', coverage))
            self.assertGreaterEqual(run['wallMs'], 0)

    def test_one_validation_run_serves_every_switched_module_to_every_mutant(self):
        report, requests = self.run_cli()
        self.assertEqual(report['baseline']['outcome'], 'passed')
        self.assertEqual([r['jobId'] for r in requests[:2]], ['baseline', 'instrumented-baseline'])
        original, instrumented = requests[:2]
        self.assertNotIn('resident', original)
        self.assertNotIn('collectCoverage', instrumented)
        self.assertIsNone(instrumented['mutation'])
        resident = {module['file']: module for module in instrumented['resident']}
        self.assertEqual(sorted(resident), FILES)
        for module in resident.values():
            self.assertIn('process.env.OXIMUT_ACTIVE_MUTANT', module['code'])
            self.assertNotIn('__oximut_m3_hit__', module['code'])
        self.assertEqual(sum(r['jobId'].startswith('instrumented') for r in requests), 1)
        self.assertFalse(any(r['jobId'].startswith('__oximut_disabled__') for r in requests))
        self.assert_mutants_serve(requests, resident, grouped=True)
        self.assert_records(report, [FILES], coverage=False)

    def test_selection_collects_coverage_from_the_validated_modules_without_duplicates(self):
        report, requests = self.run_cli(selection=True)
        self.assertEqual(report['baseline']['outcome'], 'passed')
        self.assertEqual([r['jobId'] for r in requests[:2]], ['baseline', 'instrumented-baseline'])
        instrumented = requests[1]
        coverage = {module['file']: module for module in instrumented['collectCoverage']}
        self.assertEqual(sorted(coverage), FILES)
        for module in coverage.values():
            self.assertIn('process.env.OXIMUT_ACTIVE_MUTANT', module['code'])
            self.assertIn('__oximut_m3_hit__', module['code'])
        # Identical resident modules are served through the coverage transforms.
        self.assertNotIn('resident', instrumented)
        self.assert_mutants_serve(requests, coverage, grouped=True)
        self.assert_records(report, [FILES], coverage=True)

    def test_oversized_groups_split_validation_and_keep_coverage_when_it_fits(self):
        modules = self.modules(selection=True)
        sizes = [encoded(modules[file]) for file in FILES]
        coverage = sum(sizes) + len(sizes) + 1
        # Too small for one group plus a duplicate member, enough for coverage.
        budget = sum(sizes) + max(sizes) - 64
        self.assertGreater(budget, coverage + 32)
        report, requests = self.run_cli(selection=True, budget=budget)
        self.assertEqual(report['baseline']['outcome'], 'passed')
        jobs = [r['jobId'] for r in requests if r['kind'] == 'baseline']
        self.assertEqual(jobs, ['baseline', 'instrumented-baseline-0',
                                'instrumented-baseline-1', 'instrumented-baseline-2'])
        coverage_run, *validations = requests[1:4]
        self.assertEqual(sorted(m['file'] for m in coverage_run['collectCoverage']), FILES)
        self.assertNotIn('resident', coverage_run)
        for request, file in zip(validations, FILES):
            self.assertNotIn('collectCoverage', request)
            self.assertEqual([m['file'] for m in request['resident']], [file])
            self.assertLessEqual(encoded(request['resident']), budget)
        self.assert_mutants_serve(requests, modules, grouped=False)
        self.assert_records(report, [[file] for file in FILES], coverage=False)
        self.assertNotIn('coverage-request-exceeds-limit', json.dumps(report))

    def test_untransportable_coverage_falls_back_to_full_suites_after_validation(self):
        modules = self.modules(selection=True)
        sizes = [encoded(modules[file]) for file in FILES]
        budget = max(sizes) + 64
        self.assertLess(budget, sum(sizes))
        report, requests = self.run_cli(selection=True, budget=budget)
        self.assertEqual(report['baseline']['outcome'], 'passed')
        self.assertFalse(any('collectCoverage' in r for r in requests))
        jobs = [r['jobId'] for r in requests if r['kind'] == 'baseline']
        self.assertEqual(jobs, ['baseline', 'instrumented-baseline-0', 'instrumented-baseline-1'])
        self.assert_mutants_serve(requests, modules, grouped=False)
        self.assert_records(report, [[file] for file in FILES], coverage=False)
        self.assertIn('coverage-request-exceeds-limit', json.dumps(report['baseline']))
        for mutant in report['mutants']:
            self.assertEqual(mutant['selection']['mode'], 'full', mutant)

    def test_untransportable_module_is_refused_before_dispatch(self):
        modules = self.modules(selection=False)
        smallest = min(encoded(module) for module in modules.values())
        # The hard limit is budget + budget/7; a lone module over it can't travel.
        report, requests = self.run_cli(budget=smallest // 2)
        self.assertEqual([r['jobId'] for r in requests], ['baseline'])
        self.assertEqual(report['baseline']['outcome'], 'infrastructure-error')
        self.assertIn('exceeds the', report['baseline']['errors'][0])
        self.assertIn('cannot be validated', report['baseline']['errors'][0])
        self.assertTrue(all(m['outcome'] == 'skipped' for m in report['mutants']))

    def test_lone_module_above_the_budget_but_within_the_limit_still_runs(self):
        modules = self.modules(selection=False)
        largest = max(encoded(module) for module in modules.values())
        # Above the planning budget (lone groups), within budget + budget/7.
        budget = largest - 64
        self.assertGreater(budget + budget // 7, largest + 1024)
        report, requests = self.run_cli(budget=budget)
        self.assertEqual(report['baseline']['outcome'], 'passed')
        jobs = [r['jobId'] for r in requests if r['kind'] == 'baseline']
        self.assertEqual(jobs, ['baseline', 'instrumented-baseline-0', 'instrumented-baseline-1'])
        self.assert_mutants_serve(requests, modules, grouped=False)

    def test_failed_count_changing_or_unacknowledged_validation_blocks_every_mutant(self):
        for mode, outcome, message in [
            ('fail', 'failed', 'instrumented baseline failed'),
            ('count', 'infrastructure-error', 'changed baseline test counts'),
            ('no-ack', 'infrastructure-error', 'did not acknowledge the resident modules'),
        ]:
            with self.subTest(mode=mode):
                report, requests = self.run_cli(mode)
                self.assertEqual([r['jobId'] for r in requests][:2],
                                 ['baseline', 'instrumented-baseline'])
                self.assertFalse(any(r['kind'] == 'mutant' for r in requests))
                self.assertEqual(report['baseline']['outcome'], outcome)
                self.assertFalse(report['summary']['complete'])
                self.assertIn(message, json.dumps(report))
                self.assertTrue(all(m['outcome'] == 'skipped' for m in report['mutants']))


if __name__ == '__main__':
    unittest.main()
