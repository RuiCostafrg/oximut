"""M6 gates: source identity, strict artifacts, paired cache validation and failures."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
SPEC = importlib.util.spec_from_file_location('m6_benchmark', BENCHMARKS / 'm6.py')
m6 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m6)


def location(start, end):
    return {'start': {'line': 1, 'column': start}, 'end': {'line': 1, 'column': end}}


def row(outcomes=None, **extra):
    return {'mutations': {k: {'id': k, 'outcome': v} for k, v in (outcomes or {'a': 'killed'}).items()},
            'tool': 'oximut', 'fixture': 'm1', 'round_index': 0, 'run_label': 'run',
            'scenario': 'clean-fresh', 'role': 'clean', 'warmup': False, 'success': True,
            'wall_seconds': 1., 'cpu_seconds': .5, 'peak_aggregate_rss_bytes': 100,
            'peak_process_count': 2, **extra}


class IdentityTests(unittest.TestCase):
    def test_expression_and_operator_spans_match_exact_edit(self):
        source = 'const x = value < 10;\n'
        a = m6.mutation_identity('a.ts', source, location(11, 21), 'value <= 10')
        b = m6.mutation_identity('a.ts', source, location(17, 18), '<=')
        self.assertEqual(a, b)
        self.assertNotEqual(a, m6.mutation_identity('a.ts', source, location(11, 21), '(value <= 10)'))
        self.assertNotEqual(a, m6.mutation_identity('b.ts', source, location(17, 18), '<='))

    def test_utf16_positions_and_invalid_surrogate_boundary(self):
        self.assertEqual(m6.offset('😀 < 3', {'line': 1, 'column': 4}), 2)
        with self.assertRaises(ValueError):
            m6.offset('😀 < 3', {'line': 1, 'column': 2})
        with self.assertRaises(ValueError):
            m6.offset('a', {'line': 2, 'column': 5})

    def test_same_counts_cannot_hide_swapped_classifications(self):
        comparison = m6.compare(row({'a': 'killed', 'b': 'survived'}), row({'a': 'survived', 'b': 'killed'}))
        self.assertEqual(len(comparison['mismatches']), 2)
        self.assertFalse(comparison['ratio_eligible'])

    def test_partial_overlap_publishes_exclusions_and_suppresses_ratio(self):
        comparison = m6.compare(row({'a': 'killed'}), row({'a': 'killed', 'b': 'survived'}))
        self.assertTrue(comparison['shared_classifications_agree'])
        self.assertEqual(set(comparison['right_only']), {'b'})
        self.assertFalse(comparison['ratio_eligible'])

    def test_uncovered_refinement_requires_explicit_sound_selection_evidence(self):
        first, second = row({'a': 'survived'}), row({'a': 'no-coverage'})
        self.assertFalse(m6.compare(first, second, True)['shared_classifications_agree'])
        second['mutations']['a']['selection'] = {'mode': 'none', 'reasons': ['complete-baseline-without-hits']}
        self.assertTrue(m6.compare(first, second, True)['shared_classifications_agree'])
        self.assertFalse(m6.compare(first, second)['shared_classifications_agree'])


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.report = self.project / 'report.json'
        (self.project / 'subject.ts').write_text('true;\n')
        self.result = {'exit_code': 1, 'timed_out': False}
        self.value = {'schemaVersion': 1, 'baseline': {'outcome': 'passed', 'tests': {'passed': 1, 'failed': 0}},
                      'summary': {'complete': True, 'total': 1}, 'mutants': [
                          {'id': 'x', 'file': str(self.project / 'subject.ts'), 'location': location(1, 5),
                           'replacement': 'false', 'outcome': 'survived', 'tests': {'passed': 1},
                           'operator': 'boolean', 'provenance': {'source': 'executed'}}]}

    def validate(self):
        self.report.write_text(json.dumps(self.value))
        return m6.artifact('oximut', self.report, self.project, self.result)

    def test_complete_survivor_exit_one_is_success_but_timeout_and_cleanup_are_not(self):
        self.assertEqual(self.validate()['mutant_count'], 1)
        for key in ['timed_out', 'descendant_cleanup_required', 'cleanup_error', 'spawn_error']:
            with self.subTest(key=key):
                self.result[key] = True
                with self.assertRaises(ValueError):
                    self.validate()
                self.result.pop(key)

    def test_empty_baseline_incomplete_report_zero_tests_and_duplicate_identity_fail(self):
        self.value['baseline']['tests']['passed'] = 0
        with self.assertRaises(ValueError):
            self.validate()
        self.value['baseline']['tests']['passed'] = 1
        self.value['summary']['complete'] = False
        with self.assertRaises(ValueError):
            self.validate()
        self.value['summary']['complete'] = True
        self.value['mutants'][0]['tests'] = {}
        with self.assertRaises(ValueError):
            self.validate()
        self.value['mutants'][0]['tests'] = {'passed': 1}
        self.value['mutants'].append(dict(self.value['mutants'][0], id='different-id'))
        self.value['summary']['total'] = 2
        with self.assertRaises(ValueError):
            self.validate()

    def test_skipped_only_evidence_and_unjustified_no_coverage_are_rejected(self):
        self.value['mutants'][0]['tests'] = {'passed': 0, 'failed': 0, 'skipped': 1}
        with self.assertRaises(ValueError):
            self.validate()
        self.value['mutants'][0]['outcome'] = 'no-coverage'
        with self.assertRaises(ValueError):
            self.validate()

    def test_ordinary_identities_ignore_private_project_directory(self):
        identities = []
        for name in ('first', 'second'):
            project = self.project / name
            project.mkdir()
            report = project / 'report.json'
            report.write_text(json.dumps({'success': True, 'numTotalTests': 1,
                'numPassedTests': 1, 'testResults': [{'name': str(project / 'test/a.test.ts'),
                    'assertionResults': [{'fullName': 'test', 'status': 'passed'}]}]}))
            identities.append(m6.artifact('vitest', report, project, {'exit_code': 0})
                              ['test_identity_sha256'])
        self.assertEqual(identities[0], identities[1])

    def test_stryker_configured_ignored_operators_are_explicit_scope_exclusions(self):
        value = {'schemaVersion': '1.0', 'config': {'mutator': {'excludedMutations': ['BlockStatement']}},
            'testFiles': {'test/a.test.ts': {'tests': [{'id': '1', 'name': 'test'}]}},
            'files': {'subject.ts': {'source': 'true;\n', 'mutants': [
                {'id': '1', 'status': 'Killed', 'testsCompleted': 1, 'replacement': 'false',
                 'mutatorName': 'BooleanLiteral', 'location': location(1, 5)},
                {'id': '2', 'status': 'Ignored', 'replacement': '{}', 'mutatorName': 'BlockStatement',
                 'statusReason': 'Ignored because of excluded mutation \"BlockStatement\"',
                 'location': location(1, 5)}]}}}
        self.report.write_text(json.dumps(value))
        validated = m6.artifact('stryker', self.report, self.project, {'exit_code': 0})
        self.assertEqual(validated['mutant_count'], 1)
        self.assertEqual(validated['generated_mutant_count'], 2)
        self.assertEqual(validated['explicitly_excluded_mutant_count'], 1)
        self.assertEqual(validated['scope_exclusions'][0]['id'], '2')
        value['files']['subject.ts']['mutants'][1]['statusReason'] = 'unsupported runtime'
        self.report.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            m6.artifact('stryker', self.report, self.project, {'exit_code': 0})

    def test_known_shared_boundary_sentinel_must_be_killed(self):
        project = self.project / 'm1'
        m6.prepare_project(project, 'm1')
        source = project / 'src/subject.ts'
        line = source.read_text().splitlines()[5]
        column = line.index('<') + 1
        self.value['baseline']['tests']['passed'] = 2
        self.value['mutants'][0].update(file=str(source), replacement='<=',
            location={'start': {'line': 6, 'column': column}, 'end': {'line': 6, 'column': column + 1}})
        self.report.write_text(json.dumps(self.value))
        with self.assertRaisesRegex(ValueError, 'sentinel was not killed'):
            m6.artifact('oximut', self.report, project, {'exit_code': 1, 'fixture': 'm1'})
        self.value['mutants'][0].update(outcome='killed', tests={'passed': 1, 'failed': 1})
        self.report.write_text(json.dumps(self.value))
        validated = m6.artifact('oximut', self.report, project, {'exit_code': 0, 'fixture': 'm1'})
        self.assertIn(validated['known_killed_sentinel_identity'], validated['mutations'])

    def test_skipped_baseline_is_rejected(self):
        self.value['baseline']['tests']['skipped'] = 1
        with self.assertRaises(ValueError):
            self.validate()

    def test_resource_failure_never_becomes_kill(self):
        self.value['mutants'][0]['outcome'] = 'resource-exhausted'
        with self.assertRaises(ValueError):
            self.validate()


class GateTests(unittest.TestCase):
    def test_failure_or_mismatch_blocks_every_ratio(self):
        rows = [row(run_label='fresh'), row(run_label='persistent', scenario='clean-persistent', success=False)]
        gate, _, ratios = m6.gates_and_ratios(rows, m6.summarize(rows))
        self.assertFalse(gate['passed'])
        self.assertFalse(ratios[0]['eligible'])
        self.assertNotIn('wall_median_ratio', ratios[0])

    def test_unequal_cross_tool_scope_is_diagnostic_and_never_speedup(self):
        rows = [row(run_label='fresh'), row({'a': 'killed', 'extra': 'survived'},
                    tool='stryker', run_label='stryker', scenario='clean-stryker')]
        gate, comparisons, ratios = m6.gates_and_ratios(rows, m6.summarize(rows))
        self.assertTrue(gate['passed'])
        self.assertEqual(comparisons[0]['shared_count'], 1)
        self.assertFalse(ratios[0]['eligible'])

    def test_cache_pair_requires_equal_identity_and_classification(self):
        for changed in ({'a': 'survived'}, {'a': 'killed', 'b': 'killed'}):
            rows = [row(run_label='forced', role='forced', scenario='incremental-source'),
                    row(changed, run_label='cached', role='incremental', scenario='incremental-source')]
            gate, comparisons, _ = m6.gates_and_ratios(rows, m6.summarize(rows))
            self.assertFalse(gate['passed'])
            self.assertEqual(comparisons[0]['kind'], 'cache-forced')

    def test_mutation_stability_cannot_hide_changed_test_identity(self):
        rows = [row(mutation_identity_outcome_sha256='same', test_identity_sha256='a'),
                row(mutation_identity_outcome_sha256='same', test_identity_sha256='b')]
        self.assertFalse(m6.summarize(rows)[0]['outcomes_stable'])

    def test_stability_includes_warmup_and_equal_count_swaps(self):
        rows = [row(warmup=True, mutation_identity_outcome_sha256='a'),
                row(mutation_identity_outcome_sha256='b')]
        self.assertFalse(m6.summarize(rows)[0]['outcomes_stable'])


class CacheEvidenceTests(unittest.TestCase):
    def test_policy_arguments_distinguish_legacy_and_explicit_reuse(self):
        args = SimpleNamespace(binary=Path('/binary'), workers=1, memory_budget_mib=None,
                               cache_policy='legacy')
        legacy = m6.command(args, 'oximut', 'm1', Path('/project'), Path('/report'),
                            'selection', cache=True)
        self.assertNotIn('--cache-policy', legacy)
        args.cache_policy = 'always'
        explicit = m6.command(args, 'oximut', 'm1', Path('/project'), Path('/report'),
                              'selection', cache=True)
        self.assertEqual(explicit[explicit.index('--cache-policy') + 1], 'always')

    def test_adaptive_bypass_requires_explicit_policy_and_entirely_fresh_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            cached = row(role='incremental', mutant_count=2, cache_prime_validated=True,
                         cache_provenance_count=0, change={'kind': 'unchanged'},
                         cache={'enabled': True, 'outcomesEnabled': False, 'outcomeHits': 0,
                                'reason': 'outcome-cache-cost-exceeds-fresh-work',
                                'fingerprintProfiles': []})
            with self.assertRaises(ValueError):
                m6.validate_oximut_cache(cached, Path(temporary), False, 'always')
            m6.validate_oximut_cache(cached, Path(temporary), False, 'auto')
            self.assertTrue(cached['cache_policy_bypass_verified'])
            self.assertIsNone(cached['cache_invalidation_verified'])
            for field, value in [('cache_prime_validated', False), ('cache_provenance_count', 1)]:
                altered = dict(cached, **{field: value})
                with self.assertRaises(ValueError):
                    m6.validate_oximut_cache(altered, Path(temporary), False, 'auto')
            for field, value in [('outcomeHits', 1), ('outcomesEnabled', True),
                                 ('fingerprintProfiles', [{'phase': 'prepare'}])]:
                altered = dict(cached, cache={**cached['cache'], field: value})
                with self.assertRaises(ValueError):
                    m6.validate_oximut_cache(altered, Path(temporary), False, 'auto')

    def test_disabled_or_unprimed_cache_cannot_prove_invalidation(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            cached = row(role='incremental', mutant_count=1, change={'kind': 'source'},
                         cache={'outcomesEnabled': False, 'outcomeHits': 0, 'outcomeMisses': 1})
            with self.assertRaises(ValueError):
                m6.validate_oximut_cache(cached, project, False)
            cached['cache']['outcomesEnabled'] = True
            with self.assertRaises(ValueError):
                m6.validate_oximut_cache(cached, project, False)
            cached['cache_prime_validated'] = True
            m6.validate_oximut_cache(cached, project, False)
            self.assertTrue(cached['cache_invalidation_verified'])
            cached['cache']['outcomeMisses'] = 0
            with self.assertRaises(ValueError):
                m6.validate_oximut_cache(cached, project, False)

    def test_prime_requires_current_complete_persisted_outcome_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            prime = row(role='prime', report_run_id='current', mutant_count=1,
                cache={'outcomesEnabled': True, 'outcomeHits': 0, 'outcomeMisses': 1, 'fingerprint': 'fingerprint'})
            with self.assertRaises(OSError):
                m6.validate_oximut_cache(prime, project, False)
            path = project / '.oximut/cache/outcomes/fingerprint.json'
            path.parent.mkdir(parents=True)
            value = {'complete': True, 'fingerprint': 'fingerprint', 'runId': 'old', 'results': [{}]}
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                m6.validate_oximut_cache(prime, project, False)
            value['runId'] = 'current'
            path.write_text(json.dumps(value))
            m6.validate_oximut_cache(prime, project, False)
            self.assertIn('cache_prime_publication_sha256', prime)

    def test_unchanged_requires_all_outcomes_reused_force_requires_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            cached = row(role='incremental', mutant_count=2, cache_prime_validated=True,
                         change={'kind': 'unchanged'}, cache={'outcomesEnabled': True, 'outcomeHits': 1})
            with self.assertRaises(ValueError):
                m6.validate_oximut_cache(cached, Path(temporary), False)
            cached['cache']['outcomeHits'] = 2
            m6.validate_oximut_cache(cached, Path(temporary), False)
            with self.assertRaises(ValueError):
                m6.validate_oximut_cache(cached, Path(temporary), True)


    def test_stryker_unchanged_requires_reuse_but_changed_agreement_is_not_invalidation(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'project'
            project.mkdir()
            args = SimpleNamespace(binary=Path('/unused'), workers=1, memory_budget_mib=None,
                                   timeout=1, sample_interval=.02, output_limit=10000)
            def fake_run(command, cwd, log_path, *unused):
                log_path.write_text('no reuse evidence')
                return {'exit_code': 0, 'timed_out': False, 'success': True}
            env = {'OXIMUT_FIXTURE_MODE': 'fixture', 'OXIMUT_BENCH_INPUT': 'original'}
            with mock.patch.object(m6.measurement, 'run_command', side_effect=fake_run), \
                 mock.patch.object(m6, 'artifact', return_value={'mutant_count': 1}):
                unchanged = m6.measured_run(args, 'stryker', 'm1', project, Path(temporary) / 'unchanged',
                    'selection', env, cache=True, role='incremental', cache_prime_validated=True,
                    change={'kind': 'unchanged'})
                self.assertFalse(unchanged['success'])
                self.assertIn('no explicit outcome reuse', unchanged['validation_error'])
                changed = m6.measured_run(args, 'stryker', 'm1', project, Path(temporary) / 'changed',
                    'selection', env, cache=True, role='incremental', cache_prime_validated=True,
                    change={'kind': 'environment'})
                self.assertTrue(changed['success'])
                self.assertIsNone(changed['cache_invalidation_verified'])


class WorkflowTests(unittest.TestCase):
    def test_cache_pair_holds_report_and_environment_identity_stable_and_archives_fresh_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'project'
            project.mkdir()
            args = SimpleNamespace(binary=Path('/unused'), workers=1, memory_budget_mib=None,
                                   timeout=1, sample_interval=.02, output_limit=10000)
            calls = []
            def fake_run(command, cwd, log_path, timeout, interval, limit, environment):
                destination = Path(command[command.index('--report') + 1])
                self.assertFalse(destination.exists(), 'prior report must be removed before invocation')
                calls.append((str(destination), dict(environment)))
                destination.write_text(json.dumps({'invocation': len(calls)}))
                log_path.write_text('')
                return {'exit_code': 0, 'timed_out': False, 'success': True}
            env = {'OXIMUT_FIXTURE_MODE': 'fixture', 'OXIMUT_BENCH_INPUT': 'original'}
            with mock.patch.object(m6.measurement, 'run_command', side_effect=fake_run), \
                 mock.patch.object(m6, 'artifact', return_value={'mutant_count': 1}), \
                 mock.patch.object(m6, 'validate_oximut_cache'):
                for role in ('prime', 'incremental', 'forced'):
                    directory = Path(temporary) / role
                    result = m6.measured_run(args, 'oximut', 'm1', project, directory,
                        'selection', env, cache=True, force=role == 'forced', role=role)
                    self.assertTrue(result['success'])
                    self.assertEqual(json.loads((directory / 'report.json').read_text()),
                                     {'invocation': len(calls)})
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(calls[1], calls[2])

    def test_fake_workflow_records_primes_pairs_order_changes_and_cleans_projects(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / '.oximut' / 'result'
            binary = Path(temporary) / 'fake-oximut'
            binary.write_text('#!/bin/sh\n# mocked benchmark executable\n')
            binary.chmod(0o755)
            calls = []
            def fake_run(args, tool, fixture, project, run_dir, mode, env, **labels):
                calls.append((labels['round_index'], labels['scenario'], labels['role'], env['OXIMUT_BENCH_INPUT']))
                if labels.get('change', {}).get('kind') == 'source':
                    self.assertIn('M6 deterministic fingerprint edit', (project / 'src/subject.ts').read_text())
                return row(**labels, tool=tool, fixture=fixture, mutation_identity_outcome_sha256='stable')
            with mock.patch.object(m6.measurement, 'metadata', return_value={'input_sha256': {}}), \
                 mock.patch.object(m6, 'measured_run', side_effect=fake_run):
                code = m6.main(['--output', str(output), '--binary', str(binary),
                                '--fixtures', 'm1', '--tools', 'oximut',
                                '--scenarios', 'incremental', '--changes', 'source', 'environment',
                                '--repetitions', '2', '--warmups', '1'])
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 27)  # prime + incremental + forced, 3 inputs, 3 rounds
            self.assertEqual([c[2] for c in calls if c[:2] == (0, 'incremental-unchanged')],
                             ['prime', 'incremental', 'forced'])
            self.assertEqual([c[2] for c in calls if c[:2] == (1, 'incremental-unchanged')],
                             ['prime', 'forced', 'incremental'])
            self.assertTrue(any(c[3] == 'changed' for c in calls))
            self.assertFalse(list(output.glob('*/project')))
            self.assertTrue(list(output.glob('*/project-inputs.json')))
            self.assertIn('src/subject.ts', json.loads(next(output.glob('*/project-inputs.json')).read_text()))
            self.assertEqual(len((output / 'measurements.jsonl').read_text().splitlines()), 27)
            self.assertTrue(json.loads((output / 'summary.json').read_text())['correctness_gate']['passed'])
            self.assertEqual(json.loads((output / 'metadata.json').read_text())['arguments']['repetitions'], 2)
            with self.assertRaises(FileExistsError):
                m6.main(['--output', str(output), '--tools', 'vitest', '--scenarios', 'clean'])

    def test_every_declared_changed_input_is_a_content_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'project'
            m6.prepare_project(project, 'm1')
            env = {'OXIMUT_BENCH_INPUT': 'original'}
            for change in m6.CHANGES:
                with self.subTest(change=change):
                    evidence = m6.apply_change(project, 'm1', change, env)
                    if change != 'environment':
                        self.assertNotEqual(evidence['before_sha256'], evidence['after_sha256'])
                    else:
                        self.assertEqual(env['OXIMUT_BENCH_INPUT'], 'changed')
            json.loads((project / 'package-lock.json').read_text())


if __name__ == '__main__':
    unittest.main()
