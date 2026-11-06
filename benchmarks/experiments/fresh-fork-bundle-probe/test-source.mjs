import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, writeFile, rm } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import { preserveModuleContext } from './module-context.mjs';
import { fixtures, createOutputDirectory } from './probe.mjs';

test('relocated code preserves module URL, resolver and computed import bases', async () => {
  const root = await mkdtemp(join(tmpdir(), 'bundle-context-'));
  try {
    const original = join(root, 'original'), relocated = join(root, 'relocated');
    await mkdir(original); await mkdir(relocated);
    await writeFile(join(original, 'dependency.mjs'), 'export default "original";');
    await writeFile(join(relocated, 'dependency.mjs'), 'export default "wrong base";');
    const id = join(original, 'source.mjs');
    const source = `export const url=import.meta.url;
export const resolve=import.meta.resolve;
export const text="import.meta";
export async function load(name) { return (await import(name)).default; }
export function rawLoad(name) { return import(name); }`;
    const transformed = preserveModuleContext(source, id);
    assert.deepEqual(transformed.sites.map(s => s.kind), ['meta:url', 'meta:resolve', 'computed-import', 'computed-import']);
    const candidate = join(relocated, 'candidate.mjs');
    await writeFile(candidate, transformed.code);
    const check = `import assert from 'node:assert/strict';
import * as candidate from ${JSON.stringify(pathToFileURL(candidate).href)};
assert.equal(candidate.url,${JSON.stringify(pathToFileURL(id).href)});
assert.equal(candidate.resolve('./dependency.mjs'),${JSON.stringify(pathToFileURL(join(original,'dependency.mjs')).href)});
assert.equal(await candidate.load('./dependency.mjs'),'original');
assert.equal(candidate.text,'import.meta');
let pending;
assert.doesNotThrow(()=>{pending=candidate.rawLoad('missing-module-for-bundle-test');});
await assert.rejects(pending,{code:'ERR_MODULE_NOT_FOUND'});
assert.doesNotThrow(()=>{pending=candidate.rawLoad(Symbol('not a string'));});
await assert.rejects(pending,TypeError);`;
    const result = spawnSync(process.execPath,
      ['--experimental-import-meta-resolve', '--input-type=module', '-e', check], { encoding: 'utf8', timeout: 10000 });
    assert.equal(result.status, 0, result.stderr);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('unsupported meta access fails, while strings and static imports stay intact', () => {
  const id = '/tmp/source.mjs';
  for (const code of ['export default import.meta;', 'export default import.meta.env;',
    'export default import.meta["url"];'])
    assert.throws(() => preserveModuleContext(code, id), /UNSUPPORTED/);
  const code = `export const text='import.meta.env'; export const load=()=>import('./static.mjs');`;
  assert.equal(preserveModuleContext(code, id).code, code);
});

test('stock and bundled fixture bytes are identical despite distinct output paths', async () => {
  const root = await mkdtemp(join(tmpdir(), 'bundle-fixtures-'));
  try {
    const stock = join(root, 'stock'), bundled = join(root, 'bundled');
    await fixtures(stock); await fixtures(bundled);
    for (const file of ['package.json', 'a.test.mjs', 'b.test.mjs', 'local.mjs',
      'node_modules/stateful-cjs/package.json', 'node_modules/stateful-cjs/index.js',
      'node_modules/stateful-esm/package.json', 'node_modules/stateful-esm/index.js'])
      assert.deepEqual(await readFile(join(stock, file)), await readFile(join(bundled, file)), file);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('default output is fresh and explicit existing directories are rejected', async () => {
  const output = await createOutputDirectory();
  try {
    assert.deepEqual(await readdir(output), []);
    await assert.rejects(createOutputDirectory(output), { code: 'EEXIST' });
    const nested = join(output, 'run');
    assert.equal(await createOutputDirectory(nested), nested);
    assert.deepEqual(await readdir(nested), []);
  } finally { await rm(output, { recursive: true, force: true }); }
});
