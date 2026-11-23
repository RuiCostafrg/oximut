import assert from 'node:assert/strict';
import { fork, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';

const repository = resolve(import.meta.dirname, '../../..');
const builtAddon = join(repository, 'packages/vitest-adapter/native/build/zygote-fork.node');
const required = process.env.OXIMUT_REQUIRE_ZYGOTE_ADDON === '1';

// The real addon: the existing build output, else build it with the repository script.
function addonProblem() {
  if (process.platform !== 'linux') return `zygote workers need Linux, not ${process.platform}`;
  if (!existsSync(builtAddon))
    spawnSync('python3', [join(repository, 'scripts/build-zygote-addon.py')], { stdio: 'ignore' });
  return existsSync(builtAddon) ? undefined : 'the zygote addon could not be built';
}
const problem = addonProblem();
// Tests that need the real addon skip without it, and fail when CI requires it.
function zygoteTest(name, options, body) {
  const skip = problem && !required ? problem : false;
  test(name, { ...options, skip }, async (t) => {
    if (problem) assert.fail(problem);
    await body(t);
  });
}
// Fake addons exercise failures without the real one, but still need Linux.
function linuxTest(name, options, body) {
  test(name, { ...options, skip: process.platform !== 'linux' && 'Linux only' }, body);
}

const code = "export const enabled = process.env.OXIMUT_ACTIVE_MUTANT !== 'kill';";
const map = { version: 3, sources: ['subject.ts'], names: [], mappings: 'AAAA' };
async function fixture(t) {
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/zygote-'));
  await symlink(join(repository, 'node_modules'), join(root, 'node_modules'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'counters'));
  await writeFile(join(root, 'subject.ts'), 'export const enabled = true;');
  await writeFile(
    join(root, 'vitest.config.mjs'),
    "export default { test: { include: ['*.test.ts'] } };",
  );
  await writeFile(
    join(root, 'sample.test.ts'),
    `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { enabled } from './subject';
appendFileSync(${JSON.stringify(join(root, 'events'))}, JSON.stringify({ pid: process.pid, ppid: process.ppid, active: process.env.OXIMUT_ACTIVE_MUTANT }) + '\\n');
test('enabled', () => expect(enabled).toBe(true));`,
  );
  await writeFile(join(root, 'events'), '');
  const job = (id) => ({
    resident: [{ id: '__resident__', file: join(root, 'subject.ts'), code, map }],
    protocol: 1,
    runId: 'zygote',
    jobId: id,
    generation: 1,
    kind: 'mutant',
    root,
    config: join(root, 'vitest.config.mjs'),
    deadlineMs: 60000,
    mutation: { id, file: join(root, 'subject.ts'), code, map },
  });
  return { root, job };
}

const evidence = (result) => ({ outcome: result.outcome, tests: result.tests });

/**
 * One controller (m2-worker.ts, as the CLI drives it) running mutants as
 * single jobs and then as a two-lane shared batch: five mutant workers in all.
 * `between` runs after the first job's reply, with all replies so far.
 */
async function scenario(t, environment, between = async () => {}) {
  const { root, job } = await fixture(t);
  const env = { ...process.env, OXIMUT_ZYGOTE_COUNTERS_DIR: join(root, 'counters') };
  for (const name of Object.keys(env)) if (name.startsWith('OXIMUT_ZYGOTE')) delete env[name];
  Object.assign(env, { OXIMUT_ZYGOTE_COUNTERS_DIR: join(root, 'counters'), ...environment });
  const child = fork(join(repository, 'packages/vitest-adapter/m2-worker.ts'), [], {
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    env,
  });
  let diagnostics = '';
  child.stderr.on('data', (data) => {
    diagnostics += data;
  });
  let sequence = 0;
  const exited = new Promise((resolve) => child.once('exit', resolve));
  t.after(async () => {
    child.kill('SIGKILL');
    await exited;
  });
  const command = (command, args) =>
    new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => reject(new Error(`IPC deadline: ${diagnostics}`)), 90000);
      child.once('message', (reply) => {
        clearTimeout(timer);
        if (reply.id !== id || reply.error) reject(new Error(JSON.stringify(reply) + diagnostics));
        else resolve(reply.result);
      });
      child.send({ protocol: 1, id, command, args });
    });
  await command('initialize', { ...job('init'), kind: 'baseline', mutation: null });
  const replies = [];
  for (const id of ['kill', 'survive', 'kill']) {
    replies.push(await command('run', { request: job(id) }));
    if (replies.length === 1) await between(replies);
  }
  const jobs = ['kill', 'survive'].map(job);
  replies.push(await command('run', { request: { ...jobs[0], lanes: jobs } }));
  await command('close', {});
  child.disconnect();
  await exited;
  const files = await readdir(join(root, 'counters'));
  const counters = files.length
    ? JSON.parse(await readFile(join(root, 'counters', files[0]), 'utf8'))
    : undefined;
  const events = (await readFile(join(root, 'events'), 'utf8')).trim().split('\n').map(JSON.parse);
  return {
    root,
    outcomes: replies.map((reply) => (reply.lanes ? reply.lanes.map(evidence) : evidence(reply))),
    helpers: replies.map((reply) => reply.residentHelpers),
    counterFiles: files,
    counters,
    events,
    diagnostics,
  };
}
const expected = [
  { outcome: 'failed', tests: { passed: 0, failed: 1, skipped: 0 } },
  { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 } },
  { outcome: 'failed', tests: { passed: 0, failed: 1, skipped: 0 } },
  [
    { outcome: 'failed', tests: { passed: 0, failed: 1, skipped: 0 } },
    { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 } },
  ],
];
const workers = 5;

test('without OXIMUT_ZYGOTE, replies carry no helpers and no zygote starts', {
  timeout: 240000,
}, async (t) => {
  const result = await scenario(t, {});
  assert.deepEqual(result.outcomes, expected);
  assert.deepEqual(result.helpers, Array(4).fill(undefined));
  assert.deepEqual(result.counterFiles, []);
  assert.equal(result.events.length, workers);
  assert.doesNotMatch(result.diagnostics, /zygote/);
});

zygoteTest(
  'zygote workers give the same outcomes, fork every mutant worker and report helpers',
  {
    timeout: 240000,
  },
  async (t) => {
    const result = await scenario(t, { OXIMUT_ZYGOTE: '1' });
    assert.deepEqual(result.outcomes, expected);
    assert.ok(result.counters.zygotes >= 1);
    assert.equal(result.counters.forks, workers);
    assert.equal(result.counters.exits, workers);
    assert.equal(result.counters.zygoteFailures, 0);
    assert.equal(result.counters.fallbacks, 0);
    // Every reply after the first mutant names the live zygote(s).
    for (const helpers of result.helpers) {
      assert.ok(Array.isArray(helpers) && helpers.length >= 1);
      assert.ok(helpers.every(Number.isSafeInteger));
    }
    // Each mutant worker really is a child of a zygote, with its own PID.
    const zygotes = new Set(result.helpers.flat());
    assert.equal(new Set(result.events.map((event) => event.pid)).size, workers);
    for (const event of result.events) assert.ok(zygotes.has(event.ppid), JSON.stringify(event));
    for (const pid of zygotes) assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
  },
);

zygoteTest('baseline workers stay stock under OXIMUT_ZYGOTE', { timeout: 120000 }, async (t) => {
  const { root, job } = await fixture(t);
  const env = {
    ...process.env,
    OXIMUT_ZYGOTE: '1',
    OXIMUT_ZYGOTE_COUNTERS_DIR: join(root, 'counters'),
  };
  const child = fork(join(repository, 'packages/vitest-adapter/m2-worker.ts'), [], {
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    env,
  });
  const exited = new Promise((resolve) => child.once('exit', resolve));
  t.after(async () => {
    child.kill('SIGKILL');
    await exited;
  });
  const command = (command, args) =>
    new Promise((resolve, reject) => {
      child.once('message', (reply) =>
        reply.error ? reject(new Error(reply.error)) : resolve(reply.result),
      );
      child.send({ protocol: 1, id: 1, command, args });
    });
  const baseline = { ...job('baseline'), kind: 'baseline', mutation: null };
  await command('initialize', baseline);
  const result = await command('run', { request: baseline });
  assert.equal(result.outcome, 'passed');
  assert.equal(result.residentHelpers, undefined);
  child.disconnect();
  await exited;
  assert.equal(
    JSON.parse(
      await readFile(join(root, 'counters', (await readdir(join(root, 'counters')))[0]), 'utf8'),
    ).zygotes,
    0,
  );
  const [event] = (await readFile(join(root, 'events'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(event.ppid, child.pid);
});

linuxTest(
  'a missing addon means normal workers, one log line and no helpers',
  {
    timeout: 240000,
  },
  async (t) => {
    const result = await scenario(t, {
      OXIMUT_ZYGOTE: '1',
      OXIMUT_ZYGOTE_ADDON: '/nonexistent/zygote-fork.node',
    });
    assert.deepEqual(result.outcomes, expected);
    assert.deepEqual(result.helpers, Array(4).fill(undefined));
    assert.deepEqual(result.counters, {
      zygotes: 0,
      forks: 0,
      exits: 0,
      zygoteFailures: 0,
      fallbacks: 0,
    });
    assert.equal(result.diagnostics.match(/zygote workers unavailable/g).length, 1);
    assert.match(result.diagnostics, /addon not found at \/nonexistent\/zygote-fork\.node/);
  },
);

// A JS module can stand in for the addon: the controller only loads it, while
// the zygote preload loads it too and can be made to die or stall right there.
async function fakeAddon(root, source) {
  const file = join(root, 'fake-addon.cjs');
  await writeFile(file, source);
  return file;
}
const inZygote = "(process.argv[1] ?? '').endsWith('forks.js')";
async function failing(t, source, environment = {}) {
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/zygote-fake-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const file = await fakeAddon(root, source);
  return scenario(t, { OXIMUT_ZYGOTE: '1', OXIMUT_ZYGOTE_ADDON: file, ...environment });
}
function assertFellBack(result) {
  assert.deepEqual(result.outcomes, expected);
  assert.equal(result.counters.zygoteFailures, 1);
  assert.equal(result.counters.forks, 0);
  // The queued child and every later worker were served by a real fork.
  assert.equal(result.counters.fallbacks, workers);
  assert.equal(result.events.length, workers);
  assert.deepEqual(result.helpers, Array(4).fill(undefined));
  assert.equal(result.diagnostics.match(/zygote workers disabled/g).length, 1);
}

linuxTest(
  'a zygote killed before the first mutant fork falls back to real workers',
  {
    timeout: 240000,
  },
  async (t) => {
    assertFellBack(
      await failing(
        t,
        `if (${inZygote}) process.kill(process.pid, 'SIGKILL'); module.exports = {};`,
      ),
    );
  },
);

zygoteTest(
  'a zygote that dies on the first fork request falls back to real workers',
  {
    timeout: 240000,
  },
  async (t) => {
    assertFellBack(
      await failing(
        t,
        `const real = require(${JSON.stringify(builtAddon)});
module.exports = { ...real, fork() { if (${inZygote}) process.kill(process.pid, 'SIGKILL'); return real.fork(); } };`,
      ),
    );
  },
);

linuxTest(
  'a zygote that never reports ready is killed and replaced by real workers',
  {
    timeout: 240000,
  },
  async (t) => {
    assertFellBack(
      await failing(
        t,
        `if (${inZygote}) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 120000); module.exports = {};`,
        { OXIMUT_ZYGOTE_READY_TIMEOUT_MS: '1500' },
      ),
    );
  },
);

zygoteTest(
  'a zygote killed between mutants disables itself and later workers are real',
  {
    timeout: 240000,
  },
  async (t) => {
    const result = await scenario(t, { OXIMUT_ZYGOTE: '1' }, async ([first]) => {
      assert.equal(first.residentHelpers.length, 1);
      process.kill(first.residentHelpers[0], 'SIGKILL');
      // Wait for the process to be gone so the controller has seen its exit.
      const until = Date.now() + 10000;
      while (Date.now() < until) {
        try {
          process.kill(first.residentHelpers[0], 0);
        } catch {
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    });
    assert.deepEqual(result.outcomes, expected);
    assert.equal(result.counters.forks, 1);
    assert.equal(result.counters.zygoteFailures, 1);
    assert.equal(result.counters.fallbacks, workers - 1);
    assert.equal(result.events.length, workers);
    assert.ok(Array.isArray(result.helpers[0]));
    assert.deepEqual(result.helpers.slice(1), Array(3).fill(undefined));
    assert.equal(result.diagnostics.match(/zygote workers disabled/g).length, 1);
  },
);
