import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import {
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rename,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';

const repository = resolve(import.meta.dirname, '../../..');
const source = 'export const enabled = true;';
const switched =
  "export const enabled = process.env.OXIMUT_ACTIVE_MUTANT === 'kill' ? false : true;";
async function session(t, environment = {}) {
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/m2-test-'));
  const directory = join(root, 'session');
  await mkdir(directory);
  await writeFile(join(root, 'source.ts'), source);
  await writeFile(
    join(root, 'vitest.config.mjs'),
    "export default { test: { include: ['sample.test.ts'] } };",
  );
  await writeFile(
    join(root, 'sample.test.ts'),
    `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs'; import { enabled } from './source';
appendFileSync(${JSON.stringify(join(directory, 'observations'))}, JSON.stringify({pid: process.pid, parent: process.ppid, active: process.env.OXIMUT_ACTIVE_MUTANT})+'\\n');
let state = 0; test('enabled', () => { expect(state++).toBe(0); expect(enabled).toBe(true); });`,
  );
  const child = spawn(
    process.execPath,
    [
      '--no-experimental-strip-types',
      join(repository, 'packages/vitest-adapter/m2-runner.mjs'),
      directory,
    ],
    { stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, ...environment } },
  );
  let diagnostics = '';
  child.stderr.on('data', (data) => {
    diagnostics += data;
  });
  const exited = new Promise((resolve) => child.once('exit', resolve));
  t.after(async () => {
    await writeFile(join(directory, 'stop'), '');
    await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 10000).unref())]);
    if (child.exitCode === null) child.kill('SIGKILL');
    await rm(root, { recursive: true, force: true });
  });
  let sequence = 0;
  return {
    root,
    async run(
      id,
      code = switched,
      deadlineMs = 30000,
      filename = 'source.ts',
      requestBytes,
      extra = {},
    ) {
      const n = sequence++;
      const input = {
        protocol: 1,
        runId: 'm2',
        jobId: `${n}`,
        generation: n,
        kind: code === null ? 'baseline' : 'mutant',
        root,
        config: join(root, 'vitest.config.mjs'),
        deadlineMs,
        mutation:
          code === null
            ? null
            : {
                id,
                file: join(root, filename),
                code,
                map: { version: 3, sources: [filename], mappings: 'AAAA', names: [] },
              },
        ...extra,
      };
      let payload = JSON.stringify(input);
      if (requestBytes !== undefined) {
        const padding = requestBytes - Buffer.byteLength(payload);
        assert.ok(padding >= 0);
        // JSON whitespace exercises the actual serialized request limit without
        // making test transformation cost depend on an enormous source comment.
        payload += ' '.repeat(padding);
        assert.equal(Buffer.byteLength(payload), requestBytes);
      }
      await writeFile(join(directory, 'request.tmp'), payload);
      await rename(join(directory, 'request.tmp'), join(directory, `request-${n}.json`));
      const until = Date.now() + deadlineMs + 10000;
      while (Date.now() < until) {
        try {
          return JSON.parse(await readFile(join(directory, `response-${n}.json`), 'utf8'));
        } catch (error) {
          if (error.code !== 'ENOENT') throw error;
        }
        assert.equal(child.exitCode, null, diagnostics);
        await new Promise((resolve) => setTimeout(resolve, 15));
      }
      throw new Error(`Response timeout: ${diagnostics}`);
    },
  };
}

test(
  'M2 reuses controller with fresh test forks, switches import-time selector and clears baseline',
  { timeout: 120000 },
  async (t) => {
    const s = await session(t);
    for (const [id, code, expected] of [
      ['disabled', switched, 'passed'],
      ['kill', switched, 'failed'],
      ['disabled', switched, 'passed'],
      ['', null, 'passed'],
      ['kill', switched, 'failed'],
      ['disabled', 'export const enabled = false;', 'failed'],
      ['disabled', switched, 'passed'],
    ])
      assert.equal((await s.run(id, code)).outcome, expected);
    const observations = (await readFile(join(s.root, 'session/observations'), 'utf8'))
      .trim()
      .split('\n')
      .map(JSON.parse);
    assert.equal(
      new Set(observations.map((item) => item.parent)).size,
      1,
      await readFile(join(s.root, 'session/events.jsonl'), 'utf8'),
    );
    assert.equal(new Set(observations.map((item) => item.pid)).size, observations.length);
    assert.deepEqual(
      observations.map((item) => item.active),
      ['disabled', 'kill', 'disabled', undefined, 'kill', 'disabled', 'disabled'],
    );
  },
);

test(
  'M2 serves validated resident modules in every job and restores changed files exactly',
  { timeout: 120000 },
  async (t) => {
    const s = await session(t);
    const observed = join(s.root, 'session/resident-observations');
    await writeFile(join(s.root, 'other.ts'), 'export const other = 1;');
    await writeFile(
      join(s.root, 'sample.test.ts'),
      `import { test, expect } from 'vitest';
import { enabled } from './source'; import { other } from './other';
test('enabled', () => { expect(enabled).toBe(true); expect(other).toBe(1); });`,
    );
    const map = { version: 3, sources: ['other.ts'], mappings: 'AAAA', names: [] };
    const resident = [
      {
        id: '__oximut_resident__',
        file: join(s.root, 'other.ts'),
        code: `import { appendFileSync } from 'node:fs';
appendFileSync(${JSON.stringify(observed)}, 'resident\\n');
export const other = process.env.OXIMUT_ACTIVE_MUTANT === 'other' ? 2 : 1;`,
        map,
      },
    ];
    const count = async () => {
      try {
        return (await readFile(observed, 'utf8')).split('\n').filter(Boolean).length;
      } catch (error) {
        if (error.code === 'ENOENT') return 0;
        throw error;
      }
    };
    const steps = [
      // [id, mutation code, resident, expected outcome, resident evaluations]
      ['', null, undefined, 'passed', 0],
      ['', null, resident, 'passed', 1],
      ['other', switched, resident, 'failed', 2],
      ['kill', switched, resident, 'failed', 3],
      ['disabled', switched, resident, 'passed', 4],
      ['static', 'export const other = 3;', resident, 'failed', 4, 'other.ts'],
      ['disabled', switched, resident, 'passed', 5],
      ['', null, undefined, 'passed', 5],
    ];
    for (const [id, code, shared, expected, evaluations, filename = 'source.ts'] of steps) {
      const response = await s.run(
        id,
        code,
        30000,
        filename,
        undefined,
        shared ? { resident: shared } : {},
      );
      assert.equal(response.outcome, expected, JSON.stringify([id, response]));
      // Executed results acknowledge exactly the resident modules served.
      assert.equal(response.resident, shared?.length, id);
      assert.equal(await count(), evaluations, id);
    }
  },
);

test(
  'M2 test forks populate the provided compile cache while the controller persists',
  { timeout: 120000 },
  async (t) => {
    await mkdir(join(repository, '.oximut'), { recursive: true });
    const cache = await mkdtemp(join(repository, '.oximut/m2-compile-cache-'));
    const s = await session(t, { NODE_COMPILE_CACHE: cache });
    // after() hooks run in registration order: remove the cache only once the
    // session hook has stopped the runner, so its stop-time flush cannot
    // recreate the directory under .oximut/.
    t.after(() => rm(cache, { recursive: true, force: true }));
    assert.equal((await s.run('disabled')).outcome, 'passed');
    // The controller only flushes at exit; entries now come from test forks.
    const entries = await readdir(cache, { recursive: true, withFileTypes: true });
    assert.ok(
      entries.some((entry) => entry.isFile()),
      'fork compile cache was not flushed',
    );
    assert.equal((await s.run('kill')).outcome, 'failed');
  },
);

test(
  'M2 accepts the request byte limit and rejects one byte over it',
  { timeout: 120000 },
  async (t) => {
    const s = await session(t);
    const limit = 8 * 1024 * 1024;
    const atLimit = await s.run('kill', switched, 30000, 'source.ts', limit);
    assert.equal(atLimit.outcome, 'failed', JSON.stringify(atLimit));
    assert.equal(atLimit.tests.failed, 1);
    assert.equal((await s.run('original', null)).outcome, 'passed');
    const oversized = await s.run('kill', switched, 30000, 'source.ts', limit + 1);
    assert.equal(oversized.outcome, 'infrastructure-error', JSON.stringify(oversized));
    assert.match(oversized.errors.join('\n'), /at most 8 MiB|exceeds 8 MiB/);
    assert.equal((await s.run('original', null)).outcome, 'passed');
  },
);

test(
  'M2 replaces controller after timeout and crash and preserves total deadline',
  { timeout: 120000 },
  async (t) => {
    const s = await session(t);
    assert.equal((await s.run('disabled')).outcome, 'passed');
    assert.equal(
      (await s.run('hang', 'while(true) {}; export const enabled = true;', 1000)).outcome,
      'timeout',
    );
    assert.equal((await s.run('disabled')).outcome, 'passed');
    assert.equal(
      (await s.run('crash', "process.kill(process.pid, 'SIGKILL'); export const enabled = true;"))
        .outcome,
      'infrastructure-error',
    );
    assert.equal((await s.run('disabled')).outcome, 'passed');
  },
);

test(
  'M2 reaps detached descendants of passing jobs before publishing results',
  { timeout: 60000 },
  async (t) => {
    const s = await session(t);
    const marker = join(s.root, 'session/detached.pid');
    const code = `import { spawn } from 'node:child_process'; import { writeFileSync } from 'node:fs';
const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { detached: true, stdio: 'ignore' });
child.unref(); writeFileSync(${JSON.stringify(marker)}, String(child.pid)); export const enabled = true;`;
    assert.equal((await s.run('leak', code)).outcome, 'passed');
    const pid = Number(await readFile(marker, 'utf8'));
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
    assert.equal((await s.run('disabled')).outcome, 'passed');
  },
);

test(
  'M2 exposes fresh fallback for global setup and evaluates active setup imports',
  { timeout: 60000 },
  async (t) => {
    const s = await session(t);
    await writeFile(
      join(s.root, 'vitest.config.mjs'),
      "export default { test: { include: ['sample.test.ts'], globalSetup: ['./setup.ts'] } };",
    );
    await writeFile(
      join(s.root, 'setup.ts'),
      `import { enabled } from './source';
import { appendFileSync } from 'node:fs'; export default () => {
appendFileSync(${JSON.stringify(join(s.root, 'session/setup-observations'))}, JSON.stringify({enabled, active: process.env.OXIMUT_ACTIVE_MUTANT})+'\\n'); };`,
    );
    assert.equal((await s.run('kill')).outcome, 'failed');
    assert.equal((await s.run('disabled')).outcome, 'passed');
    const observations = (await readFile(join(s.root, 'session/setup-observations'), 'utf8'))
      .trim()
      .split('\n')
      .map(JSON.parse);
    assert.deepEqual(observations, [
      { enabled: false, active: 'kill' },
      { enabled: true, active: 'disabled' },
    ]);
    const events = (await readFile(join(s.root, 'session/events.jsonl'), 'utf8'))
      .trim()
      .split('\n')
      .map(JSON.parse);
    assert.equal(events.length, 2);
    assert.ok(events.every((event) => event.reuse.startsWith('fresh:')));
    assert.notEqual(events[0].controllerPid, events[1].controllerPid);
  },
);

test(
  'M2 invalidates raw imported assets when their contents change',
  { timeout: 60000 },
  async (t) => {
    const s = await session(t);
    await writeFile(join(s.root, 'flag.txt'), 'enabled');
    const code = "import flag from './flag.txt?raw'; export const enabled = flag === 'enabled';";
    assert.equal((await s.run('disabled', code)).outcome, 'passed');
    await writeFile(join(s.root, 'flag.txt'), 'disabled');
    assert.equal((await s.run('disabled', code)).outcome, 'failed');
    const events = (await readFile(join(s.root, 'session/events.jsonl'), 'utf8'))
      .trim()
      .split('\n')
      .map(JSON.parse);
    assert.equal(events.length, 2);
    assert.notEqual(events[0].controllerPid, events[1].controllerPid);
  },
);

test(
  'M2 uses fresh controllers for custom plugins and symlinked project inputs',
  { timeout: 90000 },
  async (t) => {
    for (const feature of ['plugin', 'symlink']) {
      await t.test(feature, async (t) => {
        const s = await session(t);
        if (feature === 'plugin') {
          await writeFile(
            join(s.root, 'vitest.config.mjs'),
            "export default { plugins: [{ name: 'custom-contract-plugin' }], test: { include: ['sample.test.ts'] } };",
          );
        } else {
          await symlink('source.ts', join(s.root, 'linked-source.ts'));
        }
        assert.equal((await s.run('kill')).outcome, 'failed');
        assert.equal((await s.run('disabled')).outcome, 'passed');
        const events = (await readFile(join(s.root, 'session/events.jsonl'), 'utf8'))
          .trim()
          .split('\n')
          .map(JSON.parse);
        assert.equal(events.length, 2);
        assert.ok(events.every((event) => event.reuse.startsWith('fresh:')));
        assert.match(events[0].reuse, feature === 'plugin' ? /custom plugins/ : /symlink/);
        assert.notEqual(events[0].controllerPid, events[1].controllerPid);
      });
    }
  },
);

test(
  'M2 restores alternating source transforms through re-exports and dynamic query imports',
  { timeout: 120000 },
  async (t) => {
    const s = await session(t);
    await writeFile(join(s.root, 'other.ts'), 'export const extra = true;');
    await writeFile(join(s.root, 'barrel.ts'), "export { enabled } from './source';");
    await writeFile(
      join(s.root, 'consumer.ts'),
      "import { enabled } from './barrel'; export function observe() { return enabled; }",
    );
    await writeFile(
      join(s.root, 'sample.test.ts'),
      `
import { test, expect } from 'vitest';
import { observe } from './consumer';
let state = 0;
test('all dependencies', async () => {
  expect(state++).toBe(0);
  expect(observe()).toBe(true);
  expect((await import('./source.ts?variant')).enabled).toBe(true);
  expect((await import('./other.ts?variant')).extra).toBe(true);
});`,
    );
    const other =
      "export const extra = process.env.OXIMUT_ACTIVE_MUTANT === 'kill' ? false : true;";
    // Long → short → long requests catch stale suffixes in the shared buffer.
    const padded = `${switched}\n/*${'x'.repeat(65536)}*/`;
    for (const [id, code, file, outcome] of [
      ['original', null, 'source.ts', 'passed'],
      ['kill', padded, 'source.ts', 'failed'],
      ['kill', other, 'other.ts', 'failed'],
      ['pass', switched, 'source.ts', 'passed'],
      ['kill', padded, 'source.ts', 'failed'],
      ['original', null, 'source.ts', 'passed'],
      ['kill', other, 'other.ts', 'failed'],
      ['original', null, 'source.ts', 'passed'],
    ]) {
      const result = await s.run(id, code, 30000, file);
      assert.equal(result.outcome, outcome, JSON.stringify(result));
      assert.equal(result.tests.passed + result.tests.failed, 1);
    }
  },
);

test('M2 fresh-thread switching, coverage and worker isolation', { timeout: 120000 }, async (t) => {
  const s = await session(t);
  await writeFile(
    join(s.root, 'sample.test.ts'),
    `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { threadId } from 'node:worker_threads';
import * as subject from './source';
let state = 0;
appendFileSync(${JSON.stringify(join(s.root, 'session/threads'))}, JSON.stringify({pid: process.pid, threadId, state: state++})+'\\n');
test('enabled', () => { subject.hit?.(); expect(state).toBe(1); expect(subject.enabled).toBe(true); });`,
  );
  const run = (id, code = switched, extra = {}) =>
    s.run(id, code, 30000, 'source.ts', undefined, { pool: 'threads', ...extra });
  assert.equal((await run('', null)).outcome, 'passed');
  const probe = {
    id: 'probe',
    file: join(s.root, 'source.ts'),
    code: `export const enabled = true; export function hit() { globalThis.__oximut_m3_hit__?.("${'a'.repeat(64)}"); }`,
    map: { version: 3, sources: ['source.ts'], mappings: 'AAAA', names: [] },
  };
  const coverage = await run('', null, { collectCoverage: [probe] });
  assert.equal(coverage.outcome, 'passed');
  assert.equal(coverage.coverage.complete, true);
  assert.equal(coverage.coverage.hits[0].mutantId, 'a'.repeat(64));
  assert.equal(coverage.coverage.hits[0].tests.length, 1);
  assert.equal(coverage.coverage.hits[0].outsideTest, false);
  const selection = {
    mode: 'files',
    files: [join(s.root, 'sample.test.ts')],
    selectedTests: 1,
    totalTests: 1,
    reasons: [],
  };
  assert.equal((await run('disabled', switched, { selection })).outcome, 'passed');
  assert.equal((await run('kill', switched, { selection })).outcome, 'failed');
  assert.equal((await run('disabled')).outcome, 'passed');
  const observations = (await readFile(join(s.root, 'session/threads'), 'utf8'))
    .trim()
    .split('\n')
    .map(JSON.parse);
  const events = (await readFile(join(s.root, 'session/events.jsonl'), 'utf8'))
    .trim()
    .split('\n')
    .map(JSON.parse);
  assert.equal(observations.length, 5);
  assert.equal(events.length, 1);
  assert.equal(new Set(events.map((e) => e.controllerPid)).size, 1);
  assert.ok(
    observations.every((o) => o.pid === events[0].controllerPid && o.threadId > 0 && o.state === 0),
  );
  assert.equal(new Set(observations.map((o) => o.threadId)).size, observations.length);
  const changed = await run('disabled', switched, { pool: undefined });
  assert.equal(changed.outcome, 'infrastructure-error');
  assert.match(changed.errors.join(' '), /Pool cannot change/);
});

// Observe graceful worker shutdown, including a deliberately slow stop listener.
// Each job gets its own file so a later worker cannot hide an earlier leak.
test('M2 fresh-thread reply waits for test-thread termination', { timeout: 120000 }, async (t) => {
  const s = await session(t);
  for (let job = 0; job < 3; job++) {
    const marker = join(s.root, `session/thread-${job}`);
    await writeFile(
      join(s.root, 'sample.test.ts'),
      `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { parentPort, threadId } from 'node:worker_threads';
const tick = () => appendFileSync(${JSON.stringify(marker)}, threadId + '\\n');
tick();
setInterval(tick, 5).unref();
// Vitest 4.1.12 sends this message before terminating the thread. Delay its
// handling so the run/termination gap is observable even on a slow host.
parentPort.on('message', (message) => {
  if (!message?.__vitest_worker_request__ || message.type !== 'stop') return;
  const until = Date.now() + 500;
  while (Date.now() < until) {
    tick();
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
  }
});
test('alive', async () => { await new Promise(r => setTimeout(r, 30)); expect(threadId).toBeGreaterThan(0); });`,
    );
    const response = await s.run('', null, 30000, 'source.ts', undefined, { pool: 'threads' });
    assert.equal(response.outcome, 'passed', JSON.stringify(response));
    const atReply = await readFile(marker, 'utf8');
    assert.ok(atReply.trim().split('\n').length > 1, 'timer ran in the test thread');
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(await readFile(marker, 'utf8'), atReply, 'thread wrote after its job reply');
  }
});

// Each case first proves bail is active, so safety checks also regress on the
// pre-bail adapter rather than merely repeating its existing classifications.
async function bailFixture(t, failingBaseline = false) {
  const s = await session(t);
  const observations = join(s.root, 'session/executed');
  await writeFile(
    join(s.root, 'sample.test.ts'),
    `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { enabled } from './source';
for (let i = 0; i < 4; i++) test('enabled ' + i, () => {
  appendFileSync(${JSON.stringify(observations)}, String(i));
  expect(enabled).toBe(${failingBaseline ? 'Boolean(process.env.OXIMUT_ACTIVE_MUTANT)' : 'true'});
});`,
  );
  const killed = await s.run('kill');
  assert.equal(killed.outcome, 'failed', JSON.stringify(killed));
  assert.equal(killed.tests.failed, 1, 'bail stops after one actual test failure');
  assert.equal(await readFile(observations, 'utf8'), '0');
  await writeFile(observations, '');
  return { s, observations };
}

test('M2 bail stops a killed mutant at its first failure', { timeout: 60000 }, async (t) => {
  await bailFixture(t);
});

test('M2 bail never leaks into a subsequent failing baseline', { timeout: 90000 }, async (t) => {
  const { s, observations } = await bailFixture(t, true);
  // A failing baseline proves bail=0 without changing any project inputs.
  // Input changes would retire the controller and hide configuration leaks.
  const baseline = await s.run('', null);
  assert.equal(baseline.outcome, 'failed', JSON.stringify(baseline));
  assert.equal(baseline.tests.failed, 4);
  assert.equal(await readFile(observations, 'utf8'), '0123');
  const events = (await readFile(join(s.root, 'session/events.jsonl'), 'utf8'))
    .trim()
    .split('\n')
    .map(JSON.parse);
  assert.equal(events.length, 1, 'baseline reuses the controller that bailed');
  assert.equal(events[0].reuse, 'controller');
});

test('M2 bail lets surviving mutants run every test', { timeout: 90000 }, async (t) => {
  const { s, observations } = await bailFixture(t);
  const survivor = await s.run('disabled');
  assert.equal(survivor.outcome, 'passed', JSON.stringify(survivor));
  assert.equal(survivor.tests.passed, 4);
  assert.equal(await readFile(observations, 'utf8'), '0123');
});

test('M2 bail preserves unhandled, teardown and timeout errors', { timeout: 120000 }, async (t) => {
  const { s } = await bailFixture(t);
  for (const [body, expected] of [
    [
      "setTimeout(() => { throw new Error('unhandled sentinel'); }, 0); await new Promise(r => setTimeout(r, 30)); expect(enabled).toBe(true);",
      'infrastructure-error',
    ],
    ['await new Promise(() => {});', 'timeout'],
  ]) {
    await writeFile(
      join(s.root, 'sample.test.ts'),
      `import { test, expect } from 'vitest'; import { enabled } from './source';
test('error', async () => { ${body} }, 100);
test('must be cancelled', () => { expect(enabled).toBe(true); });`,
    );
    const result = await s.run('kill');
    assert.equal(result.outcome, expected, JSON.stringify(result));
    assert.equal(result.tests.failed, 1);
  }
  await writeFile(
    join(s.root, 'sample.test.ts'),
    `import { test, expect, afterAll } from 'vitest'; import { enabled } from './source';
afterAll(() => { throw new Error('teardown sentinel'); });
test('fails', () => { expect(enabled).toBe(true); });
test('cancelled', () => { expect(enabled).toBe(true); });`,
  );
  const teardown = await s.run('kill');
  assert.equal(teardown.outcome, 'infrastructure-error', JSON.stringify(teardown));
});

async function parallelFixture(t) {
  const s = await session(t);
  const observations = join(s.root, 'session/parallel');
  const release = join(s.root, 'session/release');
  const mode = join(s.root, 'session/mode');
  await writeFile(mode, 'pass');
  await writeFile(
    join(s.root, 'vitest.config.mjs'),
    "export default { test: { include: ['file-*.test.ts'] } };",
  );
  for (let i = 0; i < 4; i++)
    await writeFile(
      join(s.root, `file-${i}.test.ts`),
      `
import { test, expect } from 'vitest';
import { appendFileSync, existsSync, readFileSync } from 'node:fs';
import * as subject from './source';
const log = event => appendFileSync(${JSON.stringify(observations)}, JSON.stringify({event, pid:process.pid, file:${i}, parent:process.ppid})+'\\n');
test('file ${i}', async () => {
  log('start');
  const until = Date.now() + 15000;
  while (!existsSync(${JSON.stringify(release)})) {
    if (Date.now() > until) throw new Error('parallel barrier never released');
    await new Promise(r => setTimeout(r, 10));
  }
  const mode = readFileSync(${JSON.stringify(mode)}, 'utf8');
  subject.hit?.();
  if (${i} === 0) {
    if (mode === 'hang') while (true) {}
    if (mode === 'timeout') await new Promise(() => {});
    if (mode === 'unhandled') {
      setTimeout(() => { throw new Error('parallel unhandled sentinel'); }, 0);
      await new Promise(r => setTimeout(r, 50));
    }
    expect(mode).not.toBe('fail');
  }
  expect(subject.enabled).toBe(true);
  log('end');
}, readFileSync(${JSON.stringify(mode)}, 'utf8') === 'timeout' ? 100 : 20000);`,
    );
  const read = async () => {
    try {
      return (await readFile(observations, 'utf8'))
        .trim()
        .split('\n')
        .filter(Boolean)
        .map(JSON.parse);
    } catch (e) {
      if (e.code === 'ENOENT') return [];
      throw e;
    }
  };
  // Hold all files at an explicit barrier until two distinct forks are alive.
  const running = s.run('', null, 30000, 'source.ts', undefined, { workers: 2 });
  const until = Date.now() + 20000;
  let records;
  do {
    records = await read();
    if (records.filter((r) => r.event === 'start').length >= 2) break;
    await new Promise((r) => setTimeout(r, 15));
  } while (Date.now() < until);
  await writeFile(release, '');
  const result = await running;
  assert.equal(result.outcome, 'passed', JSON.stringify(result));
  assert.equal(records.filter((r) => r.event === 'start').length, 2);
  assert.equal(records.filter((r) => r.event === 'end').length, 0);
  const all = await read();
  let active = 0;
  for (const record of all) {
    active += record.event === 'start' ? 1 : -1;
    assert.ok(active >= 0 && active <= 2, 'no more than two files execute at once');
  }
  assert.equal(active, 0);
  assert.equal(new Set(all.map((r) => r.pid)).size, 4);
  assert.equal(new Set(all.map((r) => r.parent)).size, 1);
  for (const pid of new Set(all.map((r) => r.pid)))
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
  return { s, observations, mode, read };
}

test(
  'M2 parallel baseline overlaps fresh forks, preserves coverage and restores serial mutants',
  { timeout: 120000 },
  async (t) => {
    const { s, observations, read } = await parallelFixture(t);
    const probe = {
      id: 'probe',
      file: join(s.root, 'source.ts'),
      code: `export const enabled = true; export function hit() { globalThis.__oximut_m3_hit__?.("${'b'.repeat(64)}"); }`,
      map: { version: 3, sources: ['source.ts'], mappings: 'AAAA', names: [] },
    };
    const baseline = (workers) =>
      s.run('', null, 30000, 'source.ts', undefined, { workers, collectCoverage: [probe] });
    const serial = await baseline(1);
    const parallel = await baseline(2);
    assert.equal(parallel.coverage.complete, true);
    assert.deepEqual(parallel.tests, serial.tests);
    assert.equal(parallel.outcome, serial.outcome);
    assert.deepEqual(parallel.coverage, serial.coverage);
    await writeFile(observations, '');
    const selection = {
      mode: 'files',
      files: parallel.coverage.tests.map((t) => t.file),
      selectedTests: 4,
      totalTests: 4,
      reasons: [],
    };
    const mutant = await s.run('disabled', switched, 30000, 'source.ts', undefined, {
      workers: 2,
      selection,
    });
    assert.equal(mutant.outcome, 'passed', JSON.stringify(mutant));
    assert.deepEqual(mutant.tests, parallel.tests);
    const records = await read();
    assert.deepEqual(
      records.map((r) => r.event),
      ['start', 'end', 'start', 'end', 'start', 'end', 'start', 'end'],
    );
    assert.equal(new Set(records.map((r) => r.pid)).size, 4);
    const events = (await readFile(join(s.root, 'session/events.jsonl'), 'utf8'))
      .trim()
      .split('\n')
      .map(JSON.parse);
    assert.equal(events.length, 1, 'same controller handles parallel baseline then serial mutant');
  },
);

for (const [mode, expected] of [
  ['fail', 'failed'],
  ['unhandled', 'infrastructure-error'],
  ['timeout', 'timeout'],
  ['hang', 'timeout'],
]) {
  test(
    `M2 parallel baseline classifies ${mode} and reaps every fork`,
    { timeout: 120000 },
    async (t) => {
      const { s, observations, read, mode: marker } = await parallelFixture(t);
      await writeFile(observations, '');
      await writeFile(marker, mode);
      const result = await s.run('', null, mode === 'hang' ? 5000 : 30000, 'source.ts', undefined, {
        workers: 2,
      });
      assert.equal(result.outcome, expected, JSON.stringify(result));
      const records = await read();
      assert.ok(records.length > 0);
      if (mode !== 'hang') {
        assert.equal(new Set(records.map((r) => r.pid)).size, 4);
        assert.deepEqual(result.tests, {
          passed: mode === 'unhandled' ? 4 : 3,
          failed: mode === 'unhandled' ? 0 : 1,
          skipped: 0,
        });
      }
      for (const pid of new Set(records.map((r) => r.pid)))
        assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
      await writeFile(marker, 'pass');
      assert.equal(
        (await s.run('', null, 30000, 'source.ts', undefined, { workers: 2 })).outcome,
        'passed',
      );
    },
  );
}
