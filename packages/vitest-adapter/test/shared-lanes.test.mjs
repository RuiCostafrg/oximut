import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';

import { SharedLanes } from '../shared-lanes.ts';

const repository = resolve(import.meta.dirname, '../../..');
async function fixture(t) {
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/lanes-'));
  await symlink(join(repository, 'node_modules'), join(root, 'node_modules'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const code = `export const active = process.env.OXIMUT_ACTIVE_MUTANT; let n = 0; export function count() { return n++; }`;
  await writeFile(
    join(root, 'subject.ts'),
    'export const active = "uninstrumented"; export function count() { return -1; }',
  );
  await writeFile(
    join(root, 'vitest.config.mjs'),
    "export default { test: { include: ['*.test.ts'], testTimeout: 300 } };",
  );
  await writeFile(
    join(root, 'shared.test.ts'),
    `
import { test, expect } from 'vitest';
import { appendFileSync, existsSync, readdirSync, writeFileSync } from 'node:fs';
import { active, count } from './subject';
const root = ${JSON.stringify(root)};
appendFileSync(root+'/events', JSON.stringify({type:'enter', id:active, pid:process.pid})+'\\n');
test('fresh and overlap', { timeout: 10000 }, async () => {
  expect(count()).toBe(0);
  if (existsSync(root+'/barrier')) {
    writeFileSync(root+'/ready-'+active, '');
    while (readdirSync(root).filter(n => n.startsWith('ready-')).length < 3)
      await new Promise(r => setTimeout(r, 5));
  }
  appendFileSync(root+'/events', JSON.stringify({type:'released', id:active, pid:process.pid})+'\\n');
});
test('mutation', async () => {
  if (active === 'timeout') await new Promise(r => setTimeout(r, 10000));
  if (active === 'crash') process.kill(process.pid, 'SIGKILL');
  if (active === 'hang') while (true) {}
  expect(active).not.toBe('killed');
});
test('after', () => {
 appendFileSync(root+'/events', JSON.stringify({type:'after', id:active, pid:process.pid})+'\\n');
 expect(count()).toBe(1);
});`,
  );
  const job = (id) => ({
    resident: [
      {
        id: '__resident__',
        file: join(root, 'subject.ts'),
        code,
        map: { version: 3, sources: ['subject.ts'], names: [], mappings: 'AAAA' },
      },
    ],
    protocol: 1,
    runId: 'lanes',
    jobId: id,
    generation: 1,
    kind: 'mutant',
    root,
    config: join(root, 'vitest.config.mjs'),
    deadlineMs: 30000,
    mutation: {
      id,
      file: join(root, 'subject.ts'),
      code,
      map: { version: 3, sources: ['subject.ts'], names: [], mappings: 'AAAA' },
    },
  });
  async function controller() {
    const child = fork(join(repository, 'packages/vitest-adapter/m2-worker.ts'), [], {
      stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
      execArgv: [],
    });
    let diagnostics = '';
    child.stderr.on('data', (data) => {
      diagnostics += data;
    });
    let sequence = 0;
    const exited = new Promise((resolve) => child.once('exit', resolve));
    const command = (command, args) =>
      new Promise((resolve, reject) => {
        const id = ++sequence;
        const timer = setTimeout(() => reject(new Error('IPC deadline: ' + diagnostics)), 45000);
        child.once('message', (reply) => {
          clearTimeout(timer);
          if (reply.id !== id || reply.error)
            reject(new Error(JSON.stringify(reply) + diagnostics));
          else resolve(reply.result);
        });
        child.send({ protocol: 1, id, command, args });
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
  return { root, job, controller };
}
function evidence(result) {
  return { outcome: result.outcome, tests: result.tests };
}
async function assertGone(root) {
  const events = (await readFile(join(root, 'events'), 'utf8')).trim().split('\n').map(JSON.parse);
  for (const pid of new Set(events.map((e) => e.pid)))
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
  return events;
}

test(
  'shared lanes match fresh-process oracle, overlap, local bail, isolation, reuse and cleanup',
  { timeout: 120000 },
  async (t) => {
    const s = await fixture(t);
    const ids = ['killed', 'survived', 'timeout'];
    const oracle = [];
    for (const id of ids) {
      const c = await s.controller();
      oracle.push(evidence(await c.run(s.job(id))));
      await c.close();
    }
    assert.deepEqual(
      oracle.map((r) => r.outcome),
      ['failed', 'passed', 'timeout'],
    );
    await writeFile(join(s.root, 'events'), '');
    await writeFile(join(s.root, 'barrier'), '');
    const c = await s.controller();
    const jobs = ids.map(s.job);
    const result = await c.run({ ...jobs[0], lanes: jobs });
    assert.deepEqual(result.lanes.map(evidence), oracle);
    const events = await assertGone(s.root);
    const enters = events.filter((e) => e.type === 'enter');
    assert.equal(new Set(enters.map((e) => e.pid)).size, 3);
    assert.equal(
      events.slice(0, 3).every((e) => e.type === 'enter'),
      true,
    );
    assert.deepEqual(
      events.filter((e) => e.type === 'after').map((e) => e.id),
      ['survived'],
    );
    // Reassign the same bounded lane projects after all prior forks have exited.
    const again = await c.run({ ...jobs[0], lanes: jobs });
    assert.deepEqual(again.lanes.map(evidence), oracle);
    const all = await assertGone(s.root);
    assert.equal(new Set(all.filter((e) => e.type === 'enter').map((e) => e.pid)).size, 6);
    await c.close();
  },
);

test('a crashed shared fork fails closed and every fork exits', { timeout: 90000 }, async (t) => {
  const s = await fixture(t);
  const c = await s.controller();
  const jobs = ['crash', 'survived'].map(s.job);
  const result = await c.run({ ...jobs[0], lanes: jobs });
  assert.equal(result.lanes[0].outcome, 'infrastructure-error');
  assert.equal(result.lanes[1].outcome, 'passed');
  await assertGone(s.root);
  const hung = ['hang', 'survived'].map((id) => ({ ...s.job(id), deadlineMs: 5000 }));
  const stopped = await c.run({ ...hung[0], lanes: hung });
  // A hung lane is a timeout, as with one controller per worker, and its
  // finished neighbour keeps its result.
  assert.equal(stopped.lanes[0].outcome, 'timeout');
  assert.equal(stopped.lanes[1].outcome, 'passed');
  await assertGone(s.root);
  await c.close();
});

test(
  'batch assignments reject conflicting modules and duplicate identities',
  { timeout: 90000 },
  async (t) => {
    const s = await fixture(t);
    const c = await s.controller();
    const jobs = ['killed', 'survived'].map(s.job);
    await assert.rejects(
      c.run({ ...jobs[0], lanes: [jobs[0], jobs[0]] }),
      /Invalid lane assignment/,
    );
    jobs[1].mutation.code = 'export const active = "wrong";';
    await assert.rejects(c.run({ ...jobs[0], lanes: jobs }), /resident instrumented module/);
    await c.close();
  },
);

test(
  'multi-file selections use one fresh process per mutant and match the oracle',
  { timeout: 90000 },
  async (t) => {
    const s = await fixture(t);
    await writeFile(
      join(s.root, 'other.test.ts'),
      `
import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { active, count } from './subject';
appendFileSync(${JSON.stringify(join(s.root, 'events'))}, JSON.stringify({type:'other', id:active, pid:process.pid})+'\\n');
test('other', () => expect(count()).toBe(0));
`,
    );
    const jobs = ['survived', 'killed', 'timeout', 'selected'].map(s.job);
    jobs[3].selection = {
      mode: 'files',
      files: [join(s.root, 'other.test.ts')],
      selectedTests: 1,
      totalTests: 4,
      reasons: [],
    };
    const oracle = [];
    for (const job of jobs) {
      const c = await s.controller();
      oracle.push(evidence(await c.run(job)));
      await c.close();
    }
    await writeFile(join(s.root, 'events'), '');
    const c = await s.controller();
    const result = await c.run({ ...jobs[0], lanes: jobs });
    // A killed mutant stops at its first failure, so how many tests passed before
    // it depends on file order. Its outcome and failure count must still match.
    const comparable = (entry) =>
      entry.outcome === 'failed'
        ? { outcome: entry.outcome, failed: entry.tests.failed, skipped: entry.tests.skipped }
        : entry;
    assert.deepEqual(result.lanes.map(evidence).map(comparable), oracle.map(comparable));
    const events = await assertGone(s.root);
    for (const job of jobs) {
      const own = events.filter((e) => e.id === job.jobId);
      if (job.jobId === 'survived' || job.jobId === 'selected')
        assert.equal(
          own.some((e) => e.type === 'other'),
          true,
        );
      assert.equal(
        own.some((e) => e.type === 'after'),
        job.jobId === 'survived',
      );
      assert.equal(new Set(own.map((e) => e.pid)).size, 1);
    }
    await c.close();
  },
);

test(
  'a file the current run did not report never reuses earlier results',
  { timeout: 90000 },
  async (t) => {
    const s = await fixture(t);
    const lanes = new SharedLanes();
    t.after(() => lanes.close());
    const jobs = ['survived', 'selected'].map(s.job);
    const first = await lanes.run({ ...jobs[0], lanes: jobs });
    assert.deepEqual(
      first.lanes.map((lane) => lane.outcome),
      ['passed', 'passed'],
    );
    // A run that delivers nothing: only earlier state could still report a result.
    lanes.context.runTestSpecifications = async () => ({
      testModules: lanes.context.state.getTestModules(),
      unhandledErrors: [],
    });
    const second = await lanes.run({ ...jobs[0], lanes: jobs });
    assert.deepEqual(
      second.lanes.map((lane) => lane.outcome),
      ['infrastructure-error', 'infrastructure-error'],
    );
  },
);

test('a run that throws drops the context', { timeout: 90000 }, async (t) => {
  const s = await fixture(t);
  const lanes = new SharedLanes();
  t.after(() => lanes.close());
  const jobs = ['survived', 'selected'].map(s.job);
  await lanes.run({ ...jobs[0], lanes: jobs });
  lanes.context.runTestSpecifications = async () => {
    throw new Error('run failed');
  };
  await assert.rejects(lanes.run({ ...jobs[0], lanes: jobs }), /run failed/);
  assert.equal(lanes.context, undefined);
});

test('lane errors are bounded', { timeout: 90000 }, async (t) => {
  const s = await fixture(t);
  await writeFile(
    join(s.root, 'long.test.ts'),
    "import { test } from 'vitest'; test('long', () => { throw new Error('x'.repeat(100000)); });",
  );
  const jobs = ['long-a', 'long-b'].map(s.job);
  for (const job of jobs)
    job.selection = {
      mode: 'files',
      files: [join(s.root, 'long.test.ts')],
      selectedTests: 1,
      totalTests: 1,
      reasons: [],
    };
  const c = await s.controller();
  const result = await c.run({ ...jobs[0], lanes: jobs });
  await c.close();
  for (const lane of result.lanes) {
    assert.equal(lane.outcome, 'failed');
    assert.ok(lane.errors.length && lane.errors.every((error) => error.length <= 2048));
  }
});

test('a custom runner falls back to one controller per worker', { timeout: 90000 }, async (t) => {
  const s = await fixture(t);
  await writeFile(
    join(s.root, 'custom-runner.mjs'),
    "export { TestRunner as default } from 'vitest';",
  );
  await writeFile(
    join(s.root, 'vitest.config.mjs'),
    "export default { test: { include: ['*.test.ts'], testTimeout: 300, runner: './custom-runner.mjs' } };",
  );
  const c = await s.controller();
  const jobs = ['survived', 'selected'].map(s.job);
  const result = await c
    .run({ ...jobs[0], lanes: jobs })
    .catch((error) => ({ errors: [error.message] }));
  await c.close();
  assert.equal(result.lanes, undefined);
  assert.match(result.errors.join(';'), /Shared lanes require/);
});

test('an unexplained batch error outranks a lane timeout', { timeout: 90000 }, async (t) => {
  const s = await fixture(t);
  const lanes = new SharedLanes();
  t.after(() => lanes.close());
  const jobs = ['survived', 'selected'].map(s.job);
  await lanes.run({ ...jobs[0], lanes: jobs });
  const run = lanes.context.runTestSpecifications.bind(lanes.context);
  lanes.context.runTestSpecifications = async (...args) => {
    const result = await run(...args);
    return { ...result, unhandledErrors: [...result.unhandledErrors, new Error('unrelated')] };
  };
  const hung = ['hang', 'survived'].map((id) => ({ ...s.job(id), deadlineMs: 3000 }));
  const result = await lanes.run({ ...hung[0], lanes: hung });
  process.exitCode = undefined;
  assert.deepEqual(
    result.lanes.map((lane) => lane.outcome),
    ['infrastructure-error', 'infrastructure-error'],
  );
});

test('a smaller batch reuses the lanes of a stable width', { timeout: 90000 }, async (t) => {
  const s = await fixture(t);
  const lanes = new SharedLanes();
  t.after(() => lanes.close());
  const jobs = ['survived', 'selected'].map(s.job);
  const full = await lanes.run({ ...jobs[0], workers: 2, lanes: jobs });
  assert.deepEqual(
    full.lanes.map((lane) => lane.outcome),
    ['passed', 'passed'],
  );
  const context = lanes.context;
  await writeFile(join(s.root, 'events'), '');
  const small = await lanes.run({ ...jobs[1], workers: 2, lanes: [jobs[1]] });
  assert.deepEqual(
    small.lanes.map((lane) => lane.outcome),
    ['passed'],
  );
  assert.equal(lanes.context, context);
  // Only the requested mutant ran; the idle lane did nothing.
  const events = (await readFile(join(s.root, 'events'), 'utf8'))
    .split('\n')
    .filter(Boolean)
    .map(JSON.parse);
  assert.ok(events.length);
  // The idle lane last ran 'selected' too, so count processes as well.
  assert.deepEqual([...new Set(events.map((event) => event.id))], ['selected']);
  assert.equal(new Set(events.map((event) => event.pid)).size, 1);
  await lanes.run({ ...jobs[1], lanes: [jobs[1]] });
  assert.notEqual(lanes.context, context);
});

test(
  'lanes share one transform per file and a changed resident module invalidates it',
  { timeout: 90000 },
  async (t) => {
    const s = await fixture(t);
    const transforms = join(s.root, 'transforms');
    await writeFile(transforms, '');
    await writeFile(
      join(s.root, 'vitest.config.mjs'),
      `import { appendFileSync } from 'node:fs';
export default {
  plugins: [{ name: 'count', enforce: 'post', transform(code, id) {
    if (!id.includes('node_modules')) appendFileSync(${JSON.stringify(transforms)}, id + '\\n');
  } }],
  test: { include: ['probe.test.ts'], testTimeout: 300 },
};`,
    );
    await writeFile(
      join(s.root, 'probe.test.ts'),
      `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import * as subject from './subject';
test('probe', () => {
  appendFileSync(${JSON.stringify(join(s.root, 'events'))},
    JSON.stringify({ id: subject.active, marker: subject.marker }) + '\\n');
  expect(subject.active).toMatch(/^survived/);
});`,
    );
    await writeFile(join(s.root, 'events'), '');
    const resident = (marker) => {
      const code = `export const active = process.env.OXIMUT_ACTIVE_MUTANT; export const marker = '${marker}';`;
      return {
        id: '__resident__',
        file: join(s.root, 'subject.ts'),
        code,
        map: { version: 3, sources: ['subject.ts'], names: [], mappings: 'AAAA' },
      };
    };
    const batch = (marker) => {
      const module = resident(marker);
      const jobs = ['survived', 'survived2', 'survived3'].map((id) => ({
        ...s.job(id),
        resident: [module],
        mutation: { ...module, id },
      }));
      return { ...jobs[0], workers: 3, lanes: jobs };
    };
    const seen = async () => (await readFile(transforms, 'utf8')).split('\n').filter(Boolean);
    const events = async () =>
      (await readFile(join(s.root, 'events'), 'utf8')).split('\n').filter(Boolean).map(JSON.parse);
    const lanes = new SharedLanes();
    t.after(() => lanes.close());

    const first = await lanes.run(batch('a'));
    assert.deepEqual(
      first.lanes.map((lane) => lane.outcome),
      ['passed', 'passed', 'passed'],
    );
    const firstSeen = await seen();
    // Three lanes, one transform per file.
    assert.equal(new Set(firstSeen).size, firstSeen.length);
    assert.ok(firstSeen.some((id) => id.endsWith('probe.test.ts')));
    assert.ok(firstSeen.some((id) => id.endsWith('subject.ts')));
    assert.ok(lanes.transforms.stats.hits > 0);
    assert.deepEqual(
      (await events()).map((event) => event.marker),
      ['a', 'a', 'a'],
    );

    // An unchanged resident module keeps the shared results, and its lanes still ran.
    await writeFile(join(s.root, 'events'), '');
    const again = await lanes.run(batch('a'));
    assert.deepEqual(
      again.lanes.map((lane) => lane.outcome),
      ['passed', 'passed', 'passed'],
    );
    assert.deepEqual(
      (await events()).map((event) => event.marker),
      ['a', 'a', 'a'],
    );
    assert.deepEqual(await seen(), firstSeen);

    // A changed resident module must never be served from the earlier cache.
    await writeFile(join(s.root, 'events'), '');
    const generation = lanes.transforms.generation;
    await lanes.run(batch('b'));
    assert.ok(lanes.transforms.generation > generation);
    assert.deepEqual(
      (await events()).map((event) => event.marker),
      ['b', 'b', 'b'],
    );
    const after = await seen();
    // Invalidation dropped every lane's graph: each file was transformed once more
    // for all lanes together, not once per lane.
    for (const id of new Set(after)) assert.equal(after.filter((seen) => seen === id).length, 2);
  },
);
