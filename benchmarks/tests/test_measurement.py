import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('benchmark_runner', Path(__file__).parents[1] / 'run.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ProcessStatsTests(unittest.TestCase):
    def test_rollup_rss_is_strict_and_does_not_include_pss(self):
        self.assertEqual(runner.parse_rollup_rss('Rss: 123 kB\nPss: 7 kB\n'), 123 * 1024)
        for value in ('Pss: 1 kB', 'Rss: -1 kB', 'Rss: 1 MB',
                      'Rss: 1 kB\nRss: 2 kB', 'Rss: 1', 'Rss: ١ kB'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.parse_rollup_rss(value)

    def test_memory_unavailable_for_live_process_invalidates_sample(self):
        tail = ['S', '12', '34'] + ['0'] * 16 + ['9876', '1234', '7']
        stat = '99 (worker) ' + ' '.join(tail)
        with mock.patch.object(Path, 'read_text', side_effect=[PermissionError('restricted'), stat]):
            rss, errors = runner.aggregate_rollup_rss({99: {'starttime': 9876}})
        self.assertEqual(rss, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn('RSS unavailable', errors[0])

    def test_exit_or_reused_pid_during_memory_sample_is_ignored(self):
        tail = ['S', '12', '34'] + ['0'] * 16 + ['9877', '1234', '7']
        for values in ([FileNotFoundError(), FileNotFoundError()],
                       ['Rss: 123 kB', ProcessLookupError()],
                       [ProcessLookupError()],
                       ['Rss: 123 kB', '99 (worker) ' + ' '.join(tail)]):
            with mock.patch.object(Path, 'read_text', side_effect=values):
                self.assertEqual(runner.aggregate_rollup_rss({99: {'starttime': 9876}}), (0, []))

    def test_stat_command_can_contain_parentheses_and_spaces(self):
        # Real proc layout: fields 3..24, with arbitrary comm text in field 2.
        tail = ['S', '12', '34'] + ['0'] * 16 + ['9876', '1234', '7']
        result = runner.parse_proc_stat('99 (worker ) name) ' + ' '.join(tail))
        self.assertEqual((result['pid'], result['ppid'], result['pgrp']), (99, 12, 34))
        self.assertEqual(result['starttime'], 9876)
        self.assertEqual(result['rss_bytes'], 7 * runner.PAGE_SIZE)

    def test_tree_tracks_reparented_and_nested_children_but_excludes_other_jobs(self):
        def stat(pid, ppid, pgrp=10, start=100):
            return dict(pid=pid, ppid=ppid, pgrp=pgrp, starttime=start)
        snapshot = {10: stat(10, 1), 11: stat(11, 10),
                    12: stat(12, 11, 12), 20: stat(20, 1, 20)}
        known = {}
        self.assertEqual(set(runner.process_tree(snapshot, 10, known, 999)), {10, 11, 12})
        reparented = {12: stat(12, 999, 12), 20: stat(20, 1, 20)}
        self.assertEqual(set(runner.process_tree(reparented, 10, known, 999)), {12})


@unittest.skipUnless(runner.LINUX_KERNEL, 'requires Linux /proc and wait4')
class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_python(self, script, **kwargs):
        return runner.run_command([sys.executable, '-u', '-c', script], self.path,
                                  self.path / 'output.log', sample_interval=.01, **kwargs)

    def test_exit_failure_and_spawn_failure_are_not_success(self):
        result = self.run_python("print('diagnostic'); raise SystemExit(3)")
        self.assertFalse(result['success'])
        self.assertEqual(result['exit_code'], 3)
        self.assertIn('diagnostic', (self.path / 'output.log').read_text())
        missing = runner.run_command(['/nonexistent/oximut-command'], self.path,
                                     self.path / 'missing.log')
        self.assertFalse(missing['success'])
        self.assertIsNotNone(missing['spawn_error'])

    def test_noisy_output_is_bounded_without_changing_success(self):
        result = self.run_python("import os; os.write(1, b'x' * 200000)", output_limit=1234)
        self.assertTrue(result['success'])
        self.assertTrue(result['output_truncated'])
        self.assertEqual(result['output_bytes'], 200000)
        self.assertEqual((self.path / 'output.log').stat().st_size, 1234)

    def test_timeout_reaps_a_detached_descendant(self):
        script = ("import subprocess,sys,time; "
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
                  "start_new_session=True); print(p.pid,flush=True); time.sleep(60)")
        before = time.monotonic()
        result = self.run_python(script, timeout=.3)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['success'])
        self.assertLess(time.monotonic() - before, 4)
        descendant = int((self.path / 'output.log').read_text().strip())
        self.assertFalse(Path(f'/proc/{descendant}').exists(), 'descendant must be reaped')
        # Discovery can span scans when the parent exits before the child is seen.
        self.assertGreaterEqual(result['observed_process_count'], 2)

    def test_successful_parent_leaving_descendant_is_failed_and_cleaned(self):
        script = ("import subprocess,sys; "
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
                  "start_new_session=True); print(p.pid,flush=True)")
        result = self.run_python(script, timeout=3)
        self.assertEqual(result['exit_code'], 0)
        self.assertTrue(result['descendant_cleanup_required'])
        self.assertFalse(result['success'])
        descendant = int((self.path / 'output.log').read_text().strip())
        self.assertFalse(Path(f'/proc/{descendant}').exists())

    def test_reaped_child_cpu_and_process_tree_memory_are_recorded(self):
        child = "import time; a=bytearray(10000000); end=time.process_time()+.05\nwhile time.process_time()<end: pass"
        result = self.run_python(f'import subprocess,sys; subprocess.run([sys.executable,"-c",{child!r}],check=True)')
        self.assertTrue(result['success'])
        self.assertGreater(result['cpu_seconds'], .03)
        self.assertGreater(result['peak_aggregate_rss_bytes'], 0)
        self.assertEqual(result['memory_method'], 'sum-live-smaps_rollup-rss')
        self.assertEqual(result['memory_errors'], [])
        self.assertGreaterEqual(result['peak_process_count'], 2)


class ArtifactTests(unittest.TestCase):
    @staticmethod
    def pure_test_files():
        return {'fixtures/pure/test/logic.test.ts': {
            'tests': [{'id': str(i), 'name': f'test {i}'} for i in range(7)]}}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'report.json'

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps(value))

    def test_failed_exit_cannot_be_rescued_by_a_passing_report(self):
        self.write({'success': True, 'numTotalTests': 1, 'numPassedTests': 1})
        with self.assertRaises(ValueError):
            runner.validate_artifact('vitest', self.path, False)

    def test_missing_malformed_empty_and_failed_reports_are_rejected(self):
        with self.assertRaises(OSError):
            runner.validate_artifact('vitest', self.path, True)
        self.path.write_text('{partial')
        with self.assertRaises(ValueError):
            runner.validate_artifact('vitest', self.path, True)
        for report in [{'success': True, 'numTotalTests': 0, 'numPassedTests': 0},
                       {'success': False, 'numTotalTests': 1, 'numPassedTests': 0}]:
            self.write(report)
            with self.assertRaises(ValueError):
                runner.validate_artifact('vitest', self.path, True)
        self.write({'schemaVersion': '2', 'files': {}})
        with self.assertRaises(ValueError):
            runner.validate_artifact('stryker', self.path, True)

    def test_counts_and_outcome_identity_preserve_real_classification_changes(self):
        def report(statuses):
            return {'schemaVersion': '2', 'files': {'a.ts': {'mutants': [
                {'id': str(i), 'status': status, 'testsCompleted': 1} for i, status in enumerate(statuses)]}}}
        self.write(report(['Killed', 'Survived', 'NoCoverage', 'CompileError', 'Timeout', 'Ignored']))
        original = runner.validate_artifact('stryker', self.path, True)
        self.assertEqual(original['mutant_count'], 6)
        self.assertEqual(original['mutation_status_counts']['Killed'], 1)
        self.write(report(['Survived', 'Killed', 'NoCoverage', 'CompileError', 'Timeout', 'Ignored']))
        changed = runner.validate_artifact('stryker', self.path, True)
        self.assertEqual(original['mutation_status_counts'], changed['mutation_status_counts'])
        self.assertNotEqual(original['mutation_outcome_sha256'], changed['mutation_outcome_sha256'])
        for status in ['Pending', 'unknown', 'RuntimeError']:
            self.write(report([status]))
            with self.assertRaises(ValueError):
                runner.validate_artifact('stryker', self.path, True)

    def test_existing_output_directory_cannot_reuse_reports(self):
        # No tools are launched because a pre-existing destination is rejected first.
        with self.assertRaises(FileExistsError):
            runner.main(['--output', self.temp.name, '--repetitions', '1', '--warmups', '0'])

    def test_zero_test_classifications_and_failed_semantic_sentinel_are_rejected(self):
        sentinel = {'id': '0', 'mutatorName': 'BlockStatement', 'replacement': '{}',
                    'location': {'start': {'line': 1}}, 'status': 'Killed', 'testsCompleted': 1}
        report = {'schemaVersion': '1.0', 'testFiles': self.pure_test_files(),
                  'files': {'fixtures/pure/src/logic.ts': {'mutants': [sentinel]}}}
        self.write(report)
        self.assertEqual(runner.validate_artifact('stryker', self.path, True, 'pure')['mutant_count'], 1)
        report['testFiles'] = {}
        self.write(report)
        with self.assertRaises(ValueError):
            runner.validate_artifact('stryker', self.path, True, 'pure')
        report['testFiles'] = self.pure_test_files()
        for status, completed in [('Survived', 0), ('Killed', 0), ('Survived', 1)]:
            sentinel.update(status=status, testsCompleted=completed)
            self.write(report)
            with self.assertRaises(ValueError):
                runner.validate_artifact('stryker', self.path, True, 'pure')

    def test_main_fails_when_measured_mutant_outcomes_disagree(self):
        runs = 0
        def fake_run(command, cwd, log_path, timeout, interval, limit, env):
            nonlocal runs
            runs += 1
            self.assertEqual(env['OXIMUT_FIXTURE_MODE'], 'fixture')
            sentinel = {'id': '0', 'mutatorName': 'BlockStatement', 'replacement': '{}',
                        'location': {'start': {'line': 1}}, 'status': 'Killed', 'testsCompleted': 1}
            outcomes = [sentinel, {'id': '1', 'status': 'Killed' if runs == 1 else 'Survived',
                                   'testsCompleted': 1}]
            Path(env['OXIMUT_BENCH_REPORT']).write_text(json.dumps(
                {'schemaVersion': '1.0', 'testFiles': self.pure_test_files(),
                 'files': {'fixtures/pure/src/logic.ts': {'mutants': outcomes}}}))
            log_path.write_text('')
            return dict(success=True, wall_seconds=1, cpu_seconds=1,
                        peak_aggregate_rss_bytes=1000, peak_process_count=1)
        output = Path(self.temp.name) / 'new-output'
        with mock.patch.object(runner, 'metadata', return_value={'input_sha256': {}}), \
             mock.patch.object(runner, 'run_command', side_effect=fake_run), \
             mock.patch.dict(os.environ, {'OXIMUT_FIXTURE_MODE': 'unexpected'}):
            code = runner.main(['--output', str(output), '--tools', 'stryker',
                                '--fixtures', 'pure', '--repetitions', '2', '--warmups', '0'])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads((output / 'summary.json').read_text())[0]['outcomes_agree'])

    def test_vitest_identity_changes_even_with_equal_test_counts(self):
        report = {'success': True, 'numTotalTests': 1, 'numPassedTests': 1,
                  'testResults': [{'name': 'test/a.test.ts', 'assertionResults': [
                      {'fullName': 'first test', 'status': 'passed', 'duration': 1}]}]}
        self.write(report)
        first = runner.validate_artifact('vitest', self.path, True)
        report['testResults'][0]['assertionResults'][0]['fullName'] = 'different test'
        self.write(report)
        second = runner.validate_artifact('vitest', self.path, True)
        rows = [dict(fixture='pure', tool='vitest', warmup=False, success=True,
                     wall_seconds=1, cpu_seconds=1, peak_aggregate_rss_bytes=1,
                     peak_process_count=1, **data) for data in [first, second]]
        self.assertFalse(runner.summarize(rows)[0]['outcomes_agree'])

    def test_vitest_fixture_scope_rejects_wrong_counts_and_other_fixture_files(self):
        report = {'success': True, 'numTotalTests': 7, 'numPassedTests': 7,
                  'testResults': [{'name': str(runner.ROOT / 'fixtures/pure/test/logic.test.ts'),
                                   'assertionResults': [
                                       {'fullName': f'test {i}', 'status': 'passed'} for i in range(7)]}]}
        self.write(report)
        self.assertEqual(runner.validate_artifact('vitest', self.path, True, 'pure')
                         ['test_counts']['numTotalTests'], 7)
        with self.assertRaises(ValueError):
            runner.validate_artifact('vitest', self.path, True, 'stateful')
        report['testResults'][0]['name'] = str(runner.ROOT / 'fixtures/stateful/test/service.test.ts')
        self.write(report)
        with self.assertRaises(ValueError):
            runner.validate_artifact('vitest', self.path, True, 'pure')


if __name__ == '__main__':
    unittest.main()
