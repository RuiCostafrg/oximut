// colord-scale diagnostic only. Preserve test and source URLs while serving
// precompiled ESM and current resident code to Vitest's native Node runner.
const path = require('node:path');
const fs = require('node:fs');
const { registerHooks } = require('node:module');
const { fileURLToPath, pathToFileURL } = require('node:url');
const compilerRoot = path.resolve(__dirname, '../../..');
const table = process.env.OXIMUT_NATIVE_MODULE_TABLE;
const evidence = process.env.OXIMUT_NATIVE_MODULE_EVIDENCE;

if (table && process.argv[1]?.endsWith('/vitest/dist/workers/forks.js')) {
  const {base, resident} = JSON.parse(fs.readFileSync(table, 'utf8'));
  const modules = { ...base, ...resident };
  const loaded = new Set();
  registerHooks({
    resolve(specifier, context, nextResolve) {
      if (!specifier.startsWith('.') || !context.parentURL?.startsWith('file:'))
        return nextResolve(specifier, context);
      const parent = new URL(context.parentURL);
      const parentFile = fileURLToPath(parent);
      if (!Object.hasOwn(modules, parentFile)) return nextResolve(specifier, context);
      const file = fileURLToPath(new URL(specifier, parent));
      for (const candidate of [file, file + '.ts', path.join(file, 'index.ts')]) {
        if (!Object.hasOwn(modules, candidate)) continue;
        const url = pathToFileURL(candidate);
        // A single fork can cover several selected files. Give each test
        // file an independent project module cache as Vitest isolate does.
        url.searchParams.set('oximutScope', parent.searchParams.get('oximutScope') ?? parentFile);
        return nextResolve(url.href, context);
      }
      return nextResolve(specifier, context);
    },
    load(url, context, nextLoad) {
      if (!url.startsWith('file:')) return nextLoad(url, context);
      const file = fileURLToPath(url);
      if (!Object.hasOwn(modules, file)) return nextLoad(url, context);
      if (Object.hasOwn(resident, file)) loaded.add(file);
      return {format: 'module', source: modules[file], shortCircuit: true};
    },
  });
  const send = process.send;
  process.send = function (message, ...args) {
    if (message?.__vitest_worker_response__ === true && message.type === 'testfileFinished') {
      fs.writeFileSync(path.join(evidence, `native-${process.pid}.json`), JSON.stringify({
        pid: process.pid, ppid: process.ppid,
        selector: process.env.OXIMUT_ACTIVE_MUTANT, residentsLoaded: [...loaded],
      }), {flag: 'wx'});
    }
    return Reflect.apply(send, this, [message, ...args]);
  };
}

if (evidence && process.argv[1]?.endsWith('/m2-worker.ts')) {
  const assert = require('node:assert/strict');
  const { execFileSync } = require('node:child_process');
  const scratch = path.join(evidence, 'controller-' + process.pid);
  const manifest = path.join(scratch, 'modules.json');
  let base;
  const key = Symbol.for('oximut.diagnostic.nativeModuleTable');
  globalThis[key] = async (root, modules) => {
    const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
    assert.equal(packageJson.name, 'oximut-benchmark-colord');
    if (!base) {
      fs.mkdirSync(scratch);
      const outDir = path.join(scratch, 'compiled');
      const tsconfig = path.join(scratch, 'tsconfig.json');
      fs.writeFileSync(tsconfig, JSON.stringify({
        compilerOptions: {
          target: 'esnext', module: 'esnext', moduleResolution: 'bundler',
          rootDir: root, outDir, noCheck: true,
        },
        include: ['src/**/*.ts', 'tests/**/*.ts'].map((name) => path.join(root, name)),
      }));
      execFileSync(path.join(compilerRoot, 'node_modules/.bin/tsc'), ['-p', tsconfig], {cwd: root});
      base = Object.fromEntries(fs.readdirSync(outDir, {recursive: true}).filter((name) => name.endsWith('.js'))
        .map((name) => [path.join(root, name.slice(0, -3) + '.ts'), fs.readFileSync(path.join(outDir, name), 'utf8')]));
    }
    const { transformSync } = await import('rolldown/utils');
    const resident = {};
    for (const [file, module] of modules) {
      assert(Object.hasOwn(base, file));
      const compiled = transformSync(file, module.code, {lang: 'ts'});
      assert.deepEqual(compiled.errors, []);
      resident[file] = compiled.code;
    }
    // SharedLanes has drained the previous batch before replacing this table.
    fs.writeFileSync(manifest, JSON.stringify({base, resident}));
    return manifest;
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
      const publish = 'this.modules = modules;';
      source = replaceOnce(source, publish, publish + `
        if (pool !== 'forks') throw new Error('Native module diagnostic requires forks');
        const nativeModuleTable = ${JSON.stringify(manifest)};
        if (changed || !this.context)
          await globalThis[Symbol.for('oximut.diagnostic.nativeModuleTable')](batch.root, modules);
      `);
      const create = "const context = await createVitest('test', {";
      source = replaceOnce(source, create, create + '\n        experimental: {viteModuleRunner: false, nodeLoader: false},');
      const selector = 'OXIMUT_ACTIVE_MUTANT: job.mutation!.id';
      source = replaceOnce(source, selector, selector + ', OXIMUT_NATIVE_MODULE_TABLE: nativeModuleTable');
      return {...result, source};
    },
  });
}
