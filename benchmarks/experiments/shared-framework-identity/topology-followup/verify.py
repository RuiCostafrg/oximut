"""Independent offline graph checks. Never executes framework or candidate code."""
import hashlib,json,pathlib,sys
folder=pathlib.Path(sys.argv[1])
x=json.loads((folder/'audit.json').read_text())
assert x['candidateBuilt'] is False and x['candidateExecuted'] is False
assert x['classification'].startswith('NO-GO before candidate:')
static={'ImportDeclaration','ExportNamedDeclaration','ExportAllDeclaration'}
modules={r['url']:r for r in x['modules']}
adj={u:[e['resolved'] for e in r['edges'] if e['kind'] in static and e.get('resolved') in modules] for u,r in modules.items()}
def reach(root):
    seen=set();todo=[root]
    while todo:
        u=todo.pop()
        if u in seen:continue
        seen.add(u);todo.extend(adj.get(u,[]))
    return seen
closures={u:reach(u) for u in modules}
selected={u for u,r in modules.items() if r['selected']}
worker=next(u for u in modules if u.endswith('/workers/forks.js'))
api=next(u for u in modules if u.endswith('/dist/index.js'))
startup=reach(worker)
assert len(startup)==30 and len(startup&selected)==22
assert len(reach(api)&selected)==16
assert sorted(u.rsplit('/',1)[-1] for u in selected-startup)==sorted(['acorn.C1kjbUFw.js','console.CQZ877Hi.js','constants.DLYM6cZY.js','globals.ZKtKSkEf.js','node.B9mkVmLH.js','index.js'])
assert len(x['missingKindAudit'])==7
assert all(e['kind']=='ImportExpression' and e['ancestors'] and e['error']['code']=='ERR_MODULE_NOT_FOUND' for e in x['missingKindAudit'])
assert sum(e['source']=='@edge-runtime/vm' for e in x['missingKindAudit'])==2
assert sum(e['source']=='happy-dom' for e in x['missingKindAudit'])==2
assert sum(e['source']=='jsdom' for e in x['missingKindAudit'])==2
assert sum(e['source']=='@opentelemetry/api' for e in x['missingKindAudit'])==1
pairs=x['grouping']['pairwiseRejections']
assert len(pairs)==231 and len({tuple(p['pair']) for p in pairs})==231
for p in pairs:
    assert set(p['pair'])<=startup&selected
    assert set(p['witness'].values())==set(p['pair'])
    assert p['witness']['newlyEvaluated'] not in reach(p['witness']['ingress'])
assert all(not (a!=b and b in closures[a] and a in closures[b]) for a in modules for b in modules)
assert x['grouping']['admittedCohorts']==[]
for group in x['coReachableUnderNamedRoots']:
    assert {u.rsplit('/',1)[-1] for u in group}=={'run.CjdgFZiW.js','task-utils.js'}
for r in x['modules']:
    assert hashlib.sha256(pathlib.Path(r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
for r in x['sources']:
    assert hashlib.sha256(pathlib.Path(r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
assert all(x['auditOnlyCosts'][k]==0 for k in ['newWorkerReads','newWorkerLoads','newFacadeLoads','newSharedBodyLoads','newLoaderRegistrations','newRuntimeManifestValidations','newMapReads','workerRuntimeInvocations','builds'])
print(json.dumps({'verification':'passed','kind':'offline independently recomputed graph and source hashes','runtimeCandidateExecutions':0,'workerStaticModules':30,'workerSelectedModules':22,'publicSelectedModules':16,'missingKindsRecoveredAsDynamic':7,'rejectedStartupPairs':231}))
