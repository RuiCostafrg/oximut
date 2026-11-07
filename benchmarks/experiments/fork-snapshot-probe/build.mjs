import { rolldown } from 'rolldown';
import { writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
const dir=resolve('benchmarks/experiments/fork-snapshot-probe');
await mkdir(`${dir}/generated`,{recursive:true});
const entry=`
import * as runner from '@vitest/runner';
import * as expect from '@vitest/expect';
import * as chai from 'chai';
import * as utils from '@vitest/utils';
import * as spy from '@vitest/spy';
import {startupSnapshot} from 'node:v8';
import {createRequire} from 'node:module';
import {Console} from 'node:console';
globalThis.__snapshotConsole = Console;
globalThis.__snapshotDeps = {runner,expect,chai,utils,spy};
globalThis.__snapshotBuild = {date:Date.now(),random:Math.random(),env:process.env.OX_SNAPSHOT_STATE,cwd:process.cwd(),argv:process.argv.slice(),symbols:Object.getOwnPropertySymbols(globalThis).map(String)};
startupSnapshot.setDeserializeMainFunction(() => {
 const req=createRequire(process.cwd()+'/snapshot-runtime.cjs');
 req('node:console').Console=globalThis.__snapshotConsole;
 if(process.env.OX_SNAPSHOT_MODE==='ipc') {
   process.on('message', m=>{process.send({echo:m,connected:process.connected});process.disconnect();});
   process.send({ready:true,send:typeof process.send,build:globalThis.__snapshotBuild});
 } else req(process.env.OX_SNAPSHOT_ENTRY);
});`;
await writeFile(`${dir}/generated/deps.mjs`,entry);
const bundle=await rolldown({input:`${dir}/generated/deps.mjs`,platform:'node',external:[/^node:/]});
await bundle.write({file:`${dir}/generated/deps.cjs`,format:'cjs',codeSplitting:false});
await bundle.close();
const r=spawnSync(process.execPath,['--snapshot-blob',`${dir}/generated/deps.blob`,'--build-snapshot',`${dir}/generated/deps.cjs`],{encoding:'utf8',env:{...process.env,OX_SNAPSHOT_STATE:'BUILD'}});
await writeFile(`${dir}/build-result.json`,JSON.stringify({status:r.status,signal:r.signal,stdout:r.stdout,stderr:r.stderr},null,2)+'\n');
console.log(r.status,r.stderr.slice(-4000));
if(r.status!==0)process.exitCode=1;
