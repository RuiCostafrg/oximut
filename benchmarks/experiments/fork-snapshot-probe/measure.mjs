import { spawnSync } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { performance } from 'node:perf_hooks';

const dir = resolve('benchmarks/experiments/fork-snapshot-probe');
const trivial = `${dir}/trivial.js`, blob = `${dir}/trivial.blob`;
await writeFile(trivial, "globalThis.probe = 1; require('v8').startupSnapshot.setDeserializeMainFunction(() => {});\n");
let build = spawnSync(process.execPath, ['--snapshot-blob', blob, '--build-snapshot', trivial], { encoding: 'utf8' });
const records=[];
const cases=[['plain', []], ['snapshot', ['--snapshot-blob', blob]], ['fork-import', ['--input-type=module','-e',"import('./node_modules/vitest/dist/workers/forks.js')"]], ['fork-import-cache', ['--input-type=module','-e',"import('./node_modules/vitest/dist/workers/forks.js')"]]];
for(let i=0;i<8;i++) for(const [name,args] of (i%2 ? [...cases].reverse() : cases)) {
 const env={...process.env}; if(name==='fork-import-cache') env.NODE_COMPILE_CACHE=`${dir}/cache`;
 const start=performance.now(); const r=spawnSync(process.execPath,args.length?args:['-e','0'],{encoding:'utf8',env});
 records.push({rep:i,name,wallMs:performance.now()-start,childUserMs:r.status===0?null:null,status:r.status,signal:r.signal,stderr:r.stderr?.slice(-1000)});
}
// Child CPU and wall time from /usr/bin/time -v are unavailable through spawnSync; time reports via shell wrapper below.
await writeFile(`${dir}/measurements.json`,JSON.stringify({node:process.version,platform:process.platform,arch:process.arch,snapshotBuild:{status:build.status,stderr:build.stderr},records},null,2)+'\n');
