"""The multi-project benchmark must match scope honestly, reject unusable runs and report both tools' evidence."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import multi_project as bench
import multi_project_report as report

SOURCE = 'export const a = 1 + 2;\nexport const b = 3 < 4;\n'


def location(line, column, end_column):
    return {'start': {'offset': 0, 'line': line, 'column': column}, 'end': {'offset': 0, 'line': line, 'column': end_column}}


def oximut_report(project, outcomes, complete=True, baseline='passed', workers=2, filename='src/a.ts'):
    """An Oximut report with one mutant per outcome: `1 + 2` -> `1 - 2` on line 1, `3 < 4` -> `3 <= 4` on line 2."""
    edits = [(location(1, 20, 21), '-'), (location(2, 20, 21), '<=')]
    mutants = [{'id': f'm{index}', 'file': str(project / filename), 'operator': 'arithmetic',
                'replacement': edit, 'location': place, 'outcome': outcome}
               for index, (outcome, (place, edit)) in enumerate(zip(outcomes, edits))]
    return {'summary': {'complete': complete, 'total': len(mutants)}, 'baseline': {'outcome': baseline, 'tests': {'passed': 3}},
            'mutants': mutants, 'diagnostics': ['zygote=on', 'rolling-lanes=on'],
            'resources': {'effectiveWorkers': workers, 'admissionPauses': 0, 'phaseWallMs': {'mutationExecution': 800}}}


def stryker_report(statuses, source=SOURCE, filename='src/a.ts'):
    edits = [(location(1, 20, 21), '-'), (location(2, 20, 21), '<=')]
    mutants = [{'id': str(index), 'mutatorName': 'ArithmeticOperator', 'status': status,
                'replacement': edit, 'location': place}
               for index, (status, (place, edit)) in enumerate(zip(statuses, edits))]
    return {'files': {filename: {'source': source, 'mutants': mutants}},
            'testFiles': {'test/a.test.ts': {'tests': [{'name': 'one'}, {'name': 'two'}]}}}


def clean_row(**overrides):
    row = {'exit_code': 0, 'timed_out': False, 'descendant_cleanup_required': False, 'memory_errors': [],
           'wall_seconds': 1., 'cpu_seconds': 1., 'peak_aggregate_rss_bytes': 1, 'peak_process_count': 1}
    row.update(overrides)
    return row


class ProjectCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.project = self.directory / 'project'
        (self.project / 'src').mkdir(parents=True)
        (self.project / 'src/a.ts').write_text(SOURCE)

    def write(self, name, text=''):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class ScopeTests(ProjectCase):
    def test_globs_resolve_to_sorted_files_minus_exclusions(self):
        for name in ('src/b/x.ts', 'src/a/y.ts', 'src/a/y.test.ts', 'src/a/index.ts', 'src/z.ts'):
            self.write(name)
        spec = {'name': 'p', 'mutate': ['src/**/*.ts'], 'mutateExclude': ['*.test.ts', '*/index.ts']}
        self.assertEqual(bench.resolve_mutate(spec, self.project),
                         ['src/a.ts', 'src/a/y.ts', 'src/b/x.ts', 'src/z.ts'])

    def test_a_pattern_matching_nothing_is_an_error_not_a_smaller_scope(self):
        with self.assertRaisesRegex(bench.BenchmarkError, 'matches no file'):
            bench.resolve_mutate({'name': 'p', 'mutate': ['src/a.ts', 'src/missing/*.ts']}, self.project)
        with self.assertRaisesRegex(bench.BenchmarkError, 'matches no file'):
            bench.resolve_mutate({'name': 'p', 'mutate': ['src/nope.ts']}, self.project)

    def test_exclusions_that_remove_everything_are_an_error(self):
        with self.assertRaisesRegex(bench.BenchmarkError, 'empty'):
            bench.resolve_mutate({'name': 'p', 'mutate': ['src/a.ts'], 'mutateExclude': ['src/*']}, self.project)

    def test_matched_scope_keeps_only_the_operators_oximut_generates(self):
        excluded = set(bench.stryker_excluded('matched'))
        self.assertTrue({'ArithmeticOperator', 'BooleanLiteral', 'EqualityOperator'}.isdisjoint(excluded))
        self.assertIn('StringLiteral', excluded)
        self.assertIn('ConditionalExpression', excluded)
        self.assertEqual(bench.stryker_excluded('full'), [])

    def test_stryker_is_configured_for_the_matched_concurrency_and_per_test_coverage(self):
        matched = bench.stryker_config(['src/a.ts'], 4, 'matched')
        self.assertEqual((matched['concurrency'], matched['coverageAnalysis'], matched['testRunner']),
                         (4, 'perTest', 'vitest'))
        self.assertEqual(matched['mutate'], ['src/a.ts'])
        self.assertFalse(matched['vitest']['related'])
        self.assertIn('StringLiteral', matched['mutator']['excludedMutations'])
        self.assertNotIn('mutator', bench.stryker_config(['src/a.ts'], 2, 'full'))
        self.assertTrue(bench.stryker_config(['src/a.ts'], 2, 'full', related=True)['vitest']['related'])

    def test_oximut_runs_with_its_defaults_and_the_declared_scope(self):
        command = bench.tool_command('oximut', Path('/bin/oximut'), self.project, ['src/a.ts', 'src/b.ts'], 3,
                                     self.directory / 'r.json')
        self.assertEqual(command[:7], ['/bin/oximut', '--root', str(self.project), '--config', bench.CONFIG_NAME,
                                       '--workers', '3'])
        self.assertEqual([command[i + 1] for i, word in enumerate(command) if word == '--source'],
                         ['src/a.ts', 'src/b.ts'])
        for flag in ('--execution', '--zygote', '--no-zygote', '--test-selection', '--no-test-selection',
                     '--rolling-lanes', '--isolation', '--mutators'):
            self.assertNotIn(flag, command)


class CorpusTests(unittest.TestCase):
    def test_corpus_has_the_parity_projects_and_two_large_ones_pinned_to_commits(self):
        projects = bench.load_projects()
        kinds = {project['name']: project['kind'] for project in projects}
        self.assertEqual({name for name, kind in kinds.items() if kind == 'startup'},
                         {'ufo', 'pathe', 'hookable', 'std-env', 'tslog'})
        self.assertEqual({name for name, kind in kinds.items() if kind == 'large'}, {'es-toolkit', 'es-toolkit-no-globalthis'})
        for project in projects:
            self.assertEqual(len(project['commit']), 40)
            self.assertTrue(Path(project['configSource']).is_file(), project['name'])

    def test_unknown_project_names_are_rejected(self):
        projects = bench.load_projects()
        with self.assertRaisesRegex(bench.BenchmarkError, 'unknown projects'):
            bench.select_projects(projects, ['ufo', 'nope'])
        self.assertEqual([p['name'] for p in bench.select_projects(projects, ['tslog', 'ufo'])], ['ufo', 'tslog'])
        self.assertEqual(len(bench.select_projects(projects, [])), len(projects))

    def test_worker_counts_and_matrix(self):
        self.assertEqual(bench.parse_workers('2, 4 8 4'), [2, 4, 8])
        for bad in ('', '0', 'two', '2 -1', '2000'):
            with self.assertRaises(bench.BenchmarkError):
                bench.parse_workers(bad)
        matrix = bench.plan_matrix(bench.select_projects(bench.load_projects(), ['ufo', 'es-toolkit']), [2, 4])
        self.assertEqual([(c['project'], c['kind'], c['workers']) for c in matrix['include']],
                         [('ufo', 'startup', 2), ('ufo', 'startup', 4), ('es-toolkit', 'large', 2), ('es-toolkit', 'large', 4)])


class PrepareTests(ProjectCase):
    def test_copy_list_limits_the_private_project_and_adds_config_and_dependencies(self):
        self.write('docs/big.md', 'docs')
        self.write('package.json', '{}')
        self.write('src/node_modules/skipped.js', 'x')
        config = self.directory / 'config.ts'
        config.write_text('export default {};')
        destination = self.directory / 'run'
        bench.prepare_run({'name': 'p', 'copy': ['src', 'package.json'], 'configSource': str(config)},
                          self.project, destination)
        self.assertTrue((destination / 'src/a.ts').is_file())
        self.assertTrue((destination / 'package.json').is_file())
        self.assertFalse((destination / 'docs').exists())
        self.assertFalse((destination / 'src/node_modules').exists())
        self.assertEqual((destination / bench.CONFIG_NAME).read_text(), 'export default {};')
        self.assertTrue((destination / 'node_modules').is_symlink())

    def test_tsconfig_files_are_left_out_because_stryker_cannot_rewrite_them_under_typescript_7(self):
        self.write('tsconfig.json', '{}')
        self.write('tsconfig.base.json', '{}')
        self.write('src/tsconfig.json', '{}')
        config = self.directory / 'config.ts'
        config.write_text('')
        destination = self.directory / 'run'
        bench.prepare_run({'name': 'p', 'copy': None, 'configSource': str(config)}, self.project, destination)
        self.assertTrue((destination / 'src/a.ts').is_file())
        self.assertEqual([p.name for p in destination.rglob('tsconfig*.json')], [])

    def test_a_declared_copy_path_missing_from_the_checkout_is_an_error(self):
        config = self.directory / 'config.ts'
        config.write_text('')
        with self.assertRaisesRegex(bench.BenchmarkError, 'absent'):
            bench.prepare_run({'name': 'p', 'copy': ['nothing'], 'configSource': str(config)},
                              self.project, self.directory / 'run')


class OutcomeTests(ProjectCase):
    def test_both_tools_give_the_same_edit_the_same_identity(self):
        oximut = bench.oximut_outcomes(oximut_report(self.project, ['killed', 'survived']), self.project)
        stryker, ignored, duplicates = bench.stryker_outcomes(stryker_report(['Killed', 'Survived']), self.project)
        self.assertEqual(oximut, stryker)
        self.assertEqual((ignored, duplicates), (0, 0))

    def test_stryker_statuses_map_and_ignored_mutants_are_not_counted(self):
        outcomes, ignored, _ = bench.stryker_outcomes(stryker_report(['Timeout', 'Ignored']), self.project)
        self.assertEqual(list(outcomes.values()), ['timeout'])
        self.assertEqual(ignored, 1)
        outcomes, _, _ = bench.stryker_outcomes(stryker_report(['NoCoverage', 'RuntimeError']), self.project)
        self.assertEqual(sorted(outcomes.values()), ['invalid', 'no-coverage'])

    def test_unknown_stryker_status_is_rejected(self):
        with self.assertRaisesRegex(bench.BenchmarkError, 'unknown Stryker mutant status'):
            bench.stryker_outcomes(stryker_report(['Exploded', 'Killed']), self.project)

    def test_two_stryker_operators_making_one_edit_are_both_kept(self):
        data = stryker_report(['Killed', 'Survived'])
        data['files']['src/a.ts']['mutants'][1]['location'] = location(1, 20, 21)
        data['files']['src/a.ts']['mutants'][1]['replacement'] = '-'
        outcomes, _, duplicates = bench.stryker_outcomes(data, self.project)
        self.assertEqual((len(outcomes), duplicates), (2, 1))

    def test_detection_rate_counts_timeouts_as_detected_and_ignores_invalid(self):
        counts = {'killed': 6, 'timeout': 2, 'survived': 1, 'no-coverage': 1, 'invalid': 5}
        self.assertEqual(bench.detection_rate(counts), 0.8)
        self.assertIsNone(bench.detection_rate({'invalid': 3}))

    def test_scope_comparison_separates_overlap_from_disagreement(self):
        oximut = {'a': 'killed', 'b': 'survived', 'c': 'killed', 'only-o': 'killed'}
        stryker = {'a': 'timeout', 'b': 'no-coverage', 'c': 'survived', 'only-s': 'killed'}
        result = bench.compare_scope(oximut, stryker)
        self.assertEqual((result['shared'], result['oximut_only'], result['stryker_only']), (3, 1, 1))
        # killed/timeout and survived/no-coverage agree; killed/survived does not.
        self.assertEqual((result['shared_agree'], result['shared_disagree']), (2, 1))
        self.assertEqual(result['disagreements'], [{'identity': 'c', 'oximut': 'killed', 'stryker': 'survived'}])

    def test_stryker_log_start_up_and_mutation_phase_timing_is_parsed_or_left_absent(self):
        log = ('\x1b[32m17:15:45 (3032) INFO DryRunExecutor\x1b[39m Initial test run succeeded. Ran 489 tests in 1 second '
               '(net 96.8 ms, overhead 1416.2 ms).\n'
               '17:15:53 (3032) INFO MutationTestExecutor Done in 1 minute 10 seconds.')
        self.assertEqual(bench.parse_stryker_log(log),
                         {'initial_run_seconds': 1.0, 'initial_run_net_ms': 96.8, 'initial_run_overhead_ms': 1416.2,
                          'mutation_phase_seconds': 70})
        self.assertEqual(set(bench.parse_stryker_log('nothing useful').values()), {None})


class AnalyzeTests(ProjectCase):
    def write_report(self, data):
        path = self.directory / 'report.json'
        path.write_text(json.dumps(data))
        return path

    def test_a_complete_oximut_run_is_recorded_with_outcomes_and_phase_timing(self):
        path = self.write_report(oximut_report(self.project, ['killed', 'survived']))
        extra = bench.analyze('oximut', clean_row(exit_code=1), path, self.project, 2)
        self.assertEqual(sorted(extra['outcomes'].values()), ['killed', 'survived'])
        self.assertTrue(extra['complete'])
        self.assertEqual(extra['phase_wall_ms'], {'mutationExecution': 800})

    def test_oximut_selection_fallbacks_are_recorded_with_their_reasons(self):
        data = oximut_report(self.project, ['killed', 'survived'])
        data['mutants'][0]['selection'] = {'mode': 'full', 'reasons': ['probe-globalThis-conflict', 'incomplete-attribution']}
        data['mutants'][1]['selection'] = {'mode': 'files', 'reasons': []}
        extra = bench.analyze('oximut', clean_row(exit_code=1), self.write_report(data), self.project, 2)
        self.assertEqual(extra['selection_modes'], {'files': 1, 'full': 1})
        self.assertEqual(extra['full_suite_reasons'], {'incomplete-attribution': 1, 'probe-globalThis-conflict': 1})

    def test_an_oximut_run_with_timeouts_is_complete(self):
        # A timeout is a detection (StrykerJS semantics): Oximut reports the run complete.
        path = self.write_report(oximut_report(self.project, ['killed', 'timeout']))
        extra = bench.analyze('oximut', clean_row(), path, self.project, 2)
        self.assertTrue(extra['complete'])
        self.assertEqual(sorted(extra['outcomes'].values()), ['killed', 'timeout'])

    def test_an_oximut_run_with_invalid_mutants_is_kept_but_marked_incomplete(self):
        path = self.write_report(oximut_report(self.project, ['killed', 'invalid'], complete=False))
        extra = bench.analyze('oximut', clean_row(exit_code=2), path, self.project, 2)
        self.assertFalse(extra['complete'])

    def test_engine_failures_make_a_run_unusable(self):
        path = self.write_report(oximut_report(self.project, ['killed', 'infrastructure-error']))
        with self.assertRaisesRegex(bench.BenchmarkError, 'engine failure'):
            bench.analyze('oximut', clean_row(), path, self.project, 2)

    def test_a_failed_baseline_makes_a_run_unusable(self):
        path = self.write_report(oximut_report(self.project, ['killed', 'killed'], baseline='failed'))
        with self.assertRaisesRegex(bench.BenchmarkError, 'baseline'):
            bench.analyze('oximut', clean_row(), path, self.project, 2)

    def test_fewer_effective_workers_than_matched_is_not_a_matched_run(self):
        path = self.write_report(oximut_report(self.project, ['killed', 'killed'], workers=1))
        with self.assertRaisesRegex(bench.BenchmarkError, 'matched 2'):
            bench.analyze('oximut', clean_row(), path, self.project, 2)

    def test_memory_pressure_pauses_keep_the_run_but_are_recorded(self):
        data = oximut_report(self.project, ['killed', 'killed'])
        data['resources']['admissionPauses'] = 5
        extra = bench.analyze('oximut', clean_row(), self.write_report(data), self.project, 2)
        self.assertEqual(extra['admission_pauses'], 5)

    def test_timeouts_and_failed_commands_are_rejected(self):
        path = self.write_report(stryker_report(['Killed', 'Killed']))
        for override in ({'timed_out': True}, {'exit_code': 1}, {'descendant_cleanup_required': True},
                         {'memory_errors': ['bad']}):
            with self.assertRaises(bench.BenchmarkError, msg=override):
                bench.analyze('stryker', clean_row(**override), path, self.project, 2)
        with self.assertRaises(bench.BenchmarkError):
            bench.analyze('oximut', clean_row(exit_code=3), path, self.project, 2)

    def test_stryker_run_reports_test_count_and_outcomes(self):
        path = self.write_report(stryker_report(['Killed', 'Survived']))
        extra = bench.analyze('stryker', clean_row(), path, self.project, 2)
        self.assertEqual(extra['tests'], 2)
        self.assertEqual(sorted(extra['outcomes'].values()), ['killed', 'survived'])

    def test_vitest_baseline_must_pass_every_test(self):
        path = self.write_report({'numTotalTests': 5, 'numFailedTests': 1})
        with self.assertRaisesRegex(bench.BenchmarkError, 'failed'):
            bench.analyze('vitest', clean_row(), path, self.project, 2)
        path = self.write_report({'numTotalTests': 5, 'numFailedTests': 0})
        self.assertEqual(bench.analyze('vitest', clean_row(), path, self.project, 2), {'tests': 5})

    def test_a_missing_report_is_unusable(self):
        with self.assertRaisesRegex(bench.BenchmarkError, 'no readable report'):
            bench.analyze('stryker', clean_row(), self.directory / 'absent.json', self.project, 2)


class OversubscriptionTests(ProjectCase):
    def run_main(self, *extra):
        output = self.directory / 'cell'
        with mock.patch.object(bench, 'usable_cpus', return_value=4), \
                mock.patch.object(bench, 'select_projects', return_value=[bench.load_projects()[0]]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = bench.main(['--projects', 'ufo', '--workers', '8', '--output', str(output),
                               '--tools', 'vitest', *extra])
        return code, output

    def test_more_workers_than_cpus_is_recorded_as_skipped_never_run(self):
        code, output = self.run_main('--skip-oversubscribed')
        self.assertEqual(code, 0)
        cell = json.loads((output / 'cell.json').read_text())
        self.assertEqual(cell['status'], 'skipped')
        self.assertEqual(cell['runs'], [])
        self.assertIn('4 usable CPUs', cell['error'])

    def test_without_the_skip_flag_oversubscription_fails(self):
        code, _ = self.run_main()
        self.assertEqual(code, 1)


def run(tool, round_index, wall, cpu=1., rss=100, sha='abc', counts=None, warmup=False, **extra):
    base = {'tool': tool, 'round': round_index, 'warmup': warmup, 'valid': True, 'wall_seconds': wall,
            'cpu_seconds': cpu, 'peak_aggregate_rss_bytes': rss, 'peak_process_count': 4, 'exit_code': 0}
    if tool != 'vitest':
        counts = counts or {'killed': 8, 'survived': 1, 'no-coverage': 1}
        base.update(mutants=sum(counts.values()), counts=counts, outcome_sha256=sha)
    if tool == 'oximut':
        base.update(complete=True, phase_wall_ms={'mutationExecution': 500}, diagnostics=['zygote=on'])
    if tool == 'stryker':
        base.update(log={'initial_run_seconds': 3.0, 'mutation_phase_seconds': 8.0})
    base.update(extra)
    return base


def cell(runs, kind='large', name='demo', workers=4, status='ok', **extra):
    result = {'schemaVersion': 1, 'status': status, 'error': extra.pop('error', None), 'scope': 'matched',
              'project': {'name': name, 'kind': kind, 'repository': 'https://example.test/demo',
                          'commit': 'a' * 40, 'description': 'demo'},
              'workers': workers, 'machine': {'cpu_model': 'Test CPU 9000', 'usable_cpus': 4}, 'runs': runs,
              'scope_comparison': {'oximut_mutants': 10, 'stryker_mutants': 12, 'shared': 8, 'oximut_only': 2,
                                   'stryker_only': 4, 'shared_agree': 8, 'shared_disagree': 0, 'disagreements': []}}
    result.update(extra)
    return result


def paired_runs(oximut_walls, stryker_walls, warmup_pair=True, **kwargs):
    runs = []
    if warmup_pair:
        runs += [run('oximut', 0, 99., warmup=True), run('stryker', 0, 99., warmup=True)]
    for index, (o, s) in enumerate(zip(oximut_walls, stryker_walls), start=1):
        runs += [run('vitest', index, 2.), run('oximut', index, o, **kwargs.get('oximut', {})),
                 run('stryker', index, s, **kwargs.get('stryker', {}))]
    return runs


class ReportTests(unittest.TestCase):
    def test_ratios_are_paired_per_round_and_warmups_do_not_count(self):
        summary = report.summarize_cell(cell(paired_runs([4., 5., 6.], [10., 10., 10.])))
        wall = summary['ratios']['wall']
        self.assertAlmostEqual(wall['median'], 0.5)
        self.assertEqual(wall['rounds'], 3)
        self.assertAlmostEqual(wall['low'], 0.4)
        self.assertAlmostEqual(wall['high'], 0.6)
        self.assertEqual(summary['oximut']['wall_seconds'], 5.)
        self.assertEqual(summary['stryker']['wall_seconds'], 10.)
        self.assertEqual(summary['vitest']['wall_seconds'], 2.)

    def test_cpu_and_memory_ratios_use_their_own_measurements(self):
        runs = [run('oximut', 1, 4., cpu=6., rss=300), run('stryker', 1, 8., cpu=12., rss=1200)]
        ratios = report.summarize_cell(cell(runs))['ratios']
        self.assertAlmostEqual(ratios['cpu']['median'], 0.5)
        self.assertAlmostEqual(ratios['rss']['median'], 0.25)

    def test_a_ratio_pairs_only_rounds_where_both_tools_are_valid(self):
        runs = paired_runs([4., 5., 6.], [10., 10., 10.], warmup_pair=False)
        runs[4]['valid'] = False  # round 2 oximut
        wall = report.summarize_cell(cell(runs))['ratios']['wall']
        self.assertEqual(wall['rounds'], 2)

    def test_slower_than_stryker_and_missing_the_target_are_flagged(self):
        slow = report.summarize_cell(cell(paired_runs([12., 12., 12.], [10., 10., 10.])))
        self.assertIn('Oximut slower than Stryker', slow['flags'])
        middling = report.summarize_cell(cell(paired_runs([7., 7., 7.], [10., 10., 10.])))
        self.assertEqual([f for f in middling['flags'] if 'target' in f], ['wall ratio above the 0.5x target'])
        fast = report.summarize_cell(cell(paired_runs([4., 4., 4.], [10., 10., 10.])))
        self.assertEqual(fast['flags'], [])

    def test_a_large_score_gap_is_flagged(self):
        stryker = {'killed': 5, 'survived': 3, 'no-coverage': 2}
        summary = report.summarize_cell(cell(paired_runs([4.] * 3, [10.] * 3, stryker={'counts': stryker})))
        self.assertTrue(any(flag.startswith('score gap +30.0 pp') for flag in summary['flags']), summary['flags'])

    def test_the_score_gap_flag_says_whether_the_shared_mutants_agree(self):
        stryker = {'killed': 5, 'survived': 3, 'no-coverage': 2}
        summary = report.summarize_cell(cell(paired_runs([4.] * 3, [10.] * 3, stryker={'counts': stryker})))
        self.assertTrue(any('the 8 shared mutants: 8 same verdict' in flag for flag in summary['flags']), summary['flags'])

    def test_admission_pauses_are_flagged(self):
        summary = report.summarize_cell(cell(paired_runs([4.] * 3, [10.] * 3, oximut={'admission_pauses': 5})))
        self.assertIn("memory pressure paused Oximut's admission 5 time(s)", summary['flags'])

    def test_a_small_score_gap_is_not_flagged(self):
        stryker = {'killed': 79, 'survived': 11, 'no-coverage': 10}
        summary = report.summarize_cell(cell(paired_runs([4.] * 3, [10.] * 3, stryker={'counts': stryker})))
        self.assertFalse(any('score gap' in flag for flag in summary['flags']), summary['flags'])

    def test_shared_mutants_classified_differently_are_flagged(self):
        comparison = {'oximut_mutants': 10, 'stryker_mutants': 10, 'shared': 10, 'oximut_only': 0, 'stryker_only': 0,
                      'shared_agree': 9, 'shared_disagree': 1, 'disagreements': []}
        summary = report.summarize_cell(cell(paired_runs([4.] * 3, [10.] * 3), scope_comparison=comparison))
        self.assertIn('1/10 shared mutants classified differently', summary['flags'])

    def test_mutant_count_skew_between_the_tools_is_flagged(self):
        stryker = {'killed': 80, 'survived': 10, 'no-coverage': 10}
        summary = report.summarize_cell(cell(paired_runs([4.] * 3, [10.] * 3, stryker={'counts': stryker})))
        self.assertIn('Stryker ran 10.0x as many mutants', summary['flags'])

    def test_unstable_outcomes_and_incomplete_oximut_runs_are_flagged(self):
        runs = paired_runs([4., 4., 4.], [10., 10., 10.])
        for entry in runs:
            if entry['tool'] == 'oximut' and entry['round'] == 2:
                entry['outcome_sha256'] = 'different'
                entry['complete'] = False
        flags = report.summarize_cell(cell(runs))['flags']
        self.assertIn('Oximut outcomes differ between rounds', flags)
        self.assertIn('Oximut run incomplete (invalid, skipped or failed mutants)', flags)

    def test_whole_suite_fallback_for_most_mutants_is_flagged_with_its_reason(self):
        fallback = {'selection_modes': {'full': 9, 'files': 1}, 'full_suite_reasons': {'probe-globalThis-conflict': 9}}
        summary = report.summarize_cell(cell(paired_runs([4.] * 3, [10.] * 3, oximut=fallback)))
        self.assertIn('test selection fell back to the whole suite for 9/10 mutants (probe-globalThis-conflict)',
                      summary['flags'])
        healthy = {'selection_modes': {'full': 1, 'files': 9}, 'full_suite_reasons': {'outside-test-execution': 1}}
        summary = report.summarize_cell(cell(paired_runs([4.] * 3, [10.] * 3, oximut=healthy)))
        self.assertFalse(any('fell back' in flag for flag in summary['flags']))

    def test_a_failed_plain_vitest_run_does_not_hide_the_comparison_but_is_flagged(self):
        runs = paired_runs([4.] * 3, [10.] * 3)
        runs.append({'tool': 'vitest', 'round': 4, 'warmup': False, 'valid': False, 'error': 'vitest: exit code 1',
                     'wall_seconds': 2., 'cpu_seconds': 2., 'peak_aggregate_rss_bytes': 1, 'exit_code': 1})
        summary = report.summarize_cell(cell(runs))
        self.assertEqual(summary['status'], 'ok')
        self.assertIn('plain Vitest run failed in 1 round(s): vitest: exit code 1', summary['flags'])

    def test_failed_and_skipped_cells_are_reported_not_dropped(self):
        failed = report.summarize_cell(cell([], status='failed', error='round 1 stryker: exit code 1'))
        skipped = report.summarize_cell(cell([], status='skipped', error='8 workers exceed the 4 usable CPUs',
                                             workers=8))
        markdown = report.render_markdown([failed, skipped])
        self.assertIn('**failed**', markdown)
        self.assertIn('round 1 stryker: exit code 1', markdown)
        self.assertIn('skipped: oversubscribed', markdown)
        self.assertIn('8 workers exceed the 4 usable CPUs', markdown)

    def test_a_cell_without_a_valid_pair_is_failed_not_averaged(self):
        summary = report.summarize_cell(cell([run('oximut', 1, 4.)]))
        self.assertEqual(summary['status'], 'failed')

    def test_markdown_has_every_requested_column_and_separates_the_kinds(self):
        startup = cell(paired_runs([4.] * 3, [10.] * 3), kind='startup', name='tiny', workers=2)
        large = cell(paired_runs([5.] * 3, [10.] * 3, oximut={'sha': 'f' * 64, 'outcome_sha256': 'f' * 64}),
                     kind='large', name='huge', workers=4)
        markdown = report.render_markdown([report.summarize_cell(large), report.summarize_cell(startup)])
        for column in ('Project', 'Workers', 'CPU', 'Mutants Oximut / Stryker', 'Oximut wall s', 'Stryker wall s',
                       'Wall ratio [95% CI]', 'CPU ratio', 'RSS ratio', 'Oximut outcome sha', 'Oximut K/S/NC',
                       'Stryker K/S/NC'):
            self.assertIn(column, markdown)
        self.assertLess(markdown.index('Start-up dominated'), markdown.index('Per-mutant dominated'))
        tiny = next(line for line in markdown.splitlines() if line.startswith('| tiny'))
        self.assertIn('Test CPU 9000', tiny)
        self.assertIn('10 / 10', tiny)
        self.assertIn('0.400x [0.400, 0.400] (n=3)', tiny)
        self.assertIn('8/1/1', tiny)
        huge = next(line for line in markdown.splitlines() if line.startswith('| huge'))
        self.assertIn('`ffffffffffff`', huge)

    def test_diagnostics_show_suite_time_phase_share_and_per_mutant_cost(self):
        summary = report.summarize_cell(cell(paired_runs([5.] * 3, [10.] * 3)))
        row = report.diagnostics_rows([summary])[0]
        self.assertEqual(row[:3], ['demo', '4', '2.0'])
        self.assertEqual(row[3], '10%')       # 500 ms of mutation phase in a 5 s run
        self.assertEqual(row[6:8], ['500', '1000'])

    def test_main_summarizes_a_directory_of_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, workers in enumerate((2, 4)):
                folder = Path(tmp) / f'cells/cell-{index}'
                folder.mkdir(parents=True)
                (folder / 'cell.json').write_text(json.dumps(cell(paired_runs([4.] * 3, [10.] * 3), workers=workers)))
            output = Path(tmp) / 'out'
            with contextlib.redirect_stdout(io.StringIO()):
                code = report.main([str(Path(tmp) / 'cells'), '--output', str(output)])
            self.assertEqual(code, 0)
            summaries = json.loads((output / 'summary.json').read_text())
            self.assertEqual([s['workers'] for s in summaries], [2, 4])
            self.assertIn('Mutant overlap and agreement', (output / 'summary.md').read_text())

    def test_main_without_cells_fails(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(report.main([tmp]), 1)


if __name__ == '__main__':
    unittest.main()
