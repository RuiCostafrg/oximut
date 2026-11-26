"""M3 selection must preserve the full-suite mutant outcomes."""

import json
import hashlib
import pathlib
import subprocess
import tempfile
import unittest

from test_m1 import BINARY, REPO


class PreciseSelection(unittest.TestCase):
    def run_fixture(self, config, source, selection, execution='fresh'):
        root = REPO / 'fixtures' / 'm3'
        sources = [source] if isinstance(source, str) else source
        with tempfile.TemporaryDirectory(prefix='oximut-m3-acceptance-') as temporary:
            report = pathlib.Path(temporary) / 'report.json'
            command = [
                str(BINARY), '--root', str(root), '--config', str(root / config),
                *[arg for item in sources for arg in ('--source', str(root / item))],
                '--execution', execution, '--workers', '1',
                '--order-seed', '73', '--report', str(report),
            ]
            # Selection is on by default; the full-suite runs opt out explicitly.
            command.append('--test-selection' if selection else '--no-test-selection')
            result = subprocess.run(command, cwd=REPO, text=True, capture_output=True, timeout=240)
            self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
            payload = json.loads(report.read_text())
            self.assertEqual(payload['baseline']['outcome'], 'passed', payload)
            self.assertTrue(payload['summary']['complete'], payload)
            expected_exit = 1 if (payload['summary']['survived'] or payload['summary']['noCoverage']) else 0
            self.assertEqual(result.returncode, expected_exit, payload)
            return payload

    @staticmethod
    def outcome_map(report):
        return {mutant['id']: mutant['outcome'] for mutant in report['mutants']}

    def assert_equivalent(self, full, optimized):
        full_outcomes = self.outcome_map(full)
        optimized_outcomes = self.outcome_map(optimized)
        self.assertEqual(full_outcomes.keys(), optimized_outcomes.keys())
        for mutant_id, outcome in full_outcomes.items():
            selected_outcome = optimized_outcomes[mutant_id]
            if selected_outcome == 'no-coverage':
                self.assertEqual(outcome, 'survived', mutant_id)
            else:
                self.assertEqual(selected_outcome, outcome, mutant_id)

    @staticmethod
    def fixture_sources():
        root = REPO / 'fixtures' / 'm3'
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*')
            if path.is_file() and path.suffix in {'.ts', '.js'}
        }

    def test_selection_matches_full_suite_and_narrows_ordinary_mutants(self):
        before = self.fixture_sources()
        full = self.run_fixture('vitest.config.ts', 'src/subject.ts', False)
        selected = self.run_fixture('vitest.config.ts', 'src/subject.ts', True)
        self.assert_equivalent(full, selected)
        self.assertTrue(selected['baseline']['coverage']['complete'])

        selections = selected['mutants']
        self.assertTrue(all('selection' in mutant for mutant in selections))
        narrowed = [
            mutant for mutant in selections
            if mutant['selection']['mode'] in ('tests', 'files')
            and mutant['selection']['selectedTests'] < mutant['selection']['totalTests']
        ]
        self.assertTrue(narrowed, selections)
        self.assertTrue(all(mutant['selection']['selectedTests'] > 0 for mutant in narrowed))

        # The same-file state transition is hit by the first test, while only
        # the second test asserts its effect. Selection must retain that file.
        coverage = selected['baseline']['coverage']
        transition_ids = {
            test['id'] for test in coverage['tests']
            if test['name'] == 'transition state'
        }
        transition_hits = [
            hit for hit in coverage['hits']
            if transition_ids.intersection(hit['tests'])
        ]
        self.assertTrue(transition_hits, coverage)
        for hit in transition_hits:
            mutant = next(item for item in selections if item['id'] == hit['mutantId'])
            self.assertEqual(mutant['selection']['mode'], 'files', mutant)
            self.assertGreaterEqual(mutant['selection']['selectedTests'], 2, mutant)

        outside_hits = [hit for hit in coverage['hits'] if hit['outsideTest']]
        self.assertTrue(outside_hits, coverage)
        for hit in outside_hits:
            mutant = next(item for item in selections if item['id'] == hit['mutantId'])
            self.assertEqual(mutant['selection']['mode'], 'full', mutant)
            self.assertTrue(any('outside' in reason.lower() for reason in mutant['selection']['reasons']), mutant)

        no_coverage = [mutant for mutant in selections if mutant['outcome'] == 'no-coverage']
        self.assertTrue(no_coverage, selections)
        for mutant in no_coverage:
            self.assertEqual(mutant['selection']['mode'], 'none', mutant)
            self.assertEqual(mutant['selection']['selectedTests'], 0, mutant)

        persistent = self.run_fixture(
            'vitest.config.ts', 'src/subject.ts', True, execution='persistent'
        )
        self.assert_equivalent(full, persistent)
        self.assertEqual(before, self.fixture_sources(), 'Selection changed fixture sources')

    def test_one_unprobeable_file_does_not_widen_other_files(self):
        sources = ['src/environment.ts', 'src/shadowed.ts', 'src/plain.ts']
        config = 'global-this.config.ts'
        for execution in ('fresh', 'persistent'):
            with self.subTest(execution=execution):
                full = self.run_fixture(config, sources, False, execution)
                selected = self.run_fixture(config, sources, True, execution)
                self.assert_equivalent(full, selected)
                # Kills are never lost: every killed full-suite mutant is killed.
                self.assertEqual(
                    {i for i, o in self.outcome_map(full).items() if o == 'killed'},
                    {i for i, o in self.outcome_map(selected).items() if o == 'killed'},
                )
                coverage = selected['baseline']['coverage']
                # The run-level decision stays sound: complete, no run-level reasons.
                self.assertTrue(coverage['complete'], coverage)
                self.assertEqual(coverage['reasons'], [], coverage)
                by_file = {}
                for mutant in selected['mutants']:
                    by_file.setdefault(pathlib.Path(mutant['file']).name, []).append(mutant)
                self.assertEqual(set(by_file), {'environment.ts', 'shadowed.ts', 'plain.ts'})
                # The shadowing file alone runs the full suite, with its reason.
                for mutant in by_file['shadowed.ts']:
                    selection = mutant['selection']
                    self.assertEqual(selection['mode'], 'full', mutant)
                    self.assertEqual(selection['selectedTests'], selection['totalTests'], mutant)
                    self.assertTrue(any(
                        reason.startswith('unprobed-file:') and 'probe-globalThis-conflict' in reason
                        for reason in selection['reasons']), mutant)
                    self.assertEqual(mutant['outcome'], 'killed', mutant)
                # Files that merely read globalThis are probed and keep narrow selection.
                for name in ('environment.ts', 'plain.ts'):
                    for mutant in by_file[name]:
                        selection = mutant['selection']
                        self.assertIn(selection['mode'], ('files', 'none'), mutant)
                        self.assertLess(selection['selectedTests'], selection['totalTests'], mutant)
                        self.assertFalse(
                            any(reason.startswith('unprobed-file:') for reason in selection['reasons']),
                            mutant)
                self.assertTrue(any(m['outcome'] == 'killed' for m in by_file['environment.ts']))

    def test_concurrent_tests_force_full_suite_with_reason(self):
        report = self.run_fixture('concurrent.config.ts', 'src/subject.ts', True)
        full = self.run_fixture('concurrent.config.ts', 'src/subject.ts', False)
        self.assert_equivalent(full, report)
        for mutant in report['mutants']:
            selection = mutant['selection']
            self.assertEqual(selection['mode'], 'full', mutant)
            self.assertEqual(selection['selectedTests'], selection['totalTests'], mutant)
            self.assertTrue(any('concurrent' in reason.lower() for reason in selection['reasons']), mutant)

    def test_setup_files_do_not_disable_selection(self):
        # A setup file that imports the mutated module and registers a beforeEach.
        # Hits while it loads have no test to attribute to, so those mutants run the
        # whole suite; hits inside tests (including the setup file's own beforeEach)
        # keep their narrow selection, and no kill is lost.
        config, source = 'setup-files.config.ts', 'src/setup-subject.ts'
        for execution in ('fresh', 'persistent'):
            with self.subTest(execution=execution):
                full = self.run_fixture(config, source, False, execution)
                selected = self.run_fixture(config, source, True, execution)
                self.assert_equivalent(full, selected)
                self.assertEqual(
                    {i for i, o in self.outcome_map(full).items() if o == 'killed'},
                    {i for i, o in self.outcome_map(selected).items() if o == 'killed'},
                )
                coverage = selected['baseline']['coverage']
                self.assertTrue(coverage['complete'], coverage)
                self.assertEqual(coverage['reasons'], [], coverage)
                by_function = {}
                for mutant in selected['mutants']:
                    line = mutant['location']['start']['line']
                    name = {5: 'setupValue', 9: 'hookValue', 13: 'alpha', 17: 'beta'}[line]
                    by_function.setdefault(name, []).append(mutant)
                self.assertEqual(set(by_function), {'setupValue', 'hookValue', 'alpha', 'beta'})
                for name, mutants in by_function.items():
                    self.assertTrue(any(m['outcome'] == 'killed' for m in mutants), name)
                # Hit while the setup file loads: outside any test, so the full suite.
                for mutant in by_function['setupValue']:
                    selection = mutant['selection']
                    self.assertEqual(selection['mode'], 'full', mutant)
                    self.assertEqual(selection['selectedTests'], selection['totalTests'], mutant)
                    self.assertIn('outside-test-execution', selection['reasons'], mutant)
                    self.assertNotIn('setup-files', selection['reasons'], mutant)
                # Hit by the setup file's beforeEach in every test: attributed to tests.
                for mutant in by_function['hookValue']:
                    selection = mutant['selection']
                    self.assertEqual(selection['mode'], 'files', mutant)
                    self.assertEqual(selection['selectedTests'], selection['totalTests'], mutant)
                    self.assertNotIn('outside-test-execution', selection['reasons'], mutant)
                # Hit only by one test file: that file alone runs.
                for name, file in (('alpha', 'setup-alpha.test.ts'), ('beta', 'setup-beta.test.ts')):
                    for mutant in by_function[name]:
                        selection = mutant['selection']
                        self.assertEqual(selection['mode'], 'files', mutant)
                        self.assertEqual(
                            [pathlib.Path(f).name for f in selection['files']], [file], mutant)
                        self.assertLess(selection['selectedTests'], selection['totalTests'], mutant)

    def test_global_setup_forces_full_suite_with_reason(self):
        report = self.run_fixture('global-setup.config.ts', 'src/subject.ts', True)
        full = self.run_fixture('global-setup.config.ts', 'src/subject.ts', False)
        self.assert_equivalent(full, report)
        for mutant in report['mutants']:
            selection = mutant['selection']
            self.assertEqual(selection['mode'], 'full', mutant)
            self.assertEqual(selection['selectedTests'], selection['totalTests'], mutant)
            self.assertTrue(any('global' in reason.lower() and 'setup' in reason.lower()
                                for reason in selection['reasons']), mutant)

    def test_import_time_mutation_forces_full_suite_with_reason(self):
        report = self.run_fixture('import-init.config.ts', 'src/top-level.ts', True)
        full = self.run_fixture('import-init.config.ts', 'src/top-level.ts', False)
        self.assert_equivalent(full, report)
        self.assertTrue(report['mutants'])
        for mutant in report['mutants']:
            selection = mutant['selection']
            self.assertEqual(selection['mode'], 'full', mutant)
            self.assertEqual(selection['selectedTests'], selection['totalTests'], mutant)
            self.assertTrue(any(
                'import' in reason.lower() or 'outside' in reason.lower()
                for reason in selection['reasons']
            ), mutant)


if __name__ == '__main__':
    unittest.main()
