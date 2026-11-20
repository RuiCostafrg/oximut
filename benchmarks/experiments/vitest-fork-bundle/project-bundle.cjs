// Colord-only probe: serve one bundled module per test file via the existing
// Vite evaluator, retaining fresh workers, resident mutation code, and test URLs.
const directory = process.env.OXIMUT_PROJECT_BUNDLE_EVIDENCE;
if (directory && process.argv[1]?.endsWith('/m2-worker.ts')) {
  const assert = require('node:assert/strict');
  const { execFileSync } = require('node:child_process');
  const fs = require('node:fs');
  const path = require('node:path');
  const { registerHooks } = require('node:module');
  const compiler = path.resolve(__dirname, '../../../node_modules/.bin/tsc');
  let barrel;

  globalThis[Symbol.for('oximut.diagnostic.projectBundle')] = async (root, residents) => {
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'package.json'))).name,
      'oximut-benchmark-colord');
    const entry = path.join(root, 'src/index.ts');
    assert(!residents.has(entry), 'the type-only barrel must not carry a mutant');
    if (!barrel) {
      // TypeScript resolves the fixture's type-only re-exports in this file.
      const emitted = path.join(directory, 'emit-' + process.pid);
      execFileSync(compiler, ['--noCheck', '--target', 'esnext', '--module', 'esnext',
        '--moduleResolution', 'bundler', '--rootDir', root, '--outDir', emitted, entry], {cwd: root});
      barrel = fs.readFileSync(path.join(emitted, 'src/index.js'), 'utf8');
    }
    const { rolldown } = await import('rolldown');
    const files = fs.readdirSync(path.join(root, 'tests')).filter(name => name.endsWith('.test.ts')).sort();
    assert.equal(files.length, 4);
    const outputs = new Map();
    for (const name of files) {
      const input = path.join(root, 'tests', name);
      const build = await rolldown({
        cwd: root,
        input,
        platform: 'node',
        treeshake: false,
        resolve: {extensions: ['.ts', '.js']},
        external: id => !id.startsWith('.') && !path.isAbsolute(id),
        plugins: [{
          name: 'oximut-resident-overlay',
          load(id) {
            if (id === entry) return {code: barrel, moduleType: 'js'};
            const module = residents.get(id);
            if (module) return {code: module.code, map: module.map, moduleType: 'ts'};
          },
        }],
      });
      try {
        const {output} = await build.generate({
          file: input.replace(/\.ts$/, '.js'),
          format: 'es',
          sourcemap: 'hidden',
          sourcemapPathTransform: (source, mapPath) => path.resolve(path.dirname(mapPath), source),
          codeSplitting: false,
        });
        const chunks = output.filter(item => item.type === 'chunk');
        assert.equal(chunks.length, 1);
        const [chunk] = chunks;
        assert(chunk.isEntry && chunk.map);
        const members = Object.keys(chunk.modules);
        const observe = `globalThis[Symbol.for('oximut.diagnostic.bundledEntry')]?.(${JSON.stringify(input)},${JSON.stringify(members)});\n`;
        // Appending the observation avoids shifting existing mappings.
        outputs.set(input, {code: chunk.code + '\n' + observe, map: JSON.stringify(chunk.map)});
      } finally {
        await build.close();
      }
    }
    return outputs;
  };

  function replaceOnce(source, marker, replacement) {
    assert.equal(source.split(marker).length, 2, `Unexpected SharedLanes source near ${marker}`);
    return source.replace(marker, replacement);
  }
  registerHooks({
    load(url, context, nextLoad) {
      const result = nextLoad(url, context);
      if (!url.endsWith('/packages/vitest-adapter/shared-lanes.ts')) return result;
      let source = String(result.source);
      const klass = 'export class SharedLanes {';
      source = replaceOnce(source, klass, klass + '\n  private bundledEntries = new Map();');
      const publish = 'this.modules = modules;';
      source = replaceOnce(source, publish, publish + `
        if (pool !== 'forks') throw new Error('Project bundle diagnostic requires forks');
        if (changed || !this.context)
          this.bundledEntries = await globalThis[Symbol.for('oximut.diagnostic.projectBundle')](batch.root, modules);
      `);
      const transform = 'const module = this.modules.get(resolve(id.split(\'?\')[0]));';
      source = replaceOnce(source, transform, `
        const bundled = this.bundledEntries.get(resolve(id.split('?')[0]));
        if (bundled) return bundled;
        ${transform}
      `);
      return {...result, source};
    },
  });
}
