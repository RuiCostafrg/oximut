import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';

const repository = resolve(import.meta.dirname, '../../..');
// Longest a lane waits for its neighbours, and how long a `mid*` lane holds its slot.
const WAIT_MS = 40000;
const MID_MS = 10000;

// Experimental rolling lanes: a batch with more lanes than slots keeps every slot
// busy, so a slot starts its next mutant as soon as its previous worker has exited.
async function fixture(t) {
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/rolling-'));
  await symlink(join(repository, 'node_modules'), join(root, 'node_modules'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const code = 'export const active = process.env.OXIMUT_ACTIVE_MUTANT;';
  await writeFile(join(root, 'subject.ts'), 'export const active = "uninstrumented";');
  await writeFile(
    join(root, 'vitest.config.mjs'),
    "export default { test: { include: ['*.test.ts'], testTimeout: 120000 } };",
  );
  // Lanes order themselves through the events file instead of through wall-clock
  // margins, so a loaded host only makes the test slower, never wrong:
  // - `slow*` mutants wait until three `f*` mutants have finished: a batch that holds
  //   them behind a barrier would never get there, and fails after WAIT_MS;
  // - `mid*` mutants hold their slot for MID_MS;
  // - `hang` waits until four `f*` mutants have finished, then never yields: the
  //   neighbouring slot must keep serving lanes while `hang` holds its own slot;
  // - `crash` kills its own process, `killed` fails; every other mutant passes at once.
  // The events record when each mutant's worker entered and finished.
  await writeFile(
    join(root, 'rolling.test.ts'),
    `
import { test, expect } from 'vitest';
import { appendFileSync, readFileSync } from 'node:fs';
import { active } from './subject';
const events = ${JSON.stringify(join(root, 'events'))};
const log = (type) => appendFileSync(events,
  JSON.stringify({ type, id: active, pid: process.pid, t: Date.now() }) + '\\n');
const finishedFast = () => readFileSync(events, 'utf8').split('\\n').filter(Boolean)
  .map((line) => JSON.parse(line)).filter((e) => e.type === 'done' && e.id.startsWith('f')).length;
const WAIT_MS = ${WAIT_MS};
const pause = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
log('enter');
test('work', async () => {
  if (active.startsWith('slow')) {
    const give_up = Date.now() + WAIT_MS;
    while (finishedFast() < 3) {
      if (Date.now() > give_up) throw new Error('fast lanes never ran beside a slow one');
      await new Promise((r) => setTimeout(r, 50));
    }
  }
  if (active.startsWith('mid')) await new Promise((r) => setTimeout(r, ${MID_MS}));
  if (active === 'crash') process.kill(process.pid, 'SIGKILL');
  if (active === 'hang') {
    const give_up = Date.now() + WAIT_MS;
    while (finishedFast() < 4) {
      if (Date.now() > give_up) throw new Error('fast lanes never ran beside the hung one');
      pause(50);
    }
    while (true) {}
  }
  expect(active.startsWith('killed')).toBe(false);
  log('done');
});`,
  );
  const module = {
    id: '__resident__',
    file: join(root, 'subject.ts'),
    code,
    map: { version: 3, sources: ['subject.ts'], names: [], mappings: 'AAAA' },
  };
  const job = (id, deadlineMs = 60000) => ({
    resident: [module],
    protocol: 1,
    runId: 'rolling',
    jobId: id,
    generation: 1,
    kind: 'mutant',
    root,
    config: join(root, 'vitest.config.mjs'),
    deadlineMs,
    mutation: { ...module, id },
  });
  async function controller() {
    const child = fork(join(repository, 'packages/vitest-adapter/m2-worker.ts'), [], {
      stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    });
    let diagnostics = '';
    child.stderr.on('data', (data) => {
      diagnostics += data;
    });
    let sequence = 0;
    const exited = new Promise((done) => child.once('exit', done));
    const command = (name, args) =>
      new Promise((done, fail) => {
        const id = ++sequence;
        const timer = setTimeout(() => fail(new Error(`IPC deadline: ${diagnostics}`)), 90000);
        child.once('message', (reply) => {
          clearTimeout(timer);
          if (reply.id !== id || reply.error) fail(new Error(JSON.stringify(reply) + diagnostics));
          else done(reply.result);
        });
        child.send({ protocol: 1, id, command: name, args });
      });
    t.after(async () => {
      child.kill('SIGKILL');
      await exited;
    });
    await command('initialize', { ...job('init'), kind: 'baseline', mutation: null });
    return {
      run: (request) => command('run', { request }),
      async close() {
        await command('close', {});
        child.disconnect();
        await exited;
      },
    };
  }
  async function events() {
    const text = await readFile(join(root, 'events'), 'utf8').catch(() => '');
    return text.trim().split('\n').filter(Boolean).map(JSON.parse);
  }
  return { root, job, controller, events };
}

const gone = (pids) => {
  for (const pid of pids) assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
};
const summary = (lanes) => lanes.map((lane) => [lane.jobId, lane.outcome]);

test('rolling lanes attribute lanes of very different durations and match a barrier batch', {
  timeout: 180000,
}, async (t) => {
  const s = await fixture(t);
  const ids = ['slow1', 'killed', 'f1', 'f2', 'f3', 'killed2', 'f4', 'slow2'];
  const expected = ids.map((id) => [id, id.startsWith('killed') ? 'failed' : 'passed']);
  const c = await s.controller();
  // The barrier oracle: every lane at once, one fresh process each.
  const jobs = ids.map((id) => s.job(id));
  const oracle = await c.run({ ...jobs[0], workers: ids.length, lanes: jobs });
  assert.deepEqual(summary(oracle.lanes), expected);
  await rm(join(s.root, 'events'));
  const rolled = await c.run({ ...jobs[0], workers: 2, lanes: jobs });
  assert.deepEqual(summary(rolled.lanes), expected);
  assert.deepEqual(
    rolled.lanes.map((lane) => lane.tests),
    oracle.lanes.map((lane) => lane.tests),
  );
  const log = await s.events();
  const byId = (id) => log.filter((event) => event.id === id);
  // Every mutant ran in its own process, and nothing was left running.
  const pids = ids.map((id) => new Set(byId(id).map((event) => event.pid)));
  assert.ok(pids.every((set) => set.size === 1), 'one process per mutant');
  assert.equal(new Set(pids.flatMap((set) => [...set])).size, ids.length);
  gone(pids.flatMap((set) => [...set]));
  // Never more workers alive than slots, measured over enter .. done/death.
  const spans = ids.map((id, index) => {
    const own = byId(id);
    return { enter: own.find((e) => e.type === 'enter').t, end: Math.max(...own.map((e) => e.t)), index };
  });
  for (const span of spans)
    assert.ok(
      spans.filter((other) => other.enter <= span.enter && other.end >= span.enter).length <= 2,
      `more than two workers alive at once at ${span.enter}`,
    );
  // No barrier: slow1 only finishes once three fast lanes have, and those can only run
  // in its slot's neighbour, so they started before it ended (ordering, not durations).
  const slow1 = spans[0];
  const during = spans.filter((span) => span.index > 1 && span.enter < slow1.end);
  assert.ok(during.length >= 3, `only ${during.length} later lanes started while slow1 ran`);
  await c.close();
});

test('a lane deadline starts with its lane and one timeout leaves its neighbours alone', {
  timeout: 240000,
}, async (t) => {
  const s = await fixture(t);
  const c = await s.controller();
  // Six lanes of MID_MS through two slots with a deadline just under 2 x MID_MS: the last
  // round starts after the batch is older than that deadline, which a batch-start clock
  // would have used up. Each lane keeps 0.9 x MID_MS to spare for its own start-up.
  const deadline = Math.floor(1.9 * MID_MS);
  const mids = ['mid1', 'mid2', 'mid3', 'mid4', 'mid5', 'mid6'].map((id) => s.job(id, deadline));
  const midResult = await c.run({ ...mids[0], workers: 2, lanes: mids });
  assert.deepEqual(summary(midResult.lanes), mids.map((job) => [job.jobId, 'passed']));
  const enters = (await s.events()).filter((event) => event.type === 'enter').map((e) => e.t);
  assert.ok(
    Math.max(...enters) - Math.min(...enters) >= deadline,
    'the last lanes must start after the batch is older than the lane deadline',
  );
  await rm(join(s.root, 'events'));
  // A hung lane times out on its own clock. Its slot-mates keep running and are
  // reported with their own results, before and after it. The hung lane only hangs
  // once four fast lanes have finished beside it, so they ran while it held its slot.
  const ids = ['f1', 'hang', 'f2', 'f3', 'f4', 'f5'];
  // The deadline outlasts WAIT_MS, so a starved neighbour fails the assertions below
  // instead of being masked by the timeout.
  const jobs = ids.map((id) => s.job(id, 45000));
  const result = await c.run({ ...jobs[0], workers: 2, lanes: jobs });
  assert.deepEqual(
    summary(result.lanes),
    ids.map((id) => [id, id === 'hang' ? 'timeout' : 'passed']),
  );
  const log = await s.events();
  const hangEnter = log.find((event) => event.id === 'hang' && event.type === 'enter').t;
  const finished = log.filter((event) => event.type === 'done' && event.t > hangEnter);
  assert.ok(finished.length >= 4, 'later lanes finished while the hung lane held its slot');
  gone([...new Set(log.map((event) => event.pid))]);
  await c.close();
});

test('a crashed lane fails closed and the slot keeps serving the lanes behind it', {
  timeout: 180000,
}, async (t) => {
  const s = await fixture(t);
  const c = await s.controller();
  const jobs = ['crash', 'f1', 'f2', 'f3', 'f4'].map((id) => s.job(id));
  const result = await c.run({ ...jobs[0], workers: 2, lanes: jobs });
  assert.deepEqual(
    summary(result.lanes),
    jobs.map((job) => [job.jobId, job.jobId === 'crash' ? 'infrastructure-error' : 'passed']),
  );
  const log = await s.events();
  gone([...new Set(log.map((event) => event.pid))]);
  // The controller is intact: a second rolling batch on the same lanes is correct.
  await rm(join(s.root, 'events'));
  const again = await c.run({ ...jobs[0], workers: 2, lanes: jobs.slice(1) });
  assert.deepEqual(summary(again.lanes), jobs.slice(1).map((job) => [job.jobId, 'passed']));
  await c.close();
});

test('a changed resident module drains every slot first', { timeout: 180000 }, async (t) => {
  const s = await fixture(t);
  const c = await s.controller();
  const jobs = ['f1', 'f2', 'f3', 'f4'].map((id) => s.job(id));
  const first = await c.run({ ...jobs[0], workers: 2, lanes: jobs });
  assert.deepEqual(summary(first.lanes), jobs.map((job) => [job.jobId, 'passed']));
  const changed = jobs.map((job) => {
    const code = 'export const active = process.env.OXIMUT_ACTIVE_MUTANT; export const more = 1;';
    return { ...job, resident: [{ ...job.resident[0], code }], mutation: { ...job.mutation, code } };
  });
  const second = await c.run({ ...changed[0], workers: 2, lanes: changed });
  assert.deepEqual(summary(second.lanes), changed.map((job) => [job.jobId, 'passed']));
  const log = await s.events();
  gone([...new Set(log.map((event) => event.pid))]);
  await c.close();
});
