// Identical diagnostic shim for stock and snapshot; no worker code at build time.
const fs = require('node:fs');
const req = require('node:module').createRequire(process.cwd()+'/runtime.cjs');
const deps = Object.fromEntries(['@vitest/runner','@vitest/expect','chai','@vitest/utils','@vitest/spy'].map(n=>[n,req(n)]));
const pre=globalThis.__snapshotDeps;
const state={env:process.env.OX_SNAPSHOT_STATE,cwd:process.cwd(),argv:process.argv.slice(2),
 cleanGlobal:globalThis.__testDirty===undefined,chaiTruncate:deps.chai.config.truncateThreshold,
 runnerTestType:typeof deps['@vitest/runner'].test,
 identity:pre?{runner:pre.runner.test===deps['@vitest/runner'].test,expect:pre.expect.getState===deps['@vitest/expect'].getState,chai:pre.chai.expect===deps.chai.expect,spy:pre.spy.fn===deps['@vitest/spy'].fn}:null,
 build:globalThis.__snapshotBuild??null};
const record={state};
const send=process.send;
process.send=function(message,...args){
 if(message?.__vitest_worker_response__){
  if(message.type==='started')record.bootstrapCpu=process.resourceUsage();
  if(message.type==='stopped')record.stoppedCpu=process.resourceUsage();
 }
 return send.call(this,message,...args);
};
process.on('SIGTERM',()=>{record.exitCpu=process.resourceUsage();fs.writeFileSync(process.env.OX_SNAPSHOT_METRICS,JSON.stringify(record));process.exit(0);});
req(process.env.OX_REAL_FORK);
