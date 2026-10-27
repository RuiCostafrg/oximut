import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';

const repository = resolve(import.meta.dirname, '../../..');
const source = 'export const enabled = true;';
const switched =
  "export const enabled = process.env.OXIMUT_ACTIVE_MUTANT === 'kill' ? false : true;";
async function session(t) {
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
    [join(repository, 'packages/vitest-adapter/m2-runner.mjs'), directory],
    { stdio: ['ignore', 'pipe', 'pipe'] },
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
    async run(id, code = switched, deadlineMs = 30000, filename = 'source.ts') {
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
      };
      await writeFile(join(directory, 'request.tmp'), JSON.stringify(input));
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
    // Long → short → long requests also exercise the shared buffer's byte limit.
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
