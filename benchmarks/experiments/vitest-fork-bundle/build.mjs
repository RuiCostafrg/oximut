// Disposable experiment. Keep stock package files intact and route only fresh
// fork imports through entry facades that share the bundled runtime.
import { readdir, writeFile } from 'node:fs/promises';
import { isBuiltin } from 'node:module';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { rolldown } from 'rolldown';
import { preserveMetadata } from './metadata.mjs';

const dist = fileURLToPath(new URL('./dist/', import.meta.resolve('vitest/package.json')));
const output = resolve(process.argv[2]);
const files = (await readdir(dist)).filter((file) =>
  file.endsWith('.js') && !['browser.js', 'cli.js', 'node.js'].includes(file));
files.push('workers/forks.js');
const input = Object.fromEntries(files.map((file) => [file.slice(0, -3), join(dist, file)]));
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
  sourcemap: 'hidden',
});
const entries = Object.fromEntries(result.output.filter((entry) => entry.type === 'chunk' && entry.isEntry)
  .map((entry) => [pathToFileURL(entry.facadeModuleId).href, pathToFileURL(join(output, entry.fileName)).href]));
await writeFile(join(output, 'routes.json'), JSON.stringify(entries, null, 2) + '\n');
