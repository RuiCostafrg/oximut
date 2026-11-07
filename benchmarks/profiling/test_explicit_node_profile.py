"""Verify launch flags against the pinned Vitest fork transport, without tests."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_capture_main_routes_workload_node_through_the_profiler_launcher(self):
        """Exercise main's PATH wiring without substituting the fork or capture gates."""
        with tempfile.TemporaryDirectory(prefix="oximut caller's profile ") as temporary:
            root = Path(temporary)
            parent, child, stub = (root / name for name in ('parent.mjs', 'child.mjs', 'workload.py'))
            parent.write_text(PARENT)
            child.write_text(CHILD.replace('ENTRY', json.dumps(self.entry.as_uri())))
            stub.write_text('''import json, pathlib, shutil, subprocess, sys
parent, child = sys.argv[1:3]
output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
output.mkdir()
result = subprocess.run(['node', parent, child], text=True, capture_output=True, timeout=20)
if result.returncode:
    raise RuntimeError(result.stderr)
observed = json.loads(result.stdout)
observed['resolved_node'] = shutil.which('node')
(output / 'launcher-observation.json').write_text(json.dumps(observed))
''')
            output = root / 'capture'
            run = subprocess.run

            def launch_stub(command, **kwargs):
                """Replace only the qualification workload, retaining its supplied environment."""
                if command[:2] == [sys.executable, str(ROOT / 'benchmarks/real_project.py')]:
                    return run([sys.executable, str(stub), str(parent), str(child), *command[2:]], **kwargs)
                return run(command, **kwargs)

            with (mock.patch.dict(os.environ, {'NODE_OPTIONS': ''}),
                  mock.patch.object(diagnostic.workload, 'execution_inputs', return_value={'fixture': True}) as inputs,
                  mock.patch.object(diagnostic.subprocess, 'run', side_effect=launch_stub),
                  redirect_stdout(io.StringIO())):
                status = diagnostic.main(['--binary', str(root / 'unused-binary'),
                                          '--output', str(output), '--cpu-prof-exec-argv'])
            report = json.loads((output / 'diagnostic.json').read_text())
            observed = json.loads((output / 'qualification/launcher-observation.json').read_text())
            self.assertEqual(observed['resolved_node'], str(output / 'shim/node'))
            self.assertFalse(observed['timedOut'])
            self.assertIs(observed['ready']['ready'], True)
            self.assertEqual(observed['ready']['execArgv'], report['node_launcher']['exec_argv'])
            self.assertEqual(observed['status'], {'code': 0, 'signal': None})
            self.assertEqual(report['node_capture_mode'], 'exit-time-cpu-prof-exec-argv')
            profiles, pid = output / 'profiles', observed['pid']
            identity = json.loads((profiles / f'node-{pid}-0.json').read_text())
            self.assertEqual(identity['pid'], pid)
            self.assertEqual(identity['argv'][1:], [str(child)])
            self.assertEqual(identity['execArgv'], report['node_launcher']['exec_argv'])
            self.assertEqual(identity['exitCode'], 0)
            self.assertIn('exitObserved', identity)
            self.assertTrue(identity['startTime'].isascii() and identity['startTime'].isdecimal())
            captures = list(profiles.glob(f'CPU.*.{pid}.0.*.cpuprofile'))
            self.assertEqual(len(captures), 1)
            self.assertTrue(diagnostic.node_profile_finalized(json.loads(captures[0].read_text())))
            self.assertEqual(inputs.call_count, 2)
            self.assertIsNone(report['failure'])
            # A launcher fixture is not a qualified three-tool colord campaign.
            self.assertEqual(status, 1)
            self.assertFalse(report['passed'])
            self.assertTrue(report['qualification_errors'])
            self.assertTrue(report['captures']['errors'])

    def test_real_node_profile_accepts_the_pinned_vitest_wrapper_offset(self):
        """Accept an untouched VM profile emitted with Vitest's actual wrapper layout."""
        script = r'''
import {Session} from 'node:inspector';
import vm from 'node:vm';
const names=['__vite_ssr_exports__','__vite_ssr_import_meta__','__vite_ssr_import__',
 '__vite_ssr_dynamic_import__','__vite_ssr_exportAll__','__vite_ssr_exportName__',
 '__filename','__dirname','module','exports','require'];
const prefix=`'use strict';async (${names.join(',')})=>{{`;
const session=new Session();session.connect();
const post=(method,params={})=>new Promise((resolve,reject)=>session.post(method,params,(e,r)=>e?reject(e):resolve(r)));
await post('Profiler.enable');await post('Profiler.setSamplingInterval',{interval:10000});
await post('Profiler.start');
await vm.runInThisContext(prefix+'const until=Date.now()+150;while(Date.now()<until){}\n}}',
 {filename:'file:///oximut-wrapper-probe/module.mjs',lineOffset:0,columnOffset:-prefix.length})();
const {profile}=await post('Profiler.stop');session.disconnect();
console.log(JSON.stringify({prefixLength:prefix.length,profile}));
'''
        result = subprocess.run([NODE, '--input-type=module', '-e', script],
                                text=True, capture_output=True, timeout=10,
                                env={**os.environ, 'NODE_OPTIONS': ''})
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record['prefixLength'], 209)
        profile = record['profile']
        wrappers = [node['callFrame'] for node in profile['nodes']
                    if node['callFrame']['url'] == 'file:///oximut-wrapper-probe/module.mjs'
                    and node['callFrame']['columnNumber'] == -196]
        self.assertTrue(wrappers, 'real profiler must exercise the negative wrapper location')
        self.assertTrue(all(frame['functionName'] == '' and frame['lineNumber'] == 0 for frame in wrappers))
        self.assertTrue(diagnostic.node_profile_finalized(profile))


if __name__ == '__main__':
    unittest.main()
