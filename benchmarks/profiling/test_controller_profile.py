"""Diagnostic IPC fixtures only; no builds, dependencies or performance claims."""
import copy
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
const assert = require('node:assert/strict');
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
  let sendReturned = false;
  const returned = process.send({protocol:1,id:msg.id,result:{ok:true}}, error => {
    assert.equal(error, null);
    assert.equal(sendReturned, true, 'callback must not run synchronously');
    console.log('callback');
  });
  sendReturned = true;
  console.log('returned:' + returned);
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
    def test_send_contract_preserves_both_booleans_arguments_receiver_and_async_callback(self):
        """Deterministic underlying sends expose inversions and synchronous callbacks."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = root / 'm2-worker.ts'
            worker.write_text(r'''
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const cp = require('node:child_process');
const records = [];
const failure = new Error('original send failure');
const callbackError = new Error('underlying async callback');
const original = function(...args) {
  if (args[0].fail) throw failure;
  records.push({receiver:this,args});
  setImmediate(() => args.at(-1)(callbackError));
  return args[0].expected;
};
process.send = original;
const child = Object.assign(new EventEmitter(), {pid:123,send:original});
cp.fork = () => child;
require(process.env.FIXTURE_PRELOAD);
const wrappedChild = cp.fork();
let callbacks = 0;
for (const send of [process.send, wrappedChild.send]) {
  for (const expected of [true,false]) {
    let returned = false;
    const receiver = {};
    const message = {protocol:1,id:7,expected};
    const handle = {};
    const options = {keepOpen:true};
    const callback = error => {
      assert.equal(returned, true, 'callback must follow return');
      assert.equal(error, callbackError);
      callbacks++;
    };
    assert.equal(send.call(receiver, message, handle, options, callback), expected);
    returned = true;
    const record = records.at(-1);
    assert.equal(record.receiver, receiver);
    assert.equal(record.args.length, 4);
    for (const [index,value] of [message,handle,options,callback].entries())
      assert.equal(record.args[index], value);
  }
  assert.throws(() => send({fail:true}), error => error === failure);
}
assert.equal(callbacks, 0);
setImmediate(() => { assert.equal(callbacks, 4); console.log('contract passed'); });
''')
            env = {**os.environ, 'NODE_OPTIONS': '', 'OXIMUT_PROFILE_CONTROLLER_DIR': str(root),
                   'FIXTURE_PRELOAD': str(PRELOAD)}
            result = subprocess.run([NODE, str(worker)], env=env, text=True,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'contract passed')

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
        self.assertEqual(lines.count('returned:true'), 5)
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
            for bad in [{}, {'nodes':[], 'startTime':0, 'endTime':1},
                        {'nodes':[{}], 'startTime':0, 'endTime':1}]:
                profile.write_text(json.dumps(bad))
                self.assertTrue(diagnostic.controller_capture_inventory(root)['errors'])
            profile.write_text(raw)
            timeline = next(root.glob('controller-*.jsonl'))
            for kind in ['controller-send','profile-started','profile-written','child-exit','controller-start','controller-exit']:
                with self.subTest(missing=kind):
                    timeline.write_text('\n'.join(json.dumps(e) for e in events if e['type'] != kind))
                    self.assertTrue(diagnostic.controller_capture_inventory(root)['errors'])
            for boundary in ['start', 'run', 'stop', 'onCollected']:
                with self.subTest(missing=boundary):
                    # Remove just one worker's event, leaving all responses intact.
                    broken = copy.deepcopy(events)
                    index = next(i for i, e in enumerate(broken) if
                                 (e['type'] == 'child-send' and e.get('messageType') == boundary)
                                 or (e['type'] == 'child-message' and e.get('rpcMethod') == boundary))
                    del broken[index]
                    timeline.write_text('\n'.join(json.dumps(e) for e in broken))
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


class ProfileStructureTests(unittest.TestCase):
    def profile(self):
        def frame(name):
            return dict(functionName=name, scriptId='0', url='', lineNumber=-1, columnNumber=-1)
        return dict(nodes=[dict(id=1, callFrame=frame('(root)'), children=[2]),
                           dict(id=2, callFrame=frame('work'))],
                    startTime=0, endTime=20, samples=[2], timeDeltas=[10])

    def test_usable_samples_require_valid_call_tree_and_sample_references(self):
        profile = self.profile()
        self.assertTrue(diagnostic.node_profile_finalized(profile))
        corruptions = [
            lambda p: p.update(nodes=[{}]),
            lambda p: p.pop('samples'),
            lambda p: p.pop('timeDeltas'),
            lambda p: p.update(samples=[], timeDeltas=[]),
            lambda p: p.update(samples=[999]),
            lambda p: p.update(samples=[True]),
            lambda p: p.update(timeDeltas=[]),
            lambda p: p.update(timeDeltas=[-1]),
            lambda p: p.update(timeDeltas=[float('nan')]),
            lambda p: p['nodes'][1].pop('callFrame'),
            lambda p: p['nodes'][1]['callFrame'].update(functionName=3),
            lambda p: p['nodes'][1]['callFrame'].update(lineNumber=True),
            lambda p: p['nodes'][1].update(id=1),
            lambda p: p['nodes'][0].update(children=[999]),
            lambda p: p['nodes'][0].update(children=[2, 2]),
            lambda p: p['nodes'][0].update(children=[]),
            lambda p: p['nodes'][1].update(children=[1]),
            lambda p: p.update(endTime=0),
        ]
        for i, corrupt in enumerate(corruptions):
            with self.subTest(corruption=i):
                broken = copy.deepcopy(profile)
                corrupt(broken)
                self.assertFalse(diagnostic.node_profile_finalized(broken))


if __name__ == '__main__':
    unittest.main()
