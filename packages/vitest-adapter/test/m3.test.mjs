import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { promisify } from 'node:util';
import { validateRequest } from '../protocol.ts';

const exec = promisify(execFile);
const repository = resolve(import.meta.dirname, '../../..');
const covered = 'a'.repeat(64);
const uncalled = 'b'.repeat(64);
const hook = 'c'.repeat(64);
const cleanups = new Map();
const source = 'export const enabled = () => true;';
const probe = `export const enabled = () => (globalThis.__oximut_m3_hit__?.("${covered}"), true);
export const uncalled = () => (globalThis.__oximut_m3_hit__?.("${uncalled}"), false);
export const hook = () => (globalThis.__oximut_m3_hit__?.("${hook}"), true);`;

async function fixture(t) {
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/m3-adapter-'));
  t.after(async () => {
    await cleanups.get(root)?.();
    cleanups.delete(root);
    await rm(root, { recursive: true, force: true });
  });
  await Promise.all([
    writeFile(
      join(root, 'vitest.config.mjs'),
      "export default { test: { include: ['*.test.ts'] } };",
    ),
    writeFile(join(root, 'source.ts'), source),
    writeFile(
      join(root, 'a.test.ts'),
      `import { test, expect } from 'vitest'; import { enabled } from './source';
let remembered; test('duplicate', () => { remembered = enabled(); });
test('duplicate', () => { expect(remembered).toBe(true); });`,
    ),
    writeFile(
      join(root, 'b.test.ts'),
      "import { test, expect } from 'vitest'; test('unrelated', () => expect(1).toBe(1));",
    ),
  ]);
  return root;
}
function request(root, code) {
  return {
    protocol: 1,
    runId: 'm3',
    jobId: 'j',
    generation: 0,
    kind: code === undefined ? 'baseline' : 'mutant',
    root,
    config: join(root, 'vitest.config.mjs'),
    deadlineMs: 30000,
    mutation: code === undefined ? null : mutation(root, code),
  };
}
function mutation(root, code, file = 'source.ts') {
  return {
    id: covered,
    file: join(root, file),
    code,
    map: { version: 3, sources: [file], mappings: 'AAAA', names: [] },
  };
}
function collect(root, code = probe) {
  return { ...request(root), collectCoverage: [mutation(root, code)] };
}
function selection(root, selectedTests = 2) {
  return {
    mode: 'files',
    files: [join(root, 'a.test.ts')],
    selectedTests,
    totalTests: 3,
    reasons: [],
  };
}
async function run(root, input) {
  const inputPath = join(root, 'request.json');
  const outputPath = join(root, 'response.json');
  await writeFile(inputPath, JSON.stringify(input));
  await rm(outputPath, { force: true });
  await exec(
    process.execPath,
    [join(repository, 'packages/vitest-adapter/m1-runner.mjs'), inputPath, outputPath],
    { timeout: 45000 },
  );
  return JSON.parse(await readFile(outputPath, 'utf8'));
}
async function session(root) {
  const directory = join(root, 'session');
  await mkdir(directory);
  const child = spawn(
    process.execPath,
    [join(repository, 'packages/vitest-adapter/m2-runner.mjs'), directory],
    { stdio: ['ignore', 'ignore', 'pipe'] },
  );
  let diagnostics = '';
  child.stderr.on('data', (chunk) => {
    diagnostics += chunk;
  });
  const exited = new Promise((resolve) => child.once('exit', resolve));
  cleanups.set(root, async () => {
    await writeFile(join(directory, 'stop'), '');
    await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 10000).unref())]);
    if (child.exitCode === null) child.kill('SIGKILL');
  });
  let sequence = 0;
  return async (input) => {
    const generation = sequence++;
    await writeFile(
      join(directory, 'request.tmp'),
      JSON.stringify({ ...input, jobId: `${generation}`, generation }),
    );
    await rename(join(directory, 'request.tmp'), join(directory, `request-${generation}.json`));
    const deadline = Date.now() + 40000;
    while (Date.now() < deadline) {
      try {
        return JSON.parse(await readFile(join(directory, `response-${generation}.json`), 'utf8'));
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
      assert.equal(child.exitCode, null, diagnostics);
      await new Promise((resolve) => setTimeout(resolve, 15));
    }
    throw new Error(`Session timeout: ${diagnostics}`);
  };
}

test('M3 validates selection and collection requests before executing', async (t) => {
  const root = await fixture(t);
  const valid = { ...request(root, source), selection: selection(root) };
  validateRequest(valid);
  for (const selected of [
    { ...selection(root), mode: 'none' },
    { ...selection(root), selectedTests: 0 },
    { ...selection(root), selectedTests: 4 },
    { ...selection(root), expectedSkipped: 3 },
    { ...selection(root), files: [] },
    { ...selection(root), files: [root] },
    { ...selection(root), files: [join(root, '../escaped.test.ts')] },
    { ...selection(root), files: [join(root, 'a.test.ts'), join(root, 'a.test.ts')] },
    { ...selection(root), mode: 'full' },
    { ...selection(root), reasons: [null] },
  ])
    assert.throws(() => validateRequest({ ...valid, selection: selected }), /selection/);
  assert.throws(
    () => validateRequest({ ...request(root), selection: selection(root) }),
    /selection/,
  );
  assert.throws(() => validateRequest({ ...valid, collectCoverage: [] }), /baseline/);
  assert.throws(
    () =>
      validateRequest({
        ...collect(root),
        collectCoverage: [{ ...mutation(root, probe), map: {} }],
      }),
    /source map/,
  );
});

test(
  'M3 collects stable duplicate identities, explicit empty hits and preserves whole-file observers',
  { timeout: 120000 },
  async (t) => {
    const root = await fixture(t);
    const first = await run(root, collect(root));
    assert.equal(first.outcome, 'passed', JSON.stringify(first));
    assert.equal(first.coverage.complete, true, JSON.stringify(first.coverage));
    assert.equal(first.coverage.tests.length, 3);
    assert.equal(new Set(first.coverage.tests.map((test) => test.id)).size, 3);
    const duplicate = first.coverage.tests.filter((test) => test.name === 'duplicate');
    assert.equal(duplicate.length, 2);
    assert.deepEqual(first.coverage.hits.find((hit) => hit.mutantId === covered).tests, [
      duplicate[0].id,
    ]);
    assert.deepEqual(first.coverage.hits.find((hit) => hit.mutantId === uncalled).tests, []);
    const second = await run(root, collect(root));
    assert.deepEqual(second.coverage, first.coverage);
    const selected = await run(root, {
      ...request(root, 'export const enabled = () => false;'),
      selection: selection(root),
    });
    assert.equal(selected.outcome, 'failed', JSON.stringify(selected));
    assert.deepEqual(selected.tests, { passed: 1, failed: 1, skipped: 0 });
    const mismatch = await run(root, { ...request(root, source), selection: selection(root, 1) });
    assert.equal(mismatch.outcome, 'infrastructure-error');
    assert.match(mismatch.errors.join(), /inventory changed/);
  },
);

test('M3 treats native require bypass as incomplete collection', { timeout: 60000 }, async (t) => {
  const root = await fixture(t);
  await writeFile(join(root, 'native.cjs'), 'module.exports = { enabled: true };');
  await writeFile(
    join(root, 'a.test.ts'),
    "import { test, expect } from 'vitest'; import { createRequire } from 'node:module'; const require = createRequire(import.meta.url); const { enabled } = require('./native.cjs'); test('native', () => expect(enabled).toBe(true));",
  );
  const input = {
    ...request(root),
    collectCoverage: [
      mutation(
        root,
        `module.exports = { enabled: (globalThis.__oximut_m3_hit__?.("${covered}"), true) };`,
        'native.cjs',
      ),
    ],
  };
  const result = await run(root, input);
  assert.equal(result.outcome, 'passed', JSON.stringify(result));
  assert.equal(result.coverage.complete, false);
  assert.ok(result.coverage.reasons.includes('source-not-transformed'));
});

test(
  'M3 captures hooks and import execution and falls back for concurrent tests',
  { timeout: 90000 },
  async (t) => {
    const root = await fixture(t);
    await writeFile(
      join(root, 'a.test.ts'),
      `import { beforeAll, beforeEach, afterEach, test, expect } from 'vitest'; import { enabled, hook } from './source';
const initialized = hook(); beforeAll(() => { hook(); }); beforeEach(() => { hook(); }); afterEach(() => { hook(); });
test('hook', () => expect(enabled() && initialized).toBe(true));`,
    );
    const hooks = await run(root, collect(root));
    assert.equal(hooks.coverage.complete, true, JSON.stringify(hooks));
    assert.equal(hooks.coverage.hits.find((hit) => hit.mutantId === hook).outsideTest, true);
    await writeFile(
      join(root, 'a.test.ts'),
      "import { test, expect } from 'vitest'; import { enabled } from './source'; test.concurrent('concurrent', async () => expect(enabled()).toBe(true));",
    );
    const concurrent = await run(root, collect(root));
    assert.equal(concurrent.coverage.complete, false);
    assert.ok(concurrent.coverage.reasons.includes('concurrent-tests'));
  },
);

test(
  'M3 persistent collection does not retain probes or test filters across jobs',
  { timeout: 120000 },
  async (t) => {
    const root = await fixture(t);
    const send = await session(root);
    const first = await send(collect(root));
    assert.equal(first.coverage.complete, true, JSON.stringify(first));
    const selected = await send({ ...request(root, source), selection: selection(root) });
    assert.equal(selected.outcome, 'passed', JSON.stringify(selected));
    assert.equal(selected.tests.passed, 2);
    assert.equal(selected.coverage, undefined);
    const baseline = await send(request(root));
    assert.equal(baseline.outcome, 'passed', JSON.stringify(baseline));
    assert.equal(baseline.tests.passed, 3);
    assert.equal(baseline.coverage, undefined);
    const again = await send(collect(root));
    assert.deepEqual(again.coverage, first.coverage);
  },
);

test(
  'M3 retains kills when a mutation dynamically skips another test',
  { timeout: 120000 },
  async (t) => {
    const root = await fixture(t);
    await writeFile(
      join(root, 'a.test.ts'),
      `import { test, expect } from 'vitest'; import { enabled } from './source';
test('conditionally skipped', (context) => { if (!enabled()) context.skip(); expect(1).toBe(1); });
test('observer', () => expect(enabled()).toBe(true));`,
    );
    const input = {
      ...request(root, 'export const enabled = () => false;'),
      selection: selection(root),
    };
    const fresh = await run(root, input);
    assert.equal(fresh.outcome, 'failed', JSON.stringify(fresh));
    assert.deepEqual(fresh.tests, { passed: 0, failed: 1, skipped: 1 });
    const send = await session(root);
    const persistent = await send(input);
    assert.equal(persistent.outcome, 'failed', JSON.stringify(persistent));
    assert.deepEqual(persistent.tests, fresh.tests);
  },
);

test(
  'M3 function source reflection cannot establish no-coverage',
  { timeout: 60000 },
  async (t) => {
    const root = await fixture(t);
    await writeFile(
      join(root, 'a.test.ts'),
      "import { test, expect } from 'vitest'; import { enabled } from './source'; test('source reflection', () => expect(enabled.toString().includes('true')).toBe(true));",
    );
    const result = await run(root, collect(root));
    assert.equal(result.outcome, 'passed', JSON.stringify(result));
    assert.equal(result.coverage.complete, false);
    assert.ok(result.coverage.reasons.includes('source-reflection'));
    const killed = await run(root, request(root, 'export const enabled = () => false;'));
    assert.equal(killed.outcome, 'failed', JSON.stringify(killed));
    const fallback = await run(root, {
      ...request(root, 'export const enabled = () => false;'),
      selection: {
        mode: 'full',
        files: [],
        selectedTests: 2,
        totalTests: 2,
        reasons: ['source-reflection'],
      },
    });
    assert.equal(fallback.outcome, killed.outcome, JSON.stringify(fallback));
  },
);
