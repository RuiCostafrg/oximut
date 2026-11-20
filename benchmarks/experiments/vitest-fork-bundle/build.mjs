// Disposable experiment. Keep stock package files intact and route only fresh
// fork imports through entry facades that share the bundled runtime.
import { readdir, writeFile } from 'node:fs/promises';
import { isBuiltin } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { RolldownMagicString, rolldown } from 'rolldown';
import { parseAst } from 'rolldown/parseAst';

const dist = fileURLToPath(new URL('./dist/', import.meta.resolve('vitest/package.json')));
const output = resolve(process.argv[2]);
const files = (await readdir(dist)).filter((file) =>
  file.endsWith('.js') && !['browser.js', 'cli.js', 'node.js'].includes(file));
files.push('workers/forks.js');
const input = Object.fromEntries(files.map((file) => [file.slice(0, -3), join(dist, file)]));
function preserveMetadata(source, id) {
  const origin = JSON.stringify(pathToFileURL(id).href);
  const edits = new RolldownMagicString(source);
  function visit(node, parent) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'MemberExpression' && node.object.type === 'MetaProperty'
        && node.object.meta.name === 'import' && node.object.property.name === 'meta') {
      const key = node.computed ? node.property.value : node.property.name;
      const metadata = {url: origin, dirname: JSON.stringify(dirname(id)), filename: JSON.stringify(id)};
      if (key in metadata) edits.overwrite(node.start, node.end, metadata[key]);
      else if (key === 'resolve' && parent?.callee === node) {
        edits.overwrite(node.start, node.end, `((id, parent = ${origin}) => import.meta.resolve(id, parent))`);
      }
    }
    if (Array.isArray(node)) {
      for (const child of node) visit(child, parent);
    } else {
      for (const child of Object.values(node)) visit(child, node);
    }
  }
  visit(parseAst(source, null, id));
  return {code: edits.toString(), map: edits.generateMap({hires: true}).toString()};
}

const bundle = await rolldown({
  input,
  platform: 'node',
  external: (source) => isBuiltin(source) || /^[^./#]/.test(source),
  plugins: [{
    name: 'original-vitest-metadata',
    transform(source, id) {
      if (!id.startsWith(dist) || !source.includes('import.meta')) return;
      return preserveMetadata(source, id);
    },
  }],
});
const result = await bundle.write({
  dir: output, format: 'esm',
  entryFileNames: '[name].mjs', chunkFileNames: 'chunks/[name]-[hash].mjs',
  sourcemap: true,
});
const entries = Object.fromEntries(result.output.filter((entry) => entry.type === 'chunk' && entry.isEntry)
  .map((entry) => [pathToFileURL(entry.facadeModuleId).href, pathToFileURL(join(output, entry.fileName)).href]));
await writeFile(join(output, 'routes.json'), JSON.stringify(entries, null, 2) + '\n');
