"""Verify launch flags against the pinned Vitest fork transport, without tests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'benchmarks'))
import profile_native as diagnostic

NODE = shutil.which('node')

# Import the actual worker entry before announcing readiness. No substitute
# Vitest implementation, test runner, or profiling signal handler is installed.
CHILD = "import ENTRY; process.send({ready:true,execArgv:process.execArgv});\n"
PARENT = r'''
import {fork} from 'node:child_process';
const child=fork(process.argv[2],[],{stdio:['ignore','ignore','pipe','ipc']});
let ready,stderr='',timedOut=false;
child.stderr.on('data',value=>{stderr+=value;});
const timer=setTimeout(()=>{timedOut=true;child.kill('SIGKILL');},10000);
child.on('message',message=>{ready=message;child.kill('SIGTERM');});
const status=await new Promise((resolve,reject)=>{
 child.once('error',reject);child.once('close',(code,signal)=>resolve({code,signal}));
});
clearTimeout(timer);
console.log(JSON.stringify({ready,status,stderr,timedOut,pid:child.pid}));
'''


@unittest.skipUnless(NODE and os.name == 'posix' and Path('/proc/self/stat').exists(),
                     'requires Node and Linux process identities')
class ExplicitNodeProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Resolve the pinned fork transport or explicitly skip missing dependencies."""
        result = subprocess.run([NODE, '-p', 'require.resolve("vitest/package.json")'],
                                cwd=ROOT, text=True, capture_output=True, timeout=15)
        if result.returncode:
            if "Cannot find module 'vitest/package.json'" in result.stderr:
                raise unittest.SkipTest('pinned Vitest dependencies not installed')
            raise AssertionError('cannot resolve installed Vitest: ' + result.stderr)
        cls.entry = Path(result.stdout.strip()).parent / 'dist/workers/forks.js'
        if json.loads(Path(result.stdout.strip()).read_text())['version'] != '4.1.12':
            raise AssertionError('profiling contract requires pinned Vitest 4.1.12')
        if not cls.entry.is_file():
            raise AssertionError('installed Vitest fork entry missing')

    def test_explicit_launch_flags_finalize_real_vitest_forks_on_sigterm(self):
        """NODE_OPTIONS is a negative control for the real transport's flag gate."""
        with tempfile.TemporaryDirectory(prefix="oximut profile's ") as temporary:
            root = Path(temporary)
            parent, child = root / 'parent.mjs', root / 'child.mjs'
            parent.write_text(PARENT)
            child.write_text(CHILD.replace('ENTRY', json.dumps(self.entry.as_uri())))
            for explicit in (False, True):
                with self.subTest(explicit=explicit):
                    profiles = root / str(explicit)
                    profiles.mkdir()
                    launcher = profiles / 'bin'
                    launcher.mkdir()
                    env = {**os.environ, 'OXIMUT_PROFILE_DIR': str(profiles),
                           'OXIMUT_PROFILE_SEGMENTS': '0',
                           'NODE_OPTIONS': '--require=' + json.dumps(str(diagnostic.SUPPORT / 'node-identity.cjs'))}
                    if explicit:
                        record = diagnostic.install_node_profiler_launcher(launcher, profiles)
                        command = str(launcher / 'node')
                    else:
                        env['NODE_OPTIONS'] += ' --cpu-prof --cpu-prof-dir=' + json.dumps(str(profiles))
                        command = NODE
                    result = subprocess.run([command, str(parent), str(child)], cwd=ROOT,
                                            env=env, text=True, capture_output=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    observed = json.loads(result.stdout)
                    self.assertFalse(observed['timedOut'])
                    self.assertIs(observed['ready']['ready'], True)
                    pid = observed['pid']
                    identity = json.loads((profiles / f'node-{pid}-0.json').read_text())
                    self.assertEqual(identity['pid'], pid)
                    self.assertEqual(identity['argv'][1:], [str(child)])
                    self.assertEqual(identity['cwd'], str(ROOT))
                    self.assertTrue(identity['startTime'].isascii() and identity['startTime'].isdecimal())
                    captures = list(profiles.glob(f'CPU.*.{pid}.0.*.cpuprofile'))
                    if explicit:
                        self.assertEqual(observed['ready']['execArgv'], record['exec_argv'])
                        self.assertEqual(observed['status'], {'code': 0, 'signal': None})
                        self.assertEqual(identity['exitCode'], 0)
                        self.assertIn('exitObserved', identity)
                        self.assertEqual(len(captures), 1)
                        self.assertTrue(diagnostic.node_profile_finalized(json.loads(captures[0].read_text())))
                    else:
                        self.assertEqual(observed['ready']['execArgv'], [])
                        self.assertEqual(observed['status'], {'code': None, 'signal': 'SIGTERM'})
                        self.assertNotIn('exitObserved', identity)
                        self.assertEqual(captures, [])


if __name__ == '__main__':
    unittest.main()
