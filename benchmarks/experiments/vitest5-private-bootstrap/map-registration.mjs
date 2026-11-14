// Use Vite's public retrieval hook; never rewrite Error.stack or replace its API owners.
import { createHash } from 'node:crypto';
import { appendFileSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { ModuleRunner } from 'vite/module-runner';

const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const fail = message => { throw new Error(`W13_MAP: ${message}`); };
const seenBundles = new Set();

export function registerBundleMap(bundleURL, metadataURL) {
  const started = process.cpuUsage(), heap = process.memoryUsage().heapUsed;
  const bundle = new URL(bundleURL), metadata = new URL(metadataURL);
  if (bundle.protocol !== 'file:' || metadata.protocol !== 'file:') fail('file URLs required');
  const spec = JSON.parse(readFileSync(metadata, 'utf8'));
  if (spec.format !== 1 || spec.bundleURL !== bundle.href || spec.viteVersion !== '8.3.5') fail('unsupported registration metadata');
  if (!/^[a-f0-9]{64}$/.test(spec.mapSha256) || !Array.isArray(spec.sources) || !spec.sources.length) fail('missing source/map provenance');
  const mapURL = new URL(spec.mapURL, metadata);
  if (mapURL.href !== new URL('./private-worker.mjs.map', bundle).href) fail('unsupported map URL/base');
  // Missing maps reject before the worker starts; bytes/JSON/decode stay lazy.
  const mapBytes = statSync(mapURL).size;
  if (seenBundles.has(bundle.href)) fail('bundle URL already registered; native Vite cache cannot be cleared safely');
  seenBundles.add(bundle.href);
  let parsed, closed = false, lookups = 0;
  const log = event => {
    if (spec.events) appendFileSync(fileURLToPath(spec.events), JSON.stringify({ pid: process.pid, ...event }) + '\n');
  };
  function retrieveSourceMap(source) {
    if (closed || (source !== bundle.href && source !== fileURLToPath(bundle))) return null;
    lookups++;
    if (!parsed) {
      const cpu = process.cpuUsage(), heapBefore = process.memoryUsage().heapUsed;
      const bytes = readFileSync(mapURL);
      if (digest(bytes) !== spec.mapSha256) fail('map hash mismatch');
      let decoded;
      try { decoded = JSON.parse(bytes); } catch { fail('invalid map JSON'); }
      if (decoded.version !== 3 || decoded.file !== 'private-worker.mjs' || decoded.sourceRoot || decoded.sections || !Array.isArray(decoded.sources) || !Array.isArray(decoded.sourcesContent) || decoded.sources.length !== spec.sources.length || decoded.sourcesContent.length !== decoded.sources.length || typeof decoded.mappings !== 'string' || !decoded.mappings.length || !/^[A-Za-z0-9+/;,]+$/.test(decoded.mappings)) fail('unsupported or corrupt map structure');
      const expected = new Map(spec.sources.map(row => [row.url, row.sha256]));
      for (let i = 0; i < decoded.sources.length; i++) {
        const sourceURL = new URL(decoded.sources[i], mapURL).href;
        if (typeof decoded.sourcesContent[i] !== 'string' || digest(decoded.sourcesContent[i]) !== expected.get(sourceURL)) fail(`source provenance mismatch: ${sourceURL}`);
        expected.delete(sourceURL);
      }
      if (expected.size) fail('missing or duplicate original sources');
      // Vite 8.3.5's DecodedMap resolves source paths with posixResolve, then
      // supportRelativeURL restores the base's protocol. Absolute URL pathnames
      // prevent its dirname(file:...) from becoming part of a source path.
      // Keep percent escapes: Vite joins these pathnames back into file URLs.
      parsed = { ...decoded, sources: decoded.sources.map(source => {
        const url = new URL(source, mapURL);
        fileURLToPath(url); // Validate that this is a local file URL.
        return url.pathname;
      }) };
      log({ type: 'map-loaded', bytes: bytes.length, callbackCPU: process.cpuUsage(cpu), heapDelta: process.memoryUsage().heapUsed - heapBefore });
    }
    return { url: mapURL.href, map: parsed };
  }
  // No modules are fetched/evaluated by this registration owner. Normal Vitest
  // runners share Vite's interceptor and retain their own module graphs/hooks.
  const runner = new ModuleRunner({ hmr: false, transport: { invoke() { fail('registration owner must never fetch modules'); } }, sourcemapInterceptor: { retrieveSourceMap } });
  log({ type: 'map-registered', statBytes: mapBytes, mapReadBytes: 0, callbackCPU: process.cpuUsage(started), heapDelta: process.memoryUsage().heapUsed - heap });
  return {
    retrieveSourceMap,
    get loaded() { return !!parsed; },
    async close() {
      if (closed) return;
      closed = true;
      await runner.close();
      log({ type: 'map-unregistered', lookups });
      parsed = undefined;
    },
  };
}

export function installBundleMap(bundleURL, metadataURL) {
  const registration = registerBundleMap(bundleURL, metadataURL);
  // close() removes its hook synchronously before its first await. SIGKILL or
  // SIGTERM destroys the entire fresh process instead; no cleanup claim on reuse.
  process.once('exit', () => { void registration.close(); });
  return registration;
}
