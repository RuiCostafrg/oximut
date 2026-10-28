"""Lightweight diagnostic fixtures only: no builds or real mutation timings."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profile_native as diagnostic


class QualificationTests(unittest.TestCase):
    def rows(self):
        return [dict(tool=tool, round=0, warmup=False, success=True,
                     memory_method='sum-live-smaps_rollup-rss', memory_errors=[],
                     declared_test_identity_sha256='same', mutator_families=['arithmetic'],
                     mutations={str(i): {'outcome': 'killed'} for i in range(10)},
                     **{metric: 2. for metric in diagnostic.workload.METRICS})
                for tool in ('vitest', 'oximut', 'stryker')]

    def errors(self, rows, code=0, ratios=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'summary.json').write_text(json.dumps({'correctness_gate': True, 'ratios': ratios or []}))
            (root / 'measurements.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            return diagnostic.qualification_errors(root, code)

    def test_full_group_passes_but_missing_duplicate_failed_and_ratios_reject(self):
        rows = self.rows()
        self.assertEqual(self.errors(rows), [])
        self.assertTrue(self.errors(rows[:2]))
        self.assertTrue(self.errors(rows + [copy.deepcopy(rows[0])]))
        self.assertTrue(self.errors(rows, code=1))
        self.assertTrue(self.errors(rows, ratios=[{'wall_seconds': 1}]))
        rows[1]['success'] = False
        self.assertTrue(self.errors(rows))

    def test_wrong_edit_count_or_outcome_reject_even_equal_cross_tool(self):
        for count, outcome in [(9, 'killed'), (10, 'survived'), (11, 'killed')]:
            rows = self.rows()
            for row in rows:
                row['mutations'] = {str(i): {'outcome': outcome} for i in range(count)}
            self.assertTrue(self.errors(rows))

    def test_missing_summary_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(diagnostic.qualification_errors(Path(tmp), 0))

    def test_preflight_failure_retains_explicit_failed_summary(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'NODE_OPTIONS': '--trace-warnings'}):
            out = Path(tmp) / 'capture'
            code = diagnostic.main(['--binary', '/missing', '--output', str(out)])
            summary = json.loads((out / 'diagnostic.json').read_text())
            self.assertEqual(code, 1)
            self.assertFalse(summary['passed'])
            self.assertIn('NODE_OPTIONS', summary['failure'])
            self.assertTrue(summary['captures']['errors'])
            self.assertNotIn('ratios', summary)


class ShimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.supervisor = self.root / 'supervisor.py'
        self.shim = self.root / 'python3'
        self.shim.write_text(f'#!{sys.executable}\n' + (diagnostic.SUPPORT / 'python-shim.py').read_text())
        self.shim.chmod(0o755)
        self.env = {**os.environ, 'OXIMUT_PROFILE_PYTHON': sys.executable,
                    'OXIMUT_PROFILE_SUPERVISOR': str(self.supervisor),
                    'OXIMUT_PROFILE_DIR': str(self.root)}

    def test_supervisor_keeps_argv_ipc_signal_handler_and_nonzero_exit(self):
        self.supervisor.write_text('''import os, signal, sys
assert sys.argv[1:] == ['literal argument', '7']
fd = int(os.environ['NODE_CHANNEL_FD'])
signal.signal(signal.SIGTERM, lambda *_: os.write(fd, b'IPC intact'))
os.kill(os.getpid(), signal.SIGTERM)
sys.exit(7)
''')
        read, write = os.pipe()
        try:
            env = {**self.env, 'NODE_CHANNEL_FD': str(write)}
            result = subprocess.run([str(self.shim), str(self.supervisor), 'literal argument', '7'],
                                    env=env, pass_fds=(write,), capture_output=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(os.read(read, 10), b'IPC intact')
        finally:
            os.close(read)
            os.close(write)
        records = list(self.root.glob('python-*.json'))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text())
        self.assertIn('self CPU', record['timer'])
        self.assertTrue(record['completed'])
        self.assertTrue(records[0].with_suffix('.pstats').is_file())

    def test_killed_supervisor_leaves_explicit_incomplete_identity(self):
        self.supervisor.write_text('import os, signal; os.kill(os.getpid(), signal.SIGKILL)')
        result = subprocess.run([str(self.shim), str(self.supervisor)], env=self.env)
        self.assertEqual(result.returncode, -9)
        inventory = diagnostic.capture_inventory(self.root)
        self.assertTrue(inventory['errors'])
        record = json.loads(next(self.root.glob('python-*.json')).read_text())
        self.assertFalse(record['completed'])

    def test_dump_failure_does_not_replace_supervisor_exit(self):
        self.supervisor.write_text('''import os, pathlib, sys
root = pathlib.Path(os.environ['OXIMUT_PROFILE_DIR'])
identity = next(root.glob('python-*.json'))
identity.with_suffix('.pstats').mkdir()
sys.exit(9)
''')
        result = subprocess.run([str(self.shim), str(self.supervisor)], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 9)
        self.assertIn(b'profile write failed', result.stderr)
        self.assertTrue(diagnostic.capture_inventory(self.root)['errors'])

    def test_unrelated_invocations_exec_real_python_without_profile(self):
        result = subprocess.run([str(self.shim), '-c', 'import sys; sys.exit(23)'], env=self.env)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(list(self.root.glob('python-*.json')), [])
        result = subprocess.run([str(self.shim), '-c', 'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'], env=self.env)
        self.assertEqual(result.returncode, -15)

    def test_incomplete_raw_evidence_is_retained(self):
        file = self.root / 'python-999.json'
        file.write_text(json.dumps({'completed': False}))
        raw = self.root / 'CPU.orphan.cpuprofile'
        raw.write_text('{broken')
        inventory = diagnostic.capture_inventory(self.root)
        self.assertTrue(inventory['errors'])
        self.assertTrue(raw.exists())
        self.assertEqual(inventory['orphan_profiles'][0]['profile'], raw.name)
        self.assertFalse(inventory['orphan_profiles'][0]['profile_finalized'])
        self.assertFalse(inventory['observed'][0]['capture_present_and_finalized'])


class NodeFixtureTests(unittest.TestCase):
    def test_inherited_node_profiles_and_identity_include_fork_with_ipc(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / 'child.cjs'
            child.write_text("process.send({ok: true}); process.disconnect();")
            parent = root / 'parent.cjs'
            parent.write_text("const {fork} = require('node:child_process');\n"
                              "const c = fork(process.argv[2]);\n"
                              "c.on('message', m => { if (!m.ok) process.exitCode = 1; });\n"
                              "c.on('exit', code => { process.exitCode = code; });\n")
            env = {**os.environ, 'OXIMUT_PROFILE_DIR': str(root),
                   'NODE_OPTIONS': f'--cpu-prof --cpu-prof-dir="{root}" --require="{diagnostic.SUPPORT / "node-identity.cjs"}"'}
            result = subprocess.run([node, str(parent), str(child)], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            inventory = diagnostic.capture_inventory(root)
            nodes = [r for r in inventory['observed'] if r['identity'].startswith('node-')]
            self.assertEqual(len(nodes), 2)
            self.assertTrue(all(r['capture_present_and_finalized'] for r in nodes), inventory)
            identities = [json.loads((root / r['identity']).read_text()) for r in nodes]
            self.assertEqual({r['argv'][1] for r in identities}, {str(parent), str(child)})
            self.assertTrue(all(r['startTime'].isdigit() for r in identities))


    def test_exit_identity_write_failure_preserves_original_node_exit(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / 'exit.cjs'
            child.write_text("const fs = require('node:fs'); const path = require('node:path');\n"
                             "const identity = fs.readdirSync(process.env.OXIMUT_PROFILE_DIR).find(f => f.startsWith('node-') && f.endsWith('.json'));\n"
                             "fs.mkdirSync(path.join(process.env.OXIMUT_PROFILE_DIR, identity + '.tmp')); process.exitCode = 23;\n")
            env = {**os.environ, 'OXIMUT_PROFILE_DIR': str(root),
                   'NODE_OPTIONS': f'--cpu-prof --cpu-prof-dir="{root}" --require="{diagnostic.SUPPORT / "node-identity.cjs"}"'}
            result = subprocess.run([node, str(child)], env=env, capture_output=True)
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertIn(b'Diagnostic Node identity write failed', result.stderr)
            identity = json.loads(next(root.glob('node-*.json')).read_text())
            self.assertNotIn('exitObserved', identity)
            self.assertTrue(diagnostic.capture_inventory(root)['errors'])


class MinimumCaptureTests(unittest.TestCase):
    def fixture(self, root):
        import cProfile
        profiles = root / 'profiles'
        profiles.mkdir()
        qualification = root / 'qualification'
        adapter = str(diagnostic.ROOT / 'packages/vitest-adapter')
        entries = [('node', 1, 100, 'vitest', ['node', 'vitest.mjs']),
                   ('node', 2, 100, 'stryker', ['node', 'stryker.mjs']),
                   ('node', 3, 100, 'oximut', ['node', adapter + '/m2-runner.mjs']),
                   ('python', 4, 3, 'oximut', [adapter + '/supervisor.py']),
                   ('node', 5, 4, 'oximut', ['node', adapter + '/m2-worker.ts']),
                   ('node', 6, 5, 'oximut', ['node', 'test-fork.js'])]
        for kind, pid, ppid, tool, argv in entries:
            record = dict(pid=pid, ppid=ppid, cwd=str(qualification / f'00-{tool}' / 'project'),
                          argv=argv, startTime='1')
            if kind == 'node':
                record.update(threadId=0, exitObserved='now')
                profile = profiles / f'CPU.20261028.000000.{pid}.0.001.cpuprofile'
                profile.write_text(json.dumps({'nodes': [{'id': 1}], 'startTime': 1, 'endTime': 2}))
            else:
                record['completed'] = True
                profiler = cProfile.Profile()
                profiler.runcall(lambda: None)
                profiler.dump_stats(str(profiles / f'python-{pid}.pstats'))
            (profiles / f'{kind}-{pid}.json').write_text(json.dumps(record))
        return profiles, qualification

    def test_all_tool_and_oximut_process_roles_must_have_finalized_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles, qualification = self.fixture(Path(tmp))
            inventory = diagnostic.capture_inventory(profiles, qualification)
            self.assertEqual(inventory['errors'], [])
            self.assertTrue(inventory['minimum_role_evidence']['oximut_test_fork'])
        for missing in ('node-1.json', 'node-2.json', 'node-3.json', 'python-4.json', 'node-5.json', 'node-6.json'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                profiles, qualification = self.fixture(Path(tmp))
                (profiles / missing).unlink()
                inventory = diagnostic.capture_inventory(profiles, qualification)
                self.assertTrue(inventory['errors'])
                self.assertTrue(inventory['orphan_profiles'])

    def test_broken_parent_link_and_invalid_identity_fail_closed(self):
        for field, value in [('ppid', 999), ('pid', None)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                profiles, qualification = self.fixture(Path(tmp))
                identity = profiles / 'python-4.json'
                record = json.loads(identity.read_text())
                record[field] = value
                identity.write_text(json.dumps(record))
                inventory = diagnostic.capture_inventory(profiles, qualification)
                self.assertTrue(inventory['errors'])
                self.assertFalse(inventory['minimum_role_evidence']['oximut_supervisor'])

    def test_corrupt_orphan_profile_is_explicitly_retained_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles, qualification = self.fixture(Path(tmp))
            orphan = profiles / 'CPU.orphan.cpuprofile'
            orphan.write_text('{truncated')
            inventory = diagnostic.capture_inventory(profiles, qualification)
            self.assertTrue(inventory['errors'])
            self.assertEqual(inventory['orphan_profiles'], [{'profile': orphan.name,
                             'profile_finalized': False, 'identity_missing': True}])
            self.assertEqual(orphan.read_text(), '{truncated')


if __name__ == '__main__':
    unittest.main()
