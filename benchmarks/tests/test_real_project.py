"""Real workload gates must reject changed corpora and unequal full experiments."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import real_project as benchmark


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((benchmark.CORPUS / 'workload.json').read_text())

    def test_pinned_upstream_inventory(self):
        benchmark.verify_inputs(benchmark.CORPUS, self.manifest)
        files = self.manifest['upstreamSha256']
        self.assertEqual(sum(name.startswith('src/') for name in files), 41)
        self.assertEqual(sum(name.endswith('.test.ts') for name in files), 4)
        self.assertEqual(len(self.manifest['expectedTests']), 102)

    def test_changed_test_or_extra_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'project'
            benchmark.prepare(project, self.manifest)
            test = project / 'tests/colord.test.ts'
            original = test.read_text()
            test.write_text(original + '\n// changed\n')
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                benchmark.verify_inputs(project, self.manifest)
            test.write_text(original)
            (project / 'src/extra.ts').write_text('export const x = 1;')
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                benchmark.verify_inputs(project, self.manifest)

    def test_missing_tests_and_duplicate_names_are_rejected(self):
        identities = self.manifest['expectedTests']
        report = {'testFiles': {}}
        for filename, name in identities:
            report['testFiles'].setdefault(filename, {'tests': []})['tests'].append({'name': name})
        benchmark.validate_tests('stryker', report, benchmark.CORPUS, self.manifest)
        report['testFiles']['tests/delta-e00.test.ts']['tests'].pop()
        with self.assertRaisesRegex(ValueError, 'identities differ'):
            benchmark.validate_tests('stryker', report, benchmark.CORPUS, self.manifest)
        # Count alone must not allow one duplicated test to replace the missing one.
        tests = report['testFiles']['tests/delta-e00.test.ts']['tests']
        tests.append(copy.deepcopy(tests[0]))
        with self.assertRaisesRegex(ValueError, 'identities differ'):
            benchmark.validate_tests('stryker', report, benchmark.CORPUS, self.manifest)

    def test_exact_programmatic_names_map_to_declared_test_scope(self):
        report = {'baseline': {'tests': {'passed': 102}, 'coverage': {
            'complete': True, 'reasons': [], 'tests': [
                {'file': str(benchmark.CORPUS / filename), 'name': name}
                for filename, name in self.manifest['expectedOximutTests']]}}}
        result = benchmark.validate_tests('oximut', report, benchmark.CORPUS, self.manifest)
        self.assertEqual(result, benchmark.m6.digest(sorted(self.manifest['expectedTests'])))
        report['baseline']['coverage']['tests'][0]['name'] += ' changed'
        with self.assertRaisesRegex(ValueError, 'identities differ'):
            benchmark.validate_tests('oximut', report, benchmark.CORPUS, self.manifest)

    def test_incomplete_oximut_attribution_is_rejected(self):
        report = {'baseline': {'tests': {'passed': 102}, 'coverage': {'complete': False}}}
        with self.assertRaisesRegex(ValueError, 'attribution incomplete'):
            benchmark.validate_tests('oximut', report, benchmark.CORPUS, self.manifest)


class WarmupOnlyTests(unittest.TestCase):
    def test_warmup_only_keeps_raw_evidence_without_measured_medians(self):
        # Exercise orchestration only: no child process or timed command runs.
        def prepare(project, manifest):
            project.mkdir()
            for name in ('vitest.config.ts', 'stryker.config.mjs', 'package.json'):
                (project / name).write_text('{}')
        versions = {'node': 'v26.3.1', 'vitest': '4.1.12',
                    '@stryker-mutator/core': '10.0.0', '@stryker-mutator/vitest-runner': '10.0.0'}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'warmup'
            with mock.patch.object(benchmark.measurement, 'metadata', return_value={'versions': versions}), \
                 mock.patch.object(benchmark, 'prepare', side_effect=prepare), \
                 mock.patch.object(benchmark.measurement, 'run_command', return_value={
                     'wall_seconds': 1., 'cpu_seconds': 1., 'peak_aggregate_rss_bytes': 1.,
                     'peak_process_count': 1}), \
                 mock.patch.object(benchmark, 'validate', return_value={}):
                code = benchmark.main(['--tools', 'vitest', '--warmups', '1', '--repetitions', '0',
                                       '--output', str(output)])
            self.assertEqual(code, 1)  # Ordinary-only does not claim a comparative gate.
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['raw_measurement_count'], 1)
            self.assertEqual(summary['ratios'], [])
            self.assertEqual(summary['groups'][0]['runs'], 0)
            self.assertIsNone(summary['groups'][0]['raw_medians']['wall_seconds'])
            self.assertTrue(json.loads((output / 'measurements.jsonl').read_text())['warmup'])


class AdapterProvenanceTests(unittest.TestCase):
    def test_binary_default_is_preserved_and_override_is_explicit(self):
        args = SimpleNamespace(binary=Path('/candidate/oximut'), adapter=None,
                               diagnostic_unrestricted=False)
        command = benchmark.command('oximut', args, Path('/project'), Path('/report'),
                                    {'mutate': ['src/a.ts'], 'families': ['arithmetic']})
        self.assertNotIn('--adapter', command)
        args.adapter = Path('/candidate/packages/vitest-adapter/m2-runner.mjs')
        command = benchmark.command('oximut', args, Path('/project'), Path('/report'),
                                    {'mutate': ['src/a.ts'], 'families': ['arithmetic']})
        self.assertEqual(command[command.index('--adapter') + 1], str(args.adapter))

    def test_full_adapter_graph_and_binary_changes_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'oximut'
            binary.write_bytes(b'candidate')
            adapter = root / 'adapter'
            adapter.mkdir()
            (adapter / 'm2-runner.mjs').write_text('import "./worker.ts";')
            (adapter / 'worker.ts').write_text('export const version = 1;')
            args = SimpleNamespace(binary=binary, adapter=None, adapter_source_dir=adapter)
            before = benchmark.execution_inputs(args)
            self.assertEqual(len(before['adapter_source_sha256']), 2)
            (adapter / 'worker.ts').write_text('export const version = 2;')
            self.assertNotEqual(before, benchmark.execution_inputs(args))
            binary.write_bytes(b'other candidate')
            self.assertNotEqual(before['binary_sha256'],
                                benchmark.execution_inputs(args)['binary_sha256'])


class RatioTests(unittest.TestCase):
    def rows(self):
        return [dict(tool=tool, round=0, warmup=False, success=True,
                     memory_method='sum-live-smaps_rollup-rss', memory_errors=[],
                     declared_test_identity_sha256='same', mutator_families=['arithmetic'],
                     mutations={'a': {'outcome': 'killed'}},
                     **{metric: 2. for metric in benchmark.METRICS})
                for tool in ('vitest', 'oximut', 'stryker')]

    def test_exact_full_match_allows_ratio(self):
        comparisons, ratios = benchmark.compare_rows(self.rows())
        self.assertTrue(comparisons[0]['ratio_eligible'])
        self.assertEqual(ratios[0]['wall_seconds'], 1.)

    def test_overlap_failure_or_changed_tests_never_allow_ratio(self):
        for cause in ('extra-mutant', 'outcome', 'failed-run', 'test-scope', 'families'):
            with self.subTest(cause=cause):
                rows = self.rows()
                if cause == 'extra-mutant':
                    rows[1]['mutations']['b'] = {'outcome': 'killed'}
                elif cause == 'outcome':
                    rows[1]['mutations']['a']['outcome'] = 'survived'
                elif cause == 'failed-run':
                    rows[0]['success'] = False
                elif cause == 'families':
                    rows[1]['mutator_families'] = ['unrestricted']
                else:
                    rows[2]['declared_test_identity_sha256'] = 'different'
                comparisons, ratios = benchmark.compare_rows(rows)
                self.assertFalse(comparisons[0]['ratio_eligible'])
                self.assertEqual(ratios, [])

    def test_invalid_measurements_missing_identity_and_duplicate_rows_reject_ratios(self):
        for key, value in [('wall_seconds', 0), ('cpu_seconds', float('nan')),
                           ('peak_aggregate_rss_bytes', float('inf')),
                           ('memory_method', 'stat'), ('memory_errors', ['restricted']),
                           ('declared_test_identity_sha256', None), ('warmup', True)]:
            rows = self.rows()
            rows[1][key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(benchmark.compare_rows(rows)[1], [])
        rows = self.rows()
        rows.append(copy.deepcopy(rows[1]))
        self.assertEqual(benchmark.compare_rows(rows)[1], [])


if __name__ == '__main__':
    unittest.main()
