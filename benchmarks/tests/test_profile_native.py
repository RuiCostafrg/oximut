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
    def test_display_summary_is_bounded_without_changing_raw_errors(self):
        """Large inventories and errors belong in artifacts rather than step summaries."""
        errors = ['x' * 2000 for _ in range(10000)]
        result = dict(passed=False, failure='failure' * 1000, captures=dict(
            errors=errors, observed=[{}] * 10000, orphan_profiles=[{}] * 10000))
        summary = diagnostic.compact_summary(result)
        self.assertEqual(summary['error_count'], 10000)
        self.assertEqual(summary['observed_identity_count'], 10000)
        self.assertEqual(summary['orphan_profile_count'], 10000)
        self.assertEqual(len(summary['first_errors']), 5)
        self.assertLess(len(json.dumps(summary)), 4000)
        self.assertEqual(result['captures']['errors'], errors)

    def rows(self):
        """Build one equivalent three-tool result group for qualification fixtures."""
        return [dict(tool=tool, round=0, warmup=False, success=True,
                     memory_method='sum-live-smaps_rollup-rss', memory_errors=[],
                     declared_test_identity_sha256='same', mutator_families=['arithmetic'],
                     mutations={str(i): {'outcome': 'killed'} for i in range(10)},
                     **{metric: 2. for metric in diagnostic.workload.METRICS})
                for tool in ('vitest', 'oximut', 'stryker')]

    def errors(self, rows, code=0, ratios=None):
        """Validate fixture evidence through the public qualification checker."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'summary.json').write_text(json.dumps({'correctness_gate': True, 'ratios': ratios or []}))
            (root / 'measurements.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            return diagnostic.qualification_errors(root, code)

    def test_full_group_passes_but_missing_duplicate_failed_and_ratios_reject(self):
        """Reject incomplete groups and comparative output in diagnostic mode."""
        rows = self.rows()
        self.assertEqual(self.errors(rows), [])
        self.assertTrue(self.errors(rows[:2]))
        self.assertTrue(self.errors(rows + [copy.deepcopy(rows[0])]))
        self.assertTrue(self.errors(rows, code=1))
        self.assertTrue(self.errors(rows, ratios=[{'wall_seconds': 1}]))
        rows[1]['success'] = False
        self.assertTrue(self.errors(rows))

    def test_wrong_edit_count_or_outcome_reject_even_equal_cross_tool(self):
        """Require the declared killed edits even when both tools agree."""
        for count, outcome in [(9, 'killed'), (10, 'survived'), (11, 'killed')]:
            rows = self.rows()
            for row in rows:
                row['mutations'] = {str(i): {'outcome': outcome} for i in range(count)}
            self.assertTrue(self.errors(rows))

    def test_missing_summary_rejected(self):
        """Treat absent qualification evidence as failure."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(diagnostic.qualification_errors(Path(tmp), 0))

    def test_preflight_failure_retains_explicit_failed_summary(self):
        """Retain explicit failure evidence before any workload starts."""
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
        """Prepare an isolated interpreter shim and its target supervisor."""
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
        """Preserve supervisor arguments, inherited IPC and nonzero exit."""
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
        """Keep killed supervisors visibly incomplete."""
        self.supervisor.write_text('import os, signal; os.kill(os.getpid(), signal.SIGKILL)')
        result = subprocess.run([str(self.shim), str(self.supervisor)], env=self.env)
        self.assertEqual(result.returncode, -9)
        inventory = diagnostic.capture_inventory(self.root)
        self.assertTrue(inventory['errors'])
        record = json.loads(next(self.root.glob('python-*.json')).read_text())
        self.assertFalse(record['completed'])

    def test_dump_failure_does_not_replace_supervisor_exit(self):
        """A profile write error must preserve the target exit code."""
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
        """Delegate unrelated Python commands without instrumentation."""
        result = subprocess.run([str(self.shim), '-c', 'import sys; sys.exit(23)'], env=self.env)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(list(self.root.glob('python-*.json')), [])
        result = subprocess.run([str(self.shim), '-c', 'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'], env=self.env)
        self.assertEqual(result.returncode, -15)

    def test_incomplete_raw_evidence_is_retained(self):
        """Retain malformed evidence and report incomplete captures."""
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
    def test_initial_identity_and_stderr_write_failures_preserve_workload_exit(self):
        """A failing preload diagnostic must still allow the target to run and exit."""
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        for fail_stderr in (False, True):
            with self.subTest(fail_stderr=fail_stderr), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sabotage = root / 'bad-fs.cjs'
                sabotage.write_text("const fs = require('node:fs'); const path = require('node:path');\n"
                                    "const write = fs.writeFileSync; fs.writeFileSync = (p,...args) => {\n"
                                    "if (path.basename(String(p)).startsWith('node-')) throw Error('initial fixture'); return write(p,...args); };\n"
                                    + ("const raw = fs.writeSync; fs.writeSync = (fd,...args) => { if(fd===2) throw Error('stderr fixture'); return raw(fd,...args); };\n"
                                       if fail_stderr else ''))
                child = root / 'workload.cjs'
                child.write_text("console.log('workload ran'); process.exitCode = 23;")
                env = {**os.environ, 'OXIMUT_PROFILE_DIR': str(root),
                       'NODE_OPTIONS': f'--cpu-prof --cpu-prof-dir="{root}" --require="{sabotage}" --require="{diagnostic.SUPPORT / "node-identity.cjs"}"'}
                result = subprocess.run([node, str(child)], env=env, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 23, result.stderr)
                self.assertIn(b'workload ran', result.stdout)
                if not fail_stderr:
                    self.assertIn(b'Diagnostic Node identity write failed', result.stderr)
                self.assertEqual(list(root.glob('node-*.json')), [])
                inventory = diagnostic.capture_inventory(root)
                self.assertTrue(inventory['errors'])
                self.assertTrue(inventory['orphan_profiles'])

    def test_segment_timer_starvation_and_kill_before_checkpoint_stay_incomplete(self):
        """Do not infer capture completeness from a requested checkpoint interval."""
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        for busy in (False, True):
            with self.subTest(busy=busy), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                child = root / 'kill.cjs'
                child.write_text("console.log('ready');\n" + ('while (true) {}' if busy else 'setInterval(() => {}, 1000);'))
                env = {**os.environ, 'OXIMUT_PROFILE_DIR': str(root), 'OXIMUT_PROFILE_SEGMENTS': '1',
                       'NODE_OPTIONS': f'--require="{diagnostic.SUPPORT / "node-identity.cjs"}"'}
                process = subprocess.Popen([node, str(child)], env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True)
                self.addCleanup(lambda p=process: p.poll() is None and p.kill())
                import select
                self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                if busy:
                    # A busy isolate cannot checkpoint even after several nominal intervals.
                    import time
                    time.sleep(.2)
                process.kill()
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, -9)
                inventory = diagnostic.capture_inventory(root)
                self.assertTrue(inventory['errors'])
                self.assertFalse(inventory['observed'][0]['capture_present_and_finalized'])
                if busy:
                    self.assertEqual(list(root.glob('Segment.*.cpuprofile')), [])

    def test_segment_preload_is_inherited_by_ipc_fork_and_terminated_worker_thread(self):
        """Capture partial isolate stacks while preserving fork IPC."""
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / 'child.cjs'
            child.write_text("setTimeout(() => { process.send('ok'); process.disconnect(); }, 220);")
            parent = root / 'parent.cjs'
            parent.write_text("const {fork} = require('node:child_process'); const {Worker} = require('node:worker_threads');\n"
                              "const w = new Worker('setInterval(() => {}, 10)', {eval:true});\n"
                              "const c = fork(process.argv[2]); c.on('message', m => { if(m !== 'ok') process.exitCode=1; });\n"
                              "setTimeout(() => w.terminate(), 300);\n")
            env = {**os.environ, 'OXIMUT_PROFILE_DIR': str(root), 'OXIMUT_PROFILE_SEGMENTS': '1',
                   'NODE_OPTIONS': f'--require="{diagnostic.SUPPORT / "node-identity.cjs"}"'}
            result = subprocess.run([node, str(parent), str(child)], env=env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            inventory = diagnostic.capture_inventory(root)
            nodes = inventory['observed']
            self.assertEqual(len(nodes), 3, inventory)
            self.assertTrue(all(r['partial_segments_finalized'] for r in nodes), inventory)
            self.assertTrue(inventory['errors'])
            self.assertEqual(inventory['orphan_profiles'], [])

    def test_segment_checkpoints_survive_sigterm_without_changing_signal_exit(self):
        """Retain partial stacks without intercepting termination."""
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / 'terminated.cjs'
            child.write_text("setInterval(() => { for (let i=0;i<10000;i++) Math.sqrt(i); }, 5);\n"
                             "setTimeout(() => console.log('ready'), 220);\n")
            env = {**os.environ, 'OXIMUT_PROFILE_DIR': str(root), 'OXIMUT_PROFILE_SEGMENTS': '1',
                   'NODE_OPTIONS': f'--require="{diagnostic.SUPPORT / "node-identity.cjs"}"'}
            process = subprocess.Popen([node, str(child)], env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            # Poll the pipe with a bound so preload regressions cannot hang the suite.
            import select
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            process.terminate()
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, -15, stderr)
            record = json.loads(next(root.glob('node-*.json')).read_text())
            self.assertNotIn('exitObserved', record)
            inventory = diagnostic.capture_inventory(root)
            captured = inventory['observed'][0]
            self.assertTrue(captured['partial_segments_finalized'], inventory)
            self.assertEqual(captured['partial_segment_count'], 1)
            events = [json.loads(line) for line in next(root.glob('*.segment-events.jsonl')).read_text().splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['maxSegmentsPerIsolate'], 1)
            self.assertNotIn('restartedAtNs', events[0])
            self.assertGreaterEqual(int(events[0]['disconnectedAtNs']), int(events[0]['stoppedAtNs']))
            self.assertFalse(captured['capture_present_and_finalized'])
            self.assertTrue(inventory['errors'])
            self.assertEqual(inventory['orphan_profiles'], [])

    def test_segment_capture_does_not_retain_event_loop_or_replace_failure_status(self):
        """Allow natural exit and preserve status after checkpoint write failure."""
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        for fail_write in (False, True):
            with self.subTest(fail_write=fail_write), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                child = root / 'exit.cjs'
                child.write_text("const fs = require('node:fs'); const original = fs.renameSync;\n"
                                 + ("fs.renameSync = (a,b) => { if (String(b).includes('Segment.')) throw Error('segment fixture'); return original(a,b); };\n"
                                    if fail_write else '')
                                 + "setTimeout(() => { process.exitCode = 23; }, 160);\n")
                env = {**os.environ, 'OXIMUT_PROFILE_DIR': str(root), 'OXIMUT_PROFILE_SEGMENTS': '1',
                       'NODE_OPTIONS': f'--require="{diagnostic.SUPPORT / "node-identity.cjs"}"'}
                result = subprocess.run([node, str(child)], env=env, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 23, result.stderr)
                if fail_write:
                    self.assertIn(b'Diagnostic segment capture failed', result.stderr)
                else:
                    self.assertTrue(list(root.glob('Segment.*.cpuprofile')))

    def test_inherited_node_profiles_and_identity_include_fork_with_ipc(self):
        """Link normally exiting child profiles without breaking their IPC."""
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
        """Keep the initial identity when exit-record writes fail."""
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
    def test_thousands_of_segments_keep_metadata_bounded_and_raw_files_intact(self):
        """Checkpoint names are summarized by count and chronological endpoints."""
        with tempfile.TemporaryDirectory() as tmp:
            profiles, qualification = self.fixture(Path(tmp))
            initial = diagnostic.capture_inventory(profiles, qualification)
            empty = next(r for r in initial['observed'] if r['identity'] == 'node-6.json')
            self.assertEqual(empty['partial_segment_count'], 0)
            self.assertEqual(empty['partial_segment_range'], [])
            raw = json.dumps({'nodes': [{'id': 1}], 'startTime': 1, 'endTime': 2})
            for index in range(2000):
                (profiles / f'Segment.6.0.1.{index}.cpuprofile').write_text(raw)
            inventory = diagnostic.capture_inventory(profiles, qualification)
            observed = next(r for r in inventory['observed'] if r['identity'] == 'node-6.json')
            self.assertEqual(observed['partial_segment_count'], 2000)
            self.assertEqual(observed['partial_segment_range'],
                             ['Segment.6.0.1.0.cpuprofile', 'Segment.6.0.1.1999.cpuprofile'])
            self.assertNotIn('partial_segments', observed)
            self.assertLess(len(json.dumps(observed)), 600)
            self.assertEqual(inventory['errors'], [])
            self.assertEqual(len(list(profiles.glob('Segment.*.cpuprofile'))), 2000)

    def fixture(self, root):
        """Create finalized evidence for each required tool and Oximut role."""
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
        """Require evidence for every declared tool and Oximut role."""
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

    def test_extra_killed_node_does_not_pass_when_all_required_roles_are_complete(self):
        """Complete role coverage cannot hide an additional incomplete process."""
        with tempfile.TemporaryDirectory() as tmp:
            profiles, qualification = self.fixture(Path(tmp))
            extra = dict(pid=7, ppid=5, threadId=0, startTime='2',
                         cwd=str(qualification / '00-oximut' / 'project'),
                         argv=['node', 'terminated-extra-fork.js'])
            (profiles / 'node-7.json').write_text(json.dumps(extra))
            inventory = diagnostic.capture_inventory(profiles, qualification)
            self.assertTrue(all(inventory['minimum_role_evidence'].values()))
            self.assertIn('incomplete capture: node-7.json', inventory['errors'])
            observed = next(r for r in inventory['observed'] if r['identity'] == 'node-7.json')
            self.assertFalse(observed['capture_present_and_finalized'])
            self.assertFalse(observed['exit_observed'])
            self.assertEqual(observed['profiles'], [])

    def test_broken_parent_link_and_invalid_identity_fail_closed(self):
        """Reject invalid identities and broken process ancestry."""
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

    def test_partial_fork_stack_is_usable_but_never_makes_capture_complete(self):
        """Separate usable partial stacks from lifecycle completeness."""
        with tempfile.TemporaryDirectory() as tmp:
            profiles, qualification = self.fixture(Path(tmp))
            identity = profiles / 'node-6.json'
            record = json.loads(identity.read_text())
            del record['exitObserved']
            identity.write_text(json.dumps(record))
            (profiles / 'CPU.20261028.000000.6.0.001.cpuprofile').rename(
                profiles / 'Segment.6.0.1.0.cpuprofile')
            inventory = diagnostic.capture_inventory(profiles, qualification)
            self.assertTrue(inventory['minimum_role_evidence']['oximut_test_fork'])
            self.assertIn('incomplete capture: node-6.json', inventory['errors'])
            self.assertEqual(inventory['orphan_profiles'], [])

    def test_corrupt_orphan_profile_is_explicitly_retained_and_rejected(self):
        """Preserve orphan bytes while rejecting them as capture evidence."""
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
