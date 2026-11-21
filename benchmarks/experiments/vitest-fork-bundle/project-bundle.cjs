// Colord-only probe: serve one bundled module per test file via the existing
// Vite evaluator, retaining fresh workers, resident mutation code, and test URLs.
const directory = process.env.OXIMUT_PROJECT_BUNDLE_DIR;
if (directory && process.argv[1]?.endsWith('/m2-worker.ts')) {
  const assert = require('node:assert/strict');
  const { execFileSync } = require('node:child_process');
  const fs = require('node:fs');
  const path = require('node:path');
  const { registerHooks } = require('node:module');
  const compiler = path.resolve(__dirname, '../../../node_modules/.bin/tsc');
  const projectFactories = process.env.OXIMUT_PROJECT_FACTORY_SNAPSHOT === '1';
  const shareProjectImage = process.env.OXIMUT_SHARE_PROJECT_IMAGE === '1';
  let barrel;

  async function frameworkSeed(base) {
    const filename = path.join(base, 'seed.offline.cjs');
    if (process.env.OXIMUT_SNAPSHOT_MUTANTS_ONLY === '1') {
      const {setTimeout} = require('node:timers/promises');
      while (!fs.existsSync(filename)) {
        if (fs.existsSync(path.join(base, 'seed.failed'))) throw new Error('Framework seed generation failed');
        await setTimeout(5);
      }
    }
    return fs.readFileSync(filename, 'utf8');
  }

  async function buildProjectImage(factories) {
    const base = path.dirname(process.env.OXIMUT_SNAPSHOT_BLOB);
    const seedPath = path.join(base, 'project-seed-' + process.pid + '.cjs');
    const seed = await frameworkSeed(base);
    const entries = `\nglobalThis[Symbol.for('oximut.diagnostic.projectFactories')] = new Map([
${factories.join(',\n')}
]);
for (const factory of globalThis[Symbol.for('oximut.diagnostic.projectFactories')].values()) factory();
`;
    const key = shareProjectImage
      ? require('node:crypto').createHash('sha256').update(seed).update(entries).digest('hex')
      : process.pid;
    const image = path.join(directory, `project-${key}.blob`);
    if (shareProjectImage && fs.existsSync(image)) {
      globalThis[Symbol.for('oximut.diagnostic.projectImage')] = image;
      return;
    }
    const temporaryImage = path.join(directory, `project-build-${process.pid}.blob`);
    try {
      fs.writeFileSync(seedPath, seed + entries, {flag: 'wx'});
      execFileSync(process.execPath, ['--build-snapshot', `--snapshot-blob=${temporaryImage}`, seedPath],
        {env: {...process.env, NODE_OPTIONS: ''}, stdio: ['ignore', 'inherit', 'inherit']});
      fs.renameSync(temporaryImage, image);
      globalThis[Symbol.for('oximut.diagnostic.projectImage')] = image;
    } finally {
      fs.rmSync(seedPath, {force: true});
      fs.rmSync(temporaryImage, {force: true});
    }
  }

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
    const factories = [];
    for (const name of files) {
      const input = path.join(root, 'tests', name);
      const build = await rolldown({
        cwd: root,
        input,
        platform: projectFactories ? 'neutral' : 'node',
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
          format: projectFactories ? 'cjs' : 'es',
          polyfillRequire: !projectFactories,
          sourcemap: 'hidden',
          sourcemapPathTransform: (source, mapPath) => path.resolve(path.dirname(mapPath), source),
          codeSplitting: false,
        });
        const chunks = output.filter(item => item.type === 'chunk');
        assert.equal(chunks.length, 1);
        const [chunk] = chunks;
        assert(chunk.isEntry && chunk.map);
        const members = Object.keys(chunk.modules);
        const observation = (factory = false) =>
          `globalThis[Symbol.for('oximut.diagnostic.bundledEntry')]?.(${JSON.stringify(input)},${JSON.stringify(members)},${factory});\n`;
        // Appending the observation avoids shifting existing mappings.
        outputs.set(input, {code: chunk.code + '\n' + observation(), map: JSON.stringify(chunk.map)});
        if (projectFactories) {
          assert.deepEqual({imports: chunk.imports, dynamicImports: chunk.dynamicImports, exports: chunk.exports},
            {imports: [], dynamicImports: [], exports: []}, `Project factories require a closed graph for ${name}`);
          assert(!chunk.code.includes('import.meta'), 'Project factories require context-free module metadata');
          const body = chunk.code.replace(
            /process[.]env[.]OXIMUT_ACTIVE_MUTANT *=== *["']([0-9a-f]{64})["']/g,
            (original, id) => `activeSelector === "${id}"`.padEnd(original.length));
          factories.push(`[${JSON.stringify(input)}, function (activeSelector) {
  "use strict";
  if (activeSelector === undefined) return;
${body}
${observation(true)}
}]`);
        }
      } finally {
        await build.close();
      }
    }
    if (projectFactories) await buildProjectImage(factories);
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
      if (process.env.OXIMUT_WINDOWED_LANES === '1') {
        const run = 'async run(batch: Request): Promise<Result> {';
        source = replaceOnce(source, run, run + `
    const windowWidth = batch.workers;
    if (windowWidth && batch.lanes && batch.lanes.length > windowWidth) {
      const results: NonNullable<Result['lanes']> = [];
      const messageLimit = Math.min(2048, Math.floor((128 * 1024) / (batch.lanes.length * 8)));
      for (let index = 0; index < batch.lanes.length; index += windowWidth) {
        const lanes = batch.lanes.slice(index, index + windowWidth);
        const result = await this.run({...batch, lanes});
        if (!result.lanes || result.lanes.length !== lanes.length)
          throw new Error('Incomplete controller-side window');
        for (const lane of result.lanes)
          results.push({...lane, errors: lane.errors.map(error => error.slice(0, messageLimit))});
      }
      return {
        outcome: 'skipped',
        tests: {passed: 0, failed: 0, skipped: 0},
        errors: [],
        ...(batch.resident ? {resident: batch.resident.length} : {}),
        lanes: results,
      };
    }
`);
      }
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
