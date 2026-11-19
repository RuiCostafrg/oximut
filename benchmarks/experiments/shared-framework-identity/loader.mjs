// Registered before dynamic import of a worker. Original resolution runs first.
import { registerHooks } from 'node:module';
import { readFileSync, appendFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
export function register(manifestPath, trustedHash, trace) {
  const bytes = readFileSync(manifestPath);
  const hash = b => createHash('sha256').update(b).digest('hex');
  if (hash(bytes) !== trustedHash) throw Error('Untrusted artifact manifest');
  const artifact = JSON.parse(bytes);
  if (artifact.classification !== 'built-not-qualified') throw Error('Incomplete artifact');
  // Narrow assurance: audit sources, emitted code/maps and package metadata;
  // no bundler/compiler runs in a child. This is NOT full-install attestation.
  for (const row of [...artifact.topology, ...artifact.outputs, ...artifact.modules.filter(x=>!x.path.startsWith('\0')), ...Object.values(artifact.versions)]) {
    if (hash(readFileSync(row.path)) !== row.sha256) throw Error(`Artifact/source override: ${row.path}`);
  }
  const originals = new Set(artifact.topology.map(x=>new URL(`file://${x.path}`).href));
  return registerHooks({ resolve(specifier, context, nextResolve) {
    const result = nextResolve(specifier, context);
    const parsed = result.url.startsWith('file:') ? new URL(result.url) : null;
    if (parsed && (parsed.search || parsed.hash)) {
      const base = new URL(parsed); base.search=''; base.hash='';
      if (originals.has(base.href)) throw Error(`Unaudited framework query/hash identity: ${result.url}`);
    }
    const redirect = artifact.facades[result.url];
    appendFileSync(trace, JSON.stringify({phase:'resolve',specifier,parent:context.parentURL,conditions:context.conditions,original:result.url,redirect})+'\n');
    return redirect ? {...result,url:redirect} : result;
  }, load(url,context,nextLoad) {
    const result = nextLoad(url,context);
    appendFileSync(trace,JSON.stringify({phase:'load',url,format:result.format})+'\n');
    return result;
  }});
}
