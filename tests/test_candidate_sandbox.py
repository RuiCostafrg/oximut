"""Real kernel boundary regressions; CI requires execution, never a mocked sandbox."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SANDBOX = load('candidate-sandbox')
SMOKE = load('smoke-install')
GATE = load('validate-source')


class CandidateSandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            SANDBOX.run(['/bin/true'], cwd='/tmp', writable=[])
        except (RuntimeError, OSError) as error:
            if os.environ.get('OXIMUT_REQUIRE_SANDBOX') == '1':
                raise
            raise unittest.SkipTest(f'kernel boundary unavailable: {error}')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.work = self.base / 'candidate'
        self.work.mkdir()
        self.harness = self.base / 'trusted.py'
        self.evidence = self.base / 'evidence.json'
        self.harness.write_text('trusted')
        self.evidence.write_text('{"status":"failed"}')

    def attack(self):
        return f'''import os, json
from pathlib import Path
denied = []
for target in {list(map(str, [self.harness, self.evidence]))!r}:
    try:
        Path(target).write_text('{{"status":"passed"}}')
    except OSError:
        denied.append(target)
assert len(denied) == 2, denied
assert 'HOST_SECRET_SENTINEL' not in os.environ
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run').exists()
status = Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert 'CapEff:\t0000000000000000' in status
print('DENIED_BOTH')
'''

    def test_hostile_install_cannot_forge_trusted_files(self):
        script = self.work / 'install-source.py'
        script.write_text(self.attack())
        os.environ['HOST_SECRET_SENTINEL'] = 'must-not-inherit'
        self.addCleanup(os.environ.pop, 'HOST_SECRET_SENTINEL', None)
        log = SANDBOX.run(['/usr/bin/python3', script], cwd=self.work,
                          writable=[self.work], network=True)
        self.assertIn('DENIED_BOTH', log)
        self.assertEqual(self.harness.read_text(), 'trusted')
        self.assertEqual(json.loads(self.evidence.read_text())['status'], 'failed')

    def test_hostile_installer_zero_exit_cannot_forge_passing_evidence(self):
        output = self.base / 'validation'
        self.evidence = output / 'evidence.json'
        files = {'scripts/install-source.py': (0o644, self.attack().encode(), 0)}
        with mock.patch.object(GATE, 'inspect_archive', return_value=(
                files, {'source': {'commit': 'a' * 40}}, 'b' * 64)), \
                mock.patch.object(GATE, 'run', return_value='tool version'):
            with self.assertRaises(FileNotFoundError):
                GATE.validate(self.base / 'unused', '1.0.0-rc.1', 'a' * 40, 'c' * 40, output)
        self.assertEqual(json.loads(self.evidence.read_text())['status'], 'failed')
        self.assertEqual(self.harness.read_text(), 'trusted')

    def test_hostile_installed_and_relocated_commands_cannot_pass_smoke(self):
        prefix = self.work / 'prefix'
        (prefix / 'bin').mkdir(parents=True)
        (prefix / 'lib/oximut/node_modules').mkdir(parents=True)
        launcher = prefix / 'bin/oximut'
        # Exit zero after the attacks. Missing/false output still fails in the trusted harness.
        launcher.write_text('#!/usr/bin/python3\n' + self.attack())
        launcher.chmod(0o755)
        for phase in ('installed', 'relocated'):
            if phase == 'relocated':
                destination = self.work / 'relocated prefix'
                prefix.rename(destination)
                prefix = destination
            with self.subTest(phase=phase), self.assertRaises(FileNotFoundError):
                SMOKE.smoke(prefix, reports_dir=self.base / phase,
                            validator=ROOT / 'scripts/validate-reports.mjs', isolated=True)
            self.assertEqual(self.harness.read_text(), 'trusted')
            self.assertEqual(json.loads(self.evidence.read_text())['status'], 'failed')

    def test_success_failure_and_timeout_kill_detached_descendants(self):
        self.assertEqual(SANDBOX.run(['/bin/echo', 'ok'], cwd=self.work,
                                    writable=[self.work]), 'ok')
        with self.assertRaisesRegex(RuntimeError, 'exited 7'):
            SANDBOX.run(['/bin/sh', '-c', 'exit 7'], cwd=self.work, writable=[self.work])
        marker = self.work / 'late-write'
        code = f'''import os, time
if os.fork() == 0:
    os.setsid()
    time.sleep(0.4)
    open({str(marker)!r}, 'w').write('survived')
else:
    {{parent}}
'''
        for parent in ('pass', 'time.sleep(10)'):
            if parent == 'pass':
                SANDBOX.run(['/usr/bin/python3', '-c', code.replace('{parent}', parent)],
                            cwd=self.work, writable=[self.work])
            else:
                with self.assertRaises(subprocess.TimeoutExpired):
                    SANDBOX.run(['/usr/bin/python3', '-c', code.replace('{parent}', parent)],
                                cwd=self.work, writable=[self.work], timeout=0.1)
            time.sleep(0.6)
            self.assertFalse(marker.exists(), 'detached writer survived namespace teardown')

    @unittest.skipUnless(os.environ.get('OXIMUT_TEST_BINARY'), 'real binary supplied by Ubuntu CI')
    def test_legitimate_installed_and_relocated_smoke(self):
        prefix = self.work / 'legitimate prefix'
        bundle = prefix / 'lib/oximut'
        bundle.mkdir(parents=True)
        (prefix / 'bin').mkdir()
        for name in ('package.json', 'package-lock.json', '.npmrc', 'LICENSE'):
            shutil.copy2(ROOT / name, bundle / name)
        shutil.copy2(os.environ['OXIMUT_TEST_BINARY'], bundle / 'oximut')
        shutil.copy2(ROOT / 'scripts/oximut-launcher.py', prefix / 'bin/oximut')
        (prefix / 'bin/oximut').chmod(0o755)
        shutil.copytree(ROOT / 'packages/vitest-adapter', bundle / 'packages/vitest-adapter')
        shutil.copytree(ROOT / 'node_modules', bundle / 'node_modules', symlinks=True)
        for phase in ('installed', 'relocated'):
            if phase == 'relocated':
                destination = self.work / 'relocated legitimate prefix'
                prefix.rename(destination)
                prefix = destination
            SANDBOX.safe_tree(prefix)
            results = SMOKE.smoke(prefix, reports_dir=self.base / phase,
                                  validator=ROOT / 'scripts/validate-reports.mjs', isolated=True)
            self.assertEqual(len(results), 12)

    def test_readonly_assets_and_untrusted_output_types(self):
        asset = self.base / 'asset'
        asset.write_text('exact')
        with self.assertRaises(RuntimeError):
            SANDBOX.run(['/bin/sh', '-c', f'echo forged > {asset}'], cwd=self.work,
                        writable=[self.work], readonly=[asset])
        self.assertEqual(asset.read_text(), 'exact')
        report = self.work / 'report.json'
        report.symlink_to(self.evidence)
        with self.assertRaises(OSError):
            SANDBOX.read_regular(report)
        with self.assertRaises(ValueError):
            SANDBOX.safe_tree(self.work)
        report.unlink()
        os.mkfifo(report)
        with self.assertRaises(ValueError):
            SANDBOX.read_regular(report)
