import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import test from 'node:test';

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
  assert.notEqual(result.lanes[1].outcome, 'failed');
  await assertGone(s.root);
  const hung = ['hang', 'survived'].map((id) => ({ ...s.job(id), deadlineMs: 5000 }));
  const stopped = await c.run({ ...hung[0], lanes: hung });
  assert.equal(stopped.lanes[0].outcome, 'infrastructure-error');
  assert.notEqual(stopped.lanes[1].outcome, 'failed');
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
    assert.deepEqual(result.lanes.map(evidence), oracle);
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
