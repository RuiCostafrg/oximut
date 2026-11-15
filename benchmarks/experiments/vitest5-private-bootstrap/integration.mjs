// Installation-bound, diagnostic-only hook for real shared lanes. Never a default backend.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { appendFileSync, readFileSync, lstatSync, realpathSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

const repo = fileURLToPath(new URL('../../../', import.meta.url));
const require = createRequire(import.meta.url);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const pins = { vitest:'5.1.0', vite:'8.3.5', '@vitest/coverage-v8':'5.1.0', rolldown:'1.2.15', '@babel/parser':'8.0.10', 'magic-string':'1.4.5' };
const versions = () => Object.fromEntries(Object.keys(pins).map(name => [name, JSON.parse(readFileSync(require.resolve(name + '/package.json'), 'utf8')).version]));
const check = () => { assert.equal(process.version, 'v26.3.1', 'W13 requires Node 26.3.1'); assert.deepEqual(versions(), pins, 'W13 dependency mismatch'); };

export async function prepare(output) {
  check();
  assert.ok(isAbsolute(output), 'W13 preparation requires an absolute new directory');
  await mkdir(output); // Caller owns the output; no old artifact can be overwritten.
  const started = performance.now(), cpu = process.cpuUsage();
  const { buildCandidate, manifest } = await import('./build.mjs');
  const evidence = { format:1, owner: randomUUID(), repo, versions:pins, node: {path:process.execPath,sha256:hash(await readFile(process.execPath))}, policy:'Explicit setup before product execution. Full dependency/source/artifact validation once per controller; map registration/stat per worker, map parsing/validation lazy. All costs unscored.' };
  await buildCandidate(output, evidence);
  const installed = await manifest(join(repo, 'node_modules'));
  const sources = [...await manifest(import.meta.dirname), ...await manifest(join(repo, 'packages/vitest-adapter'))];
  const files = [...installed, ...sources,
    ...await Promise.all(['private-worker.mjs','private-worker.mjs.map','private-worker.map-registration.json','rolldown-runtime.js'].map(async name => ({path:join(output,name),sha256:hash(await readFile(join(output,name)))})))];
  const bytes = JSON.stringify(files);
  const inventory = join(output,'validation-files.json.gz'); await writeFile(inventory,gzipSync(bytes));
  evidence.validationFiles = {path:inventory,sha256:hash(await readFile(inventory)),uncompressedSha256:hash(bytes),count:files.length};
  evidence.preparation = {wallMs:performance.now()-started,cpu:process.cpuUsage(cpu)};
  await writeFile(join(output,'integration-manifest.json'),JSON.stringify(evidence,null,2)+'\n');
  return evidence;
}

export async function loadExperimentalBundle(manifestPath, pool) {
  const started = performance.now(), cpu = process.cpuUsage();
  check();
  assert.equal(process.platform,'linux','W13 requires Linux process identities');
  assert.equal(pool,'forks','W13 bundle rejects threads');
  assert.ok(!process.env.NODE_OPTIONS, 'W13 rejects NODE_OPTIONS/custom loaders');
  assert.ok(isAbsolute(manifestPath) && resolve(manifestPath)===manifestPath, 'W13 requires an absolute canonical manifest path');
  assert.equal(realpathSync(manifestPath),manifestPath,'W13 artifact cannot be symlinked');
  assert.equal(lstatSync(manifestPath).isFile(),true,'W13 manifest must be a regular file');
  assert.equal(manifestPath,join(dirname(manifestPath),'integration-manifest.json'),'W13 invalid manifest filename');
  const spec=JSON.parse(await readFile(manifestPath,'utf8'));
  assert.equal(spec.format,1,'W13 unsupported artifact metadata');
  assert.equal(spec.repo,repo,'W13 artifact belongs to another checkout/install');
  assert.deepEqual(spec.versions,pins); assert.ok(typeof spec.owner==='string' && spec.owner.length>10);
  assert.equal(spec.node.sha256,hash(await readFile(process.execPath)),'W13 executable changed');
  const dir=dirname(manifestPath), entrypoint=join(dir,'private-worker.mjs');
  assert.equal(spec.bundle.path,entrypoint,'W13 invalid bundle path');
  assert.equal(spec.validationFiles.path,join(dir,'validation-files.json.gz'));
  const inventory=await readFile(spec.validationFiles.path);
  assert.equal(hash(inventory),spec.validationFiles.sha256,'W13 validation inventory changed');
  const { gunzipSync }=await import('node:zlib'); const raw=gunzipSync(inventory);
  assert.equal(hash(raw),spec.validationFiles.uncompressedSha256);
  const files=JSON.parse(raw); assert.equal(files.length,spec.validationFiles.count);
  for(const file of files) {
    assert.ok(isAbsolute(file.path) && lstatSync(file.path).isFile() && !lstatSync(file.path).isSymbolicLink(),`W13 invalid validation file ${file.path}`);
    assert.equal(hash(await readFile(file.path)),file.sha256,`W13 changed/missing input ${file.path}`);
  }
  const events=join(dir,'integration-events.jsonl');
  const event=(value)=>appendFileSync(events,JSON.stringify({controller:process.pid,owner:spec.owner,...value})+'\n');
  event({type:'validated-controller',manifestPath,entrypoint,wallMs:performance.now()-started,cpu:process.cpuUsage(cpu),policy:spec.policy});
  return {
    entrypoint,
    configure(worker) {
      const started=performance.now(),cpu=process.cpuUsage();
      for(const [path,digest] of [[entrypoint,spec.bundle.sha256],[entrypoint+'.map',spec.bundle.mapSha256],[spec.bundle.registration.path,spec.bundle.registration.sha256],[fileURLToPath(spec.bundle.registration.helper),spec.bundle.registration.helperSha256]]) {
        assert.ok(lstatSync(path).isFile() && !lstatSync(path).isSymbolicLink(),'W13 worker artifact must be regular');
        assert.equal(hash(readFileSync(path)),digest,`W13 changed worker artifact ${path}`);
      }
      assert.equal(worker.entrypoint,join(dirname(require.resolve('vitest/node')),'workers/forks.js'),'W13 unsupported default worker entry');
      worker.entrypoint=entrypoint;
      event({type:'validated-fork-artifact',wallMs:performance.now()-started,cpu:process.cpuUsage(cpu)});
    },
    baseline(job,command) { event({type:command==='warm'?'transform-only-warm':'stock-baseline',command,jobId:job.jobId,runId:job.runId,kind:job.kind,coverage:!!job.collectCoverage}); },
    validateConfig(project) {
      assert.ok(!project.config.runner && !project.config.globalSetup?.length && project.config.environment==='node' && !project.config.browser?.enabled, 'W13 custom runner/environment/setup/browser unsupported');
      assert.ok(project.config.experimental?.viteModuleRunner!==false, 'W13 native/custom module loader unsupported');
      assert.ok(!project.config.execArgv.some(arg=>/^--(?:loader|experimental-loader|import)(?:=|$)/.test(arg)), 'W13 custom loader execArgv unsupported');
      const argv=project.config.execArgv;
      for(let index=0;index<argv.length;index++) {
        if(argv[index]==='--require' && argv[index+1]===join(repo,'packages/vitest-adapter/compile-cache.cjs')) {index++;continue;}
        assert.ok(argv[index]!=='-r' && !/^--require(?:=|$)/.test(argv[index]),'W13 custom preload unsupported');
      }
      assert.equal(createRequire(join(project.config.root,'package.json')).resolve('vitest'),require.resolve('vitest'),'W13 project imports another Vitest/API instance');
    },
    started(worker) {
      assert.equal(worker.entrypoint,entrypoint,'W13 stock worker cannot stand in for bundle');
      const child=worker.fork, stat=readFileSync(`/proc/${child.pid}/stat`,'utf8');
      const fields=stat.slice(stat.lastIndexOf(') ')+2).split(' ');
      const command=readFileSync(`/proc/${child.pid}/cmdline`,'utf8').split('\0');
      assert.equal(Number(fields[1]),process.pid,'W13 requires a new direct OS child');
      assert.ok(command.includes(entrypoint),'W13 actual command line is not bundled');
      event({type:'fork',pid:child.pid,startTime:fields[19],entrypoint,command});
    },
    dispatched(worker, message) {
      assert.equal(worker.entrypoint,entrypoint);
      // Vitest 5 sends config in start(), not run(). The selector is an actual
      // fork environment input; verify its kernel-observed initial environment.
      const selector=worker.env.OXIMUT_ACTIVE_MUTANT;
      assert.ok(readFileSync(`/proc/${worker.fork.pid}/environ`,'utf8').split('\0').includes('OXIMUT_ACTIVE_MUTANT='+selector),'W13 fork selector differs from worker environment');
      event({type:'run',pid:worker.fork.pid,entrypoint,selector,files:message.context.files.map(file=>file.filepath)});
    },
  };
}

if (process.argv[1]===fileURLToPath(import.meta.url)) {
  assert.equal(process.argv.length,3,'Usage: node --experimental-import-meta-resolve integration.mjs NEW_ABSOLUTE_DIRECTORY');
  const result=await prepare(process.argv[2]); console.log(JSON.stringify({owner:result.owner,entrypoint:result.bundle.path,preparation:result.preparation}));
}
