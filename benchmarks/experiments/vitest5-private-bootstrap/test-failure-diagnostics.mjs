import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { SharedLanes } from '../../../packages/vitest-adapter/shared-lanes.ts';
import { diagnosticRecord, diagnosticWriter, LIMITS } from './failure-diagnostics.mjs';

const repo=resolve(import.meta.dirname,'../../..');
const width=process.env.W13_FAILURE_TEST_WIDTH==='4'?4:1;
test('bounded nested errors retain original causes without invoking accessors or dumping inputs',()=>{
  const leaf=Object.assign(new Error('original cause'),{code:'EINJECTED',syscall:'read'});
  const wrapped=new Error('Failed to start worker',{cause:leaf});
  const e=new AggregateError([wrapped,leaf],'aggregate');e.cause=e;
  Object.defineProperty(e,'environment',{get(){throw Error('must not read environment')}});
  Object.defineProperty(leaf,'actual',{get(){throw Error('must not invoke getter')}});
  const result=diagnosticRecord('batch-unhandled',{errors:[e],environment:{SECRET:'do not log'}});
  assert.equal(result.errors[0].errors[0].cause.message,'original cause');
  assert.equal(result.errors[0].errors[0].cause.code,'EINJECTED');
  assert.equal(result.errors[0].cause.truncated,'cycle');
  assert.ok(!JSON.stringify(result).includes('SECRET'));
  const huge=diagnosticRecord('identity-check',{error:new Error('\0'.repeat(100000)),observedCommand:Array(100).fill('é'.repeat(100000))});
  assert.ok(Buffer.byteLength(JSON.stringify(huge))<LIMITS.eventBytes);
  assert.equal(huge.stringsTruncated,true);assert.equal(huge.commandTruncated,true);
});
test('native lazy Error.stack is captured without invoking an arbitrary stack getter',()=>{
  const original=new Error('native stack fixture');
  const record=diagnosticRecord('fork-start',{error:original});
  assert.match(record.error.stack,/native stack fixture/);
  assert.match(record.error.stack,/test-failure-diagnostics.mjs/);
  const arbitrary={message:'plain'};Object.defineProperty(arbitrary,'stack',{get(){throw Error('must not invoke arbitrary stack getter')}});
  assert.equal(diagnosticRecord('plain',{error:arbitrary}).error.stack,undefined);
});
test('capture storage failures reject explicitly',()=>{
  assert.throws(()=>diagnosticWriter('/nonexistent/w13-diagnostic/events.jsonl',{controller:process.pid,owner:'test'})('started-check',{error:new Error('cause')}),/ENOENT/);
});

for(const stage of ['artifact-configuration','fork-start','started-check','dispatch','dispatch-capture-failure']) test(`real ${width}-lane fork ${stage} failure retains nested cause, drains and recovers`,{timeout:90000},async t=>{
  const prior=process.exitCode,root=await mkdtemp(join(tmpdir(),'w13-diagnostic-fault-'));
  await symlink(join(repo,'node_modules'),join(root,'node_modules'));
  const file=join(root,'subject.ts'),code='export const value=1;';
  await writeFile(file,code);await writeFile(join(root,'vitest.config.mjs'),"export default {test:{include:['case.test.ts']}};");
  await writeFile(join(root,'case.test.ts'),"import{test,expect}from'vitest';import{value}from'./subject';test('value',()=>expect(value).toBe(1));");
  const births=[],records=[];let inject=true,hit=false;
  const birth=worker=>{const pid=worker.fork.pid;const stat=readFileSync(`/proc/${pid}/stat`,'utf8');births.push({pid,ticks:stat.slice(stat.lastIndexOf(') ')+2).split(' ')[19]})};
  const fault=()=>{hit=true;throw new Error('Injected '+stage,{cause:Object.assign(new Error('original nested '+stage),{code:'EINJECTED'})})};
  const lanes=new SharedLanes({entrypoint:'',baseline(){},validateConfig(){},
    configure(worker){
      if(inject&&worker.env.OXIMUT_ACTIVE_MUTANT==='bad-0'&&stage==='artifact-configuration')fault();
      if(inject&&worker.env.OXIMUT_ACTIVE_MUTANT==='bad-0'&&stage==='fork-start'){
        const native=worker.start.bind(worker);worker.start=async()=>{await native();birth(worker);fault()};
      }
    },
    started(worker){birth(worker);if(inject&&worker.env.OXIMUT_ACTIVE_MUTANT==='bad-0'&&stage==='started-check')fault()},
    dispatched(worker){if(inject&&worker.env.OXIMUT_ACTIVE_MUTANT==='bad-0'&&stage.startsWith('dispatch'))fault()},
    diagnostic(phase,details){records.push(diagnosticRecord(phase,details));if(stage==='dispatch-capture-failure'&&phase==='dispatch')throw Error('Injected diagnostic storage failure')},
  });
  t.after(async()=>{try{await lanes.close()}finally{await rm(root,{recursive:true,force:true})}});
  const module={id:'resident',file,code,map:{version:3,sources:['subject.ts'],names:[],mappings:'AAAA'}};
  const job={protocol:1,runId:'fault',jobId:'bad',generation:1,kind:'mutant',root,config:join(root,'vitest.config.mjs'),deadlineMs:15000,mutation:{...module,id:'bad'},resident:[module]};
  const jobs=Array.from({length:width},(_,i)=>({...job,jobId:'bad-'+i,mutation:{...module,id:'bad-'+i}}));
  let result;try{result=await lanes.run({...job,workers:width,lanes:jobs})}catch{}
  assert.equal(hit,true);if(result)assert.ok(result.lanes.every(r=>r.outcome==='infrastructure-error'));
  const error=records.find(r=>r.stage===(stage==='dispatch-capture-failure'?'dispatch':stage));assert.equal(error.error.cause.message,'original nested '+stage);
  if(stage!=='artifact-configuration')assert.ok(records.find(r=>r.stage==='batch-unhandled'||r.stage==='batch-run'));
  const gone=b=>{try{const s=readFileSync(`/proc/${b.pid}/stat`,'utf8');assert.notEqual(s.slice(s.lastIndexOf(') ')+2).split(' ')[19],b.ticks)}catch(e){if(e.code!=='ENOENT'&&e.code!=='ESRCH')throw e}};
  births.forEach(gone);await lanes.close();inject=false;
  const next={...job,jobId:'next',mutation:{...module,id:'next'}};
  const nextJobs=Array.from({length:width},(_,i)=>({...next,jobId:'next-'+i,mutation:{...module,id:'next-'+i}}));
  const recovered=await lanes.run({...next,workers:width,lanes:nextJobs});assert.ok(recovered.lanes.every(r=>r.outcome==='passed'));
  if(process.env.W13_FAILURE_TEST_CAPTURE){const path=join(process.env.W13_FAILURE_TEST_CAPTURE,stage+'.json');await writeFile(path,JSON.stringify({width,stage,records,births,failed:result,recovered},null,2)+'\n')}
  births.forEach(gone);await lanes.close();assert.equal(new Set(births.map(b=>b.pid+':'+b.ticks)).size,births.length);
  process.exitCode=prior;
});
