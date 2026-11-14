import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile, symlink } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';
import { prepare, loadExperimentalBundle } from './integration.mjs';

const repo=resolve(import.meta.dirname,'../../..');
test('real m2 shared lanes preserve stock outcomes, file/full selection, exact maps and fresh forks', {timeout:90000}, async t=>{
  const output=process.env.W13_INTEGRATION_TEST_OUTPUT || await mkdtemp(join(tmpdir(),'w13-integration-'));
  await mkdir(output,{recursive:true});
  const root=join(output,'fixture');await mkdir(root);
  await symlink(join(repo,'node_modules'),join(root,'node_modules'));
  const subject=join(root,'subject.ts'), events=join(output,'fixture-events.jsonl');
  const code=`const id=process.env.OXIMUT_ACTIVE_MUTANT; export const active=id; let n=0; export const count=()=>n++; export const add=()=>id==='A'?4:3; export const multiply=()=>id==='B'?7:6;`;
  await writeFile(subject,`export const active='original'; let n=0; export const count=()=>n++;export const add=()=>3;export const multiply=()=>6;`);
  await writeFile(join(root,'vitest.config.mjs'),`export default {test:{include:['*.test.ts'],testTimeout:1000}};`);
  await writeFile(join(root,'a.test.ts'),`import {test,expect} from 'vitest';
import {randomUUID} from 'node:crypto'; import {appendFileSync} from 'node:fs';
import {active,count,add,multiply} from './subject';
appendFileSync(${JSON.stringify(events)},JSON.stringify({pid:process.pid,realm:randomUUID(),active})+'\\n');
test('state and bootstrap URI',()=>{expect(count()).toBe(0);try{process.exit(7)}catch(e){expect(e.stack).toContain('file://'+${JSON.stringify(join(repo,'node_modules/vitest/dist/chunks/base.Cof-UHJf.js'))}+':123:9');appendFileSync(${JSON.stringify(join(output,'stacks.jsonl'))},JSON.stringify({pid:process.pid,stack:e.stack})+'\\n')}});
test('add',()=>expect(add()).toBe(3)); test('multiply',()=>expect(multiply()).toBe(6));
test('deadline',()=>{if(active==='hang')while(true){};expect(true).toBe(true)});`);
  await writeFile(join(root,'b.test.ts'),`import {test,expect} from 'vitest';test('second file',()=>expect(1).toBe(1));`);
  const job=(id,selection)=>({protocol:1,runId:'integration',jobId:id,generation:1,kind:'mutant',root,config:join(root,'vitest.config.mjs'),deadlineMs:15000,mutation:{id,file:subject,code,map:{version:3,sources:['subject.ts'],names:[],mappings:'AAAA'}},resident:[{id:'resident',file:subject,code,map:{version:3,sources:['subject.ts'],names:[],mappings:'AAAA'}}],...(selection?{selection}:{})});
  const fileSelection={mode:'files',files:[join(root,'a.test.ts')],selectedTests:4,totalTests:5,reasons:[]};
  const fullSelection={mode:'full',files:[],selectedTests:5,totalTests:5,reasons:['outside-test']};
  const spec=await prepare(join(output,'bundle')), manifest=join(output,'bundle/integration-manifest.json');
  await assert.rejects(loadExperimentalBundle(manifest,'threads'),/rejects threads/);
  await assert.rejects(loadExperimentalBundle(join(output,'does-not-exist'),'forks'),/ENOENT/);
  async function controller(candidate) {
    const env={...process.env}; delete env.OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP;
    if(candidate)env.OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP=manifest;
    const child=fork(join(repo,'packages/vitest-adapter/m2-worker.ts'),[],{env,execArgv:[],stdio:['ignore','pipe','pipe','ipc']});
    let stdout='',stderr='',sequence=0;
    child.stdout.on('data',s=>stdout+=s);child.stderr.on('data',s=>stderr+=s);
    const exited=new Promise(done=>child.once('exit',(code,signal)=>done({code,signal})));
    const command=(command,args)=>new Promise((done,reject)=>{
      const id=++sequence;const timer=setTimeout(()=>reject(Error('IPC deadline '+stderr)),25000);
      child.once('message',reply=>{clearTimeout(timer); appendFileSync(join(output,(candidate?'candidate':'stock')+'.ipc.jsonl'),JSON.stringify({command,args,reply})+'\n'); if(reply.id!==id||reply.error)reject(Error(JSON.stringify(reply)+stderr));else done(reply.result)});
      child.send({protocol:1,id,command,args});
    });
    t.after(async()=>{child.kill('SIGKILL');await exited;await writeFile(join(output,(candidate?'candidate':'stock')+'.stdout.log'),stdout);await writeFile(join(output,(candidate?'candidate':'stock')+'.stderr.log'),stderr)});
    await command('initialize',{...job('init'),kind:'baseline',mutation:null,resident:undefined});
    return {command,async close(){await command('close',{});child.disconnect();await exited;}};
  }
  const results={};
  for(const candidate of [false,true]) {
    const c=await controller(candidate), runs=[];
    // The actual original baseline is executed, then switched inactive/A/B/A lanes.
    runs.push(await c.command('run',{request:{...job('baseline'),kind:'baseline',mutation:null,resident:undefined}}));
    for(const [id,selection] of [['inactive',fileSelection],['A',fileSelection],['B',fullSelection],['A',fileSelection]]) {
      const request=job(id,selection);runs.push(await c.command('run',{request:{...request,lanes:[request]}}));
    }
    const request={...job('hang',fileSelection),deadlineMs:2500};runs.push(await c.command('run',{request:{...request,lanes:[request]}}));
    await c.close();results[candidate?'candidate':'stock']=runs;
  }
  const comparable=r=>({outcome:r.outcome,tests:r.tests,lanes:r.lanes?.map(x=>({outcome:x.outcome,tests:x.tests}))});
  assert.deepEqual(results.candidate.map(comparable),results.stock.map(comparable));
  assert.equal(results.candidate[0].outcome,'passed');
  assert.deepEqual(results.candidate.slice(1).map(r=>r.lanes[0].outcome),['passed','failed','failed','failed','timeout']);
  const observations=(await readFile(events,'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(observations.length,12);assert.equal(new Set(observations.map(e=>e.pid)).size,12);assert.equal(new Set(observations.map(e=>e.realm)).size,12);
  for(const observation of observations) assert.throws(()=>process.kill(observation.pid,0),{code:'ESRCH'});
  const audit=(await readFile(join(output,'bundle/integration-events.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(audit.filter(e=>e.type==='stock-baseline').length,1);
  assert.equal(audit.filter(e=>e.type==='fork').length,5);
  assert.deepEqual(audit.filter(e=>e.type==='run').map(e=>e.selector),['inactive','A','B','A','hang']);
  assert.equal(audit.filter(e=>e.type==='run' && e.files.length===2).length,1,'Whole-file full fallback must run both files');
  assert.ok(audit.filter(e=>e.type==='fork').every(e=>e.entrypoint===spec.bundle.path));
  await writeFile(join(output,'results.json'),JSON.stringify({results,observations,audit},null,2)+'\n');
});
