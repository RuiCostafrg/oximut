import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { validateRequest } from '../protocol.ts';

const exec = promisify(execFile);
const repository = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const runner = join(repository, 'packages/vitest-adapter/m1-runner.mjs');
const source = 'export const enabled: boolean = true; export const unused: number = 1;\n';
const standardTest =
  "import { test, expect } from 'vitest'; import { enabled } from './source'; test('enabled', () => expect(enabled).toBe(true));\n";

async function fixture(t, testCode = standardTest) {
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/m1-adapter-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await Promise.all([
    writeFile(
      join(root, 'vitest.config.mjs'),
      "export default { test: { include: ['sample.test.ts'] } };\n",
    ),
    writeFile(join(root, 'sample.test.ts'), testCode),
    writeFile(join(root, 'source.ts'), source),
  ]);
  return root;
}
function request(root, code) {
  return {
    protocol: 1,
    runId: 'adapter-test',
    jobId: 'job-1',
    generation: 3,
    kind: code === undefined ? 'baseline' : 'mutant',
    root,
    config: join(root, 'vitest.config.mjs'),
    deadlineMs: 30000,
    mutation:
      code === undefined
        ? null
        : {
            id: 'mutation-1',
            file: join(root, 'source.ts'),
            code,
            map: {
              version: 3,
              names: [],
              sources: ['source.ts'],
              sourcesContent: [source],
              mappings: 'AAAA',
            },
          },
  };
}
async function run(root, input) {
  const inputPath = join(root, 'request.json');
  const outputPath = join(root, 'response.json');
  await writeFile(inputPath, typeof input === 'string' ? input : JSON.stringify(input));
  await rm(outputPath, { force: true });
  const { stdout } = await exec(
    process.execPath,
    ['--no-experimental-strip-types', runner, inputPath, outputPath],
    {
      timeout: 45000,
      maxBuffer: 131072,
    },
  );
  assert.equal(stdout, '', 'test output must not carry protocol messages');
  const output = JSON.parse(await readFile(outputPath, 'utf8'));
  assert.equal(output.protocol, 1);
  if (typeof input !== 'string') {
    assert.equal(output.runId, input.runId);
    assert.equal(output.jobId, input.jobId);
    assert.equal(output.generation, input.generation);
  }
  return output;
}

test('M1 rejects malformed requests and protocol versions before execution', async (t) => {
  const root = await fixture(t);
  for (const input of [
    '{broken',
    { ...request(root), protocol: 2 },
    { ...request(root), deadlineMs: 0 },
  ]) {
    const output = await run(root, input);
    assert.equal(output.outcome, 'infrastructure-error');
    assert.ok(output.errors.length);
  }
  assert.throws(() => validateRequest({ ...request(root), generation: -1 }), /generation/);
  assert.throws(() => validateRequest({ ...request(root), mutation: {} }), /Baseline/);
  assert.throws(
    () =>
      validateRequest({
        ...request(root, source),
        mutation: { ...request(root, source).mutation, map: {} },
      }),
    /source map/,
  );
});

test(
  'M1 runs baseline, unchanged code, known killed and surviving mutations without changing source',
  { timeout: 120000 },
  async (t) => {
    const root = await fixture(t);
    for (const [code, outcome] of [
      [undefined, 'passed'],
      [source, 'passed'],
      [source.replace('= true', '= false'), 'failed'],
      [source.replace('= 1', '= 2'), 'passed'],
    ]) {
      const output = await run(root, request(root, code));
      assert.equal(output.outcome, outcome, JSON.stringify(output));
      assert.equal(output.tests.passed + output.tests.failed, 1);
      assert.equal(await readFile(join(root, 'source.ts'), 'utf8'), source);
    }
  },
);

test(
  'M1 distinguishes invalid transforms from assertions and worker crashes',
  { timeout: 90000 },
  async (t) => {
    const root = await fixture(t);
    const invalid = await run(root, request(root, 'export const enabled = ;'));
    assert.equal(invalid.outcome, 'invalid', JSON.stringify(invalid));
    await writeFile(
      join(root, 'sample.test.ts'),
      "import { test } from 'vitest'; test('crashes', () => process.kill(process.pid, 'SIGKILL'));\n",
    );
    const crashed = await run(root, request(root));
    assert.equal(crashed.outcome, 'infrastructure-error', JSON.stringify(crashed));
    await writeFile(
      join(root, 'sample.test.ts'),
      "import { test } from 'vitest'; test('exits', () => process.exit(9));\n",
    );
    const exited = await run(root, request(root));
    assert.equal(exited.outcome, 'infrastructure-error', JSON.stringify(exited));
  },
);

test('M1 never passes empty or entirely skipped execution', { timeout: 90000 }, async (t) => {
  const root = await fixture(t, "import { test } from 'vitest'; test.skip('skipped', () => {});\n");
  assert.equal((await run(root, request(root))).outcome, 'skipped');
  await rm(join(root, 'sample.test.ts'));
  assert.notEqual((await run(root, request(root))).outcome, 'passed');
});

test(
  'M1 classifies Vitest async test and hook timeouts as timeout',
  { timeout: 90000 },
  async (t) => {
    const root = await fixture(
      t,
      "import { test } from 'vitest'; test('hangs', () => new Promise(() => {}), 20);\n",
    );
    const timedOut = await run(root, request(root));
    assert.equal(timedOut.outcome, 'timeout', JSON.stringify(timedOut));
    await writeFile(
      join(root, 'sample.test.ts'),
      "import { test, beforeAll } from 'vitest'; beforeAll(() => new Promise(() => {}), 20); test('never runs', () => {});\n",
    );
    const hook = await run(root, request(root));
    assert.equal(hook.outcome, 'timeout', JSON.stringify(hook));
  },
);

test(
  'M1 applies startup deadlines and reaps a CPU-hung test and detached descendant',
  { timeout: 60000 },
  async (t) => {
    const root = await fixture(t);
    assert.equal((await run(root, { ...request(root), deadlineMs: 1 })).outcome, 'timeout');
    const marker = join(root, 'started.json');
    await writeFile(
      join(root, 'sample.test.ts'),
      `
    import { test } from 'vitest';
    import { spawn } from 'node:child_process';
    import { writeFileSync } from 'node:fs';
    test('hang', () => {
      const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { detached: true, stdio: 'ignore' });
      child.unref();
      writeFileSync(${JSON.stringify(marker)}, JSON.stringify([process.pid, child.pid]));
      while (true) {}
    });
  `,
    );
    const output = await run(root, { ...request(root), deadlineMs: 10000 });
    assert.equal(output.outcome, 'timeout', JSON.stringify(output));
    for (const pid of JSON.parse(await readFile(marker, 'utf8'))) {
      assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
    }
  },
);
