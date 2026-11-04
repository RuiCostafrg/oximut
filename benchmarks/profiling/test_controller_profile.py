"""Diagnostic IPC fixtures only; no builds, dependencies or performance claims."""
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

PRELOAD = ROOT / 'benchmarks/profiling/controller-jobs.cjs'
NODE = shutil.which('node')

CONTROLLER = r'''
const { fork } = require('node:child_process');
process.on('message', async msg => {
  if (msg.command === 'run') {
    const child = fork(process.env.FIXTURE_CHILD, [], { stdio: ['ignore','ignore','inherit','ipc'] });
    await new Promise(resolve => {
      child.on('message', m => {
        if (m.type === 'started') child.send({__vitest_worker_request__:true,type:'run'});
        if (m.type === 'testfileFinished') child.send({__vitest_worker_request__:true,type:'stop'});
      });
      child.once('exit', resolve);
      child.send({__vitest_worker_request__:true,type:'start'});
    });
  }
  const returned = process.send({protocol:1,id:msg.id,result:{ok:true}}, () => console.log('callback'));
  console.log('returned:' + typeof returned);
  if (msg.command === 'close') process.disconnect();
});
process.once('disconnect', () => { process.exitCode = 23; });
'''
CHILD = r'''
process.on('message', m => {
  if (m.type === 'start') process.send({__vitest_worker_response__:true,type:'started'});
  if (m.type === 'run') {
    process.send({m:'onCollected',a:['DO NOT LOG PAYLOAD']});
    for(let i=0;i<30;i++) process.send({m:'onTaskUpdate',a:['DO NOT LOG PAYLOAD']});
    process.send({__vitest_worker_response__:true,type:'testfileFinished'});
  }
  if (m.type === 'stop') {
    process.send({__vitest_worker_response__:true,type:'stopped'});
    process.disconnect();
  }
});
'''
LAUNCHER = r'''
const { fork } = require('node:child_process');
(async () => {
  const c = fork(process.env.FIXTURE_CONTROLLER, [], {stdio:['ignore','inherit','inherit','ipc']});
  const replies=[];
  for (const [id,command,kind] of [[1,'initialize'],[2,'run','baseline'],[3,'run','mutant'],[4,'run','mutant'],[5,'close']]) {
    const response = new Promise(resolve => c.once('message', resolve));
    c.send({protocol:1,id,command,args:{request:{kind}}});
    replies.push(await response);
  }
  c.on('exit',(code) => console.log(JSON.stringify({replies,code})));
})();
'''


@unittest.skipUnless(NODE, 'Node unavailable')
class ControllerProfileTests(unittest.TestCase):
    def fixture(self, root, enabled=True, entry='m2-worker.ts', directory=None):
        worker = root / entry
        worker.write_text(CONTROLLER)
        child = root / 'child.cjs'
        child.write_text(CHILD)
        launch = root / 'launch.cjs'
        launch.write_text(LAUNCHER)
        env = {**os.environ, 'NODE_OPTIONS': f'--require="{PRELOAD}"',
               'FIXTURE_CHILD': str(child), 'FIXTURE_CONTROLLER': str(worker)}
        env.pop('OXIMUT_PROFILE_CONTROLLER_DIR', None)
        if enabled:
            env['OXIMUT_PROFILE_CONTROLLER_DIR'] = str(directory or root)
        result = subprocess.run([NODE, str(launch)], env=env, text=True,
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        data = next(json.loads(line) for line in lines if line.startswith('{'))
        self.assertEqual(data['code'], 23)
        self.assertEqual(data['replies'], [dict(protocol=1,id=i,result=dict(ok=True)) for i in range(1,6)])
        self.assertEqual(lines.count('callback'), 5)
        self.assertEqual(lines.count('returned:boolean'), 5)
        return diagnostic.controller_capture_inventory(root)

    def test_complete_first_mutant_and_bounded_worker_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = self.fixture(root)
            self.assertEqual(inventory['errors'], [])
            self.assertEqual(len(list(root.glob('controller-*.jsonl'))), 1)
            self.assertEqual(len(list(root.glob('*.cpuprofile'))), 1)
            events = [json.loads(line) for line in next(root.glob('controller-*.jsonl')).read_text().splitlines()]
            self.assertEqual([e['jobId'] for e in events if e['type']=='profile-written'], [3])
            for pid in [e['childPid'] for e in events if e['type']=='fork']:
                worker = [e for e in events if e.get('childPid')==pid]
                self.assertEqual([e['messageType'] for e in worker if e['type']=='child-send'], ['start','run','stop'])
                self.assertEqual([e['messageType'] for e in worker if e['type']=='child-message' and e['messageType']], ['started','testfileFinished','stopped'])
                self.assertEqual(len([e for e in worker if e.get('rpcMethod')=='onTaskUpdate']), 1)
                self.assertEqual(worker[-1]['counts']['onTaskUpdate'], 30)
            self.assertNotIn('DO NOT LOG PAYLOAD', next(root.glob('controller-*.jsonl')).read_text())
            self.assertLess(len(events), 65)
            # Validate corrupt/incomplete evidence without re-executing the workload.
            profile = next(root.glob('*.cpuprofile'))
            raw = profile.read_text()
            for bad in [{}, {'nodes':[], 'startTime':0, 'endTime':1}]:
                profile.write_text(json.dumps(bad))
                self.assertTrue(diagnostic.controller_capture_inventory(root)['errors'])
            profile.write_text(raw)
            timeline = next(root.glob('controller-*.jsonl'))
            for kind in ['controller-send','profile-started','profile-written','child-exit','controller-start','controller-exit']:
                with self.subTest(missing=kind):
                    timeline.write_text('\n'.join(json.dumps(e) for e in events if e['type'] != kind))
                    self.assertTrue(diagnostic.controller_capture_inventory(root)['errors'])
            timeline.write_text('\n'.join(json.dumps(e) for e in events))
            self.assertEqual(diagnostic.controller_capture_inventory(root)['errors'], [])

    def test_opt_out_and_noncontroller_are_inert(self):
        for enabled, entry in [(False,'m2-worker.ts'), (True,'ordinary.ts')]:
            with self.subTest(enabled=enabled,entry=entry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.fixture(root, enabled=enabled, entry=entry)
                self.assertFalse(list(root.glob('controller-*.jsonl')))
                self.assertFalse(list(root.glob('*.cpuprofile')))

    def test_diagnostic_write_failure_preserves_replies_callbacks_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # ENOTDIR on every diagnostic write, including profile finalization.
            blocked = root / 'blocked'
            blocked.write_text('not a directory')
            inventory = self.fixture(root, directory=blocked)
            self.assertTrue(inventory['errors'])
            self.assertFalse(list(root.glob('*.cpuprofile')))


if __name__ == '__main__':
    unittest.main()
