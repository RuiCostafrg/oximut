"""Real kernel boundary regressions; CI requires execution, never a mocked sandbox."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import socket
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
for target in {list(map(str, [self.harness, self.evidence]))!r}:
    # Parent directories may exist for bind mounts, but host files must not.
    assert not Path(target).exists(), target
    try:
        Path(target).write_text('{{"status":"passed"}}')
    except OSError:
        pass
# A successful write above creates only a private tmpfs shadow. The trusted
# parent checks original host bytes after this namespace has been destroyed.
assert 'HOST_SECRET_SENTINEL' not in os.environ
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run').exists()
status = Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert os.geteuid() != 0
for cap in ('CapEff', 'CapPrm', 'CapAmb'):
    assert cap + ':\t0000000000000000' in status
import subprocess
assert subprocess.run(['/usr/bin/unshare', '--user', '--map-root-user', '/bin/true'],
                      capture_output=True).returncode != 0
print('ATTACKS_COMPLETED')
'''

    def test_native_compiler_alias_can_build_and_execute(self):
        source = self.work / 'main.c'
        source.write_text('int main(void) { return 0; }\n')
        binary = self.work / 'compiled'
        SANDBOX.run(['/bin/sh', '-ec', 'cc main.c -o compiled && ./compiled'],
                    cwd=self.work, writable=[self.work])
        self.assertTrue(binary.is_file())

    def test_smoke_network_namespace_cannot_reach_host_loopback(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            code = f"""import socket
s = socket.socket()
s.settimeout(1)
assert s.connect_ex(('127.0.0.1', {port})) != 0
print('ISOLATED')
"""
            self.assertEqual(SANDBOX.run(['/usr/bin/python3', '-c', code],
                                        cwd=self.work, writable=[self.work]), 'ISOLATED')

    def test_hostile_install_cannot_forge_trusted_files(self):
        script = self.work / 'install-source.py'
        script.write_text(self.attack())
        os.environ['HOST_SECRET_SENTINEL'] = 'must-not-inherit'
        self.addCleanup(os.environ.pop, 'HOST_SECRET_SENTINEL', None)
        log = SANDBOX.run(['/usr/bin/python3', script], cwd=self.work,
                          writable=[self.work], network=True)
        self.assertIn('ATTACKS_COMPLETED', log)
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
        for parent in ('pass', 'os._exit(7)', 'time.sleep(10)'):
            if parent == 'pass':
                SANDBOX.run(['/usr/bin/python3', '-c', code.replace('{parent}', parent)],
                            cwd=self.work, writable=[self.work])
            elif parent == 'os._exit(7)':
                with self.assertRaisesRegex(RuntimeError, 'exited 7'):
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


class SandboxConstruction(unittest.TestCase):
    def test_isolated_project_cache_writes_preserve_installed_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            prefix = base / 'prefix'
            installed = prefix / 'lib/oximut/node_modules'
            installed.mkdir(parents=True)
            (installed / 'asset.js').write_text('original')
            root = base / 'project'
            root.mkdir()
            SMOKE.project_dependencies(prefix, root, True)
            private = root / 'node_modules'
            self.assertFalse(private.is_symlink())
            (private / '.vite-temp').mkdir()
            (private / '.vite-temp/config.mjs').write_text('compiled config')
            (private / 'asset.js').write_text('private change')
            self.assertEqual((installed / 'asset.js').read_text(), 'original')
            self.assertFalse((installed / '.vite-temp').exists())


    def test_namespace_and_privilege_contract(self):
        for network in (False, True):
            with self.subTest(network=network), \
                    mock.patch.object(SANDBOX.platform, 'release', return_value='native'), \
                    mock.patch.object(Path, 'is_file', return_value=True), \
                    mock.patch.object(SANDBOX.os, 'geteuid', return_value=1001), \
                    mock.patch.object(SANDBOX.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name if name != 'rustup' else None), \
                    mock.patch.object(SANDBOX.subprocess, 'Popen') as popen, \
                    mock.patch.object(SANDBOX.os, 'killpg'):
                popen.return_value.returncode = 0
                SANDBOX.run(['/bin/true'], cwd='/tmp', writable=[], network=network)
                args = popen.call_args.args[0]
                for option in ('--unshare-all', '--unshare-user', '--disable-userns',
                               '--die-with-parent', '--new-session', '--clearenv'):
                    self.assertIn(option, args)
                self.assertEqual(args[args.index('--cap-drop') + 1], 'ALL')
                self.assertNotIn('--cap-add', args)
                self.assertEqual('--share-net' in args, network)
                self.assertEqual(args[-2:], ['--', '/bin/true'])
                self.assertEqual(popen.call_args.kwargs['env'], {'PATH': '/usr/bin:/bin'})

    def test_root_execution_rejected(self):
        with mock.patch.object(SANDBOX.platform, 'release', return_value='native'), \
                mock.patch.object(Path, 'is_file', return_value=True), \
                mock.patch.object(SANDBOX.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'unprivileged host UID'):
                SANDBOX.run(['/bin/true'], cwd='/tmp', writable=[])
