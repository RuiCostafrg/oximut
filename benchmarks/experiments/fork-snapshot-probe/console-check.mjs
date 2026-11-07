// Reproduce the initial blocker separately, without altering measured blobs.
import { readFile, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
const dir=resolve('benchmarks/experiments/fork-snapshot-probe');
const source=await readFile(dir+'/generated/deps.cjs','utf8');
const unpatched=source.replace(/req\(["']node:console["']\)\.Console\s*=\s*globalThis\.__snapshotConsole;/,'');
if(unpatched===source)throw new Error('console restore line not found');
await writeFile(dir+'/generated/unpatched.cjs',unpatched);
const build=spawnSync(process.execPath,['--snapshot-blob',dir+'/generated/unpatched.blob','--build-snapshot',dir+'/generated/unpatched.cjs'],{encoding:'utf8'});
if(build.status!==0)throw new Error(build.stderr);
await writeFile(dir+'/generated/inspect-console.cjs',`const Console=require('node:console').Console;let error=null;try{new Console({stdout:process.stdout,stderr:process.stderr})}catch(e){error=String(e)};process.stdout.write(JSON.stringify({consoleType:typeof Console,error}));`);
const entry=dir+'/generated/inspect-console.cjs';
const run=args=>{const r=spawnSync(process.execPath,args,{encoding:'utf8',env:{...process.env,OX_SNAPSHOT_ENTRY:entry}});return {status:r.status,signal:r.signal,stdout:r.stdout,stderr:r.stderr};};
await writeFile(dir+'/console-results.json',JSON.stringify({stock:run([entry]),unpatched:run(['--snapshot-blob',dir+'/generated/unpatched.blob']),repaired:run(['--snapshot-blob',dir+'/generated/deps.blob'])},null,2)+'\n');
