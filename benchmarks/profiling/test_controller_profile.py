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
    if (process.env.FIXTURE_DEFER_PROFILE === '1') await new Promise(setImmediate);
    const child = fork(process.env.FIXTURE_CHILD, [], { stdio: ['ignore','ignore','inherit','ipc'] });
    await new Promise(resolve => {
      let running = false;
      let stopping = false;
      child.on('message', m => {
        if (m.type === 'started' && !running) {
          running = true; child.send({__vitest_worker_request__:true,type:'run'});
        }
        if (m.type === 'testfileFinished' && !stopping) {
          stopping = true; child.send({__vitest_worker_request__:true,type:'stop'});
        }
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
function reply(type) {
  const value = {__vitest_worker_response__:true,type};
  process.send(value);
  if (process.env.FIXTURE_DUPLICATE === type) process.send(value);
}
process.on('message', m => {
  if (m.type === 'start') reply('started');
  if (m.type === 'run') {
    process.send({m:'onCollected',a:['DO NOT LOG PAYLOAD']});
    if (process.env.FIXTURE_DUPLICATE === 'onCollected') process.send({m:'onCollected'});
    for(let i=0;i<30;i++) process.send({m:'onTaskUpdate',a:['DO NOT LOG PAYLOAD']});
    reply('testfileFinished');
  }
  if (m.type === 'stop') {
    reply('stopped');
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
  records.push({receiver:this,args,completedAt:process.hrtime.bigint()});
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
  assert.throws(() => send({protocol:1,id:999,fail:true}), error => error === failure);
}
assert.equal(callbacks, 0);
setImmediate(() => {
  assert.equal(callbacks, 4);
  const fs = require('node:fs');
  const path = require('node:path');
  const rows = fs.readFileSync(path.join(process.env.OXIMUT_PROFILE_CONTROLLER_DIR,
    `controller-${process.pid}.jsonl`), 'utf8').trim().split('\n').map(JSON.parse);
  const sends = rows.filter(row => row.type === 'controller-send');
  assert.equal(sends.length, 2);
  assert.deepEqual(sends.map(row => row.sendReturn), [true,false]);
  assert.ok(!sends.some(row => row.id === 999), 'a throwing send has no return boundary');
  for (let i=0;i<sends.length;i++) {
    assert.equal(sends[i].boundary, 'send-return');
    assert.ok(sends[i].monotonicMs >= Number(records[i].completedAt - BigInt(rows[0].monotonicOriginNs))/1e6);
  }
  console.log('contract passed');
});
''')
            env = {**os.environ, 'NODE_OPTIONS': '', 'OXIMUT_PROFILE_CONTROLLER_DIR': str(root),
                   'FIXTURE_PRELOAD': str(PRELOAD)}
            result = subprocess.run([NODE, str(worker)], env=env, text=True,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'contract passed')

    def write_events(self, timeline, events):
        """Preserve sequence integrity when testing independent semantic corruptions."""
        timeline.write_text('\n'.join(json.dumps(dict(e, seq=i))
                                      for i, e in enumerate(events, 1)))

    def capture(self, root):
        """Reconcile fixture events against their separate product-report evidence."""
        return diagnostic.controller_capture_inventory(root, root / 'qualification')

    def fixture(self, root, enabled=True, entry='m2-worker.ts', directory=None,
                duplicate='', defer_profile=False, fail_append=False):
        """Run real fork IPC and verify replies, callbacks, return values and exit."""
        qualification = root / 'qualification' / '00-oximut'
        qualification.mkdir(parents=True)
        (qualification / 'report.json').write_text(json.dumps({
            'mutants': [dict(provenance=dict(source='executed')) for _ in range(2)],
            'resources': {'disabledValidationRuns': []}}))
        worker = root / entry
        worker.write_text(CONTROLLER)
        child = root / 'child.cjs'
        child.write_text(CHILD)
        launch = root / 'launch.cjs'
        launch.write_text(LAUNCHER)
        env = {**os.environ, 'NODE_OPTIONS': f'--require="{PRELOAD}"',
               'FIXTURE_CHILD': str(child), 'FIXTURE_CONTROLLER': str(worker),
               'FIXTURE_DUPLICATE': duplicate,
               'FIXTURE_DEFER_PROFILE': '1' if defer_profile else '0'}
        if defer_profile:
            sabotage = root / 'deferred-inspector.cjs'
            sabotage.write_text(r'''
const { Session } = require('node:inspector');
const post = Session.prototype.post;
Session.prototype.post = function(method, ...args) {
  if (method !== 'Profiler.start') return Reflect.apply(post, this, [method,...args]);
  setImmediate(() => {
    try { Reflect.apply(post, this, [method,...args]); }
    catch (error) { args.at(-1)(error); }
  });
};
''')
            env['NODE_OPTIONS'] = f'--require="{sabotage}" ' + env['NODE_OPTIONS']
        if fail_append:
            sabotage = root / 'lost-pair.cjs'
            sabotage.write_text(r'''
const fs = require('node:fs');
const append = fs.appendFileSync;
fs.appendFileSync = function(file, data, ...args) {
  if (String(file).endsWith('.jsonl')) {
    const event = JSON.parse(String(data));
    if (event.id === 4 && ['controller-request','controller-send'].includes(event.type))
      throw Error('transient pair append failure');
  }
  return Reflect.apply(append, this, [file,data,...args]);
};
''')
            env['NODE_OPTIONS'] = f'--require="{sabotage}" ' + env['NODE_OPTIONS']
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
        return self.capture(root)

    def test_complete_first_mutant_and_bounded_worker_boundaries(self):
        """Accept one complete capture and reject missing or malformed evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = self.fixture(root)
            self.assertEqual(inventory['errors'], [])
            self.assertIn('partial controller CPU', inventory['scope'])
            window = inventory['observed'][0]['first_mutant_window']
            self.assertGreater(window['requestToProfileAckMs'], 0)
            self.assertGreater(window['profileAckToSendReturnMs'], 0)
            self.assertAlmostEqual(window['requestToSendReturnMs'], window['requestToProfileAckMs']
                                   + window['profileAckToSendReturnMs'])
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
                self.assertTrue(self.capture(root)['errors'])
            profile.write_text(raw)
            timeline = next(root.glob('controller-*.jsonl'))
            for kind in ['controller-send','profile-started','profile-written','child-exit','controller-start','controller-exit']:
                with self.subTest(missing=kind):
                    self.write_events(timeline, [e for e in events if e['type'] != kind])
                    self.assertTrue(self.capture(root)['errors'])
            for boundary in ['start', 'run', 'stop', 'onCollected']:
                with self.subTest(missing=boundary):
                    # Remove just one worker's event, leaving all responses intact.
                    broken = copy.deepcopy(events)
                    index = next(i for i, e in enumerate(broken) if
                                 (e['type'] == 'child-send' and e.get('messageType') == boundary)
                                 or (e['type'] == 'child-message' and e.get('rpcMethod') == boundary))
                    del broken[index]
                    self.write_events(timeline, broken)
                    self.assertTrue(self.capture(root)['errors'])
            self.write_events(timeline, events)
            self.assertEqual(self.capture(root)['errors'], [])

    def test_opt_out_and_noncontroller_are_inert(self):
        """Leave execution and output untouched outside the explicit controller opt-in."""
        for enabled, entry in [(False,'m2-worker.ts'), (True,'ordinary.ts')]:
            with self.subTest(enabled=enabled,entry=entry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.fixture(root, enabled=enabled, entry=entry)
                self.assertFalse(list(root.glob('controller-*.jsonl')))
                self.assertFalse(list(root.glob('*.cpuprofile')))

    def test_diagnostic_write_failure_preserves_replies_callbacks_and_exit(self):
        """Keep workload IPC semantics when every diagnostic write fails."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # ENOTDIR on every diagnostic write, including profile finalization.
            blocked = root / 'blocked'
            blocked.write_text('not a directory')
            inventory = self.fixture(root, directory=blocked)
            self.assertTrue(inventory['errors'])
            self.assertFalse(list(root.glob('*.cpuprofile')))

    def test_asynchronous_profiler_start_is_incomplete_without_changing_ipc(self):
        """Reject a late inspector callback instead of retiming the application."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = self.fixture(root, defer_profile=True)
            self.assertTrue(inventory['errors'])
            events = [json.loads(line) for line in next(root.glob('controller-*.jsonl')).read_text().splitlines()]
            self.assertTrue(any(e['type'] == 'profile-error' and 'synchronously' in e['error']
                                for e in events))
            self.assertFalse(list(root.glob('*.cpuprofile')))
            self.assertFalse(any(e['type'] == 'profile-started' for e in events))

    def test_missing_mutant_pair_fails_qualification_reconciliation(self):
        """A self-consistent shorter trace cannot replace the qualified workload."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.fixture(root)['errors'], [])
            timeline = next(root.glob('controller-*.jsonl'))
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            missing = [e for e in events if e.get('id') != 4]
            # Neutralize sequence validation to independently test qualification counts.
            self.write_events(timeline, missing)
            result = self.capture(root)
            self.assertTrue(any('workload reconciliation' in error for error in result['errors']))
            self.assertFalse(result['workload_reconciled'])

    def test_transient_timeline_append_failure_is_sticky(self):
        """Losing both sides of one exchange still makes the entire capture invalid."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.fixture(root, fail_append=True)
            self.assertTrue(result['errors'])
            events = [json.loads(line) for line in next(root.glob('controller-*.jsonl')).read_text().splitlines()]
            self.assertTrue(events[-1]['timelineWriteFailed'])
            self.assertTrue(list(root.glob('controller-*.timeline-error')))

    def test_duplicate_lifecycle_or_collection_messages_invalidate_capture(self):
        """Bounded first-event logging must not hide duplicate critical responses."""
        for kind in ['started', 'testfileFinished', 'stopped', 'onCollected']:
            with self.subTest(duplicate=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                inventory = self.fixture(root, duplicate=kind)
                self.assertTrue(any('boundary counts' in error for error in inventory['errors']))
                events = [json.loads(line) for line in next(root.glob('controller-*.jsonl')).read_text().splitlines()]
                self.assertTrue(all(e['counts'][kind] == 2 for e in events if e['type'] == 'child-exit'))

    def test_controller_exchanges_require_serial_unique_safe_integer_ids(self):
        """Reject reversed and duplicated exchanges despite matching separate ID lists."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.fixture(root)['errors'], [])
            timeline = next(root.glob('controller-*.jsonl'))
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            request = next(i for i, e in enumerate(events) if e['type'] == 'controller-request' and e['id'] == 1)
            reply = next(i for i, e in enumerate(events) if e['type'] == 'controller-send' and e['id'] == 1)
            cases = {}
            broken = copy.deepcopy(events)
            before, after = broken[request]['monotonicMs'], broken[reply]['monotonicMs']
            broken[request], broken[reply] = broken[reply], broken[request]
            broken[request]['monotonicMs'], broken[reply]['monotonicMs'] = before, after
            cases['reply-before-request'] = broken
            broken = copy.deepcopy(events)
            extra = [dict(broken[request], monotonicMs=after), dict(broken[reply])]
            broken[reply + 1:reply + 1] = extra
            cases['duplicate-paired-id'] = broken
            for value in [True, 1.5, 2**53]:
                broken = copy.deepcopy(events)
                broken[request]['id'] = broken[reply]['id'] = value
                cases[f'invalid-id-{value}'] = broken
            for name, broken in cases.items():
                with self.subTest(corruption=name):
                    self.write_events(timeline, broken)
                    self.assertTrue(self.capture(root)['errors'])
            self.write_events(timeline, events)
            self.assertEqual(self.capture(root)['errors'], [])

    def test_lifecycle_brackets_all_events_and_extreme_times_fail_closed(self):
        """Impossible lifecycle positions and huge timestamps return invalid evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.fixture(root)['errors'], [])
            timeline = next(root.glob('controller-*.jsonl'))
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            self.assertEqual(events[0]['type'], 'controller-start')
            self.assertEqual(events[-1]['type'], 'controller-exit')
            timestamps = [e['monotonicMs'] for e in events]
            late_start = copy.deepcopy(events)
            late_start.insert(2, late_start.pop(0))
            early_exit = copy.deepcopy(events)
            early_exit.insert(1, early_exit.pop())
            for name, broken in [('late-start', late_start), ('early-exit', early_exit)]:
                with self.subTest(corruption=name):
                    # Preserve chronological timestamps to exercise lifecycle semantics.
                    for event, timestamp in zip(broken, timestamps):
                        event['monotonicMs'] = timestamp
                    self.write_events(timeline, broken)
                    self.assertTrue(self.capture(root)['errors'])
            for value in [10**1000, -10**1000, float('inf'), float('nan')]:
                with self.subTest(timestamp=str(value)[:20]):
                    broken = copy.deepcopy(events)
                    broken[-1]['monotonicMs'] = value
                    self.write_events(timeline, broken)
                    self.assertTrue(self.capture(root)['errors'])
            self.write_events(timeline, events)
            self.assertEqual(self.capture(root)['errors'], [])


class ProfileStructureTests(unittest.TestCase):
    def profile(self):
        """Return a minimal connected CPU call tree with one usable sample."""
        def frame(name):
            """Build the required call-frame fields without source locations."""
            return dict(functionName=name, scriptId='0', url='', lineNumber=-1, columnNumber=-1)
        return dict(nodes=[dict(id=1, callFrame=frame('(root)'), children=[2]),
                           dict(id=2, callFrame=frame('work'))],
                    startTime=0, endTime=20, samples=[2], timeDeltas=[10])

    def test_usable_samples_require_valid_call_tree_and_sample_references(self):
        """Reject corrupt trees, call frames, timing arrays and sample references."""
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
            lambda p: p['nodes'].reverse(),
            lambda p: p.update(endTime=0),
        ]
        for i, corrupt in enumerate(corruptions):
            with self.subTest(corruption=i):
                broken = copy.deepcopy(profile)
                corrupt(broken)
                self.assertFalse(diagnostic.node_profile_finalized(broken))

    def test_cumulative_sample_timing_stays_within_profile_window(self):
        """Individually valid deltas cannot place samples after profiling ended."""
        profile = self.profile()
        profile.update(samples=[2, 2], timeDeltas=[10, 10])
        self.assertTrue(diagnostic.node_profile_finalized(profile))
        profile['timeDeltas'] = [10, 11]
        self.assertFalse(diagnostic.node_profile_finalized(profile))
        profile.update(samples=[2], timeDeltas=[21])
        self.assertFalse(diagnostic.node_profile_finalized(profile))
        profile.update(samples=[2, 2], timeDeltas=[1e308, 1e308])
        self.assertFalse(diagnostic.node_profile_finalized(profile))


if __name__ == '__main__':
    unittest.main()
