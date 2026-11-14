// Deliberate negative control: never a product worker or a performance benchmark.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile, symlink, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repo = resolve(import.meta.dirname, '../../..');
const require = createRequire(import.meta.url);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const pins = { vitest: '5.1.0', vite: '8.3.5', '@vitest/coverage-v8': '5.1.0', rolldown: '1.2.15', '@babel/parser': '8.0.10', 'magic-string': '1.4.5' };
const dist = dirname(require.resolve('vitest/node'));
const entry = join(dist, 'workers/forks.js');
const base = join(dist, 'chunks/base.Cof-UHJf.js');
const collector = join(dist, 'chunks/run.CjdgFZiW.js');
const api = join(dist, 'index.js');
const selected = new Set([entry, base, collector]);

async function installationManifest(root) {
  const rows = [];
  async function walk(folder) {
    for (const item of (await readdir(folder, { withFileTypes: true })).sort((a,b) => a.name.localeCompare(b.name))) {
      if (item.name === '.bin' || item.name === '.cache') continue;
      const path = join(folder, item.name);
      if (item.isDirectory()) await walk(path);
      else if (item.isFile()) rows.push({ path, sha256: sha(await readFile(path)) });
      else assert.fail(`Unsupported installation entry ${path}`);
    }
  }
  await walk(root);
  return rows;
}

// Topology, not a semantic proof: only published Vitest ESM literal edges.
async function topology() {
  const { parse } = await import('@babel/parser');
  const modules = new Map();
  async function closure(path, visited = new Set()) {
    if (visited.has(path)) return visited;
    visited.add(path);
    if (!modules.has(path)) {
      const code = await readFile(path, 'utf8');
      const ast = parse(code, { sourceType: 'module', createImportExpressions: true });
      const edges = [], contextSites = [];
      function visit(node) {
        if (!node || typeof node !== 'object') return;
        if (['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration', 'ImportExpression'].includes(node.type) && node.source?.type === 'StringLiteral') {
          try {
            const url = import.meta.resolve(node.source.value, pathToFileURL(path).href);
            edges.push({ kind: node.type, source: node.source.value, resolved: url });
          } catch (error) {
            // Topology must retain absent optional dependencies, never install them.
            edges.push({ kind: node.type, source: node.source.value, unresolved: error.message });
          }
        }
        if (node.type === 'MetaProperty' || (node.type === 'ImportExpression' && node.source.type !== 'StringLiteral')) contextSites.push({ kind: node.type, start: node.start, text: code.slice(node.start, node.end) });
        for (const [key, value] of Object.entries(node)) {
          if (['loc','start','end','extra','comments','tokens'].includes(key)) continue;
          if (Array.isArray(value)) value.forEach(visit);
          else if (value && typeof value === 'object') visit(value);
        }
      }
      visit(ast);
      modules.set(path, { path, bytes: Buffer.byteLength(code), sha256: sha(code), edges, contextSites });
    }
    for (const edge of modules.get(path).edges) if (edge.resolved?.startsWith(pathToFileURL(dist + '/').href)) await closure(fileURLToPath(edge.resolved), visited);
    return visited;
  }
  const worker = await closure(entry), publicAPI = await closure(api);
  return { limitation: 'Literal Vitest-internal ESM edges only; external package closures and computed imports are not proven safe.', worker: [...worker].sort(), publicAPI: [...publicAPI].sort(), shared: [...worker].filter(x => publicAPI.has(x)).sort(), private: [...worker].filter(x => !publicAPI.has(x)).sort(), modules: [...modules.values()].sort((a,b) => a.path.localeCompare(b.path)) };
}

export function checkPins(actual, node = process.version) {
  assert.equal(node, 'v26.3.1', 'Unsupported Node runtime');
  assert.deepEqual(actual, pins, 'Unsupported package versions');
}

// Narrow, syntax-checked source-context preservation, with a real source map.
// Every other metadata form rejects; no computed imports are ever inlined.
export async function preserveURL(code, id) {
  const { parse } = await import('@babel/parser');
  const { default: MagicString } = await import('magic-string');
  const ast = parse(code, { sourceType: 'module', createImportExpressions: true });
  const out = new MagicString(code), sites = [];
  function visit(node, parent) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'MetaProperty') {
      assert.ok(parent?.type === 'MemberExpression' && parent.object === node && !parent.computed && parent.property.name === 'url', `Unsupported metadata in ${id}:${node.start}`);
      out.overwrite(parent.start, parent.end, JSON.stringify(pathToFileURL(id).href));
      sites.push({ start: parent.start, end: parent.end, original: code.slice(parent.start, parent.end) });
    }
    if (node.type === 'ImportExpression') assert.equal(node.source.type, 'StringLiteral', `Unsupported computed import in ${id}:${node.start}`);
    // The selected published ESM modules contain no CommonJS require calls.
    if (node.type === 'CallExpression' && node.callee?.name === 'require') throw Error(`Unsupported require in ${id}:${node.start}`);
    for (const [key, value] of Object.entries(node)) {
      if (['loc', 'start', 'end', 'extra', 'comments', 'tokens'].includes(key)) continue;
      if (Array.isArray(value)) value.forEach(child => visit(child, node));
      else if (value && typeof value === 'object') visit(value, node);
    }
  }
  visit(ast);
  return { code: out.toString(), map: out.generateMap({ source: id, includeContent: true, hires: true }), sites };
}

async function controller(input) {
  const { createVitest, ForksPoolWorker } = await import('vitest/node');
  const workers = [], reports = [];
  const ctx = await createVitest('test', {
    root: input.root, config: false, watch: false, isolate: true, fsModuleCache: false,
    maxWorkers: 1, fileParallelism: false, coverage: { enabled: false },
    pool: { name: 'v5-negative-collector-control', createPoolWorker(options) {
      const worker = new ForksPoolWorker(options);
      if (input.bundle) worker.entrypoint = input.bundle;
      const start = worker.start.bind(worker), stop = worker.stop.bind(worker);
      const record = {};
      workers.push(record);
      worker.start = async () => { await start(); record.pid = worker.fork.pid; record.entrypoint = worker.entrypoint; };
      worker.stop = async () => { const child = worker.fork; await stop(); record.exitCode = child.exitCode; record.signal = child.signalCode; };
      return worker;
    } },
    reporters: [{ onTestRunEnd(modules, errors, reason) {
      reports.push({ reason, errors: [...errors, ...modules.flatMap(m => [...m.errors()])].map(e => ({ message: e.message, stack: e.stack })), tests: modules.flatMap(m => [...m.children.allTests()].map(t => ({ name: t.name, state: t.result().state }))) });
    } }],
  });
  try {
    await ctx.standalone();
    const specs = await ctx.globTestSpecifications();
    assert.equal(specs.length, 1);
    for (const selector of ['', 'A', 'B', 'A']) {
      ctx.projects[0].config.env = { ...ctx.projects[0].config.env, OXIMUT_ACTIVE_MUTANT: selector };
      await ctx.runTestSpecifications(specs, true);
      await ctx.pool.close();
      // Stop immediately on the first structural failure.
      if (reports.at(-1).errors.length || reports.at(-1).tests.some(t => t.state !== 'passed')) break;
    }
  } finally { await ctx.close(); }
  await writeFile(input.result, JSON.stringify({ workers, reports }, null, 2) + '\n');
}

function groupExists(pid) {
  try { process.kill(-pid, 0); return true; } catch (e) { if (e.code === 'ESRCH') return false; throw e; }
}
async function invoke(input, folder) {
  await mkdir(folder);
  const inputPath = join(folder, 'input.json');
  await writeFile(inputPath, JSON.stringify(input));
  const child = spawn(process.execPath, ['--experimental-import-meta-resolve', fileURLToPath(import.meta.url), '--controller', inputPath], { detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const stdout = [], stderr = [];
  child.stdout.on('data', x => stdout.push(x)); child.stderr.on('data', x => stderr.push(x));
  let timedOut = false, launchError;
  child.on('error', e => { launchError = String(e); });
  const timer = setTimeout(() => { timedOut = true; process.kill(-child.pid, 'SIGKILL'); }, 20000);
  const status = await new Promise(done => child.once('close', (code, signal) => done({ code, signal })));
  clearTimeout(timer);
  const residual = child.pid ? groupExists(child.pid) : false;
  if (residual) process.kill(-child.pid, 'SIGKILL');
  await writeFile(join(folder, 'stdout.log'), Buffer.concat(stdout));
  await writeFile(join(folder, 'stderr.log'), Buffer.concat(stderr));
  const cleanup = { ...status, timedOut, launchError, residualGroup: residual, cleanGroup: child.pid ? !groupExists(child.pid) : false };
  await writeFile(join(folder, 'status.json'), JSON.stringify(cleanup, null, 2) + '\n');
  // Vitest marks a reported test/collection failure with controller exit 1.
  // Accept that only for this deliberately invalid negative control, and still
  // require its exact collector error and full result below.
  assert.equal(status.code, input.bundle ? 1 : 0, JSON.stringify(cleanup));
  assert.equal(residual, false, 'Residual descendants: structural failure');
  return JSON.parse(await readFile(input.result, 'utf8'));
}

export async function main(output) {
  assert.ok(process.execArgv.includes('--experimental-import-meta-resolve'), 'Run with --experimental-import-meta-resolve');
  await mkdir(output); // Refuse overwriting earlier evidence.
  const evidence = { format: 1, classification: 'incomplete', performance: 'unmeasured: wall, CPU, aggregate RSS, ratios and confidence intervals', parent: '6cc8ce0', priorProbe: 'e036f54c6c4a255a55a411e96a353bf56c7fa995', node: process.version, versions: {}, sources: [], externals: [], edits: [] };
  try {
    for (const name of Object.keys(pins)) {
      const path = require.resolve(name + '/package.json');
      const bytes = await readFile(path);
      evidence.versions[name] = JSON.parse(bytes).version;
      evidence.sources.push({ path, sha256: sha(bytes) });
    }
    checkPins(evidence.versions);
    evidence.platform = { platform: process.platform, arch: process.arch };
    const manifest = JSON.stringify(await installationManifest(join(repo, 'node_modules')), null, 2) + '\n';
    await writeFile(join(output, 'installation.json'), manifest);
    evidence.installationSha256 = sha(manifest);
    evidence.nodeBinarySha256 = sha(await readFile(process.execPath));
    evidence.topology = await topology();
    for (const path of [fileURLToPath(import.meta.url), join(repo, 'package-lock.json')]) evidence.sources.push({ path, sha256: sha(await readFile(path)) });
    const { rolldown } = await import('rolldown');
    const bundle = join(output, 'negative-worker.mjs');
    const build = await rolldown({ input: entry, platform: 'node', treeshake: false, plugins: [{
      name: 'explicit-v5-negative-boundary',
      resolveId(source, importer) {
        const url = import.meta.resolve(source, importer ? pathToFileURL(importer).href : import.meta.url);
        const path = url.startsWith('file:') ? fileURLToPath(url) : url;
        if (selected.has(path)) return path;
        evidence.externals.push({ source, importer, resolved: url });
        return { id: url, external: true };
      },
      async transform(code, id) {
        if (id === '\0rolldown/runtime.js') {
          await writeFile(join(output, 'rolldown-runtime.js'), code);
          evidence.sources.push({ path: 'rolldown-runtime.js', virtualId: id, sha256: sha(code) });
          return null;
        }
        assert.ok(selected.has(id), `Unexpected inline module: ${id}`);
        evidence.sources.push({ path: id, sha256: sha(code) });
        const transformed = await preserveURL(code, id);
        evidence.edits.push({ id, sites: transformed.sites });
        return transformed;
      },
    }] });
    try { await build.write({ file: bundle, format: 'es', codeSplitting: false, sourcemap: true }); } finally { await build.close(); }
    evidence.sources.push({ path: api, sha256: sha(await readFile(api)) });
    evidence.bundle = { sha256: sha(await readFile(bundle)), mapSha256: sha(await readFile(bundle + '.map')) };
    const root = join(output, 'fixture'); await mkdir(root);
    await symlink(join(repo, 'node_modules'), join(root, 'node_modules'));
    const fixture = `import { test, expect } from 'vitest';\nimport { appendFileSync } from 'node:fs';\nimport { randomUUID } from 'node:crypto';\nconst selector = process.env.OXIMUT_ACTIVE_MUTANT;\nappendFileSync(${JSON.stringify(join(output, 'observations.jsonl'))}, JSON.stringify({pid:process.pid,realm:randomUUID(),selector})+'\\n');\ntest('collector identity and import-time selector', () => { expect(['','A','B']).toContain(selector); });\n`;
    await writeFile(join(root, 'identity.test.mjs'), fixture);
    evidence.fixtureSha256 = sha(fixture);
    const stock = await invoke({ root, result: join(output, 'stock/result.json') }, join(output, 'stock'));
    assert.equal(stock.reports.length, 4); assert.equal(new Set(stock.workers.map(w => w.pid)).size, 4);
    assert.ok(stock.reports.every(r => r.errors.length === 0 && r.tests.length === 1 && r.tests[0].state === 'passed'));
    const observations = (await readFile(join(output, 'observations.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.deepEqual(observations.map(x => x.selector), ['', 'A', 'B', 'A']);
    assert.equal(new Set(observations.map(x => x.realm)).size, 4);
    evidence.stock = stock;
    const candidate = await invoke({ root, bundle, result: join(output, 'candidate/result.json') }, join(output, 'candidate'));
    evidence.candidate = candidate;
    assert.equal(sha(await readFile(join(root, 'identity.test.mjs'))), evidence.fixtureSha256, 'Fixture changed during execution');
    assert.equal(candidate.workers.length, 1);
    const messages = candidate.reports.flatMap(r => r.errors).map(e => e.message).join('\n');
    assert.match(messages, /Vitest failed to find (?:the runner|the current suite|the default suite)/);
    evidence.classification = 'no-go: inlined collector duplicates native test API state';
    evidence.notRun = 'Broader contamination/mocks/hooks/late-work/throw/deadline oracles and scoring: stopped at first collector identity failure. No structural qualification.';
  } catch (error) {
    evidence.failure = { message: error.message, stack: error.stack };
    process.exitCode = 1;
  } finally {
    await writeFile(join(output, 'evidence.json'), JSON.stringify(evidence, null, 2) + '\n');
  }
  console.log(JSON.stringify({ output, classification: evidence.classification, failure: evidence.failure?.message }));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv[2] === '--controller') await controller(JSON.parse(await readFile(process.argv[3], 'utf8')));
  else { assert.equal(process.argv.length, 3, 'Usage: node --experimental-import-meta-resolve probe.mjs NEW_OUTPUT_DIR'); await main(resolve(process.argv[2])); }
}
