// W14 follow-up admission gate only: parse/hash/resolve, never import Vitest,
// create a bundle, start a worker, register a loader, or run a project.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parse } from '@babel/parser';
const require=createRequire(import.meta.url);
const sha=x=>createHash('sha256').update(x).digest('hex');
const url=p=>pathToFileURL(p).href;
const staticKinds=new Set(['ImportDeclaration','ExportNamedDeclaration','ExportAllDeclaration']);
const repo=resolve(import.meta.dirname,'../../../..');
const dist=dirname(require.resolve('vitest/node'));
const [manifestPath,expectedDigest,outputPath]=process.argv.slice(2);
assert.match(expectedDigest??'',/^[a-f0-9]{64}$/,'An independently supplied W14 manifest digest is mandatory');
assert.equal(process.version,'v26.3.1');
assert.equal(process.platform,'linux');
assert.deepEqual(process.execArgv,['--experimental-import-meta-resolve']);
const manifestBytes=await readFile(manifestPath);
assert.equal(sha(manifestBytes),expectedDigest,'W14 expected artifact identity mismatch');
const old=JSON.parse(manifestBytes);
const selected=new Set(old.selected.map(url));
const output=resolve(outputPath);await mkdir(output); // Fail rather than overwrite.
let fileReads=1,readBytes=manifestBytes.length,resolverCalls=0;
async function read(path){const b=await readFile(path);fileReads++;readBytes+=b.length;return b;}
const evidence={classification:'INCOMPLETE',candidateBuilt:false,candidateExecuted:false,expectedW14ManifestDigest:expectedDigest,manifestPath,output,runtime:{version:process.version,platform:process.platform,arch:process.arch,execPath:process.execPath,execArgv:process.execArgv,sha256:sha(await read(process.execPath))},tools:{},sources:[],modules:[],missingKindAudit:[],limitations:[]};
try {
  for(const [name,version] of Object.entries({vitest:'5.1.0',vite:'8.3.5','@vitest/coverage-v8':'5.1.0','@babel/parser':'8.0.10',rolldown:'1.2.15','magic-string':'1.4.5'})) {
    const p=require.resolve(name+'/package.json'),bytes=await read(p);assert.equal(JSON.parse(bytes).version,version);
    evidence.tools[name]={path:p,version,sha256:sha(bytes),executedHere:name==='@babel/parser'};
  }
  for(const p of [fileURLToPath(import.meta.url),join(repo,'package-lock.json'),join(repo,'packages/vitest-adapter/lane-runner.mjs'),join(repo,'packages/vitest-adapter/m3-setup.ts')]){
    const b=await read(p);evidence.sources.push({path:p,sha256:sha(b)});
  }
  const files=[];
  async function list(p){for(const e of (await readdir(p,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){const q=join(p,e.name);if(e.isDirectory())await list(q);else if(e.isFile()&&e.name.endsWith('.js'))files.push(q);}}
  await list(dist);
  const rows=new Map();
  for(const path of files){
    const bytes=await read(path),code=bytes.toString(),ast=parse(code,{sourceType:'module',createImportExpressions:true});
    const row={path,url:url(path),sha256:sha(bytes),selected:selected.has(url(path)),edges:[],contexts:[],topLevel:[]};
    const prior=old.topology.find(x=>x.path===path);if(prior)assert.equal(row.sha256,prior.sha256,`Audited source override: ${path}`);
    for(const n of ast.program.body)if(!staticKinds.has(n.type))row.topLevel.push({type:n.type,line:n.loc.start.line,text:code.slice(n.start,Math.min(n.end,n.start+220))});
    function visit(node,ancestors=[]){
      if(!node||typeof node!=='object')return;
      if((staticKinds.has(node.type)||node.type==='ImportExpression')&&node.source?.type==='StringLiteral'){
        const edge={kind:node.type,source:node.source.value,line:node.loc.start.line,start:node.start,ancestors:ancestors.filter(n=>/Function|Method/.test(n.type)||n.type==='VariableDeclarator').map(n=>({type:n.type,name:n.id?.name??n.key?.name,line:n.loc?.start.line})),text:code.slice(node.start,Math.min(node.end,node.start+200))};
        resolverCalls++;
        try{edge.resolved=import.meta.resolve(edge.source,row.url);}catch(e){edge.error={code:e.code,message:e.message};}
        row.edges.push(edge);
      }
      if((node.type==='MetaProperty'&&node.meta.name==='import')||(node.type==='ImportExpression'&&node.source.type!=='StringLiteral')||node.type==='AwaitExpression')row.contexts.push({kind:node.type,line:node.loc.start.line,text:code.slice(node.start,Math.min(node.end,node.start+300)),insideFunction:ancestors.some(n=>/Function|Method/.test(n.type))});
      for(const [k,v] of Object.entries(node)){
        if(['loc','start','end','extra','comments','tokens'].includes(k))continue;
        if(Array.isArray(v))for(const x of v)visit(x,[...ancestors,node]);
        else if(v&&typeof v==='object')visit(v,[...ancestors,node]);
      }
    }
    visit(ast);rows.set(row.url,row);evidence.modules.push(row);
  }
  for(const prior of old.topology){
    const row=rows.get(url(prior.path));assert(row,`Missing audited file ${prior.path}`);
    // Match the sequence, including repeated unresolved specifiers. The old
    // catch branch omitted kind; recover it from actual AST nodes, not names.
    assert.equal(prior.edges.length,row.edges.length);
    for(let i=0;i<prior.edges.length;i++){
      const e=prior.edges[i],actual=row.edges[i];assert.equal(e.source,actual.source);
      if(!e.kind){assert(actual.error);evidence.missingKindAudit.push({path:prior.path,oldEdgeIndex:i,...actual});}
      else assert.equal(e.kind,actual.kind);
    }
  }
  assert.equal(evidence.missingKindAudit.length,7);
  assert(evidence.missingKindAudit.every(x=>x.kind==='ImportExpression'));
  const adjacency=new Map([...rows].map(([u,r])=>[u,[...new Set(r.edges.filter(e=>staticKinds.has(e.kind)&&rows.has(e.resolved)).map(e=>e.resolved))]]));
  function closure(root,seen=new Set()){if(seen.has(root))return seen;seen.add(root);for(const q of adjacency.get(root)||[])closure(q,seen);return seen;}
  const closures=new Map([...rows.keys()].map(u=>[u,closure(u)]));
  const worker=url(join(dist,'workers/forks.js')),api=url(join(dist,'index.js'));
  const startup=closures.get(worker),startupSelected=new Set([...startup].filter(x=>selected.has(x))),lazy=[...selected].filter(x=>!startup.has(x));
  assert.equal(startup.size,30);assert.equal(startupSelected.size,22);assert.equal([...closures.get(api)].filter(x=>selected.has(x)).length,16);
  assert.equal(lazy.length,6);
  evidence.closures={workerStatic:[...startup],workerSelected:[...startupSelected],publicStatic:[...closures.get(api)],outsideWorkerStatic:lazy};
  const scc=[],remaining=new Set(rows.keys());
  while(remaining.size){const p=[...remaining].sort()[0],group=[...closures.get(p)].filter(q=>closures.get(q).has(p));group.forEach(x=>remaining.delete(x));scc.push(group);}
  evidence.staticSCC=scc;
  // Broaden read-only audit to every published distro module's literal edges;
  // do not assume the old worker/API graph covered all native/public ingresses.
  evidence.incoming=[...selected].sort().map(target=>({target,sites:[...rows.values()].flatMap(r=>r.edges.filter(e=>e.resolved===target).map(e=>({importer:r.url,kind:e.kind,line:e.line,text:e.text})))}));
  const packages=JSON.parse(await read(require.resolve('vitest/package.json')));
  evidence.publicExports=packages.exports;evidence.packageImports=packages.imports;
  const nativeRoots=[...rows.values()].filter(r=>!r.selected&&r.edges.some(e=>staticKinds.has(e.kind)&&selected.has(e.resolved))).map(r=>r.url);
  const namedRoots=[worker,api,...nativeRoots,...['test','TestRunner'].map(k=>url(old.owners[k])),...old.selected.filter(p=>/\/spy\.[^/]+\.js$/.test(p)).map(url)];
  evidence.namedIngresses=[...new Set(namedRoots)];
  const signatures=new Map();
  for(const member of startupSelected){const signature=evidence.namedIngresses.filter(root=>closures.get(root)?.has(member)).join('\n');const group=signatures.get(signature)||[];group.push(member);signatures.set(signature,group);}
  evidence.coReachableUnderNamedRoots=[...signatures.values()].filter(g=>g.length>1);
  // Proposed mechanism: ordinary ESM body coalescing, exact re-export facades.
  // No factories, snapshots or unsafe source rewriting. Importing any facade
  // instantiates/evaluates the shared body's top-level implementation cohort.
  // A necessary no-new-evaluation condition for every audited original facade
  // is mutual static reachability. Preserve that contract rather than silently
  // narrowing the old redirector's accepted absolute-file-URL ingresses.
  const pairs=[];const members=[...startupSelected].sort();
  for(let i=0;i<members.length;i++)for(let j=i+1;j<members.length;j++){
    const a=members[i],b=members[j];
    const witness=!closures.get(a).has(b)?{ingress:a,newlyEvaluated:b}:{ingress:b,newlyEvaluated:a};
    assert(!closures.get(witness.ingress).has(witness.newlyEvaluated));
    pairs.push({pair:[a,b],witness,reason:'Shared ordinary ESM body would initialize an original absent from this exact facade ingress static closure'});
  }
  assert.equal(pairs.length,231);assert(scc.every(g=>g.length===1));
  evidence.grouping={mechanism:'ordinary ESM shared implementation chunk plus exact re-export facades',admission:'preserve original evaluation closure for each audited exact facade ingress; do not assume earlier side effects are harmless',pairwiseRejections:pairs,admittedCohorts:[],scope:'necessary condition only; no theorem ruling out an explicitly lazy state-machine/factory compiler or a separately justified purity proof'};
  // Symbolic DFS traces retain source-order request traversal and opaque native
  // package barriers. These are NOT observed runtime traces or a substitute for
  // a stock/candidate phase/order oracle if a candidate were ever admitted.
  function symbolic(root){const seen=new Set(),events=[];function go(u){if(seen.has(u))return;seen.add(u);const r=rows.get(u);if(!r){events.push({opaqueExternal:u});return;}for(const e of r.edges)if(staticKinds.has(e.kind)){if(e.resolved)go(e.resolved);else events.push({unresolvedStatic:e.source,importer:u});}events.push({evaluate:u,nativeContext:!selected.has(u)});}go(root);return events;}
  evidence.symbolicOrder=Object.fromEntries(evidence.namedIngresses.map(r=>[r,symbolic(r)]));
  evidence.lazyIngresses=lazy.map(target=>({target,incoming:evidence.incoming.find(x=>x.target===target).sites}));
  evidence.limitations=['No actual candidate or stock runtime was executed; phase/order/load counts have not been remeasured','Symbolic order treats external packages as opaque barriers and is not complete native external closure provenance','Earlier evaluation could only be admitted with an explicit source/observable-state proof, not graph membership or package sideEffects metadata','Dynamic imports/computed imports/alternate conditions are not folded into static reachability','All old identity/negative assertions and failures remain unchanged; no new stack qualification','Selected source remains live and hashed for audit only; no authoritative installation artifact is declared'];
  evidence.classification='NO-GO before candidate: ordinary shared-body cohort cannot meet unchanged per-original facade evaluation-closure admission rule';
}catch(error){evidence.classification='AUDIT ERROR: STOP';evidence.failure={message:error.message,stack:error.stack};throw error;}
finally{evidence.auditOnlyCosts={sourceAndMetadataReads:fileReads,bytesRead:readBytes,originalResolverCalls:resolverCalls,newWorkerReads:0,newWorkerLoads:0,newFacadeLoads:0,newSharedBodyLoads:0,newLoaderRegistrations:0,newRuntimeManifestValidations:0,newMapReads:0,workerRuntimeInvocations:0,builds:0,performanceMeasured:false};await writeFile(join(output,'audit.json'),JSON.stringify(evidence,null,2)+'\n');}
