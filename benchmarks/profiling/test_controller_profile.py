"""Diagnostic IPC fixtures only; no builds, dependencies or performance claims."""
import copy
import json
import os
from pathlib import Path
import secrets
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
    // A shared-lane batch forks one child per lane, all at once.
    const lanes = msg.args?.request?.lanes?.length ?? 1;
    await Promise.all(Array.from({length: lanes}, () => new Promise(resolve => {
      const child = fork(process.env.FIXTURE_CHILD, [], { stdio: ['ignore','ignore','inherit','ipc'] });
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
    })));
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
process.once('disconnect', () => { process.exitCode = Number(process.env.FIXTURE_CONTROLLER_EXIT); });
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
  const mutantIds = JSON.parse(process.env.FIXTURE_MUTANT_IDS);
  const mutantJobIds = JSON.parse(process.env.FIXTURE_MUTANT_JOB_IDS);
  const baselineIds = JSON.parse(process.env.FIXTURE_BASELINE_IDS);
  const runId = process.env.FIXTURE_RUN_ID;
  // Batch sizes group the mutants into shared-lane requests; one lane per batch by default.
  const sizes = JSON.parse(process.env.FIXTURE_BATCHES || '[]');
  const batches = [];
  for (let at = 0, i = 0; at < mutantIds.length; i++) {
    const size = sizes.length ? sizes[i] : 1;
    batches.push(mutantIds.slice(at, at + size).map((m, k) => [mutantJobIds[at + k], m]));
    at += size;
  }
  const commands = [['initialize'], ...baselineIds.map(id => ['run','baseline',id]),
    ...batches.map(batch => ['run','mutant',batch[0][0],batch[0][1],batch]), ['close']];
  for (const [index,[command,kind,jobId,mutantId,batch]] of commands.entries()) {
    const id = index + 1;
    const response = new Promise(resolve => c.once('message', resolve));
    const job = {kind, jobId, runId, mutation:kind === 'mutant' ? {id:mutantId} : null};
    if (batch && sizes.length) job.lanes = batch.map(([lane, m]) => ({kind, jobId:lane, runId, mutation:{id:m}}));
    const args = command === 'run' ? {request:job} : command === 'initialize'
      ? {kind:'baseline',jobId:'baseline',runId,mutation:null} : {};
    c.send({protocol:1,id,command,args});
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
  const record = {receiver:this,args,completedAt:process.hrtime.bigint()};
  records.push(record);
  setImmediate(() => { record.callbackAt = process.hrtime.bigint(); args.at(-1)(callbackError); });
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
const fs = require('node:fs');
const path = require('node:path');
const readTimeline = () => fs.readFileSync(path.join(process.env.OXIMUT_PROFILE_CONTROLLER_DIR,
    `controller-${process.pid}.jsonl`), 'utf8').trim().split('\n').map(JSON.parse);
assert.equal(readTimeline().filter(row => row.type === 'controller-send').length, 2,
  'send observations must be synchronous before callbacks');
setImmediate(() => {
  assert.equal(callbacks, 4);
  const rows = readTimeline();
  const sends = rows.filter(row => row.type === 'controller-send');
  assert.equal(sends.length, 2);
  assert.deepEqual(sends.map(row => row.sendReturn), [true,false]);
  assert.ok(!sends.some(row => row.id === 999), 'a throwing send has no return boundary');
  for (let i=0;i<sends.length;i++) {
    assert.equal(sends[i].boundary, 'send-return');
    assert.ok(sends[i].monotonicMs >= Number(records[i].completedAt - BigInt(rows[0].monotonicOriginNs))/1e6);
    assert.ok(sends[i].monotonicMs < Number(records[i].callbackAt - BigInt(rows[0].monotonicOriginNs))/1e6);
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
                duplicate='', defer_profile=False, fail_append=False,
                request_ids=None, qualification_ids=None, validation_runs=0,
                baseline_ids=None, request_run_id=None, qualification_run_id=None,
                mutant_job_ids=None, coverage_mode="none", controller_exit_code=0, batches=None):
        """Run real fork IPC and verify replies, callbacks, return values and exit."""
        request_ids = request_ids if request_ids is not None else [secrets.token_hex(32) for _ in range(2)]
        qualification_ids = qualification_ids if qualification_ids is not None else list(request_ids)
        mutant_job_ids = mutant_job_ids if mutant_job_ids is not None else list(request_ids)
        request_run_id = request_run_id if request_run_id is not None else f'{os.getpid()}-{secrets.randbits(64)}'
        qualification_run_id = qualification_run_id if qualification_run_id is not None else request_run_id
        if baseline_ids is None:
            baseline_ids = ['baseline'] + (['instrumented-baseline'] if validation_runs == 1 else
                                           [f'instrumented-baseline-{i}' for i in range(validation_runs)])
        qualification = root / 'qualification' / '00-oximut'
        qualification.mkdir(parents=True)
        (qualification / 'report.json').write_text(json.dumps({
            'runId': qualification_run_id,
            'baseline': {'jobId': 'baseline', 'runId': qualification_run_id,
                         'coverage': {'complete':True} if coverage_mode != 'none' else None},
            'mutants': [dict(id=identity, provenance=dict(source='executed')) for identity in qualification_ids],
            'resources': {'disabledValidationRuns': [{'run':'instrumentedBaseline', 'coverage':coverage_mode == 'combined'}
                                                       for _ in range(validation_runs)]}}))
        worker = root / entry
        worker.write_text(CONTROLLER)
        child = root / 'child.cjs'
        child.write_text(CHILD)
        launch = root / 'launch.cjs'
        launch.write_text(LAUNCHER)
        env = {**os.environ, 'NODE_OPTIONS': f'--require="{PRELOAD}"',
               'FIXTURE_CHILD': str(child), 'FIXTURE_CONTROLLER': str(worker),
               'FIXTURE_MUTANT_IDS': json.dumps(request_ids),
               'FIXTURE_MUTANT_JOB_IDS': json.dumps(mutant_job_ids),
               'FIXTURE_BASELINE_IDS': json.dumps(baseline_ids), 'FIXTURE_RUN_ID': request_run_id,
               'FIXTURE_CONTROLLER_EXIT': str(controller_exit_code),
               'FIXTURE_DUPLICATE': duplicate,
               'FIXTURE_BATCHES': json.dumps(batches or []),
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
        self.assertEqual(data['code'], controller_exit_code)
        command_count = len(baseline_ids) + (len(batches) if batches else len(request_ids)) + 2
        self.assertEqual(data['replies'], [dict(protocol=1,id=i,result=dict(ok=True)) for i in range(1,command_count + 1)])
        self.assertEqual(lines.count('callback'), command_count)
        self.assertEqual(lines.count('returned:true'), command_count)
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

    def test_shared_lane_batches_count_every_lane_and_name_them(self):
        """A batch is one request carrying one mutant per lane; every lane must reconcile."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = [secrets.token_hex(32) for _ in range(3)]
            result = self.fixture(root, request_ids=ids, batches=[2, 1])
            self.assertEqual(result['errors'], [])
            self.assertTrue(result['workload_reconciled'])
            observed = result['observed'][0]
            self.assertEqual(observed['batch_sizes'], [2, 1])
            self.assertEqual(observed['mutant_count'], 3)
            self.assertEqual(observed['mutant_request_count'], 2)
            self.assertEqual(observed['mutant_ids'], ids)
            self.assertEqual(observed['observed_forks'], 1 + 3)  # one baseline fork, one fork per lane
            events = [json.loads(line) for line in next(root.glob('controller-*.jsonl')).read_text().splitlines()]
            batches = [e for e in events if e['type'] == 'controller-request' and 'laneCount' in e]
            self.assertEqual([e['laneMutantIds'] for e in batches], [ids[:2], ids[2:]])
            self.assertEqual([e['laneJobIds'] for e in batches], [ids[:2], ids[2:]])
            self.assertEqual([e['laneRunIds'] for e in batches], [[e['runId']] * len(e['laneMutantIds']) for e in batches])
        with tempfile.TemporaryDirectory() as tmp:
            # A lane whose job identity is not its mutation's must fail, not hide behind the first lane's.
            root = Path(tmp)
            ids = [secrets.token_hex(32) for _ in range(3)]
            result = self.fixture(root, request_ids=ids, mutant_job_ids=[ids[0], secrets.token_hex(32), ids[2]],
                                  batches=[2, 1])
            self.assertTrue(any('job identity differs' in e for e in result['errors']))
            self.assertFalse(result['workload_reconciled'])
        with tempfile.TemporaryDirectory() as tmp:
            # A lane from a different run is rejected as well.
            root = Path(tmp)
            ids = [secrets.token_hex(32) for _ in range(3)]
            self.fixture(root, request_ids=ids, batches=[2, 1])
            timeline = next(root.glob('controller-*.jsonl'))
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            batch = next(e for e in events if e.get('laneCount') == 2)
            batch['laneRunIds'][1] = '1-2'
            self.write_events(timeline, events)
            self.assertTrue(any('different qualification run' in e for e in self.capture(root)['errors']))
        with tempfile.TemporaryDirectory() as tmp:
            # The outer request's identity must be the first lane's, or foreign outer IDs would pass.
            root = Path(tmp)
            ids = [secrets.token_hex(32) for _ in range(3)]
            self.fixture(root, request_ids=ids, batches=[2, 1])
            timeline = next(root.glob('controller-*.jsonl'))
            clean = [json.loads(line) for line in timeline.read_text().splitlines()]
            for key, value in (('jobId', secrets.token_hex(32)), ('runId', '1-2')):
                events = copy.deepcopy(clean)
                next(e for e in events if e.get('laneCount') == 2)[key] = value
                self.write_events(timeline, events)
                self.assertTrue(any('unidentified shared-lane batch' in e for e in self.capture(root)['errors']), key)
        # A batch that names a lane the qualification does not know must not reconcile.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = [secrets.token_hex(32) for _ in range(3)]
            qualified = [ids[0], ids[1], secrets.token_hex(32)]
            result = self.fixture(root, request_ids=ids, qualification_ids=qualified, batches=[2, 1])
            self.assertFalse(result['workload_reconciled'])
            self.assertTrue(any('identities differ' in e for e in result['errors']))
        with tempfile.TemporaryDirectory() as tmp:
            # Dropping a lane's identity from a batch breaks the batch's own consistency.
            root = Path(tmp)
            ids = [secrets.token_hex(32) for _ in range(3)]
            self.fixture(root, request_ids=ids, batches=[2, 1])
            timeline = next(root.glob('controller-*.jsonl'))
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            for e in events:
                if 'laneMutantIds' in e:
                    e['laneMutantIds'] = e['laneMutantIds'][:1]
                    break
            self.write_events(timeline, events)
            self.assertTrue(any('unidentified shared-lane batch' in e for e in self.capture(root)['errors']))
            # A lane whose worker never started leaves the batch short of its declared lanes.
            short = copy.deepcopy(events)
            batch = next(e for e in short if e['type'] == 'controller-request' and e.get('laneCount') == 2)
            lane_fork = next(e for e in short if e['type'] == 'fork' and e['monotonicMs'] >= batch['monotonicMs'])
            short = [e for e in short if e.get('childPid') != lane_fork['childPid']]
            self.write_events(timeline, short)
            self.assertTrue(any('forked 1 workers for 2 lanes' in e for e in self.capture(root)['errors']))
            # An empty batch is invalid evidence, not a crash.
            for e in events:
                if 'laneMutantIds' in e:
                    e['laneMutantIds'] = []
                    e['laneCount'] = 0
                    break
            self.write_events(timeline, events)
            self.assertTrue(any('unidentified shared-lane batch' in e for e in self.capture(root)['errors']))

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
            inventory = self.fixture(root, directory=blocked, controller_exit_code=23)
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

    def test_captured_mutant_ids_follow_requests_independently_of_qualification(self):
        """Observe extraction directly with varied inputs and separate report identities."""
        for matching in [True, False]:
            with self.subTest(matching=matching), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                requests = [secrets.token_hex(32) for _ in range(2)]
                qualified = list(requests) if matching else [secrets.token_hex(32) for _ in range(2)]
                self.assertEqual(len(set(requests + qualified)), 2 if matching else 4)
                result = self.fixture(root, request_ids=requests, qualification_ids=qualified)
                events = [json.loads(line) for line in next(root.glob('controller-*.jsonl')).read_text().splitlines()]
                captured = [e['mutantId'] for e in events
                            if e['type'] == 'controller-request' and e.get('kind') == 'mutant']
                self.assertEqual(captured, requests)
                report = json.loads((root / 'qualification/00-oximut/report.json').read_text())
                self.assertEqual([m['id'] for m in report['mutants']], qualified)
                self.assertEqual(result['workload_reconciled'], matching)
                self.assertEqual(bool(result['errors']), not matching)

    def test_baseline_and_run_identities_match_qualified_jobs(self):
        """Repeated baselines and foreign run/job IDs cannot hide behind equal counts."""
        cases = [
            (1, ['baseline','instrumented-baseline'], True),
            (1, ['baseline','baseline'], False),
            (1, ['instrumented-baseline','instrumented-baseline'], False),
            (1, ['baseline','instrumented-baseline-9'], False),
            (2, ['baseline','instrumented-baseline-0','instrumented-baseline-1'], True),
            (2, ['baseline','instrumented-baseline-0','instrumented-baseline-0'], False),
        ]
        for count, identities, matching in cases:
            with self.subTest(baselines=identities), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = self.fixture(root, validation_runs=count, baseline_ids=identities)
                events = [json.loads(line) for line in next(root.glob('controller-*.jsonl')).read_text().splitlines()]
                self.assertEqual([e['jobId'] for e in events if e.get('kind') == 'baseline'], identities)
                self.assertEqual(result['workload_reconciled'], matching)
                self.assertEqual(bool(result['errors']), not matching)
        with self.subTest(foreign_run=True), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.fixture(root, request_run_id='123-456', qualification_run_id='123-789')
            events = [json.loads(line) for line in next(root.glob('controller-*.jsonl')).read_text().splitlines()]
            self.assertEqual({e['runId'] for e in events if e.get('command') == 'run'}, {'123-456'})
            self.assertTrue(any('different qualification run' in e for e in result['errors']))
            self.assertFalse(result['workload_reconciled'])
        with self.subTest(wrong_mutant_job=True), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identities = [secrets.token_hex(32) for _ in range(2)]
            result = self.fixture(root, request_ids=identities, mutant_job_ids=list(reversed(identities)))
            self.assertTrue(any('job identity differs' in e for e in result['errors']))
            self.assertFalse(result['workload_reconciled'])

    def test_coverage_only_baseline_is_included_in_counts_and_job_ids(self):
        """Reconcile separate coverage even though disabled group records omit it."""
        cases = [
            (1, 'combined', ['baseline','instrumented-baseline'], True),
            (1, 'separate', ['baseline','instrumented-baseline-0','instrumented-baseline-1'], True),
            (2, 'separate', ['baseline','instrumented-baseline-0','instrumented-baseline-1','instrumented-baseline-2'], True),
            (0, 'separate', ['baseline','instrumented-baseline'], True),
            (2, 'separate', ['baseline','instrumented-baseline-0','instrumented-baseline-1'], False),
            (1, 'separate', ['baseline','instrumented-baseline-0','instrumented-baseline-0'], False),
        ]
        for groups, mode, jobs, matching in cases:
            with self.subTest(groups=groups, mode=mode, jobs=jobs), tempfile.TemporaryDirectory() as tmp:
                result = self.fixture(Path(tmp), validation_runs=groups, baseline_ids=jobs, coverage_mode=mode)
                self.assertEqual(result['workload_reconciled'], matching)
                self.assertEqual(bool(result['errors']), not matching)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.fixture(root, validation_runs=1, coverage_mode='combined')['errors'], [])
            path = root / 'qualification/00-oximut/report.json'
            report = json.loads(path.read_text())
            for invalid in [None, [], {'coverage': []}, {'coverage': {'complete': 'yes'}}]:
                with self.subTest(malformed_baseline=invalid):
                    bad = copy.deepcopy(report)
                    bad['baseline'] = invalid
                    path.write_text(json.dumps(bad))
                    self.assertTrue(self.capture(root)['errors'])

    def test_failed_controller_exit_preserves_workload_but_rejects_capture(self):
        """Complete replies do not hide an abnormal final controller status."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.fixture(root, controller_exit_code=23)
            self.assertTrue(any('abnormal controller exit' in e for e in result['errors']))
            timeline = next(root.glob('controller-*.jsonl'))
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            for code in [None, False, '0']:
                with self.subTest(malformed_exit=code):
                    broken = copy.deepcopy(events)
                    broken[-1]['code'] = code
                    self.write_events(timeline, broken)
                    self.assertTrue(any('abnormal controller exit' in e for e in self.capture(root)['errors']))

    def test_worker_exit_status_requires_clean_or_acknowledged_vitest_stop(self):
        """Expected IPC cannot hide a failed exit; acknowledged SIGTERM is normal."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.fixture(root)['errors'], [])
            timeline = next(root.glob('controller-*.jsonl'))
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            index = next(i for i, e in enumerate(events) if e['type'] == 'child-exit')
            for code, signal, valid in [(0,None,True), (None,'SIGTERM',True),
                                        (7,None,False), (None,'SIGKILL',False),
                                        (None,'SIGABRT',False), (None,None,False),
                                        (False,None,False), (0,'SIGTERM',False)]:
                with self.subTest(code=code,signal=signal):
                    broken = copy.deepcopy(events)
                    broken[index].update(code=code, signal=signal)
                    self.write_events(timeline, broken)
                    result = self.capture(root)
                    self.assertEqual(not result['errors'], valid)
                    if not valid:
                        self.assertTrue(any('abnormal observed worker exit' in e for e in result['errors']))
            for missing in ['code', 'signal']:
                with self.subTest(missing_exit_field=missing):
                    broken = copy.deepcopy(events)
                    broken[index].pop(missing)
                    self.write_events(timeline, broken)
                    self.assertTrue(any('abnormal observed worker exit' in e for e in self.capture(root)['errors']))
            broken = copy.deepcopy(events)
            pid = broken[index]['childPid']
            broken[index].update(code=None, signal='SIGTERM')
            broken = [e for e in broken if not (e['type'] == 'child-message'
                      and e.get('childPid') == pid and e.get('messageType') == 'stopped')]
            self.write_events(timeline, broken)
            self.assertTrue(self.capture(root)['errors'])

    def test_mutant_identities_match_qualification_even_when_counts_match(self):
        """Equal totals cannot hide repeated, substituted or missing mutant identities."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.fixture(root)['errors'], [])
            timeline = next(root.glob('controller-*.jsonl'))
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            request = next(i for i, e in enumerate(events)
                           if e['type'] == 'controller-request' and e['id'] == 4)
            first_id = next(e['mutantId'] for e in events
                            if e['type'] == 'controller-request' and e.get('kind') == 'mutant')
            for identity in [first_id, 'c' * 64, None, True, 'x' * 1000]:
                with self.subTest(identity=str(identity)[:10]):
                    broken = copy.deepcopy(events)
                    broken[request]['mutantId'] = identity
                    self.write_events(timeline, broken)
                    result = self.capture(root)
                    self.assertTrue(any('mutant identities' in error for error in result['errors']))
                    self.assertFalse(result['workload_reconciled'])
            self.write_events(timeline, events)
            self.assertEqual(self.capture(root)['errors'], [])
            report_file = root / 'qualification/00-oximut/report.json'
            report = json.loads(report_file.read_text())
            report['mutants'][1]['id'] = report['mutants'][0]['id']
            report_file.write_text(json.dumps(report))
            self.assertTrue(any('duplicate qualified mutant identities' in error
                                for error in self.capture(root)['errors']))

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
