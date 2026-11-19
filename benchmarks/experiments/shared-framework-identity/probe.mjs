// Bounded, install-bound structural experiment; never a product entrypoint.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parse } from '@babel/parser';
import { rolldown } from 'rolldown';
import MagicString from 'magic-string';
const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '../../..');
const dist = dirname(require.resolve('vitest/node'));
const url = p => pathToFileURL(p).href;
const sha = b => createHash('sha256').update(b).digest('hex');
export function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc','start','end','extra','comments','tokens'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(x => walk(x, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
export async function build(output) {
  assert.equal(process.version, 'v26.3.1');
  assert.equal(process.platform, 'linux');
  assert.deepEqual(process.execArgv, ['--experimental-import-meta-resolve']);
  await mkdir(output); // Never overwrite evidence.
  const evidence = { classification: 'incomplete', root, output, node: process.version, platform: process.platform, arch: process.arch, execArgv: process.execArgv, nodeHash: sha(await readFile(process.execPath)), versions: {}, modules: [], externalEdges: [], buildToolsRunInChild: false };
  try {
    for (const [name, version] of Object.entries({ vitest:'5.1.0', vite:'8.3.5', '@vitest/coverage-v8':'5.1.0', rolldown:'1.2.15', '@babel/parser':'8.0.10', 'magic-string':'1.4.5' })) {
      const path = require.resolve(name + '/package.json');
      const bytes = await readFile(path);
      assert.equal(JSON.parse(bytes).version, version);
      evidence.versions[name] = { version, path, sha256: sha(bytes) };
    }
    for (const p of ['package-lock.json','packages/vitest-adapter/lane-runner.mjs','packages/vitest-adapter/m3-setup.ts','benchmarks/experiments/shared-framework-identity/probe.mjs']) {
      evidence.modules.push({ path: join(root,p), sha256: sha(await readFile(join(root,p))), role:'source-input' });
    }
    const graph = new Map();
    async function scan(path) {
      if (graph.has(path)) return;
      const code = await readFile(path, 'utf8');
      const ast = parse(code, { sourceType:'module', createImportExpressions:true });
      const row = { path, sha256:sha(code), edges:[], context:[] };
      graph.set(path, row);
      walk(ast, node => {
        if (['ImportDeclaration','ExportNamedDeclaration','ExportAllDeclaration','ImportExpression'].includes(node.type) && node.source?.type === 'StringLiteral') {
          let resolved;
          try { resolved = import.meta.resolve(node.source.value, url(path)); }
          catch (e) { row.edges.push({ source:node.source.value, error:String(e) }); return; }
          row.edges.push({ kind:node.type, source:node.source.value, resolved });
        }
        if ((node.type === 'MetaProperty' && node.meta.name === 'import') || (node.type === 'ImportExpression' && node.source.type !== 'StringLiteral') || (node.type === 'CallExpression' && node.callee?.name === 'require')) row.context.push({ type:node.type, line:node.loc.start.line, source:code.slice(node.start,node.end) });
      });
      for (const edge of row.edges) if (edge.resolved?.startsWith(url(dist + '/'))) await scan(fileURLToPath(edge.resolved));
    }
    const worker = join(dist,'workers/forks.js'), api = join(dist,'index.js');
    await scan(worker); await scan(api);
    // Each metadata-free original becomes its own exact-export input. Native
    // context-sensitive modules retain their original URL and resolver context.
    const selected = new Set([...graph.values()].filter(x => !x.context.length).map(x=>x.path));
    assert(selected.has(worker)); assert(selected.has(api));
    const apiAST = parse(await readFile(api,'utf8'), {sourceType:'module'});
    const owners = {};
    for (const node of apiAST.program.body) if (node.type === 'ExportNamedDeclaration' && node.source) for (const s of node.specifiers) owners[s.exported.name] = fileURLToPath(import.meta.resolve(node.source.value,url(api)));
    for (const name of ['TestRunner','expect','vi','test']) assert(selected.has(owners[name]), `Unbundleable required owner ${name}`);
    evidence.owners = owners;
    evidence.topology = [...graph.values()];
    evidence.selected = [...selected];
    const input = Object.fromEntries([...selected].sort().map((p,i)=>['owner-'+i,p]));
    const bundler = await rolldown({ input, platform:'node', treeshake:false, preserveEntrySignatures:'strict', plugins:[{
      name:'closed-shared-identity',
      resolveId(source, importer) {
        if (selected.has(source)) return source;
        assert(!source.startsWith('\0'), `Unknown virtual import ${source}`);
        const resolved = import.meta.resolve(source, importer ? url(importer) : import.meta.url);
        const p = resolved.startsWith('file:') ? fileURLToPath(resolved) : resolved;
        if (selected.has(p)) return p;
        evidence.externalEdges.push({source, importer, resolved});
        return {id:resolved, external:true};
      },
      async transform(code,id) {
        if (id === '\0rolldown/runtime.js') {
          evidence.modules.push({path:id, sha256:sha(code), code}); return null;
        }
        assert(selected.has(id));
        const ast = parse(code, {sourceType:'module', createImportExpressions:true});
        walk(ast, n=>{ assert(!(n.type === 'MetaProperty' && n.meta.name === 'import')); if(n.type==='ImportExpression') assert.equal(n.source.type,'StringLiteral'); });
        const edit = new MagicString(code);
        edit.prepend(`globalThis.__sharedEvaluations ??= {}; globalThis.__sharedEvaluations[${JSON.stringify(url(id))}] = (globalThis.__sharedEvaluations[${JSON.stringify(url(id))}] || 0) + 1;\n`);
        return {code:edit.toString(), map:edit.generateMap({hires:true,source:id,includeContent:true})};
      }
    }] });
    let generated;
    try { generated = await bundler.write({dir:join(output,'bundle'),format:'es',sourcemap:true,entryFileNames:'[name].mjs',chunkFileNames:'shared-[hash].mjs'}); }
    finally { await bundler.close(); }
    const facades = {};
    evidence.outputs = [];
    for (const file of generated.output) {
      const p = join(output,'bundle',file.fileName);
      evidence.outputs.push({path:p,sha256:sha(await readFile(p)), facade:file.facadeModuleId, exports:file.exports, imports:file.imports, dynamicImports:file.dynamicImports, modules:file.modules && Object.keys(file.modules)});
      if (file.facadeModuleId && selected.has(file.facadeModuleId)) facades[url(file.facadeModuleId)] = url(p);
    }
    assert.equal(Object.keys(facades).length,selected.size,'Missing exact original facade');
    evidence.facades = facades;
    evidence.classification = 'built-not-qualified';
  } catch (error) { evidence.classification='STOP'; evidence.failure={message:error.message,stack:error.stack}; throw error; }
  finally { await writeFile(join(output,'build.json'),JSON.stringify(evidence,null,2)+'\n'); }
  return evidence;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) await build(resolve(process.argv[2]));
