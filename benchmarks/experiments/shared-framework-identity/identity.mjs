// Gate 1: actual fork IPC, one test, positive function/class/state identity.
// Gate 2 is deliberately separate; this is not equivalent-outcomes qualification.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, symlink } from 'node:fs/promises';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root = resolve(import.meta.dirname,'../../..');
const sha = x => createHash('sha256').update(x).digest('hex');
const url = x => pathToFileURL(x).href;
const self = fileURLToPath(import.meta.url);
async function controller(input) {
  const { createVitest, ForksPoolWorker } = await import('vitest/node');
  const workers=[], reports=[];
  const ctx = await createVitest('test', {
    root: input.fixture, config:false, watch:false, isolate:true, fsModuleCache:false,
    maxWorkers:1, fileParallelism:false, environment:'node',
    runner:join(root,'packages/vitest-adapter/lane-runner.mjs'),
    setupFiles:[join(root,'packages/vitest-adapter/m3-setup.ts')],
    env:{OXIMUT_M3_COVERAGE:input.coverage},
    coverage:{enabled:false},
    pool:{name:'shared-framework-identity-gate',createPoolWorker(options) {
      const worker=new ForksPoolWorker(options), record={}; workers.push(record);
      worker.entrypoint=input.bootstrap;
      const start=worker.start.bind(worker), stop=worker.stop.bind(worker);
      worker.start=async()=>{await start();record.pid=worker.fork.pid;};
      worker.stop=async()=>{const child=worker.fork;try{await stop();}finally{record.exitCode=child?.exitCode;record.signal=child?.signalCode;}};
      return worker;
    }},
    reporters:[{onTestRunEnd(modules, errors, reason) {
      reports.push({reason, errors:[...errors,...modules.flatMap(m=>[...m.errors()])].map(e=>({message:e.message,stack:e.stack})),tests:modules.flatMap(m=>[...m.children.allTests()].map(t=>({id:t.id,name:t.name,state:t.result().state,errors:t.result().errors?.map(e=>({message:e.message,stack:e.stack}))}))) });
    }}],
  });
  try {await ctx.standalone(); const specs=await ctx.globTestSpecifications(); assert.equal(specs.length,1); await ctx.runTestSpecifications(specs,true);}
  finally {await ctx.close();await writeFile(input.result,JSON.stringify({workers,reports},null,2)+'\n');}
}
async function run(output, artifact, expectedHash, negative=false) {
  await mkdir(output);
  const manifest=join(artifact,'build.json'), bytes=await readFile(manifest), a=JSON.parse(bytes);
  assert.match(expectedHash ?? '',/^[a-f0-9]{64}$/,'Supply an independently trusted expected build-manifest digest');
  assert.equal(sha(bytes),expectedHash,'Trusted artifact identity mismatch');
  const fixture=join(output,'fixture'),coverage=join(output,'coverage');
  await mkdir(fixture);await mkdir(coverage);
  await symlink(join(root,'node_modules'),join(fixture,'node_modules'));
  const originalAPI=url(a.owners.TestRunner),collector=url(a.owners.test);
  const spy=a.selected.find(x=>/\/spy\.[^/]+\.js$/.test(x));assert(spy);
  const loader=join(import.meta.dirname,'loader.mjs');
  const loaderHash=sha(await readFile(loader));
  const bootstrap=join(output,'bootstrap.mjs');
  const originalWorker=a.selected.find(x=>x.endsWith('/workers/forks.js'));assert(originalWorker);
  await writeFile(bootstrap,`import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
if (createHash('sha256').update(readFileSync(${JSON.stringify(loader)})).digest('hex') !== ${JSON.stringify(loaderHash)}) throw Error('Loader source override');
const { register } = await import(${JSON.stringify(url(loader))});
register(${JSON.stringify(manifest)},${JSON.stringify(sha(bytes))},${JSON.stringify(join(output,'trace-'))}+process.pid+'.jsonl');
${negative ? `const {registerHooks}=await import('node:module'); registerHooks({resolve(s,c,next){if(s===${JSON.stringify(collector+'?deliberate-unredirected-negative')})return {url:s,format:'module',shortCircuit:true};return next(s,c);}});` : ''}
await import(${JSON.stringify(url(originalWorker))});
globalThis.__nativeIdentityHelper=await import(${JSON.stringify(url(join(output,'native-helper.mjs')))});
globalThis.__bootstrapIdentity={api:await import(${JSON.stringify(url(join(dirname(a.owners.TestRunner),'../index.js')))}),owner:await import(${JSON.stringify(originalAPI)}),collector:await import(${JSON.stringify(collector)}),spy:await import(${JSON.stringify(url(spy))})};
`);
  const helper=join(output,'native-helper.mjs');
  await writeFile(helper,`import * as api from 'vitest';
import * as collector from ${JSON.stringify(collector+(negative?'?deliberate-unredirected-negative':''))};
import * as spy from ${JSON.stringify(url(spy))};
import LaneRunner from ${JSON.stringify(url(join(root,'packages/vitest-adapter/lane-runner.mjs')))};
export {api,collector,spy,LaneRunner};
`);
  // Bare imports in a helper outside this checkout's node_modules need the
  // checkout's package context; link only this owned evidence directory.
  await symlink(join(root,'node_modules'),join(output,'node_modules'));
  await writeFile(join(fixture,'identity.test.mjs'),`import { test,expect,vi,TestRunner,chai } from 'vitest';
import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
const helper=globalThis.__nativeIdentityHelper;
const boot=globalThis.__bootstrapIdentity;
test('one collector current-test class expect spy and native closure',({task})=>{
 assert(boot,'bootstrap references missing');
 assert.equal(test,boot.collector.z); assert.equal(test,helper.api.test);
 assert.equal(TestRunner,boot.api.TestRunner); assert.equal(TestRunner,boot.owner.T);
 assert.equal(Object.getPrototypeOf(helper.LaneRunner),TestRunner);
 assert.equal(TestRunner.getCurrentTest,boot.collector.c);
 assert.equal(helper.collector.c(),task,'native helper collector current-test identity'); assert.equal(TestRunner.getCurrentTest(),task);
 assert.equal(expect,boot.api.expect); assert.equal(expect,helper.api.expect);
 assert.equal(vi.fn,boot.spy.f); assert.equal(vi.fn,helper.spy.f);
 const fn=vi.fn(()=>7);fn();assert.equal(fn.mock.calls.length,1);helper.spy.c();assert.equal(fn.mock.calls.length,0);
 expect.setState({sharedIdentityToken:process.pid});assert.equal(helper.api.expect.getState().sharedIdentityToken,process.pid);
 assert.equal(chai,helper.api.chai);
 globalThis.__oximut_m3_hit__('identity-attribution');
 const evaluations=globalThis.__sharedEvaluations;
 for(const count of Object.values(evaluations))assert.equal(count,1);
 writeFileSync(${JSON.stringify(join(output,'identity.json'))},JSON.stringify({pid:process.pid,realm:randomUUID(),testId:task.id,evaluations,oracle:'passed'}));
});
`);
  const input={fixture,coverage,bootstrap,result:join(output,'result.json')};
  await writeFile(join(output,'input.json'),JSON.stringify(input,null,2));
  await writeFile(join(output,'trust.json'),JSON.stringify({manifest,sha256:sha(bytes),loader,loaderHash,bootstrapHash:sha(await readFile(bootstrap)),identitySourceHash:sha(await readFile(self))},null,2));
  const child=spawn(process.execPath,['--experimental-import-meta-resolve',self,'--controller',join(output,'input.json')],{detached:true,stdio:['ignore','pipe','pipe']});
  const stdout=[],stderr=[];child.stdout.on('data',x=>stdout.push(x));child.stderr.on('data',x=>stderr.push(x));
  let timedOut=false;
  const timer=setTimeout(()=>{timedOut=true;process.kill(-child.pid,'SIGKILL');},30000);
  const status=await new Promise(done=>child.on('close',(code,signal)=>done({code,signal})));
  clearTimeout(timer);
  let residual=false;try{process.kill(-child.pid,0);residual=true;}catch(e){if(e.code!=='ESRCH')throw e;}
  if(residual)process.kill(-child.pid,'SIGKILL');
  await writeFile(join(output,'stdout.log'),Buffer.concat(stdout));
  await writeFile(join(output,'stderr.log'),Buffer.concat(stderr));
  await writeFile(join(output,'status.json'),JSON.stringify({...status,timedOut,residual},null,2));
  assert.equal(timedOut,false,'Identity gate deadline');assert.equal(residual,false,'Residual process group');
  if(negative){
    assert.equal(status.code,1,'Negative control must fail the unchanged identity oracle');
    const raw=await readFile(input.result,'utf8');
    assert.match(raw,/native helper collector current-test identity/);
    await writeFile(join(output,'negative-verdict.json'),JSON.stringify({expectedFailure:true,oracle:'native helper collector current-test identity'}));
    return;
  }
  assert.equal(status.code,0,'STOP: identity gate failed; preserve output and do not retry');
  const result=JSON.parse(await readFile(input.result));
  assert.equal(result.reports.length,1);assert.equal(result.reports[0].tests.length,1);
  assert.equal(result.reports[0].tests[0].state,'passed');assert.equal(result.reports[0].errors.length,0);
}
if(process.argv[2]==='--controller')await controller(JSON.parse(await readFile(process.argv[3],'utf8')));
else await run(resolve(process.argv[2]),resolve(process.argv[3]),process.argv[4],process.argv[5]==='--negative');
