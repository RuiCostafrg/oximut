import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { registerBundleMap } from './map-registration.mjs';
import { hash } from './build.mjs';

async function fixture(t, edit = () => {}) {
  const root = await mkdtemp(join(tmpdir(), 'w13-map-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const bundle = pathToFileURL(join(root, 'private-worker.mjs'));
  const mapURL = new URL('./private-worker.mjs.map', bundle);
  const metadata = new URL('./private-worker.map-registration.json', bundle);
  const original = new URL('./original.js', bundle);
  const code = "export function boom() { throw new Error('mapped lookup'); }\n";
  const map = { version: 3, file: 'private-worker.mjs', sources: ['original.js'], sourcesContent: ['\n\n' + code], names: [], mappings: 'AAEA' };
  const bytes = JSON.stringify(map);
  const spec = { format: 1, bundleURL: bundle.href, mapURL: mapURL.href, viteVersion: '8.3.5', mapSha256: hash(bytes), sources: [{url:original.href,sha256:hash(map.sourcesContent[0])}] };
  edit(spec, map);
  await writeFile(metadata, JSON.stringify(spec));
  await writeFile(mapURL, JSON.stringify(map));
  await writeFile(bundle, code);
  return { bundle, mapURL, metadata, spec, map, bytes, original };
}

test('registration remains lazy, exact URL/path lookup only, then caches one parsed map', async t => {
  const f = await fixture(t);
  const owner = registerBundleMap(f.bundle, f.metadata); t.after(() => owner.close());
  assert.equal(owner.loaded, false);
  await rm(f.mapURL);
  assert.equal(owner.retrieveSourceMap(f.original.href), null);
  assert.equal(owner.retrieveSourceMap(f.bundle.href + '?unknown'), null);
  assert.equal(owner.loaded, false);
  await writeFile(f.mapURL, f.bytes);
  const result = owner.retrieveSourceMap(f.bundle.href);
  assert.equal(owner.loaded, true);
  assert.equal(result.url, f.mapURL.href);
  assert.deepEqual(result.map, { ...f.map, sources: [f.original.pathname] });
  await rm(f.mapURL);
  assert.equal(owner.retrieveSourceMap(f.bundle.pathname).map, result.map, 'Parsed map must not be read again');
});

test('native Vite hook maps a real thrown frame and unregisters without removing another owner', async t => {
  const originalPrepare = Error.prepareStackTrace;
  const f = await fixture(t), other = await fixture(t);
  const owner = registerBundleMap(f.bundle, f.metadata), second = registerBundleMap(other.bundle, other.metadata);
  t.after(async () => { await owner.close(); await second.close(); });
  const installed = Error.prepareStackTrace;
  assert.notEqual(installed, originalPrepare);
  const module = await import(f.bundle.href);
  let stack;
  try { module.boom(); } catch (error) { stack = error.stack; }
  assert.match(stack, /original.js:3:/);
  assert.ok(stack.includes(`(${f.original.href}:3:`), `Original source URI diverged: ${stack}`);
  await owner.close();
  assert.equal(owner.retrieveSourceMap(f.bundle.href), null);
  assert.equal(Error.prepareStackTrace, installed);
  await second.close();
  assert.equal(Error.prepareStackTrace, originalPrepare);
  assert.throws(() => registerBundleMap(f.bundle, f.metadata), /already registered/);
});

test('native Vite mapping preserves absolute file URLs and escaped original filenames', async t => {
  for (const name of ['original.js', 'original space%#é.js']) {
    const f = await fixture(t, (spec, map) => {
      const original = new URL('./' + encodeURIComponent(name), spec.mapURL).href;
      map.sources = [original];
      spec.sources[0].url = original;
      spec.mapSha256 = hash(JSON.stringify(map));
    });
    const owner = registerBundleMap(f.bundle, f.metadata);
    t.after(() => owner.close());
    const module = await import(f.bundle.href);
    let stack;
    try { module.boom(); } catch (error) { stack = error.stack; }
    assert.ok(stack?.includes(`(${f.spec.sources[0].url}:3:`), `Original source URI diverged: ${stack}`);
    await owner.close();
  }
});

test('missing maps and unsupported metadata fail without registration', async t => {
  const f = await fixture(t); await rm(f.mapURL);
  assert.throws(() => registerBundleMap(f.bundle, f.metadata), /ENOENT/);
  const bad = await fixture(t, spec => { spec.format = 2; });
  assert.throws(() => registerBundleMap(bad.bundle, bad.metadata), /unsupported registration/);
});

test('corrupt bytes, JSON, map structure and source provenance fail closed on lookup', async t => {
  for (const type of ['hash','json','version','source']) {
    const f = await fixture(t), owner = registerBundleMap(f.bundle, f.metadata);
    if (type === 'hash') await writeFile(f.mapURL, 'corrupt');
    else {
      await owner.close();
      const changed = await fixture(t, (spec, map) => {
        if (type === 'version') map.version = 2;
        if (type === 'source') map.sourcesContent[0] = 'wrong source';
        spec.mapSha256 = hash(type === 'json' ? '{' : JSON.stringify(map));
      });
      if (type === 'json') await writeFile(changed.mapURL, '{');
      const next = registerBundleMap(changed.bundle, changed.metadata);
      assert.throws(() => next.retrieveSourceMap(changed.bundle.href), /W13_MAP:/);
      assert.equal(next.loaded, false);
      assert.throws(() => next.retrieveSourceMap(changed.bundle.href), /W13_MAP:/, 'Failed validation cannot become a cached valid map');
      await next.close(); continue;
    }
    assert.throws(() => owner.retrieveSourceMap(f.bundle.href), /map hash mismatch/);
    assert.equal(owner.loaded, false);
    await owner.close();
  }
});
