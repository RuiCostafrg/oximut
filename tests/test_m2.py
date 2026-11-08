"""Compare mutation switching/reuse with the independent fresh-process reference."""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from test_m1 import BINARY, REPO, sources


class PersistentEquivalence(unittest.TestCase):
    def run_fixture(self, fixture, source, mode, seed, extra=()):
        root = REPO / 'fixtures' / fixture
        with tempfile.TemporaryDirectory(prefix='oximut-m2-acceptance-') as temporary:
            report = pathlib.Path(temporary) / 'report.json'
            result = subprocess.run([
                str(BINARY), '--root', str(root), '--config', str(root / 'vitest.config.ts'),
                '--source', str(root / 'src' / source), '--execution', mode,
                '--order-seed', str(seed), '--report', str(report), *extra,
            ], cwd=REPO, text=True, capture_output=True, timeout=180)
            self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
            payload = json.loads(report.read_text())
            self.assertEqual(payload['baseline']['outcome'], 'passed')
            self.assertTrue(payload['summary']['complete'], payload)
            return payload

    def test_shared_controller_matches_sequential_selected_and_full(self):
        for selection in ((), ('--test-selection',)):
            with self.subTest(selection=selection):
                serial = self.run_fixture('m1', 'subject.ts', 'persistent', 17,
                                          (*selection, '--workers', '1', '--no-cache'))
                shared = self.run_fixture('m1', 'subject.ts', 'persistent', 17,
                                          (*selection, '--workers', '3', '--no-cache'))
                self.assertEqual({m['id']: (m['outcome'], m['tests']) for m in shared['mutants']},
                                 {m['id']: (m['outcome'], m['tests']) for m in serial['mutants']})
                self.assertFalse(any('spare warm' in d for d in shared['diagnostics']))
                self.assertTrue(any('native coordinator: 1 controllers' in d
                                    for d in shared['diagnostics']), shared['diagnostics'])

    def test_shared_cli_barrier_and_process_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='oximut-lane-cli-') as temporary:
            root = pathlib.Path(temporary)
            (root / 'node_modules').symlink_to(REPO / 'node_modules')
            (root / 'source.ts').write_text('export const enabled = true; export const unused = false;')
            (root / 'vitest.config.mjs').write_text(
                "export default { test: { include: ['sample.test.ts'], testTimeout: 10000 } };")
            (root / 'sample.test.ts').write_text("""
import { test, expect } from 'vitest';
import { appendFileSync, readdirSync, writeFileSync } from 'node:fs';
import { enabled } from './source';
const root = ROOT;
const id = process.env.OXIMUT_ACTIVE_MUTANT;
let state = 0;
test('barrier', async () => {
  expect(state++).toBe(0);
  if (id) {
    appendFileSync(root+'/events', JSON.stringify({type:'enter', id, pid:process.pid})+'\\n');
    writeFileSync(root+'/ready-'+id, '');
    while (readdirSync(root).filter(n => n.startsWith('ready-')).length < 2)
      await new Promise(r => setTimeout(r, 5));
    appendFileSync(root+'/events', JSON.stringify({type:'released', id, pid:process.pid})+'\\n');
  }
});
test('enabled', () => expect(enabled).toBe(true));
""".replace('ROOT', json.dumps(str(root))))
            report = root / '.oximut' / 'report.json'
            result = subprocess.run([
                str(BINARY), '--root', str(root), '--source', 'source.ts',
                '--config', 'vitest.config.mjs', '--execution', 'persistent',
                '--workers', '2', '--no-cache',
                '--mutators', 'boolean-literal', '--report', str(report),
            ], cwd=REPO, capture_output=True, text=True, timeout=90)
            payload = json.loads(report.read_text())
            self.assertIn(result.returncode, (0, 1), payload)
            self.assertEqual(sorted(m['outcome'] for m in payload['mutants']), ['killed', 'survived'])
            events = [json.loads(line) for line in (root / 'events').read_text().splitlines()]
            self.assertEqual([e['type'] for e in events[:2]], ['enter', 'enter'])
            pids = {e['pid'] for e in events}
            self.assertEqual(len(pids), 2)
            for pid in pids:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_shared_controller_can_be_disabled(self):
        for extra, shared in ((('--no-shared-controller',), False), ((), True)):
            with self.subTest(extra=extra):
                payload = self.run_fixture('m1', 'subject.ts', 'persistent', 17,
                                           ('--workers', '2', '--no-cache', *extra))
                warmed = any('spare warm' in d for d in payload['diagnostics'])
                self.assertEqual(warmed, not shared, payload['diagnostics'])

    def test_unsupported_project_falls_back_to_one_controller_per_worker(self):
        with tempfile.TemporaryDirectory(prefix='oximut-lane-fallback-') as temporary:
            root = pathlib.Path(temporary)
            (root / 'node_modules').symlink_to(REPO / 'node_modules')
            (root / 'source.ts').write_text('export const enabled = true; export const unused = false;')
            (root / 'global-setup.mjs').write_text('export default () => {};')
            (root / 'vitest.config.mjs').write_text(
                "export default { test: { include: ['sample.test.ts'], globalSetup: ['./global-setup.mjs'] } };")
            (root / 'sample.test.ts').write_text("""
import { test, expect } from 'vitest';
import { enabled } from './source';
test('enabled', () => expect(enabled).toBe(true));
""")
            outcomes = {}
            for name, extra in (('default', ()), ('disabled', ('--no-shared-controller',))):
                report = root / f'{name}.json'
                result = subprocess.run([
                    str(BINARY), '--root', str(root), '--source', 'source.ts',
                    '--config', 'vitest.config.mjs', '--execution', 'persistent',
                    '--workers', '2', '--no-cache', '--mutators', 'boolean-literal',
                    '--report', str(report), *extra,
                ], cwd=REPO, capture_output=True, text=True, timeout=120)
                payload = json.loads(report.read_text())
                self.assertIn(result.returncode, (0, 1), payload)
                self.assertTrue(payload['summary']['complete'], payload)
                outcomes[name] = sorted((m['id'], m['outcome']) for m in payload['mutants'])
                if name == 'default':
                    self.assertTrue(any('shared controller unavailable' in d
                                        for d in payload['diagnostics']), payload['diagnostics'])
            self.assertEqual(outcomes['default'], outcomes['disabled'])

    def test_fresh_thread_matches_process_with_selection(self):
        process = self.run_fixture('m1', 'subject.ts', 'persistent', 17,
                                   ('--test-selection',))
        thread = self.run_fixture('m1', 'subject.ts', 'persistent', 17,
                                  ('--test-selection', '--isolation', 'fresh-thread'))
        self.assertEqual({m['id']: m['outcome'] for m in thread['mutants']},
                         {m['id']: m['outcome'] for m in process['mutants']})
        self.assertEqual(thread['baseline']['tests'], process['baseline']['tests'])
        self.assertIn('isolation=fresh-test-threads', ' '.join(thread['diagnostics']))

    def test_warm_spares_preserve_selected_mutant_results(self):
        serial = self.run_fixture('m1', 'subject.ts', 'persistent', 17,
                                  ('--test-selection', '--workers', '1', '--no-cache'))
        parallel = self.run_fixture('m1', 'subject.ts', 'persistent', 17,
                                    ('--test-selection', '--workers', '2', '--no-cache',
                                     '--no-shared-controller'))
        self.assertEqual({m['id']: m['outcome'] for m in parallel['mutants']},
                         {m['id']: m['outcome'] for m in serial['mutants']})
        if parallel['resources']['effectiveWorkers'] >= 2:
            self.assertTrue(any(any(f'spare warm {outcome}' in d
                                    for outcome in ('ready', 'skipped', 'abandoned'))
                                for d in parallel['diagnostics']), parallel)
        self.assertFalse(any('spare warm' in d for d in serial['diagnostics']))

    def test_randomized_orders_match_fresh_reference_without_source_edits(self):
        before = sources()
        for fixture, source in [('m1', 'subject.ts'), ('stateful', 'service.ts'), ('m2', 'subject.ts')]:
            with self.subTest(fixture=fixture):
                reference = self.run_fixture(fixture, source, 'fresh', 0)
                expected = {m['id']: m['outcome'] for m in reference['mutants']}
                for seed in (17, 83):
                    reused = self.run_fixture(fixture, source, 'persistent', seed)
                    self.assertEqual({m['id']: m['outcome'] for m in reused['mutants']}, expected)
                    self.assertEqual(reused['baseline']['tests'], reference['baseline']['tests'])
        self.assertEqual(before, sources(), 'Execution changed fixture sources')


if __name__ == '__main__':
    unittest.main()
