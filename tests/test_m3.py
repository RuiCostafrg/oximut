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
        with tempfile.TemporaryDirectory(prefix='oximut-m3-acceptance-') as temporary:
            report = pathlib.Path(temporary) / 'report.json'
            command = [
                str(BINARY), '--root', str(root), '--config', str(root / config),
                '--source', str(root / source), '--execution', execution,
                '--order-seed', '73', '--report', str(report),
            ]
            if selection:
                command.append('--test-selection')
            result = subprocess.run(command, cwd=REPO, text=True, capture_output=True, timeout=240)
            self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
            payload = json.loads(report.read_text())
            self.assertEqual(payload['baseline']['outcome'], 'passed', payload)
            self.assertTrue(payload['summary']['complete'], payload)
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

    def test_concurrent_tests_force_full_suite_with_reason(self):
        report = self.run_fixture('concurrent.config.ts', 'src/subject.ts', True)
        full = self.run_fixture('concurrent.config.ts', 'src/subject.ts', False)
        self.assert_equivalent(full, report)
        for mutant in report['mutants']:
            selection = mutant['selection']
            self.assertEqual(selection['mode'], 'full', mutant)
            self.assertEqual(selection['selectedTests'], selection['totalTests'], mutant)
            self.assertTrue(any('concurrent' in reason.lower() for reason in selection['reasons']), mutant)

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
