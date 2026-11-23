import assert from 'node:assert/strict';
import { fork, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';

// Experimental --zygote-precollect: a mutant whose single selected test file has a
// warm template is forked from that template. Needs the real fork addon.
const repository = resolve(import.meta.dirname, '../../..');
const builtAddon = join(repository, 'packages/vitest-adapter/native/build/zygote-fork.node');
const required = process.env.OXIMUT_REQUIRE_ZYGOTE_ADDON === '1';

function addonProblem() {
  if (process.platform !== 'linux') return `zygote workers need Linux, not ${process.platform}`;
  if (!existsSync(builtAddon))
    spawnSync('python3', [join(repository, 'scripts/build-zygote-addon.py')], { stdio: 'ignore' });
  return existsSync(builtAddon) ? undefined : 'the zygote addon could not be built';
}
const problem = addonProblem();
function zygoteTest(name, options, body) {
  const skip = problem && !required ? problem : false;
  test(name, { ...options, skip }, async (t) => {
    if (problem) assert.fail(problem);
    await body(t);
  });
}

// `top` is evaluated while the module loads, `fn` only when a test calls it.
const code = `export const top = process.env.OXIMUT_ACTIVE_MUTANT === 'top' ? 2 : 1;
export function fn() { return process.env.OXIMUT_ACTIVE_MUTANT === 'fn' ? 2 : 1; }`;
const map = { version: 3, sources: ['subject.ts'], names: [], mappings: 'AAAA' };

async function fixture(t, testSuffix = '') {
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/precollect-'));
  await symlink(join(repository, 'node_modules'), join(root, 'node_modules'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'counters'));
  await writeFile(
    join(root, 'subject.ts'),
    'export const top = 1;\nexport function fn() { return 1; }',
  );
  await writeFile(
    join(root, 'vitest.config.mjs'),
    "export default { test: { include: ['*.test.ts'] } };",
  );
  const testFile = join(root, 'sample.test.ts');
  await writeFile(
    testFile,
    `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { top, fn } from './subject';
appendFileSync(${JSON.stringify(join(root, 'events'))}, JSON.stringify({ pid: process.pid, ppid: process.ppid, active: process.env.OXIMUT_ACTIVE_MUTANT ?? null }) + '\\n');
test('top', () => expect(top).toBe(1));
test('fn', () => expect(fn()).toBe(1));${testSuffix}`,
  );
  const otherFile = join(root, 'other.test.ts');
  await writeFile(
    otherFile,
    `import { test, expect } from 'vitest';
import { fn } from './subject';
test('other', () => expect(fn()).toBe(1));`,
  );
  await writeFile(join(root, 'events'), '');
  const job = (id, extra = {}) => ({
    resident: [{ id: '__resident__', file: join(root, 'subject.ts'), code, map }],
    protocol: 1,
    runId: 'precollect',
    jobId: id,
    generation: 1,
    kind: 'mutant',
    root,
    config: join(root, 'vitest.config.mjs'),
    deadlineMs: 60000,
    mutation: { id, file: join(root, 'subject.ts'), code, map },
    selection: {
      mode: 'files',
      files: [testFile],
      selectedTests: 2,
      totalTests: 2,
      expectedSkipped: 0,
      reasons: [],
    },
    ...extra,
  });
  return { root, job, testFile, otherFile };
}

/** One controller, one initialize, then each batch of lane requests in order. */
async function scenario(
  t,
  batches,
  environment = {},
  testSuffix = '',
  initializeWith = 'baseline',
) {
  const { root, job, testFile, otherFile } = await fixture(t, testSuffix);
  const both = {
    mode: 'files',
    files: [otherFile, testFile],
    selectedTests: 3,
    totalTests: 3,
    expectedSkipped: 0,
    reasons: [],
  };
  const env = { ...process.env };
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
  // The CLI initializes a replacement controller with its first job's request, so
  // the controller's own environment may name that job's mutant.
  await command(
    'initialize',
    initializeWith === 'mutant'
      ? job('first')
      : { ...job('init', { selection: undefined }), kind: 'baseline', mutation: null },
  );
  const replies = [];
  const alive = [];
  for (const batch of batches) {
    const jobs = batch.map(([id, precollect, selection]) =>
      job(id, {
        ...(precollect === undefined ? {} : { precollect }),
        ...(selection === 'both' ? { selection: both } : {}),
      }),
    );
    const reply = await command('run', { request: { ...jobs[0], lanes: jobs } });
    replies.push(reply);
    // Every mutant worker is gone once its batch is answered: the CLI verifies it.
    const logged = (await readFile(join(root, 'events'), 'utf8')).split('\n').filter(Boolean);
    alive.push(
      logged
        .map((line) => JSON.parse(line).pid)
        .filter((pid) => !(reply.residentHelpers ?? []).includes(pid))
        .filter((pid) => {
          try {
            process.kill(pid, 0);
            return true;
          } catch {
            return false;
          }
        }),
    );
  }
  await command('close', {});
  child.disconnect();
  await exited;
  const files = await readdir(join(root, 'counters'));
  const counters = files.length
    ? JSON.parse(await readFile(join(root, 'counters', files[0]), 'utf8'))
    : undefined;
  const events = (await readFile(join(root, 'events'), 'utf8'))
    .split('\n')
    .filter(Boolean)
    .map(JSON.parse);
  return {
    outcomes: replies.map((reply) => reply.lanes.map((lane) => lane.outcome)),
    helpers: replies.map((reply) => reply.residentHelpers),
    alive,
    counters,
    events,
    diagnostics,
  };
}

const killedBoth = ['failed', 'failed'];

test(
  'without precollect, a load-time and a call-time mutant are both killed by ordinary workers',
  {
    timeout: 240000,
  },
  async (t) => {
    const result = await scenario(t, [[['top'], ['fn']]], { OXIMUT_ZYGOTE: '1' });
    assert.deepEqual(result.outcomes, [killedBoth]);
    assert.equal(result.counters.templates, undefined);
  },
);

zygoteTest(
  'precollect kills a call-time mutant from a template and a load-time mutant on the ordinary path',
  { timeout: 240000 },
  async (t) => {
    const result = await scenario(
      t,
      [
        [
          ['top', false],
          ['fn', true],
        ],
        [
          ['fn', true],
          ['none', true],
        ],
      ],
      { OXIMUT_ZYGOTE: '1' },
    );
    // Same outcomes as the ordinary run: top and fn are killed, none survives.
    assert.deepEqual(result.outcomes, [killedBoth, ['failed', 'passed']]);
    // One template for the file; the three eligible mutants forked from it.
    assert.equal(result.counters.templates, 1);
    assert.equal(result.counters.templated, 3);
    assert.equal(result.counters.zygoteFailures, 0);
    assert.equal(result.counters.fallbacks, 0);
    assert.deepEqual(result.alive, [[], []]);
    // The template collected the file with no mutant active; every mutant worker
    // is its own process with its own mutant.
    const [template, ...workers] = result.events;
    assert.ok(!template.active);
    assert.equal(new Set(result.events.map((event) => event.pid)).size, result.events.length);
    assert.deepEqual(
      workers.map((event) => event.active).sort(),
      ['fn', 'fn', 'none', 'top'].sort(),
    );
    for (const helpers of result.helpers) assert.ok(helpers.length >= 1);
  },
);

zygoteTest(
  'why load-time mutants must not be precollected: the template evaluated them with no mutant',
  { timeout: 240000 },
  async (t) => {
    const result = await scenario(
      t,
      [
        [
          ['top', true],
          ['fn', true],
        ],
      ],
      { OXIMUT_ZYGOTE: '1' },
    );
    // `top` was evaluated once, unmutated, in the template: the mutation is lost.
    assert.deepEqual(result.outcomes, [['passed', 'failed']]);
  },
);

zygoteTest(
  'a test file using vi.mock keeps the ordinary zygote path even when marked eligible',
  { timeout: 240000 },
  async (t) => {
    const result = await scenario(
      t,
      [
        [
          ['fn', true],
          ['none', true],
        ],
      ],
      { OXIMUT_ZYGOTE: '1' },
      "\nimport { vi } from 'vitest';\nvi.mock('./unused', () => ({}));",
    );
    assert.deepEqual(result.outcomes, [['failed', 'passed']]);
    assert.equal(result.counters.templates, undefined);
  },
);

zygoteTest(
  'a selection of several files forks from a template that only warms fetched code',
  { timeout: 240000 },
  async (t) => {
    const result = await scenario(
      t,
      [
        [
          ['top', false, 'both'],
          ['fn', true, 'both'],
        ],
        [
          ['fn', true, 'both'],
          ['none', true, 'both'],
        ],
      ],
      { OXIMUT_ZYGOTE: '1' },
    );
    assert.deepEqual(result.outcomes, [killedBoth, ['failed', 'passed']]);
    assert.equal(result.counters.templates, 1);
    assert.equal(result.counters.templated, 3);
    assert.equal(result.counters.zygoteFailures, 0);
    // Several files: every file starts from fresh modules, so the template only
    // saved fetching. Each mutant worker is still its own process.
    const [template, ...workers] = result.events;
    assert.ok(!template.active);
    assert.equal(new Set(result.events.map((event) => event.pid)).size, result.events.length);
    assert.deepEqual(workers.map((event) => event.active).sort(), ['fn', 'fn', 'none', 'top']);
  },
);

zygoteTest(
  'a controller initialized with a mutant job still builds templates and leaves no worker behind',
  { timeout: 240000 },
  async (t) => {
    const result = await scenario(
      t,
      [
        [
          ['top', false],
          ['fn', true],
        ],
        [
          ['fn', true],
          ['none', true],
        ],
      ],
      { OXIMUT_ZYGOTE: '1' },
      '',
      'mutant',
    );
    assert.deepEqual(result.outcomes, [killedBoth, ['failed', 'passed']]);
    assert.equal(result.counters.templates, 1);
    assert.equal(result.counters.templated, 3);
    assert.deepEqual(result.alive, [[], []]);
  },
);
