import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
const dir=new URL('./',import.meta.url);
const result=JSON.parse(await readFile(new URL('lifecycle-results.json',dir),'utf8'));
const median=xs=>{const v=xs.sort((a,b)=>a-b),i=Math.floor(v.length/2);return v.length%2?v[i]:(v[i-1]+v[i])/2;};
for(const mode of ['stock','snapshot']) {
 const rows=result.records.filter(r=>r.mode===mode);assert.equal(rows.length,12);
 result.summaries[mode]={bootstrapWallMs:median(rows.map(r=>r.bootstrapWallMs)),lifecycleWallMs:median(rows.map(r=>r.lifecycleWallMs)),bootstrapCpuMs:median(rows.map(r=>(r.bootstrapCpu.userCPUTime+r.bootstrapCpu.systemCPUTime)/1000)),lifecycleCpuMs:median(rows.map(r=>(r.exitCpu.userCPUTime+r.exitCpu.systemCPUTime)/1000))};
}
result.deltas=Object.fromEntries(Object.keys(result.summaries.stock).map(k=>[k,100*(result.summaries.snapshot[k]/result.summaries.stock[k]-1)]));
result.sourceHashes={};
for(const f of ['build.mjs','runtime.cjs','lifecycle.mjs','generated/deps.cjs','generated/deps.blob'])result.sourceHashes[f]=createHash('sha256').update(await readFile(new URL(f,dir))).digest('hex');
assert.equal(result.equalStateObservations,true);
for(const r of result.records){assert.deepEqual(r.tests,[{name:'fresh state',state:'passed'}]);if(r.mode==='snapshot')assert.ok(Object.values(r.state.identity).every(v=>v===false));}
await writeFile(new URL('lifecycle-results.json',dir),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({summaries:result.summaries,deltas:result.deltas},null,2));
