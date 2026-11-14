"""Real workload gates must reject changed corpora and unequal full experiments."""
import argparse
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
        identities = self.manifest['expectedOximutTests']
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
        def prepare(project, manifest, concurrency=1):
            project.mkdir()
            for name in ('vitest.config.ts', 'stryker.config.mjs', 'package.json'):
                (project / name).write_text('{}')
        versions = {'node': 'v26.3.1', 'vitest': '5.1.0',
                    '@stryker-mutator/core': '10.1.1', '@stryker-mutator/vitest-runner': '10.1.1'}
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


class FailedCampaignRatioTests(unittest.TestCase):
    def campaign(self, failing_round):
        def prepare(project, manifest, concurrency=1):
            project.mkdir()
            for name in ('vitest.config.ts', 'stryker.config.mjs', 'package.json'):
                (project / name).write_text('{}')

        rounds = []

        def run_command(*_args, **_kwargs):
            return {metric: 2. for metric in benchmark.METRICS} | {
                'peak_process_count': 1, 'memory_method': 'sum-live-smaps_rollup-rss',
                'memory_errors': []}

        def validate(tool, *_args):
            rounds.append(tool)
            if (len(rounds) - 1) // 3 == failing_round:
                raise ValueError('injected validation failure')
            return {'declared_test_identity_sha256': 'same',
                    'mutations': {'a': {'outcome': 'killed'}}}

        versions = {'node': 'v26.3.1', 'vitest': '5.1.0',
                    '@stryker-mutator/core': '10.1.1', '@stryker-mutator/vitest-runner': '10.1.1'}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'campaign'
            with mock.patch.object(benchmark.measurement, 'metadata', return_value={'versions': versions}), \
                 mock.patch.object(benchmark, 'prepare', side_effect=prepare), \
                 mock.patch.object(benchmark.measurement, 'run_command', side_effect=run_command), \
                 mock.patch.object(benchmark, 'validate', side_effect=validate), \
                 mock.patch.object(benchmark, 'execution_inputs', return_value={}):
                code = benchmark.main(['--tools', 'vitest', 'oximut', 'stryker', '--warmups', '1',
                                       '--repetitions', '1', '--output', str(output),
                                       '--adapter-source-dir',
                                       str(benchmark.ROOT / 'packages/vitest-adapter')])
            return code, json.loads((output / 'summary.json').read_text())

    def test_failed_warmup_suppresses_ratios_from_passing_measured_round(self):
        code, summary = self.campaign(failing_round=None)
        self.assertTrue(summary['correctness_gate'])
        self.assertEqual(len(summary['ratios']), 1)  # Control: the measured round qualifies.
        code, summary = self.campaign(failing_round=0)
        self.assertNotEqual(code, 0)
        self.assertFalse(summary['correctness_gate'])
        self.assertEqual(summary['ratios'], [])


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

    def test_extra_arguments_append_and_are_recorded(self):
        args = SimpleNamespace(binary=Path('/candidate'), adapter=None,
                               adapter_source_dir=None, diagnostic_unrestricted=False)
        manifest = {'mutate': ['src/a.ts'], 'families': ['arithmetic']}
        original = benchmark.command('oximut', args, Path('/p'), Path('/r'), manifest)
        self.assertEqual(benchmark.execution_inputs(args)['oximut_args'], [])
        args.oximut_arg = ['--isolation', 'fresh-thread']
        self.assertEqual(benchmark.command('oximut', args, Path('/p'), Path('/r'), manifest),
                         original + args.oximut_arg)
        self.assertEqual(benchmark.execution_inputs(args)['oximut_args'], args.oximut_arg)

    def test_full_adapter_graph_and_binary_changes_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'oximut'
            binary.write_bytes(b'candidate')
            adapter = root / 'adapter'
            adapter.mkdir()
            (adapter / 'm2-runner.mjs').write_text('import "./worker.ts";')
            (adapter / 'worker.ts').write_text('export const version = 1;')
            (adapter / 'protocol.mjs').write_text('export const MAX_RESPONSE_BYTES = 1024 * 1024;')
            args = SimpleNamespace(binary=binary, adapter=None, adapter_source_dir=adapter)
            before = benchmark.execution_inputs(args)
            self.assertEqual(len(before['adapter_source_sha256']), 3)
            (adapter / 'protocol.mjs').write_text('export const MAX_RESPONSE_BYTES = 1;')
            self.assertNotEqual(before['adapter_source_sha256']['protocol.mjs'],
                                benchmark.execution_inputs(args)['adapter_source_sha256']['protocol.mjs'])
            before = benchmark.execution_inputs(args)
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


class ArgumentValidationTests(unittest.TestCase):
    def rejected(self, *extra):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'out'
            with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as raised:
                benchmark.main(['--output', str(output), *extra])
            self.assertEqual(raised.exception.code, 2)
            # Validation precedes any filesystem side effect.
            self.assertFalse(output.exists())
            return stderr.getvalue()

    def test_non_positive_or_non_finite_timeout_is_rejected(self):
        for value in ('nan', 'inf', '-inf', '0', '-1'):
            with self.subTest(timeout=value):
                self.assertIn('--timeout', self.rejected('--tools', 'vitest', '--timeout', value))

    def test_repeated_tool_is_rejected(self):
        self.assertIn('--tools', self.rejected('--tools', 'vitest', 'vitest'))



class MatchedConcurrencyTests(unittest.TestCase):
    def test_concurrency_range_matches_oximut_workers(self):
        for value in ('0', '-1', '1025'):
            with self.assertRaises(argparse.ArgumentTypeError):
                benchmark.concurrency_arg(value)
        self.assertEqual(benchmark.concurrency_arg('1024'), 1024)

    def test_concurrency_above_usable_cpus_is_refused(self):
        benchmark.require_matched_concurrency(4, available=4)
        with self.assertRaisesRegex(ValueError, 'usable CPUs'):
            benchmark.require_matched_concurrency(5, available=4)


class WorkloadSelectionTests(unittest.TestCase):
    """--workload picks the mutation scope on the one vendored corpus; default is unchanged."""
    VERSIONS = {'node': 'v26.3.1', 'vitest': '5.1.0',
                '@stryker-mutator/core': '10.1.1', '@stryker-mutator/vitest-runner': '10.1.1'}

    def test_default_is_the_original_manifest_with_its_original_sentinel(self):
        self.assertEqual(benchmark.DEFAULT_WORKLOAD, 'colord')
        self.assertEqual(benchmark.manifest_path(), benchmark.CORPUS / 'workload.json')
        manifest = benchmark.load_manifest()
        self.assertEqual(manifest['mutate'], [
            'src/manipulate/invert.ts', 'src/manipulate/lighten.ts', 'src/manipulate/mix.ts',
            'src/manipulate/saturate.ts', 'src/extend.ts'])
        self.assertEqual(manifest['families'], ['arithmetic', 'boolean-literal'])
        self.assertEqual(manifest['sentinel'], {'file': 'src/manipulate/invert.ts',
                                                'original': '255 - rgba.r',
                                                'replacement': '255 + rgba.r'})
        # Stryker's operator exclusions for the default are byte-for-byte what they always were.
        self.assertEqual(benchmark.stryker_excluded_mutations(manifest), [
            'EqualityOperator', 'AssignmentOperator', 'CallExpression', 'ArrayDeclaration',
            'ArrowFunction', 'BlockStatement', 'ConditionalExpression', 'LogicalOperator',
            'MethodExpression', 'ObjectLiteral', 'OptionalChaining', 'Regex', 'StringLiteral',
            'UnaryOperator', 'UpdateOperator'])

    def test_unknown_workload_is_rejected_before_any_side_effect(self):
        import contextlib
        import io
        with self.assertRaises(argparse.ArgumentTypeError):
            benchmark.workload_name('colord-huge')
        with self.assertRaisesRegex(argparse.ArgumentTypeError, 'unknown workload'):
            benchmark.load_manifest('../workload')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'out'
            with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as raised:
                benchmark.main(['--output', str(output), '--tools', 'vitest', '--workload', 'colord-huge'])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn('unknown workload', stderr.getvalue())
            self.assertFalse(output.exists())

    def test_scale_manifest_shares_the_corpus_and_declares_one_family(self):
        default, scale = benchmark.load_manifest('colord'), benchmark.load_manifest('colord-scale')
        self.assertEqual(scale['name'], 'colord-scale')
        self.assertNotIn('extends', scale)
        for key in ('upstreamSha256', 'expectedTests', 'expectedOximutTests', 'upstreamMetadataSha256'):
            self.assertEqual(scale[key], default[key], key)
        benchmark.verify_inputs(benchmark.CORPUS, scale)
        self.assertGreater(len(scale['mutate']), len(default['mutate']))
        self.assertEqual(len(scale['mutate']), len(set(scale['mutate'])))
        self.assertTrue({'src/manipulate/invert.ts'} <= set(scale['mutate']))
        self.assertEqual(scale['sentinel'], default['sentinel'])
        self.assertEqual(scale['families'], ['arithmetic'])
        # Stryker is told to skip the family Oximut is not asked for, so scopes agree.
        self.assertIn('BooleanLiteral', benchmark.stryker_excluded_mutations(scale))
        self.assertNotIn('ArithmeticOperator', benchmark.stryker_excluded_mutations(scale))
        # Every file left out is explained.
        for name in scale['scopeNotes']['excludedFiles']:
            self.assertNotIn(name, scale['mutate'])

    def test_prepared_projects_follow_the_chosen_manifest(self):
        for name, files in (('colord', 5), ('colord-scale', None)):
            with self.subTest(workload=name), tempfile.TemporaryDirectory() as tmp:
                manifest = benchmark.load_manifest(name)
                project = Path(tmp) / 'project'
                benchmark.prepare(project, manifest)
                text = (project / 'stryker.config.mjs').read_text()
                config = json.loads(text[len('export default { ...'):text.index(', jsonReporter')])
                self.assertEqual(config['mutate'], manifest['mutate'])
                self.assertEqual(config['mutator']['excludedMutations'],
                                 benchmark.stryker_excluded_mutations(manifest))
                self.assertEqual('BooleanLiteral' in config['mutator']['excludedMutations'],
                                 name == 'colord-scale')
                if files:
                    self.assertEqual(len(config['mutate']), files)

    def test_oximut_command_uses_the_manifest_scope_and_families(self):
        args = SimpleNamespace(binary=Path('/b'), adapter=None, diagnostic_unrestricted=False)
        for name in benchmark.WORKLOADS:
            manifest = benchmark.load_manifest(name)
            command = benchmark.command('oximut', args, Path('/p'), Path('/r'), manifest)
            self.assertEqual([command[i + 1] for i, v in enumerate(command) if v == '--source'],
                             manifest['mutate'])
            self.assertEqual(command[command.index('--mutators') + 1], ','.join(manifest['families']))

    def test_sentinel_comes_from_the_manifest(self):
        manifest = copy.deepcopy(benchmark.load_manifest('colord'))
        manifest['sentinel'] = {'file': 'src/manipulate/lighten.ts', 'original': 'amount * 100',
                                'replacement': 'amount * 101'}
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'project'
            benchmark.prepare(project, manifest)
            source = (project / 'src/manipulate/lighten.ts').read_text()
            self.assertIn('amount * 100', source)
            sentinel = 'src/manipulate/lighten.ts:' + benchmark.hashlib.sha256(
                source.replace('amount * 100', 'amount * 101', 1).encode()).hexdigest()
            report = Path(tmp) / 'report.json'
            report.write_text('{}')

            def validated(outcome, mutations=None):
                artifact = {'mutations': mutations or {sentinel: {'file': 'src/manipulate/lighten.ts',
                                                                  'outcome': outcome}},
                            'known_killed_sentinel_identity': None}
                with mock.patch.object(benchmark.m6, 'artifact', return_value=artifact), \
                     mock.patch.object(benchmark, 'validate_tests', return_value='digest'):
                    return benchmark.validate('oximut', report, project, {}, manifest)

            self.assertEqual(validated('killed')['known_killed_sentinel_identity'], sentinel)
            with self.assertRaisesRegex(ValueError, 'sentinel mutant was not killed'):
                validated('survived')
            # The hard-coded invert edit is no longer consulted: its mutant alone is not enough.
            invert = (project / 'src/manipulate/invert.ts').read_text()
            invert_id = 'src/manipulate/invert.ts:' + benchmark.hashlib.sha256(
                invert.replace('255 - rgba.r', '255 + rgba.r', 1).encode()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'sentinel mutant was not killed'):
                validated('killed', {invert_id: {'file': 'src/manipulate/invert.ts', 'outcome': 'killed'}})
            manifest['sentinel']['original'] = 'absent-text'
            with self.assertRaisesRegex(ValueError, 'absent from the source'):
                validated('killed')

    def run_main(self, workload=None, tools=('vitest',)):
        def prepare(project, manifest, concurrency=1):
            project.mkdir()
            for name in ('vitest.config.ts', 'stryker.config.mjs', 'package.json'):
                (project / name).write_text('{}')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'out'
            argv = ['--tools', *tools, '--warmups', '1', '--repetitions', '0', '--output', str(output)]
            if workload is not None:
                argv += ['--workload', workload]
            with mock.patch.object(benchmark.measurement, 'metadata',
                                   return_value={'versions': self.VERSIONS}), \
                 mock.patch.object(benchmark, 'prepare', side_effect=prepare), \
                 mock.patch.object(benchmark.measurement, 'run_command', return_value={
                     'wall_seconds': 1., 'cpu_seconds': 1., 'peak_aggregate_rss_bytes': 1.,
                     'peak_process_count': 1}), \
                 mock.patch.object(benchmark, 'validate', return_value={}):
                benchmark.main(argv)
            return (json.loads((output / 'metadata.json').read_text()),
                    json.loads((output / 'summary.json').read_text()),
                    json.loads((output / 'measurements.jsonl').read_text()))

    def test_provenance_covers_the_chosen_manifest(self):
        results = {name: self.run_main(name) for name in benchmark.WORKLOADS}
        default_meta, default_summary, default_row = self.run_main()  # no flag: the default
        self.assertEqual(default_meta['workload_name'], 'colord')
        self.assertEqual(default_summary['workload'], 'colord')
        self.assertEqual(default_row['workload_sha256'], results['colord'][2]['workload_sha256'])
        scale_meta, scale_summary, scale_row = results['colord-scale']
        self.assertEqual(scale_summary['workload'], 'colord-scale')
        self.assertEqual(scale_meta['workload']['name'], 'colord-scale')
        self.assertNotEqual(scale_row['workload_sha256'], default_row['workload_sha256'])
        self.assertEqual(scale_row['workload_sha256'],
                         benchmark.m6.digest(benchmark.load_manifest('colord-scale')))
        self.assertEqual(scale_row['mutator_families'], ['arithmetic'])
        hashes = scale_meta['harness_sha256']
        for path in ('benchmarks/real-project/colord/workload-scale.json',
                     'benchmarks/real-project/colord/workload.json'):
            self.assertEqual(hashes[path], benchmark.measurement.file_hash(benchmark.ROOT / path))
        self.assertNotIn('benchmarks/real-project/colord/workload-scale.json', default_meta['harness_sha256'])
        self.assertIn('benchmarks/real-project/colord/workload.json', default_meta['harness_sha256'])

    def test_editing_the_scale_manifest_changes_its_declared_identity(self):
        scale = benchmark.load_manifest('colord-scale')
        edited = {**scale, 'mutate': scale['mutate'][:-1]}
        self.assertNotEqual(benchmark.m6.digest(scale), benchmark.m6.digest(edited))

    def test_extends_must_name_a_complete_manifest(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(benchmark, 'CORPUS', Path(tmp)):
            (Path(tmp) / 'a.json').write_text(json.dumps({'name': 'colord', 'families': ['arithmetic'],
                                                          'extends': 'b.json'}))
            (Path(tmp) / 'b.json').write_text(json.dumps({'extends': 'a.json'}))
            with mock.patch.dict(benchmark.WORKLOADS, {'colord': 'a.json'}):
                with self.assertRaisesRegex(ValueError, 'complete manifest'):
                    benchmark.load_manifest('colord')


if __name__ == '__main__':
    unittest.main()


class ModeArgumentTests(unittest.TestCase):
    def test_only_opt_in_mode_selections_pass_through(self):
        allowed = [[], ['--isolation', 'fresh-thread'], ['--isolation=fresh-process'],
                   ['--isolation', 'fresh-process', '--isolation=fresh-thread']]
        for values in allowed:
            self.assertIsNone(benchmark.mode_args_error(values), values)
        rejected = [['--adapter', '/tmp/other.mjs'], ['--adapter=/tmp/other.mjs'],
                    ['--root=/elsewhere'], ['--source', 'src/x.ts'], ['--workers', '4'],
                    ['--mutators=all'], ['--isolation'], ['--isolation', 'fork'],
                    ['--isolation=fork'], ['fresh-thread']]
        for values in rejected:
            self.assertIsNotNone(benchmark.mode_args_error(values), values)

    def test_entrypoints_reject_untracked_extra_arguments(self):
        import contextlib
        import io
        import compare_candidates as campaign
        for main, flag in [(benchmark.main, '--oximut-arg'),
                           (campaign.main, '--after-oximut-arg')]:
            stderr = io.StringIO()
            with self.subTest(flag=flag), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    main(['--output', '/nonexistent/out', f'{flag}=--adapter=/tmp/other.mjs',
                          *(['--before-binary', 'b', '--before-adapter-dir', 'd',
                             '--after-binary', 'b', '--after-adapter-dir', 'd',
                             '--after-commit', 'a' * 40] if flag.startswith('--after') else [])])
            # The allowlist, not some later path check, must reject it.
            self.assertIn(f'{flag}: only --isolation may be passed', stderr.getvalue())
