import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, writeFile, chmod, rm } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join, relative, sep } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createRequire, isBuiltin } from 'node:module';
import { parse as parseJavaScript } from '@babel/parser';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { preserveModuleContext } from './module-context.mjs';
import { fixtures, createOutputDirectory, parseOutputArguments, probeSourceHashes, bundleRuntime } from './probe.mjs';

const require = createRequire(import.meta.url);
const { parse } = require('yaml');
const probeScript = fileURLToPath(new URL('./probe.mjs', import.meta.url));

/** Reconcile retained assertions and observed state with the exact fresh workers. */
function assertRetainedOracle(proof) {
  assert.equal(proof.structuralOracle, true);
  assert.deepEqual(proof.runs.map(run => run.mode), ['stock', 'bundled']);
  assert.deepEqual(proof.runs[0].specHashes, proof.runs[1].specHashes);
  assert.ok(proof.bundle.input.endsWith('/vitest/dist/workers/forks.js'));
  assert.ok(proof.bundle.output.endsWith('/forks.bundle.mjs'));
  assert.notEqual(proof.bundle.input, proof.bundle.output);
  const expected = ['local ESM clean', 'external CJS clean', 'external ESM clean', 'global clean',
    'env clean', 'builtin clean', 'spy clean', 'timer clean']
    .map(name => ({ name, state: 'passed' })).sort((a, b) => a.name.localeCompare(b.name));
  const pids = [], realms = [];
  for (const run of proof.runs) {
    assert.equal(run.closed, true); assert.equal(run.jobs.length, 2); assert.equal(run.observations.length, 2);
    for (const job of run.jobs) {
      assert.deepEqual(job.counts, { passed: 8, failed: 0, skipped: 0 }); pids.push(job.pid);
      assert.equal(job.entrypoint, run.mode === 'stock' ? proof.bundle.input : proof.bundle.output);
    }
    // Bind reported state to the workers and specifications that produced it.
    assert.deepEqual(new Set(run.observations.map(observation => observation.pid)), new Set(run.jobs.map(job => job.pid)));
    for (const observation of run.observations) {
      assert.deepEqual({ local: observation.local, cjs: observation.cjs, esm: observation.esm,
        global: observation.global, env: observation.env, builtin: observation.builtin,
        spy: observation.spy, fakeClock: observation.fakeClock },
      { local: 0, cjs: 0, esm: 0, global: null, env: null, builtin: null, spy: 'original', fakeClock: false });
    }
    for (let index = 0; index < run.jobs.length; index++) {
      const job = run.jobs[index], observation = run.observations.find(item => item.pid === job.pid);
      assert.equal(observation.file + '.test.mjs', run.runs[index].spec);
      assert.equal(job.spec.split('/').at(-1), run.runs[index].spec);
    }
    // Bind aggregate counts to the complete per-spec identities and states.
    assert.deepEqual(run.runs.map(spec => spec.spec), ['a.test.mjs', 'b.test.mjs']);
    for (const spec of run.runs) {
      assert.deepEqual(spec.counts, { passed: 8, failed: 0, skipped: 0 });
      assert.deepEqual([...spec.testResults].sort((a, b) => a.name.localeCompare(b.name)), expected);
    }
    realms.push(...run.observations.map(observation => observation.realm));
  }
  assert.deepEqual(proof.runs[0].runs, proof.runs[1].runs);
  assert.equal(new Set(pids).size, 4); assert.equal(new Set(realms).size, 4);
}

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
export function rawLoad(name) { return import( /* retain argument syntax */ ((name))); }`;
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

test('retained native proof has complete hashes, exact outcomes and distinct workers', async () => {
  const root = new URL('../../results/2026-11-06-fresh-fork-bundle/', import.meta.url);
  const lines = (await readFile(new URL('SHA256SUMS', root), 'utf8')).trim().split('\n');
  const covered = [];
  for (const line of lines) {
    const [expected, name] = line.split('  ');
    assert.match(expected, /^[a-f0-9]{64}$/);
    assert.ok(name && !name.startsWith('/') && !name.split('/').includes('..'));
    assert.equal(createHash('sha256').update(await readFile(new URL(name, root))).digest('hex'), expected, name);
    covered.push(name);
  }
  const retained = (await readdir(root, { recursive: true, withFileTypes: true }))
    .filter(item => item.isFile() && item.name !== 'SHA256SUMS')
    .map(item => relative(fileURLToPath(root), join(item.parentPath, item.name)).split(sep).join('/')).sort();
  assert.deepEqual(covered.sort(), retained);
  const proof = JSON.parse(await readFile(new URL('evidence.json', root), 'utf8'));
  assertRetainedOracle(proof);
  for (const run of proof.runs) {
    const status = JSON.parse(await readFile(new URL(run.mode + '/controller-status.json', root), 'utf8'));
    assert.equal(status.cleanupVerified, true); assert.equal(status.cleanupRequired, false); assert.equal(status.timedOut, false);
  }
});

test('retained proof rejects unrelated worker state and changed or duplicated test identities', async () => {
  const proof = JSON.parse(await readFile(new URL('../../results/2026-11-06-fresh-fork-bundle/evidence.json', import.meta.url), 'utf8'));
  for (let mode = 0; mode < 2; mode++) {
    const changes = [
      run => { run.observations[0].pid += 999999; },
      run => { [run.observations[0].pid, run.observations[1].pid] = [run.observations[1].pid, run.observations[0].pid]; },
      run => { run.runs[0].testResults[0].name = 'unrelated passing assertion'; },
      run => { run.runs[0].testResults[1].name = run.runs[0].testResults[0].name; },
      run => { run.runs[0].testResults[0].state = 'failed'; },
      run => { run.runs[0].testResults.reverse(); },
      run => { run.jobs[0].entrypoint = proof.runs[1 - mode].jobs[0].entrypoint; },
      run => { run.jobs[0].entrypoint = '/unrelated/forks.bundle.mjs'; },
    ];
    for (const [field, dirty] of Object.entries({ local: 1, cjs: 1, esm: 1, global: 'dirty',
      env: 'dirty', builtin: 'dirty', spy: 'dirty', fakeClock: true })) {
      changes.push(run => { run.observations[0][field] = dirty; });
      changes.push(run => { delete run.observations[0][field]; });
    }
    for (const change of changes) {
      const corrupted = structuredClone(proof); change(corrupted.runs[mode]);
      assert.throws(() => assertRetainedOracle(corrupted));
    }
  }
});

test('malformed output arguments fail before allocating or running a probe', async () => {
  for (const args of [['--output'], ['--output', ''], ['--output', '--unknown'], ['--unknown'],
    ['--output', 'one', '--output', 'two']])
    assert.throws(() => parseOutputArguments(args), /Expected --output/);
  assert.equal(parseOutputArguments([]), undefined);
  assert.equal(parseOutputArguments(['--output', 'path with spaces']), 'path with spaces');
  const root = await mkdtemp(join(tmpdir(), 'invalid-probe-output-'));
  try {
    const result = spawnSync(process.execPath, [probeScript, '--output'],
      { env: { ...process.env, TMPDIR: root }, encoding: 'utf8', timeout: 30000 });
    assert.notEqual(result.status, 0); assert.match(result.stderr, /Expected --output/);
    assert.deepEqual(await readdir(root), []);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('dependency pin failures retain unsupported evidence before any worker build', async () => {
  const root = await mkdtemp(join(tmpdir(), 'pin evidence '));
  try {
    const preload = join(root, 'wrong-version.cjs'), output = join(root, 'result');
    const packageFile = require.resolve('vitest/package.json');
    await writeFile(preload, `const fs=require('node:fs');const read=fs.readFileSync;
fs.readFileSync=function(...args){const value=Reflect.apply(read,this,args);
if(String(args[0])===${JSON.stringify(packageFile)})return JSON.stringify({...JSON.parse(value),version:'99.0.0-probe-test'});
return value;};`);
    const result = spawnSync(process.execPath, [probeScript, '--output', output],
      { env: { ...process.env, NODE_OPTIONS: '--require=' + JSON.stringify(preload) }, encoding: 'utf8', timeout: 10000 });
    assert.equal(result.status, 2, result.stderr);
    const evidence = JSON.parse(await readFile(join(output, 'evidence.json'), 'utf8'));
    assert.equal(evidence.vitest, '99.0.0-probe-test');
    assert.match(evidence.unsupported, /pinned worker API required/);
    assert.equal(evidence.bundle, null); assert.deepEqual(evidence.runs, []);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('focused oracle bytes affect provenance without changing other source hashes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'probe-source-hashes-'));
  try {
    for (const name of ['probe.mjs', 'pool.mjs', 'module-context.mjs', 'test-source.mjs'])
      await writeFile(join(root, name), name + ' initial bytes');
    const directory = pathToFileURL(root + sep);
    const before = await probeSourceHashes(directory);
    await writeFile(join(root, 'test-source.mjs'), 'changed focused oracle bytes');
    const after = await probeSourceHashes(directory);
    assert.equal(Object.keys(before).length, 4);
    assert.equal(before['test-source.mjs'], createHash('sha256').update('test-source.mjs initial bytes').digest('hex'));
    assert.notEqual(after['test-source.mjs'], before['test-source.mjs']);
    for (const name of ['probe.mjs', 'pool.mjs', 'module-context.mjs']) assert.equal(before[name], after[name]);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('bundle preparation interval includes the last awaited metadata read', async t => {
  const root = await mkdtemp(join(tmpdir(), 'bundle-preparation-clock-'));
  try {
    let lastRead = 0;
    const before = performance.now();
    let bundle;
    try {
      bundle = await bundleRuntime(join(root, 'worker.mjs'), { readEvidence: async (...args) => {
        const bytes = await readFile(...args); await delay(70); lastRead = performance.now(); return bytes;
      } });
    } catch (error) {
      if (/Cannot find native binding/.test(String(error))) { t.skip('native Rolldown binding unavailable'); return; }
      throw error;
    }
    assert.equal(typeof bundle.bundlePreparationMs, 'number');
    assert.ok(bundle.bundlePreparationMs + 15 >= lastRead - before, 'interval ended before metadata reads completed');
    assert.ok(bundle.bundlePreparationMs >= bundle.bundlerLoadMs);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('dependency and focused-test failures retain staged evidence and their failing status', async () => {
  const workflow = parse(await readFile(new URL('../../../.github/workflows/fresh-fork-bundle-probe.yml', import.meta.url), 'utf8'));
  const steps = workflow.jobs['structural-probe'].steps;
  assert.equal(workflow.concurrency['cancel-in-progress'], false, 'running attempts must retain their evidence');
  assert.equal(workflow.concurrency.queue, 'max', 'pending attempts must not replace one another');
  for (const file of ['.npmrc', '.node-version', 'package.json', 'package-lock.json'])
    assert.ok(workflow.on.pull_request.paths.some(pattern => require('node:path').matchesGlob(file, pattern)), file);
  for (const phase of ['npm', 'focused']) {
    const root = await mkdtemp(join(tmpdir(), "probe prerequisite's "));
    try {
      const bin = join(root, 'bin'); await mkdir(bin);
      const programs = {
        git: '#!/bin/sh\ncase "$1" in archive) printf source-fixture;; rev-parse) printf aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa;; *) exit 90;; esac\n',
        npm: '#!/bin/sh\necho dependency-fixture\nif test "$PROBE_TEST_FAIL_PHASE" = npm; then exit 23; fi\n',
        node: '#!/bin/sh\necho focused-fixture\nexit 29\n',
      };
      for (const [name, contents] of Object.entries(programs)) {
        await writeFile(join(bin, name), contents); await chmod(join(bin, name), 0o755);
      }
      let failure;
      for (const step of steps) {
        if (step.id === 'probe') break;
        if (!step.run) continue;
        const result = spawnSync('bash', ['-c', step.run], { encoding: 'utf8', timeout: 10000,
          env: { ...process.env, RUNNER_TEMP: root, PATH: bin + ':' + process.env.PATH, PROBE_TEST_FAIL_PHASE: phase } });
        if (result.status !== 0) { failure = result; break; }
      }
      assert.equal(failure?.status, phase === 'npm' ? 23 : 29);
      const evidence = join(root, 'fresh-fork-bundle-probe');
      assert.ok((await readFile(join(evidence, 'source.tar.gz'))).length > 0);
      assert.equal((await readFile(join(evidence, 'commit.txt'), 'utf8')).trim(), 'a'.repeat(40));
      assert.match(await readFile(join(evidence, phase === 'npm' ? 'dependencies.log' : 'focused-tests.log'), 'utf8'),
        phase === 'npm' ? /dependency-fixture/ : /focused-fixture/);
    } finally { await rm(root, { recursive: true, force: true }); }
  }
});

test('actual tooling imports have exact direct dependency and lockfile bindings', async () => {
  const base = new URL('../../../', import.meta.url);
  const manifest = JSON.parse(await readFile(new URL('package.json', base), 'utf8'));
  const lock = JSON.parse(await readFile(new URL('package-lock.json', base), 'utf8'));
  const packages = new Set();
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    const value = node.type === 'ImportDeclaration' || node.type === 'ImportExpression'
      ? node.source?.type === 'StringLiteral' && node.source.value
      : node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'require'
        && node.arguments[0]?.type === 'StringLiteral' && node.arguments[0].value;
    if (value && !isBuiltin(value) && !/^(?:\.|\/|[a-z]+:)/.test(value))
      packages.add(value.match(/^(?:@[^/]+\/[^/]+|[^/]+)/)[0]);
    for (const child of Object.values(node)) {
      if (Array.isArray(child)) { for (const item of child) visit(item); }
      else if (child && typeof child === 'object') visit(child);
    }
  }
  for (const file of ['probe.mjs', 'pool.mjs', 'module-context.mjs', 'test-source.mjs'])
    visit(parseJavaScript(await readFile(new URL(file, import.meta.url), 'utf8'), { sourceType: 'module', createImportExpressions: true }));
  for (const name of packages) {
    assert.match(manifest.devDependencies[name] ?? '', /^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/, name);
    assert.equal(lock.packages[''].devDependencies[name], manifest.devDependencies[name], name);
    assert.equal(lock.packages['node_modules/' + name].version, manifest.devDependencies[name], name);
  }
});
