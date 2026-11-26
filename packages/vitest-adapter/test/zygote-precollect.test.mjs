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

// `observe`: the test file logs one line per evaluation of its top level (an append,
// which is exactly what the template guard rejects, so those runs turn it off).
async function fixture(t, testSuffix = '', observe = true) {
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
  const suffix = typeof testSuffix === 'function' ? testSuffix(root) : testSuffix;
  await writeFile(
    testFile,
    `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { top, fn } from './subject';
${observe ? `appendFileSync(${JSON.stringify(join(root, 'events'))}, JSON.stringify({ pid: process.pid, ppid: process.ppid, active: process.env.OXIMUT_ACTIVE_MUTANT ?? null }) + '\\n');` : ''}
test('top', () => expect(top).toBe(1));
test('fn', () => expect(fn()).toBe(1));${suffix}`,
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
  width = undefined,
  observe = true,
) {
  const { root, job, testFile, otherFile } = await fixture(t, testSuffix, observe);
  const both = {
    mode: 'files',
    files: [otherFile, testFile],
    selectedTests: 3,
    totalTests: 3,
    expectedSkipped: 0,
    reasons: [],
  };
  const only = {
    mode: 'files',
    files: [otherFile],
    selectedTests: 1,
    totalTests: 3,
    expectedSkipped: 0,
    reasons: [],
  };
  const env = { ...process.env };
  for (const name of Object.keys(env)) if (name.startsWith('OXIMUT_ZYGOTE')) delete env[name];
  Object.assign(env, {
    OXIMUT_ZYGOTE_COUNTERS_DIR: join(root, 'counters'),
    ...(observe ? { OXIMUT_ZYGOTE_PRECOLLECT_GUARD: 'off' } : {}),
    ...environment,
  });
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
        ...(selection === 'other' ? { selection: only } : {}),
      }),
    );
    // A width below the lane count makes the batch rolling.
    const reply = await command('run', {
      request: { ...jobs[0], ...(width ? { workers: width } : {}), lanes: jobs },
    });
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
    ineligible: replies.flatMap((reply) => reply.precollectIneligible ?? []),
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

zygoteTest(
  'rolling lanes keep the most reused template and run other selections in fresh workers',
  { timeout: 240000 },
  async (t) => {
    // The most frequent file set appears after the first lane. Other file sets
    // and the load-time mutant run with the ordinary zygote.
    const result = await scenario(
      t,
      [
        [
          ['fn', true],
          ['top', false],
          ['none', true],
          ['n1', true, 'other'],
          ['n2', true, 'other'],
          ['n3', true, 'both'],
          ['n4', true, 'both'],
          ['n5', true, 'other'],
        ],
      ],
      { OXIMUT_ZYGOTE: '1' },
      '',
      'baseline',
      2,
    );
    assert.deepEqual(result.outcomes, [
      ['failed', 'failed', 'passed', 'passed', 'passed', 'passed', 'passed', 'passed'],
    ]);
    assert.equal(result.counters.templates, 1);
    assert.equal(result.counters.templated, 3);
    assert.equal(result.counters.zygoteFailures, 0);
    assert.equal(result.counters.fallbacks, 0);
    assert.deepEqual(result.alive, [[]]);
    // Every mutant worker has its own PID. Only sample.test.ts logs, so the
    // mutants selecting the other file do not leave an event here.
    assert.equal(new Set(result.events.map((event) => event.pid)).size, result.events.length);
    assert.deepEqual(
      result.events
        .map((event) => event.active)
        .filter(Boolean)
        .sort(),
      ['fn', 'n3', 'n4', 'none', 'top'],
    );
  },
);

// What a test file's top level leaves behind is shared by every fork of its template.
// Each of these must make the template ineligible: its mutants take the ordinary
// zygote path and still get the outcomes of an ordinary run.
const quiescence = [
  {
    name: 'a temporary directory made at load time',
    suffix: (root) =>
      `\nimport { mkdtempSync } from 'node:fs';\nmkdtempSync(${JSON.stringify(join(root, 'scratch-'))});`,
    category: 'fsWrites',
    reason: /^mkdtempSync /,
  },
  {
    name: 'a file written at load time',
    suffix: (root) =>
      `\nimport { writeFileSync } from 'node:fs';\nwriteFileSync(${JSON.stringify(join(root, 'written'))}, 'x');`,
    category: 'fsWrites',
    reason: /^writeFileSync /,
  },
  {
    name: 'a file opened for appending at load time',
    suffix: (root) =>
      `\nimport { openSync } from 'node:fs';\nopenSync(${JSON.stringify(join(root, 'opened'))}, 'a');`,
    category: 'fsWrites',
    reason: /^openSync /,
  },
  {
    name: 'a listening server',
    suffix: "\nimport { createServer } from 'node:net';\ncreateServer().listen(0);",
    category: 'handles',
    reason: /^TCPServerWrap/,
  },
  {
    name: 'an interval',
    suffix: '\nsetInterval(() => {}, 100000);',
    category: 'handles',
    reason: /^Timeout/,
  },
  {
    name: 'a timer that is not ref-counted',
    suffix: '\nsetTimeout(() => {}, 100000).unref();',
    category: 'timers',
    reason: /^setTimeout$/,
  },
  {
    name: 'a child process',
    suffix:
      "\nimport { spawnSync } from 'node:child_process';\nspawnSync(process.execPath, ['-e', '0']);",
    category: 'children',
    reason: /^spawnSync /,
  },
];
for (const { name, suffix, category, reason } of quiescence) {
  zygoteTest(
    `precollect does not template a test file that leaves ${name}`,
    { timeout: 240000 },
    async (t) => {
      const result = await scenario(
        t,
        [
          [
            ['top', false],
            ['fn', true],
            ['none', true],
          ],
        ],
        { OXIMUT_ZYGOTE: '1' },
        suffix,
        'baseline',
        undefined,
        false,
      );
      // The ordinary zygote path gives the outcomes of an ordinary run.
      assert.deepEqual(result.outcomes, [['failed', 'failed', 'passed']]);
      assert.equal(result.counters.templates, 1);
      assert.equal(result.counters.templated, 0);
      assert.equal(result.counters.ineligible, 1);
      assert.equal(result.counters.zygoteFailures, 0);
      assert.deepEqual(result.alive, [[]]);
      const [report] = result.ineligible;
      assert.ok(
        report.reasons[category].some((entry) => reason.test(entry)),
        JSON.stringify(report),
      );
    },
  );
}

zygoteTest(
  'precollect keeps templating a test file that only logs, reads and waits for a timer',
  { timeout: 240000 },
  async (t) => {
    const result = await scenario(
      t,
      [
        [
          ['top', false],
          ['fn', true],
          ['none', true],
        ],
      ],
      { OXIMUT_ZYGOTE: '1' },
      `
import { readFileSync } from 'node:fs';
readFileSync(import.meta.filename, 'utf8');
console.log('collected');
await new Promise((resolve) => setTimeout(resolve, 5));
await new Promise((resolve) => setImmediate(resolve));`,
      'baseline',
      undefined,
      false,
    );
    assert.deepEqual(result.outcomes, [['failed', 'failed', 'passed']]);
    assert.equal(result.counters.templates, 1);
    assert.equal(result.counters.templated, 2);
    assert.equal(result.counters.ineligible, 0);
    assert.deepEqual(result.ineligible, []);
    assert.equal(result.counters.zygoteFailures, 0);
    assert.deepEqual(result.alive, [[]]);
  },
);

zygoteTest(
  'tracing the timeline does not make a template ineligible',
  { timeout: 240000 },
  async (t) => {
    const { mkdtemp } = await import('node:fs/promises');
    const { tmpdir } = await import('node:os');
    const trace = await mkdtemp(join(tmpdir(), 'oximut-trace-'));
    t.after(() => rm(trace, { recursive: true, force: true }));
    const result = await scenario(
      t,
      [
        [
          ['fn', true],
          ['none', true],
        ],
      ],
      { OXIMUT_ZYGOTE: '1', OXIMUT_TRACE_TIMELINE: trace },
      '',
      'baseline',
      undefined,
      false,
    );
    assert.deepEqual(result.outcomes, [['failed', 'passed']]);
    assert.equal(result.counters.templated, 2);
    assert.equal(result.counters.ineligible, 0);
    // The trace itself was written, by the template as well.
    const written = (await readdir(trace)).filter((name) => name.startsWith('worker-'));
    assert.ok(written.length > 0, 'no worker trace files');
  },
);

zygoteTest(
  'a rolling zygote lane frees its slot at the result and the batch still leaves no worker behind',
  { timeout: 240000 },
  async (t) => {
    const { mkdtemp } = await import('node:fs/promises');
    const { tmpdir } = await import('node:os');
    const trace = await mkdtemp(join(tmpdir(), 'oximut-trace-'));
    t.after(() => rm(trace, { recursive: true, force: true }));
    const result = await scenario(
      t,
      [
        [
          ['fn', true],
          ['n1', true],
          ['n2', true],
          ['n3', true],
          ['n4', true],
        ],
      ],
      { OXIMUT_ZYGOTE: '1', OXIMUT_TRACE_TIMELINE: trace },
      '',
      'baseline',
      2,
      false,
    );
    // Outcomes are those of a worker that is stopped the ordinary way.
    assert.deepEqual(result.outcomes, [['failed', 'passed', 'passed', 'passed', 'passed']]);
    assert.equal(result.counters.zygoteFailures, 0);
    assert.equal(result.counters.fallbacks, 0);
    assert.deepEqual(result.alive, [[]]);
    const controller = (await readdir(trace)).filter((name) => name.startsWith('controller-'));
    const events = (
      await Promise.all(controller.map((name) => readFile(join(trace, name), 'utf8')))
    )
      .flatMap((text) => text.split('\n').filter(Boolean))
      .map((line) => JSON.parse(line));
    const count = (name) => events.filter((event) => event.event === name).length;
    // Every lane worker was killed at its result and every exit was still observed
    // (and so reaped) before the batch was answered.
    assert.equal(count('lane_killed'), 5);
    assert.equal(count('lane_exit'), 5);
    // Each exit is attributed to the lane's own mutant, not to its slot's next one.
    for (const id of ['fn', 'n1', 'n2', 'n3', 'n4']) {
      const mine = events.filter((event) => event.mutant === id);
      const killed = mine.find((event) => event.event === 'lane_killed');
      const exited = mine.find((event) => event.event === 'lane_exit');
      assert.ok(killed && exited && exited.t >= killed.t, `${id}: killed then exited`);
    }
  },
);
