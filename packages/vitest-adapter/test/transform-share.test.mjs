import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { fingerprint, TransformShare } from '../transform-share.ts';

const environment = { name: 'node' };
const root = '/repo';
const subject = '/repo/src/a.ts';

function lane(share, options = {}) {
  const { ns = 'lane', prefixes = [root], produce } = options;
  const calls = [];
  const service = {
    async fetch(url, importer, env, makeTmpCopies, opts) {
      calls.push({ url, importer, env, makeTmpCopies, options: opts });
      if (produce) return produce(calls.length);
      return {
        id: subject,
        file: subject,
        url,
        invalidate: false,
        code: 'shared',
        ...options.result,
      };
    },
  };
  share.wrap(service, ns, prefixes);
  return { service, calls };
}

test('one transform serves three concurrent lanes', async () => {
  const share = new TransformShare();
  const lanes = [lane(share), lane(share), lane(share)];
  const results = await Promise.all(
    lanes.map((candidate) => candidate.service.fetch('/src/a.ts', undefined, environment, true)),
  );
  assert.equal(
    lanes.reduce((total, candidate) => total + candidate.calls.length, 0),
    1,
  );
  assert.deepEqual(share.stats, { hits: 2, misses: 1, bypasses: 0 });
  assert.equal(new Set(results).size, 3);
  for (const result of results) assert.equal(result.file, subject);
});

test('only plain requests are shared', async () => {
  const share = new TransformShare();
  const first = lane(share);
  await first.service.fetch('/src/a.ts', undefined, environment, true);
  await first.service.fetch('/src/a.ts', undefined, environment, true, { cached: true });
  await first.service.fetch('/src/a.ts', undefined, environment, true, {
    cached: false,
    other: true,
  });
  assert.equal(first.calls.length, 3);
  assert.deepEqual(share.stats, { hits: 0, misses: 1, bypasses: 2 });
  // A different offset is a different transform request, not a bypass.
  const second = lane(share);
  await second.service.fetch('/src/a.ts', undefined, environment, true, { startOffset: 5 });
  assert.equal(second.calls.length, 1);
  assert.equal(share.stats.misses, 2);
});

test('a failed computation is not shared and waiters fall back', async () => {
  const share = new TransformShare();
  const failing = lane(share, { produce: () => Promise.reject(new Error('boom')) });
  const healthy = lane(share);
  const [failed, ok] = await Promise.allSettled([
    failing.service.fetch('/src/a.ts', undefined, environment, true),
    healthy.service.fetch('/src/a.ts', undefined, environment, true),
  ]);
  assert.equal(failed.status, 'rejected');
  assert.match(String(failed.reason.message), /boom/);
  assert.equal(ok.status, 'fulfilled');
  assert.equal(ok.value.file, subject);
  assert.equal(healthy.calls.length, 1);
  assert.deepEqual(share.stats, { hits: 0, misses: 1, bypasses: 1 });
  // The rejected entry is gone: the next lane transforms for itself.
  const later = lane(share);
  await later.service.fetch('/src/a.ts', undefined, environment, true);
  assert.equal(later.calls.length, 1);
});

test('a hit whose tmp copy disappeared is refetched', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'transform-share-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const tmp = join(directory, 'a.js');
  await writeFile(tmp, 'export {}');
  const produce = () => ({ id: tmp, file: tmp, tmp, invalidate: false });
  const share = new TransformShare();
  const first = lane(share, { prefixes: [directory], produce });
  const second = lane(share, { prefixes: [directory], produce });
  await first.service.fetch(tmp, undefined, environment, true);
  await rm(tmp);
  const result = await second.service.fetch(tmp, undefined, environment, true);
  assert.equal(second.calls.length, 1);
  assert.equal(result.tmp, tmp);
  assert.equal(share.stats.hits, 0);
  assert.equal(share.stats.bypasses, 1);
});

test('clear during an in-flight computation does not repopulate the new map', async () => {
  const share = new TransformShare();
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const first = lane(share, {
    produce: async () => {
      await gate;
      return { id: subject, file: subject, invalidate: false };
    },
  });
  const second = lane(share);
  const pending = first.service.fetch('/src/a.ts', undefined, environment, true);
  assert.equal(share.generation, 0);
  share.clear();
  assert.equal(share.generation, 1);
  release();
  await pending;
  await second.service.fetch('/src/a.ts', undefined, environment, true);
  assert.equal(first.calls.length, 1);
  assert.equal(second.calls.length, 1);
  assert.equal(share.stats.hits, 0);
});

test('results another lane cannot use are not stored', async () => {
  const cases = [
    ['externalized', () => ({ externalize: '/repo/node_modules/x.js', type: 'module' })],
    ['cache sentinel', () => ({ cache: true })],
    ['virtual file', () => ({ id: 'virtual:thing', file: 'virtual:thing' })],
    ['outside the prefixes', () => ({ id: '/elsewhere/a.ts', file: '/elsewhere/a.ts' })],
    [
      'inside node_modules',
      () => ({ id: '/repo/node_modules/x/a.js', file: '/repo/node_modules/x/a.js' }),
    ],
    ['a relative file', () => ({ id: 'a.ts', file: 'a.ts' })],
  ];
  for (const [label, produce] of cases) {
    const share = new TransformShare();
    const first = lane(share, { produce });
    const second = lane(share, { produce });
    await first.service.fetch('/src/a.ts', undefined, environment, true);
    await second.service.fetch('/src/a.ts', undefined, environment, true);
    assert.equal(second.calls.length, 1, label);
    assert.equal(share.stats.hits, 0, label);
    assert.equal(share.stats.misses, 2, label);
  }
});

test('a hit is a shallow copy of the stored result', async () => {
  const share = new TransformShare();
  const first = lane(share);
  const second = lane(share);
  const third = lane(share);
  await first.service.fetch('/src/a.ts', undefined, environment, true);
  const one = await second.service.fetch('/src/a.ts', undefined, environment, true);
  const two = await third.service.fetch('/src/a.ts', undefined, environment, true);
  assert.notEqual(one, two);
  one.code = 'mutated';
  one.file = '/tmp/elsewhere.ts';
  const again = await second.service.fetch('/src/a.ts', undefined, environment, true);
  assert.equal(again.code, 'shared');
  assert.equal(again.file, subject);
});

test('different fingerprints never share', async () => {
  const share = new TransformShare();
  const first = lane(share, { ns: 'one' });
  const second = lane(share, { ns: 'two' });
  await first.service.fetch('/src/a.ts', undefined, environment, true);
  await second.service.fetch('/src/a.ts', undefined, environment, true);
  assert.equal(first.calls.length, 1);
  assert.equal(second.calls.length, 1);
  assert.equal(share.stats.hits, 0);
});

test('wrapping a service twice throws', () => {
  const share = new TransformShare();
  const { service } = lane(share);
  assert.throws(() => share.wrap(service, 'lane', [root]), /already wrapped/);
});

function project() {
  return {
    vite: {
      config: {
        root,
        cacheDir: '/repo/node_modules/.vite/vitest/one',
        webSocketToken: 'token-one',
        plugins: [{ name: 'count' }],
        resolve: { alias: { '@': '/repo/src' } },
        test: {
          name: 'oximut-lane-0',
          env: {},
          pool: { name: 'oximut-lane-forks' },
          runner: '/repo/lane-runner.mjs',
          sequence: { seed: 1, hooks: 'stack' },
          cache: { dir: '/repo/.cache/one' },
        },
        inlineConfig: {
          test: {
            name: 'oximut-lane-0',
            pool: { name: 'oximut-lane-forks' },
            runner: '/repo/lane-runner.mjs',
          },
        },
      },
    },
    config: {
      name: 'oximut-lane-0',
      env: { OXIMUT_ACTIVE_MUTANT: undefined },
      pool: { name: 'oximut-lane-forks' },
      runner: '/repo/lane-runner.mjs',
      execArgv: ['--require', '/repo/compile-cache.cjs'],
      sequence: { seed: 2, hooks: 'stack' },
      cache: { dir: '/repo/.cache/one' },
      testTimeout: 300,
    },
  };
}

test('fingerprints ignore only runtime identity and storage locations', () => {
  const first = project();
  const second = project();
  second.vite.config.test.name = 'oximut-lane-1';
  second.vite.config.test.env = { OXIMUT_ACTIVE_MUTANT: 'm2' };
  second.vite.config.test.pool = {};
  second.vite.config.test.runner = '/repo/other-runner.mjs';
  second.vite.config.inlineConfig.test.name = 'oximut-lane-1';
  second.vite.config.cacheDir = '/repo/node_modules/.vite/vitest/two';
  second.vite.config.webSocketToken = 'token-two';
  second.vite.config.test.sequence.seed = 3;
  second.vite.config.test.cache.dir = '/repo/.cache/two';
  second.config.name = 'oximut-lane-1';
  second.config.env = { OXIMUT_ACTIVE_MUTANT: 'm2' };
  second.config.pool = {};
  second.config.runner = '/repo/other-runner.mjs';
  second.config.execArgv = [];
  second.config.sequence.seed = 4;
  second.config.cache.dir = '/repo/.cache/two';
  assert.equal(fingerprint(first), fingerprint(second));
});

test('fingerprints change with anything that shapes transformed output', () => {
  const base = project();
  const plugin = project();
  plugin.vite.config.plugins = [{ name: 'different' }];
  assert.notEqual(fingerprint(base), fingerprint(plugin));
  const alias = project();
  alias.vite.config.resolve.alias = { '@': '/repo/other' };
  assert.notEqual(fingerprint(base), fingerprint(alias));
  assert.notEqual(
    fingerprint(base),
    fingerprint({ ...base, config: { ...base.config, testTimeout: 301 } }),
  );
});

test('cyclic and unserializable configs cannot crash the fingerprint', () => {
  const cyclic = project();
  cyclic.config.self = cyclic.config;
  cyclic.vite.config.cycle = cyclic.vite.config;
  const digest = fingerprint(cyclic);
  assert.match(digest, /^[a-f0-9]{64}$/);
});
