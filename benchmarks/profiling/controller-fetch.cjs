// Loaded only for controller diagnostics. The source anchors pin the observed
// boundaries to Vitest 5.1.0 and the resident adapter used by this experiment.
const dir = process.env.OXIMUT_PROFILE_FETCH_DIR;
if (dir && process.argv[1]?.endsWith('/m2-worker.ts')) {
  const { AsyncLocalStorage } = require('node:async_hooks');
  const { appendFileSync } = require('node:fs');
  const { registerHooks } = require('node:module');
  const { join } = require('node:path');
  const scope = new AsyncLocalStorage();
  let context = 0;
  let generation = 0;
  const events = [];
  const file = join(dir, `fetch-controller-${process.pid}.jsonl`);
  const now = () => process.hrtime.bigint().toString();
  const observe = (type, fields = {}) => {
    events.push({type, ns: now(), context, generation, ...scope.getStore(), ...fields});
  };
  const flush = () => {
    if (events.length) {
      appendFileSync(file, events.map((event) => JSON.stringify(event)).join('\n') + '\n');
      events.length = 0;
    }
  };
  process.on('exit', flush);
  globalThis.__oximutFetchProbe = {
    observe,
    boundary(kind, changed) {
      if (kind === 'create') context++;
      if (kind === 'create' || kind === 'invalidate') generation++;
      observe(kind, {changed});
      flush();
    },
    rpc(message, worker, project, run) {
      if (message?.t !== 'q' || !['fetch', 'fetchWarmModules'].includes(message.m)) return run();
      const args = message.a;
      const context = {rpc: message.i, worker: worker._fork?.pid, project, method: message.m};
      return scope.run(context, () => {
        const fields = message.m === 'fetch'
          ? {url: args[0], importer: args[1], environment: args[2], cached: args[3]?.cached === true}
          : {environment: args[0], files: args[1]};
        observe('rpc', fields);
        return run();
      });
    },
  };
  const probe = 'globalThis.__oximutFetchProbe';
  const add = (type, fields) => `${probe}.observe('${type}'${fields ? ', ' + fields : ''});`;
  const hooks = [
    [/\/vitest\/dist\/chunks\/index\.Di8au7q4\.js$/, [
      ['else this._eventEmitter.emit("rpc", message);',
        `else ${probe}.rpc(message, this.worker, this.project.name, () => this._eventEmitter.emit("rpc", message));`],
      ['const cached = !!moduleGraphModule.transformResult;',
        add('graph', '{ id: moduleGraphModule.id, url, environment: environment.name, cached, tmp: moduleGraphModule.transformResult?.__vitestTmp }')],
      ['if (typeof tmpPath === "string") return getCachedResult(result, tmpPath);',
        `if (typeof tmpPath === "string") { ${add('tmp-reuse', '{ path: tmpPath }')} return getCachedResult(result, tmpPath); }`],
      ['if (saveCachePromises.has(cachePath)) return saveCachePromises.get(cachePath);',
        `if (saveCachePromises.has(cachePath)) { ${add('write-join', '{ path: cachePath }')} return saveCachePromises.get(cachePath); }`],
      ['const savePromise = this.fsCache.saveCachedModule(cachePath, result, transformResult, imports, mappings, coverageData).then(() => returnResult).catch((error) => {',
        `const savePromise = this.fsCache.saveCachedModule(cachePath, result, transformResult, imports, mappings, coverageData).then(() => {
          ${add('write-done', '{ path: cachePath }')} return returnResult;
        }).catch((error) => { ${add('write-failed', '{ path: cachePath }')}`],
      ['const savePromise = this.fsCache.saveCachedModule',
        `${add('write-start', '{ path: cachePath, id: result.id, codeBytes: Buffer.byteLength(result.code ?? "") }')}\n`],
      ['if ("tmp" in result) metadata.tmps[url] = result.tmp;',
        add('fetch-result', '{ id: result.id, url, tmp: result.tmp, external: result.externalize, sentinel: result.cache === true, invalidate: result.invalidate }')],
      ['const seen = /* @__PURE__ */ new Set();\n\t\t\twhile (queue.length)',
        'const snapshot = { roots: queue.length, nodes: 0, transformed: 0, temporary: 0 };\n'],
      ['if (!transformResult || node.id == null) continue;\n\t\t\t\t// the transformed code',
        'snapshot.nodes++;\nif (transformResult && node.id != null) snapshot.transformed++;\n'],
      ['const entry = {\n\t\t\t\t\tcached: true,\n\t\t\t\t\tfile: node.file,', 'snapshot.temporary++;\n'],
      ['return warm;',''+add('snapshot', '{ ...snapshot, entries: Object.keys(warm).length }')+'\n'],
    ]],
    [/\/vitest-adapter\/shared-lanes\.ts$/, [
      ['this.width = width;', `${probe}.boundary('create', changed);\n`],
      ['// The resident code changed:', `${probe}.boundary('invalidate', changed);\n`],
      ['const ctx = this.context;', `${probe}.boundary('batch', changed);\n`],
    ]],
    [/\/vitest-adapter\/transform-share\.ts$/, [
      ['this.stats.hits++;', add('share-hit')+'\n'],
      ['this.stats.misses++;', add('share-miss')+'\n'],
      ['this.stats.bypasses++;', add('share-bypass')+'\n'],
    ]],
  ];
  registerHooks({
    load(url, context, nextLoad) {
      const result = nextLoad(url, context);
      const match = hooks.find(([pattern]) => pattern.test(url));
      if (!match) return result;
      let source = Buffer.from(result.source).toString('utf8');
      for (const [anchor, insertion] of match[1]) {
        if (!source.includes(anchor)) throw Error(`Missing controller fetch anchor in ${url}: ${anchor}`);
        source = source.replace(anchor, (original) =>
          insertion.startsWith('else ') || insertion.startsWith('if ') || insertion.startsWith('const savePromise')
            ? insertion : insertion + original);
      }
      return {...result, source};
    },
  });
}
