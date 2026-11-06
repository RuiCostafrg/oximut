// Structural feasibility only. It makes no performance claim.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, symlink, unlink, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const script = fileURLToPath(import.meta.url);
const require = createRequire(import.meta.url);
const timeoutMs = 120_000;
const workerEntry = join(dirname(require.resolve('vitest/node')), 'workers', 'forks.js');
const packageVersion = name => JSON.parse(require('node:fs').readFileSync(require.resolve(`${name}/package.json`), 'utf8')).version;
const builtins = new Set((await import('node:module')).builtinModules.flatMap(x => [x, `node:${x}`]));
const hash = data => createHash('sha256').update(data).digest('hex');

/** Bundle the static fork closure while retaining its shared collector API. */
export async function bundleRuntime(output, { readEvidence = readFile } = {}) {
  const start = performance.now();
  const { rolldown } = await import('rolldown');
  const { preserveModuleContext } = await import('./module-context.mjs');
  const bundlerLoadMs = performance.now() - start;
  const contexts = [];
  const sharedRunner = new Set();
  const build = await rolldown({
    input: workerEntry,
    platform: 'node',
    external: id => builtins.has(id) || id.startsWith('node:'),
    plugins: [{ name: 'preserve-framework-module-context',
    /** Preserve the single collector instance imported by test-facing APIs. */
    resolveId(source) {
      if (source === '@vitest/runner' || source.startsWith('@vitest/runner/')) {
        // Test-facing Vitest APIs import this collector too. Both sides must
        // share its module-level runner/suite state within each fresh process.
        const id = import.meta.resolve(source);
        sharedRunner.add(id);
        return { id, external: true };
      }
    },
    /** Rewrite recognized module-context syntax, leaving source strings intact. */
    transform(code, id) {
      if (id.includes('\0') || !/\.[cm]?js$/.test(id)) return null;
      const transformed = preserveModuleContext(code, id);
      if (transformed.sites.length) contexts.push({ id, sites: transformed.sites });
      return { code: transformed.code, map: null };
    } }],
    /** Keep build errors visible; unsupported imports are recorded explicitly. */
    onLog(level, log, defaultHandler) {
      if (level === 'error') defaultHandler(level, log);
    },
  });
  let result;
  try { result = await build.write({ file: output, format: 'es', codeSplitting: false, sourcemap: false }); }
  finally { await build.close(); }
  const contents = await readEvidence(output);
  const chunk = result.output.find(item => item.type === 'chunk');
  assert.ok(chunk, 'Rolldown produced no worker chunk');
  let unsupported;
  const externals = [...new Set(chunk.imports)].sort();
  if (externals.some(id => !builtins.has(id) && !id.startsWith('node:') && !sharedRunner.has(id))) {
    unsupported ??= `UNSUPPORTED: unclassified static runtime externals: ${externals.filter(id => !builtins.has(id) && !id.startsWith('node:') && !sharedRunner.has(id)).join(', ')}`;
  }
  const modules = await Promise.all(chunk.moduleIds.map(async id => {
    if (id.includes('\0')) return { id, sha256: hash(id), sourceHash: 'virtual-module-id-only' };
    return { id, sha256: hash(await readEvidence(id)) };
  }));
  const sourceHash = hash(await readEvidence(workerEntry));
  const sharedRunnerFiles = await Promise.all([...sharedRunner].sort().map(async id =>
    ({ id, sha256: hash(await readEvidence(fileURLToPath(id))) })));
  const bundleHash = hash(contents);
  return { bundlePreparationMs: performance.now() - start, bundlerLoadMs, input: workerEntry, output,
    sourceHash, bundleHash, bytes: contents.length,
    modules, externals, sharedRunner: sharedRunnerFiles,
    dynamicImports: chunk.dynamicImports, preservedContexts: contexts, unsupported };
}

/** Execute the same state oracle through the real stock or bundled worker. */
async function controller(input) {
  process.send?.({ type: 'controller-entered', pid: process.pid });
  const stage = {}, importStart = performance.now();
  const { createVitest } = await import('vitest/node');
  stage.vitestImportMs = performance.now() - importStart;
  const poolImportStart = performance.now();
  const { createForkPool } = await import('./pool.mjs');
  stage.poolImportMs = performance.now() - poolImportStart;
  const runs = [], jobs = [];
  let context, reporterEnd;
  try {
    const stock = input.mode === 'stock';
    const forkPool = createForkPool(stock ? undefined : input.bundle);
    const initStart = performance.now();
    context = await createVitest('test', {
      root: input.root, config: false, watch: false, pool: forkPool.pool,
      maxWorkers: 1, maxConcurrency: 1, fileParallelism: false, isolate: true, coverage: { enabled: false },
      reporters: [{ onTestRunEnd(...args) { reporterEnd = args; } }],
      server: { deps: { external: ['stateful-cjs', 'stateful-esm'] } },
    });
    await context.standalone();
    assert.equal(context.projects.length, 1);
    assert.equal(context.projects[0].config.isolate, true);
    assert.equal(context.projects[0].config.pool, forkPool.pool.name);
    stage.vitestInitializeMs = performance.now() - initStart;
    const discoveryStart = performance.now();
    const specs = (await context.globTestSpecifications()).sort((a, b) => a.moduleId.localeCompare(b.moduleId));
    stage.specDiscoveryMs = performance.now() - discoveryStart;
    assert.equal(specs.length, 2, 'exactly two identical specifications required');
    for (const spec of specs) {
      reporterEnd = undefined;
      const before = forkPool.workers.length;
      const jobStart = performance.now();
      await context.runTestSpecifications([spec], true);
      const runMs = performance.now() - jobStart;
      // The API may resolve while stop() is still pending. Drain each job
      // before observing exits or admitting the next fresh fork.
      await context.pool.close();
      const jobMs = performance.now() - jobStart;
      assert.ok(reporterEnd, 'run completion event missing');
      const [modules, unhandled, reason] = reporterEnd;
      const errors = [...unhandled];
      const counts = { passed: 0, failed: 0, skipped: 0 };
      const testResults = [];
      for (const module of modules) {
        errors.push(...module.errors());
        for (const suite of module.children.allSuites()) errors.push(...suite.errors());
        for (const test of module.children.allTests()) {
          const state = test.result().state;
          assert.ok(Object.hasOwn(counts, state), `unexpected test state ${state}`);
          counts[state]++;
          testResults.push({ name: test.name, state });
        }
      }
      assert.equal(modules.length, 1); assert.equal(errors.length, 0, errors.map(e => e.message).join('\n'));
      assert.notEqual(reason, 'interrupted'); assert.deepEqual(counts, { passed: 8, failed: 0, skipped: 0 });
      assert.deepEqual(testResults.map(t => t.name).sort(), ['builtin clean', 'env clean', 'external CJS clean', 'external ESM clean', 'global clean', 'local ESM clean', 'spy clean', 'timer clean']);
      assert.equal(forkPool.workers.length, before + 1, 'one new OS fork per spec');
      const evidence = forkPool.workers.at(-1).worker.evidence;
      forkPool.assertDrained();
      jobs.push({ spec: spec.moduleId, ...evidence, wallMs: jobMs, runMs,
        drainMs: jobMs - runMs, counts });
      runs.push({ spec: basename(spec.moduleId), counts, testResults });
    }
    const closeStart = performance.now();
    await context.close(); context = undefined;
    stage.closeMs = performance.now() - closeStart;
    forkPool.assertDrained();
    assert.equal(new Set(jobs.map(j => j.pid)).size, 2, 'worker PIDs must be distinct');
    const observations = (await readFile(input.observations, 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(observations.length, 2);
    assert.deepEqual(observations.map(x => x.file).sort(), ['a', 'b']);
    assert.equal(new Set(observations.map(x => x.realm)).size, 2, 'fresh realms required');
    assert.deepEqual(new Set(observations.map(x => x.pid)), new Set(jobs.map(x => x.pid)));
    await writeFile(input.result, JSON.stringify({ mode: input.mode, runs, jobs, observations, stage, closed: true }) + '\n');
  } finally {
    try { if (context) await context.close(); }
    finally { if (process.connected) process.disconnect(); }
  }
}

/** Check the private process group used by this controlled fixture. */
function groupExists(pid) { try { process.kill(-pid, 0); return true; } catch (e) { if (e.code === 'ESRCH') return false; throw e; } }
/** Terminate fixture processes remaining in that private group. */
function killGroup(pid) { try { process.kill(-pid, 'SIGKILL'); } catch (e) { if (e.code !== 'ESRCH') throw e; } }
/** Bound a controller lifetime and reject missing completion or group cleanup. */
async function invoke(input, folder) {
  const file = join(folder, 'input.json'); await writeFile(file, JSON.stringify(input));
  const launchedAt = performance.now();
  const child = spawn(process.execPath, [script, '--controller', file], { detached: true,
    env: { ...process.env, NODE_OPTIONS: '', OXIMUT_BUNDLE_OBSERVATIONS: input.observations },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'] });
  let controllerEntryMs;
  child.on('message', message => {
    if (message?.type === 'controller-entered' && message.pid === child.pid && controllerEntryMs === undefined)
      controllerEntryMs = performance.now() - launchedAt;
  });
  let stdout = '', stderr = '', timedOut = false;
  child.stdout.on('data', b => stdout += b); child.stderr.on('data', b => stderr += b);
  const timer = setTimeout(() => { timedOut = true; if (child.pid) killGroup(child.pid); }, timeoutMs);
  let status, failure;
  try { status = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', (code, signal) => resolve({ code, signal })); }); }
  catch (error) { failure = error; }
  finally { clearTimeout(timer); }
  const cleanupStart = performance.now();
  const cleanupRequired = child.pid ? groupExists(child.pid) : false;
  if (cleanupRequired) {
    killGroup(child.pid);
    for (let i = 0; i < 20 && groupExists(child.pid); i++) await delay(25);
    failure ??= Error(`controller process group remained after completion: ${child.pid}`);
  }
  const cleanupVerified = !child.pid || !groupExists(child.pid);
  await writeFile(join(folder, 'stdout.log'), stdout); await writeFile(join(folder, 'stderr.log'), stderr);
  await writeFile(join(folder, 'controller-status.json'), JSON.stringify({ status, timedOut,
    cleanupRequired, cleanupVerified, cleanupMs: performance.now() - cleanupStart,
    controllerPid: child.pid, failure: failure ? String(failure) : null }) + '\n');
  if (failure) throw failure;
  assert.equal(cleanupVerified, true, 'controller process group must be absent');
  assert.equal(timedOut, false); assert.equal(status.signal, null); assert.equal(status.code, 0, stderr.slice(-5000));
  assert.ok(Number.isFinite(controllerEntryMs), 'controller entry observation missing');
  return { ...JSON.parse(await readFile(input.result, 'utf8')), controllerEntryMs,
    cleanupMs: performance.now() - cleanupStart };
}

/** Build byte-identical inputs; observation destinations come from the environment. */
export async function fixtures(root) {
  await mkdir(join(root, 'node_modules'), { recursive: true });
  const vitestRoot = dirname(require.resolve('vitest/package.json'));
  await symlink(vitestRoot, join(root, 'node_modules/vitest'), 'dir');
  await writeFile(join(root, 'package.json'), '{"type":"module"}\n');
  for (const [name, type, source] of [['stateful-cjs', 'commonjs', 'module.exports = { value: 0 };'], ['stateful-esm', 'module', 'export default { value: 0 };']]) {
    const dir = join(root, 'node_modules', name); await mkdir(dir);
    await writeFile(join(dir, 'package.json'), JSON.stringify({ name, type, main: 'index.js' })); await writeFile(join(dir, 'index.js'), source);
  }
  await writeFile(join(root, 'local.mjs'), 'export default { value: 0 };\n');
  for (const file of ['a', 'b']) await writeFile(join(root, `${file}.test.mjs`), `
import { test, expect, beforeAll, afterAll, vi } from 'vitest';
import { randomUUID } from 'node:crypto'; import fs from 'node:fs'; import local from './local.mjs';
import cjs from 'stateful-cjs'; import esm from 'stateful-esm';
const target = globalThis.__bundleProbeTarget ??= { call() { return 'original'; } };
const realm = globalThis.__bundleProbeRealm ??= randomUUID();
beforeAll(() => fs.appendFileSync(process.env.OXIMUT_BUNDLE_OBSERVATIONS, JSON.stringify({ file:${JSON.stringify(file)}, pid:process.pid, realm, local:local.value, cjs:cjs.value, esm:esm.value, global:globalThis.__bundleProbeGlobal ?? null, env:process.env.OXIMUT_BUNDLE_PROBE ?? null, builtin:fs.__oximutBundleProbe ?? null, spy:target.call(), fakeClock:Date.now() === 1234 }) + '\\n'));
test('local ESM clean', () => expect(local.value).toBe(0)); test('external CJS clean', () => expect(cjs.value).toBe(0)); test('external ESM clean', () => expect(esm.value).toBe(0));
test('global clean', () => expect(globalThis.__bundleProbeGlobal).toBeUndefined()); test('env clean', () => expect(process.env.OXIMUT_BUNDLE_PROBE).toBeUndefined());
test('builtin clean', () => expect(fs.__oximutBundleProbe).toBeUndefined()); test('spy clean', () => expect(target.call()).toBe('original')); test('timer clean', () => expect(Date.now()).not.toBe(1234));
afterAll(() => { local.value++; cjs.value++; esm.value++; globalThis.__bundleProbeGlobal='dirty'; process.env.OXIMUT_BUNDLE_PROBE='dirty'; fs.__oximutBundleProbe='dirty'; vi.spyOn(target,'call').mockReturnValue('dirty'); vi.useFakeTimers(); vi.setSystemTime(1234); });
`);
}

/** Allocate a fresh result directory without re-creating a mkdtemp result. */
export async function createOutputDirectory(destination) {
  if (destination === undefined) return mkdtemp(join(tmpdir(), 'fresh-fork-bundle-'));
  const output = resolve(destination);
  await mkdir(output);
  return output;
}

/** Reject incomplete or unknown CLI arguments before creating output directories. */
export function parseOutputArguments(args) {
  if (args.length === 0) return undefined;
  if (args.length !== 2 || args[0] !== '--output' || typeof args[1] !== 'string'
      || !args[1] || args[1].startsWith('--'))
    throw Error('Expected --output <new directory>');
  return args[1];
}

/** Bind every executable probe/oracle source to the captured bytes. */
export async function probeSourceHashes(directory = new URL('.', import.meta.url)) {
  const names = ['probe.mjs', 'pool.mjs', 'module-context.mjs', 'test-source.mjs'];
  return Object.fromEntries(await Promise.all(names.map(async name =>
    [name, hash(await readFile(new URL(name, directory)))])));
}

/** Retain successful and failed stages without manufacturing performance metrics. */
async function main() {
  const output = await createOutputDirectory(parseOutputArguments(process.argv.slice(2)));
  const metadata = { formatVersion: 2, command: process.argv, node: process.version,
    platform: process.platform, arch: process.arch, vitest: null, rolldown: null,
    bundlePreparationMs: null, bundle: null, runs: [],
    metrics: 'whole-tree CPU user/sys, aggregate RSS, counts and resources.phaseWallMs are unmeasured/N/A; no paired ratios or CIs' };
  const bundle = join(output, 'forks.bundle.mjs');
  try {
    metadata.vitest = packageVersion('vitest');
    metadata.rolldown = packageVersion('rolldown');
    metadata.sourceHashes = await probeSourceHashes();
    metadata.packageLockSha256 = hash(await readFile(new URL('../../../package-lock.json', import.meta.url)));
    assert.equal(metadata.vitest, '4.1.12', 'pinned worker API required');
    metadata.bundle = await bundleRuntime(bundle);
    metadata.bundlePreparationMs = metadata.bundle.bundlePreparationMs;
  }
  catch (error) { metadata.unsupported = String(error.stack || error); await writeFile(join(output, 'evidence.json'), JSON.stringify(metadata, null, 2) + '\n'); console.error(`First unsupported error: ${error.message}`); process.exitCode = 2; return; }
  if (metadata.bundle.unsupported) { metadata.unsupported = metadata.bundle.unsupported; await writeFile(join(output, 'evidence.json'), JSON.stringify(metadata, null, 2) + '\n'); console.error(`First unsupported error: ${metadata.unsupported}`); process.exitCode = 2; return; }
  const cases = [];
  try { for (const mode of ['stock', 'bundled']) {
    const folder = join(output, mode); await mkdir(folder);
    const root = join(folder, 'project'), observations = join(folder, 'observations.jsonl');
    try {
      await fixtures(root);
      const inputFiles = ['package.json', 'a.test.mjs', 'b.test.mjs', 'local.mjs', 'node_modules/stateful-cjs/package.json', 'node_modules/stateful-cjs/index.js', 'node_modules/stateful-esm/package.json', 'node_modules/stateful-esm/index.js'];
      const specHashes = Object.fromEntries(await Promise.all(inputFiles.map(async file => [file, hash(await readFile(join(root, file)))])));
      const controllerStartup = performance.now();
      const result = await invoke({ mode, root, observations, result: join(folder, 'result.json'), bundle }, folder);
      cases.push({ mode, specHashes, controllerLifetimeMs: performance.now() - controllerStartup, ...result });
    } finally {
      // Retain exact fixture inputs without uploading the shared dependencies.
      try { await unlink(join(root, 'node_modules/vitest')); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }
  assert.deepEqual(cases[0].specHashes, cases[1].specHashes, 'fixture inputs identical');
  assert.deepEqual(cases[0].runs, cases[1].runs, 'exact test identities and outcomes identical');
  metadata.runs = cases; metadata.closeCleanup = cases.map(c => ({ mode: c.mode, closed: c.closed, workerPids: c.jobs.map(j => j.pid) }));
  metadata.structuralOracle = true;
  } catch (error) { metadata.failure = String(error.stack || error); metadata.runs = cases; process.exitCode = 1; }
  finally { await writeFile(join(output, 'evidence.json'), JSON.stringify(metadata, null, 2) + '\n'); }
}

if (process.argv[1] && resolve(process.argv[1]) === script) {
  if (process.argv[2] === '--controller') await controller(JSON.parse(await readFile(process.argv[3], 'utf8')));
  else await main();
}
