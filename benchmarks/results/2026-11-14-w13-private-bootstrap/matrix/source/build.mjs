import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parse } from '@babel/parser';
import MagicString from 'magic-string';

export const require = createRequire(import.meta.url);
export const repo = fileURLToPath(new URL('../../../', import.meta.url));
export const hash = data => createHash('sha256').update(data).digest('hex');
export const pins = { vitest: '5.1.0', vite: '8.3.5', '@vitest/coverage-v8': '5.1.0', rolldown: '1.2.15', '@babel/parser': '8.0.10', 'magic-string': '1.4.5' };
export const dist = dirname(require.resolve('vitest/node'));
export const entry = join(dist, 'workers/forks.js');

export function checkVersions(actual, node = process.version) {
  assert.equal(node, 'v26.3.1', 'Unsupported Node');
  assert.deepEqual(actual, pins, 'Unsupported dependencies');
}

function walk(node, visitor, parent) {
  if (!node || typeof node !== 'object') return;
  visitor(node, parent);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'start', 'end', 'extra', 'comments', 'tokens'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(child => walk(child, visitor, node));
    else if (value && typeof value === 'object') walk(value, visitor, node);
  }
}
const ast = code => parse(code, { sourceType: 'module', createImportExpressions: true });
export function resolveOriginal(source, importer) {
  assert.ok(process.execArgv.includes('--experimental-import-meta-resolve'), 'Two-argument import.meta.resolve flag required');
  return import.meta.resolve(source, pathToFileURL(importer).href);
}

export function transformPrivate(code, id) {
  const output = new MagicString(code), edits = [];
  walk(ast(code), (node, parent) => {
    if (node.type === 'MetaProperty') {
      assert.ok(id.endsWith('/chunks/base.Cof-UHJf.js') && parent?.type === 'MemberExpression' && parent.object === node && !parent.computed && parent.property.name === 'url', `Unsupported metadata ${id}:${node.start}`);
      output.overwrite(parent.start, parent.end, JSON.stringify(pathToFileURL(id).href));
      edits.push({ start: parent.start, end: parent.end, source: code.slice(parent.start, parent.end) });
    }
    if (node.type === 'ImportExpression') {
      assert.equal(node.source.type, 'StringLiteral', `Unsupported computed import ${id}:${node.start}`);
      assert.ok(!node.options && !node.phase, `Unsupported import options/phase ${id}:${node.start}`);
    }
    if (['ImportDeclaration', 'ExportAllDeclaration', 'ExportNamedDeclaration'].includes(node.type)) assert.ok(!node.attributes?.length && !node.assertions?.length && !node.phase, `Unsupported import attributes/phase ${id}:${node.start}`);
    if (node.type === 'Identifier' && ['require', '__dirname', '__filename', 'eval'].includes(node.name)) throw Error(`Unsupported context syntax ${node.name} ${id}:${node.start}`);
    if (node.type === 'CallExpression' && node.callee?.name === 'Function') throw Error(`Unsupported Function constructor ${id}:${node.start}`);
  });
  return { code: output.toString(), map: output.generateMap({ source: id, includeContent: true, hires: true }), edits };
}

export async function auditTopology() {
  const approved = JSON.parse(await readFile(new URL('./audited-inputs.json', import.meta.url), 'utf8'));
  const approvedMap = new Map(approved.map(row => [row.path, row]));
  const modules = new Map();
  async function closure(path, visited = new Set()) {
    if (visited.has(path)) return visited;
    visited.add(path);
    if (!modules.has(path)) {
      const code = await readFile(path, 'utf8'), edges = [], uncertain = [];
      walk(ast(code), node => {
        if (['ImportDeclaration', 'ExportAllDeclaration', 'ExportNamedDeclaration', 'ImportExpression'].includes(node.type) && node.source?.type === 'StringLiteral') {
          try { edges.push({ kind: node.type, source: node.source.value, resolved: resolveOriginal(node.source.value, path) }); }
          catch (error) {
            // Optional lazy environments are not dependencies of this Node-only candidate.
            assert.equal(node.type, 'ImportExpression', `Missing static dependency ${node.source.value} from ${path}`);
            const optional = new Set(['@edge-runtime/vm', 'happy-dom', 'jsdom', '@opentelemetry/api']);
            assert.ok(optional.has(node.source.value), `Unknown missing lazy dependency ${node.source.value}`);
            edges.push({ kind: node.type, source: node.source.value, unsupportedOptional: error.message });
          }
        }
        if (node.type === 'MetaProperty' || (node.type === 'ImportExpression' && node.source.type !== 'StringLiteral')) uncertain.push({ kind: node.type, start: node.start, source: code.slice(node.start, node.end) });
      });
      const rel = relative(dist, path), expected = approvedMap.get(rel);
      // index.js is public-only; all worker-reachable modules have audited hashes.
      if (rel !== 'index.js') {
        assert.ok(expected, `Unaudited Vitest module ${rel}`);
        assert.equal(hash(code), expected.sha256, `Source changed: ${rel}`);
      }
      modules.set(path, { path, relative: rel, bytes: Buffer.byteLength(code), sha256: hash(code), edges, uncertain });
    }
    for (const edge of modules.get(path).edges) if (edge.resolved?.startsWith(pathToFileURL(dist + '/').href)) await closure(fileURLToPath(edge.resolved), visited);
    return visited;
  }
  const worker = await closure(entry), api = await closure(join(dist, 'index.js'));
  const shared = [...worker].filter(id => api.has(id));
  const selected = new Set(approved.filter(row => row.inline).map(row => join(dist, row.path)));
  assert.deepEqual([...worker].sort(), approved.map(row => join(dist, row.path)).filter(id => id !== join(dist, 'index.js')).sort(), 'Worker graph changed');
  for (const id of shared) assert.ok(!selected.has(id), `Native API owner in allowlist ${id}`);
  // Native-to-inline ingress would cause duplicate module evaluation/identity.
  for (const [id, module] of modules) if (!selected.has(id)) {
    for (const edge of module.edges) if (edge.resolved?.startsWith('file:')) assert.ok(!selected.has(fileURLToPath(edge.resolved)), `Native ingress into candidate: ${id} -> ${edge.resolved}`);
  }
  return { worker: [...worker].sort(), publicAPI: [...api].sort(), shared: shared.sort(), selected: [...selected].sort(), modules: [...modules.values()], limitation: 'Literal Vitest ESM graph. Computed imports and external dependencies remain native, not rewritten or declared universally supported.' };
}

export async function manifest(folder) {
  const rows = [];
  for (const item of (await readdir(folder, { withFileTypes: true })).sort((a,b) => a.name.localeCompare(b.name))) {
    if (['.bin', '.cache', '.vite'].includes(item.name)) continue;
    const path = join(folder, item.name);
    if (item.isDirectory()) rows.push(...await manifest(path));
    else if (item.isFile()) rows.push({ path, sha256: hash(await readFile(path)) });
    else assert.fail(`Unsupported provenance entry ${path}`);
  }
  return rows;
}

export async function buildCandidate(output, evidence) {
  evidence.topology = await auditTopology();
  const selected = new Set(evidence.topology.selected);
  const { rolldown } = await import('rolldown');
  const loaded = new Set();
  evidence.externals = []; evidence.transforms = [];
  const build = await rolldown({ input: entry, platform: 'node', treeshake: false, plugins: [{
    name: 'audited-private-bootstrap',
    resolveId(source, importer) {
      const url = resolveOriginal(source, importer || fileURLToPath(import.meta.url));
      const id = url.startsWith('file:') ? fileURLToPath(url) : url;
      if (selected.has(id)) return id;
      evidence.externals.push({ source, importer, resolved: url });
      return { id: url, external: true };
    },
    resolveDynamicImport(source, importer) {
      assert.equal(typeof source, 'string', `Unknown computed import in ${importer}`);
      const url = resolveOriginal(source, importer);
      assert.ok(!selected.has(fileURLToPath(url)), 'Dynamic target must stay native/lazy');
      evidence.externals.push({ dynamic: true, source, importer, resolved: url });
      return { id: url, external: true };
    },
    async transform(code, id) {
      if (id === '\0rolldown/runtime.js') {
        await writeFile(join(output, 'rolldown-runtime.js'), code);
        evidence.virtualRuntime = { id, sha256: hash(code) }; return null;
      }
      assert.ok(selected.has(id), `Unaudited inline module ${id}`);
      loaded.add(id);
      const transformed = transformPrivate(code, id);
      evidence.transforms.push({ id, sourceSha256: hash(code), edits: transformed.edits });
      return transformed;
    },
  }] });
  const bundle = join(output, 'private-worker.mjs');
  try { await build.write({ file: bundle, format: 'es', codeSplitting: false, sourcemap: true }); } finally { await build.close(); }
  assert.deepEqual([...loaded].sort(), [...selected].sort(), 'Candidate allowlist not fully loaded');
  evidence.bundle = { path: bundle, sha256: hash(await readFile(bundle)), mapSha256: hash(await readFile(bundle + '.map')) };
  evidence.coverage = { modules: selected.size, sourceBytes: evidence.topology.modules.filter(m => selected.has(m.path)).reduce((sum,m) => sum + m.bytes, 0), workerModules: evidence.topology.worker.length, workerSourceBytes: evidence.topology.modules.filter(m => evidence.topology.worker.includes(m.path)).reduce((sum,m) => sum + m.bytes, 0) };
  return bundle;
}
