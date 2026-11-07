import assert from 'node:assert/strict';
import { createVitest, ForksPoolWorker } from 'vitest/node';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { resolve, dirname } from 'node:path';
import { mkdir, writeFile, readFile, rm, symlink } from 'node:fs/promises';
const require=createRequire(import.meta.url),dir=resolve('benchmarks/experiments/fork-snapshot-probe');
const generated=dir+'/generated', root=generated+'/project';
await mkdir(root+'/node_modules',{recursive:true});
await symlink(dirname(require.resolve('vitest/package.json')),root+'/node_modules/vitest').catch(e=>{if(e.code!=='EEXIST')throw e;});
await writeFile(root+'/tiny.test.mjs',`import {test,expect} from 'vitest';
 test('fresh state',()=>{expect(process.env.OX_SNAPSHOT_STATE).toBe('RUNTIME');expect(globalThis.__testDirty).toBeUndefined();globalThis.__testDirty=1;expect(1+1).toBe(2);});`);
const ipc=await new Promise((resolve,reject)=>{
 const child=spawn(process.execPath,['--snapshot-blob',generated+'/deps.blob'],{env:{...process.env,OX_SNAPSHOT_MODE:'ipc'},stdio:['ignore','pipe','pipe','ipc']});
 const messages=[];let stderr='';const timer=setTimeout(()=>{child.kill('SIGKILL');reject(new Error('IPC timeout'));},10000);
 child.stderr.on('data',b=>stderr+=b);child.on('message',m=>{messages.push(m);if(m.ready)child.send('ping');});child.on('error',reject);
 child.on('close',(code,signal)=>{clearTimeout(timer);try{assert.equal(code,0);assert.equal(messages[1].echo,'ping');resolve({code,signal,messages,stderr});}catch(e){reject(e);}});
});
const records=[];
for(let rep=0;rep<12;rep++)for(const mode of rep%2?['snapshot','stock']:['stock','snapshot']){
 let timing={},last;
 const metrics=generated+'/child.json';await rm(metrics,{force:true});
 class Worker extends ForksPoolWorker {
  constructor(options){super(options);this.entrypoint=dir+'/runtime.cjs';this.env={...this.env,OX_REAL_FORK:options.distPath+'/workers/forks.js',OX_SNAPSHOT_ENTRY:dir+'/runtime.cjs',OX_SNAPSHOT_METRICS:metrics,OX_SNAPSHOT_STATE:'RUNTIME'};
   if(mode==='snapshot')this.execArgv=[...this.execArgv,'--snapshot-blob',generated+'/deps.blob'];
  }
  async start(){timing.spawnAt=performance.now();await super.start();this._fork.on('message',m=>{if(m?.__vitest_worker_response__&&m.type==='started')timing.startedAt=performance.now();});}
 }
 const ctx=await createVitest('test',{root,config:false,watch:false,pool:{name:'snapshot-probe',createPoolWorker:o=>new Worker(o)},maxWorkers:1,fileParallelism:false,isolate:true,coverage:{enabled:false},reporters:[{onTestRunEnd(...args){last=args;}}]});
 try{
  await ctx.standalone();const specs=await ctx.globTestSpecifications();assert.equal(specs.length,1);
  const begin=performance.now();await ctx.runTestSpecifications(specs,true);await ctx.pool.close();timing.lifecycleWallMs=performance.now()-begin;
  assert.ok(last);const [modules,errors,reason]=last;if(!modules.length)console.error('WORKER_ERRORS',JSON.stringify(errors));assert.equal(modules.length,1);assert.deepEqual(errors,[]);assert.notEqual(reason,'interrupted');
  const tests=[...modules[0].children.allTests()].map(t=>({name:t.name,state:t.result().state}));assert.deepEqual(tests,[{name:'fresh state',state:'passed'}]);assert.deepEqual(modules[0].errors(),[]);
  const child=JSON.parse(await readFile(metrics,'utf8'));assert.ok(child.exitCpu);assert.ok(child.bootstrapCpu);
  const record={rep,mode,bootstrapWallMs:timing.startedAt-timing.spawnAt,lifecycleWallMs:timing.lifecycleWallMs,tests,...child};records.push(record);console.log(JSON.stringify({rep,mode,bootstrap:record.bootstrapWallMs,wall:record.lifecycleWallMs,cpu:child.exitCpu.userCPUTime+child.exitCpu.systemCPUTime}));
 }finally{await ctx.close();}
}
const median=values=>{const v=values.sort((a,b)=>a-b),i=Math.floor(v.length/2);return v.length%2?v[i]:(v[i-1]+v[i])/2;};
const summaries=Object.fromEntries(['stock','snapshot'].map(mode=>{const rows=records.filter(r=>r.mode===mode);return[mode,Object.fromEntries(['bootstrapWallMs','lifecycleWallMs','bootstrapCpuMs','lifecycleCpuMs'].map(key=>[key,median(rows.map(r=>key==='bootstrapCpuMs'?(r.bootstrapCpu.userCPUTime+r.bootstrapCpu.systemCPUTime)/1000:key==='lifecycleCpuMs'?(r.exitCpu.userCPUTime+r.exitCpu.systemCPUTime)/1000:r[key]))]))];}));
const deltas=Object.fromEntries(Object.keys(summaries.stock).map(k=>[k,100*(summaries.snapshot[k]/summaries.stock[k]-1)]));
const observations=records.map(r=>{const {identity,build,...equalState}=r.state;return equalState;});for(const o of observations)assert.deepEqual(o,observations[0]);
await writeFile(dir+'/lifecycle-results.json',JSON.stringify({node:process.version,vitest:require('vitest/package.json').version,platform:process.platform,arch:process.arch,ipc,summaries,deltas,equalStateObservations:true,records},null,2)+'\n');
console.log({summaries,deltas});
