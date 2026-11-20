// Local feasibility probe. Build framework-only state, with the Vitest IPC
// setup and the native ESM resolver left until each fresh process restores.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { builtinModules, isBuiltin } from 'node:module';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { RolldownMagicString, rolldown } from 'rolldown';
import { parseAst } from 'rolldown/parseAst';
import { preserveMetadata } from './metadata.mjs';

const dist = fileURLToPath(new URL('./dist/', import.meta.resolve('vitest/package.json')));
const dir = resolve(process.argv[2]);
await mkdir(dir, {recursive: true});
const entry = join(dist, 'workers/forks.js');
const forkInit = join(dist, 'chunks/init-forks.BaDXQSD8.js');
const api = join(dist, 'index.js');
const resolveBridge = join(dir, 'resolve.mjs');
const apiBridge = pathToFileURL(join(dir, 'api.mjs')).href;
const seedId = '\0oximut-snapshot-seed';
const seed = `
import { startFork } from ${JSON.stringify(entry)};
import * as api from ${JSON.stringify(api)};
import { createRequire, registerHooks, syncBuiltinESMExports } from 'node:module';
import { startupSnapshot } from 'node:v8';
import nodeConsole from 'node:console';
import { writeFileSync } from 'node:fs';
const canonicalConsole = nodeConsole.Console;
startupSnapshot.setDeserializeMainFunction(async () => {
  // Node restores the global console's streams but drops its legacy constructor
  // property for user snapshots. Retain the canonical function from the seed.
  if (typeof canonicalConsole !== 'function') throw new Error('Snapshot did not capture Node Console');
  nodeConsole.Console = canonicalConsole;
  syncBuiltinESMExports();
  // ChildProcess.fork and Worker default to process.execArgv. Only this
  // worker uses the experimental image, so restore the stock visible args.
  const snapshotFlag = '--snapshot-blob=' + process.env.OXIMUT_SNAPSHOT_BLOB;
  const snapshotIndex = process.execArgv.indexOf(snapshotFlag);
  if (snapshotIndex < 0) throw new Error('Missing experimental snapshot launcher flag');
  const baselineTier = process.env.OXIMUT_SNAPSHOT_BASELINE_TIER === '1'
    && process.execArgv[snapshotIndex - 1] === '--max-opt=1';
  process.execArgv.splice(snapshotIndex, 1);
  if (baselineTier) process.execArgv.splice(snapshotIndex - 1, 1);
  const bridge = createRequire(${JSON.stringify(resolveBridge)})(${JSON.stringify(resolveBridge)});
  globalThis.__oximutSnapshotResolve = bridge.resolve;
  globalThis.__oximutSnapshotImport = bridge.load;
  globalThis.__oximutSnapshotApi = api;
  registerHooks({resolve(specifier, context, next) {
    const result = next(specifier, context);
    if (result.url !== ${JSON.stringify(pathToFileURL(api).href)}) return result;
    return {...result, url: ${JSON.stringify(apiBridge)}};
  }});
  const evidence = process.env.OXIMUT_SNAPSHOT_OBSERVED_DIR;
  if (process.env.OXIMUT_SNAPSHOT_COMPACT_ERRORS === '1' && process.env.OXIMUT_ACTIVE_MUTANT) {
    let count = 0;
    globalThis[Symbol.for('oximut.diagnostic.compactAssertion')] = () => ++count;
    if (evidence) {
      const send = process.send;
      process.send = function (message, ...args) {
        if (message?.__vitest_worker_response__ === true && message.type === 'testfileFinished')
          writeFileSync(evidence + '/' + process.pid + '.assertions', String(count), {flag: 'wx'});
        return Reflect.apply(send, this, [message, ...args]);
      };
    }
  }
  if (evidence && process.env.OXIMUT_SNAPSHOT_SPECIALIZE_SELECTOR === '1') {
    const selector = process.env.OXIMUT_ACTIVE_MUTANT;
    if (/^[0-9a-f]{64}$/.test(selector)) {
      let matches = 0;
      let active = 0;
      globalThis[Symbol.for('oximut.diagnostic.specializeSelector')] = (code) => code.replace(
        /process[.]env[.]OXIMUT_ACTIVE_MUTANT *=== *["']([0-9a-f]{64})["']/g,
        (source, id) => {
          matches++;
          if (id === selector) active++;
          // Keep the source offsets used by the resident module's map.
          return String(id === selector).padEnd(source.length);
        },
      );
      const send = process.send;
      process.send = function (message, ...args) {
        if (message?.__vitest_worker_response__ === true && message.type === 'testfileFinished')
          writeFileSync(evidence + '/' + process.pid + '.specialized',
            JSON.stringify({selector, matches, active}), {flag: 'wx'});
        return Reflect.apply(send, this, [message, ...args]);
      };
    }
  }
  if (evidence)
    writeFileSync(evidence + '/' + process.pid + '.json',
      JSON.stringify({pid: process.pid, ppid: process.ppid, baselineTier}), {flag: 'wx'});
  startFork();
});`;

function deferForkStart(source, id) {
  if (id === entry) {
    assert.equal(source.split('\nworkerInit({').length, 2);
    return source.replace('\nworkerInit({', '\nexport function startFork() {\nworkerInit({') + '\n}\n';
  }
  if (id !== forkInit) return source;
  const before = source.indexOf('if (!process.send)');
  const header = 'function workerInit(options) {';
  const after = source.indexOf(header);
  const onError = source.indexOf('function onError(error)');
  const end = source.indexOf('\n\nexport { workerInit');
  assert(0 < before && before < after && after < onError && onError < end);
  return source.slice(0, before) + header + '\n' + source.slice(before, after) +
    source.slice(onError, end) + '\n' + source.slice(after + header.length, onError) + source.slice(end);
}

const bundle = await rolldown({
  input: {seed: seedId},
  platform: 'node',
  external: (source) => isBuiltin(source) || ['@opentelemetry/api', 'jsdom', 'happy-dom', '@edge-runtime/vm'].includes(source),
  plugins: [{
    name: 'snapshot-seed',
    resolveId(source) {
      if (source === seedId) return source;
    },
    load(id) {
      if (id === seedId) return seed;
    },
    resolveDynamicImport(source, importer) {
      // Dynamic code is outside the pre-evaluation oracle.
      if (typeof source !== 'string') return;
      // Global registration closes over the same collector as explicit
      // imports from Vitest. Loading the stock chunk after restore would
      // create a second collector, even with the public API URL redirected.
      if (source === './globals.ZKtKSkEf.js' && importer.endsWith('/setup-common.CqAQCXzx.js'))
        return;
      if (source.startsWith('.')) return {id: pathToFileURL(resolve(importer, '..', source)).href, external: true};
      if (source.startsWith('file:')) return {id: source, external: true};
    },
    transform(source, id) {
      if (id.endsWith('/@vitest/mocker/dist/redirect.js')) {
        const access = 'module$1.builtinModules';
        assert(source.includes(access));
        return source.replace(access, JSON.stringify(builtinModules));
      }
      if (!id.startsWith(dist)) return;
      if (id === join(dist, 'module-evaluator.js')) {
        const marker = 'async _runInlinedModule(context, code, module, span) {';
        assert.equal(source.split(marker).length, 2);
        source = source.replace(marker, marker + `
    const specialize = globalThis[Symbol.for('oximut.diagnostic.specializeSelector')];
    if (specialize) code = specialize(code);
`);
      }
      if (id.endsWith('/run.CjdgFZiW.js')) {
        const marker = '\tconst err = _err;\n';
        assert.equal(source.split(marker).length, 2);
        source = "import { AssertionError as OximutAssertionError } from 'chai';\n" + source.replace(marker, marker + `
  const compactAssertion = globalThis[Symbol.for('oximut.diagnostic.compactAssertion')];
  if (compactAssertion && Object.getPrototypeOf(err) === OximutAssertionError.prototype
      && typeof err.message === 'string' && !('cause' in err) && !('__vitest_test_syntax_error__' in err)) {
    compactAssertion();
    return {name: 'AssertionError', message: err.message, ok: false};
  }
`);
      }
      // The minimal mksnapshot loader omits node:module.builtinModules.
      // It is determined entirely by the exact runtime building the blob.
      if (id.endsWith('/modules.BJuCwlRJ.js')) {
        const declaration = "import { builtinModules } from 'node:module';";
        assert(source.includes(declaration));
        source = source.replace(declaration, `const builtinModules = ${JSON.stringify(builtinModules)};`);
      }
      if (id.endsWith('/index.CZzC1gGg.js')) {
        // withGlobal probes the active timer implementation by creating and
        // cancelling a real timer. Its default instance belongs to each fork,
        // after the new process owns the event loop.
        const eager = /const defaultImplementation = withGlobal\(globalObject\);\s*fakeTimersSrc\.timers = defaultImplementation\.timers;\s*fakeTimersSrc\.createClock = defaultImplementation\.createClock;\s*fakeTimersSrc\.install = defaultImplementation\.install;/g;
        assert.equal([...source.matchAll(eager)].length, 1);
        source = source.replace(eager, `let defaultImplementation;
          for (const name of ['timers', 'createClock', 'install'])
            Object.defineProperty(fakeTimersSrc, name, {
              enumerable: true,
              configurable: true,
              get() { return (defaultImplementation ??= withGlobal(globalObject))[name]; },
            });`);
      }
      return preserveMetadata(deferForkStart(source, id), id, true);
    },
  }],
});
await bundle.write({dir, format: 'cjs', entryFileNames: '[name].cjs', codeSplitting: false});
// mksnapshot evaluates its CJS entry in a VM without an import callback.
// Keep the native importer in the post-restore ESM bridge instead.
const seedFile = join(dir, 'seed.cjs');
const seedCode = await readFile(seedFile, 'utf8');
const edits = new RolldownMagicString(seedCode);
function bridgeImport(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'ImportExpression') edits.overwrite(node.start, node.start + 6, 'globalThis.__oximutSnapshotImport');
  for (const child of Object.values(node)) bridgeImport(child);
}
bridgeImport(parseAst(seedCode, null, seedFile));
await writeFile(seedFile, edits.toString());
const stockApi = await import(pathToFileURL(api).href);
await writeFile(join(dir, 'api.mjs'), Object.keys(stockApi).map((key) =>
  `export const ${key} = globalThis.__oximutSnapshotApi[${JSON.stringify(key)}];`).join('\n') + '\n');
await writeFile(join(dir, 'resolve.mjs'), 'export const resolve = import.meta.resolve;\nexport const load = (id, options) => import(id, options);\n');
