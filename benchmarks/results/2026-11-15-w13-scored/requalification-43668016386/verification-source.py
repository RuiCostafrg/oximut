from pathlib import Path
import hashlib,json,zipfile,tarfile,gzip,subprocess,re
ROOT=Path('/tmp/oximut-w13-requalification-43668016386'); A=ROOT/'artifact'; HEAD='e9a368face7d8e3fd657383c2c979e2866370a38'
sha=lambda b:hashlib.sha256(b).hexdigest()
read=lambda p:json.loads(p.read_text())
run=read(ROOT/'run.json'); metadata=read(ROOT/'artifacts.json')['artifacts'][0]; jobs=read(ROOT/'jobs.json')
assert run['head_sha']==HEAD and run['conclusion']=='success' and run['status']=='completed'
assert run['run_attempt']==1 and metadata['workflow_run']['head_sha']==HEAD
assert 'sha256:'+sha((ROOT/'artifact.zip').read_bytes())==metadata['digest']
assert len(jobs['jobs'])==1 and jobs['jobs'][0]['conclusion']=='success'
assert (A/'commit.txt').read_text().strip()==HEAD
inventory={}
for archive in ['artifact.zip','logs.zip']:
 with zipfile.ZipFile(ROOT/archive) as z:
  assert z.testzip() is None
  inventory[archive]=[{ 'path':i.filename,'bytes':i.file_size,'sha256':sha(z.read(i))} for i in z.infolist() if not i.is_dir()]
for line in (A/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split('  ',1); assert sha((A/name).read_bytes())==digest,name
assert {r['path'] for r in inventory['artifact.zip']}=={line.split('  ',1)[1] for line in (A/'SHA256SUMS').read_text().splitlines()}|{'SHA256SUMS'}
# Compare every regular archived source byte with the exact tested Git tree.
expected=set(subprocess.check_output(['git','ls-tree','-r','--name-only',HEAD,'--','benchmarks/experiments/vitest5-private-bootstrap','package.json','package-lock.json','.node-version','.github/workflows/w13-private-bootstrap.yml'],text=True).splitlines())
source={}
with tarfile.open(A/'source.tar.gz') as t:
 files=[m for m in t.getmembers() if m.isfile()]
 assert {m.name for m in files}==expected
 for member in files:
  actual=t.extractfile(member).read(); target=subprocess.check_output(['git','show',f'{HEAD}:{member.name}'])
  assert actual==target,member.name
  source[member.name]=sha(actual)
capture=read(A/'capture.json'); evidence=read(A/'run/evidence.json'); runtime=read(A/'runtime-provenance.json')
assert capture['status']=='complete structural qualification passed on this host; no performance qualification'
assert [s['name'] for s in capture['steps']]==['install','focused','matrix']
assert all(s['exitCode']==0 for s in capture['steps'])
assert evidence['status']=='complete local structural matrix passed; native qualification pending'
assert runtime==capture['runtime'] and runtime['version']=='v26.3.1' and runtime['platform']=='linux' and runtime['arch']=='x64'
assert runtime['executableSha256']==evidence['node']['sha256']
assert sha((A/'upstream-SHASUMS256.txt').read_bytes())==runtime['checksumFileSha256']
assert runtime['archiveSha256'] in (A/'upstream-SHASUMS256.txt').read_text().split('node-v26.3.1-linux-x64.tar.xz')[0].splitlines()[-1]
versions={'vitest':'5.1.0','vite':'8.3.5','@vitest/coverage-v8':'5.1.0','rolldown':'1.2.15','@babel/parser':'8.0.10','magic-string':'1.4.5'}
assert evidence['versions']==versions
lock=json.loads(subprocess.check_output(['git','show',HEAD+':package-lock.json']))
for name,version in versions.items():assert lock['packages']['node_modules/'+name]['version']==version
assert evidence['lock']['sha256']==source['package-lock.json']
for row in evidence['sources']: assert row['sha256']==source['benchmarks/experiments/vitest5-private-bootstrap/'+Path(row['path']).name]
installZip=(A/'run/installation.json.gz').read_bytes(); install=gzip.decompress(installZip)
assert sha(installZip)==evidence['compressedInstallationSha256'] and sha(install)==evidence['installationSha256']
installed={row['path']:row['sha256'] for row in json.loads(install)}
for module in evidence['topology']['modules']:assert installed[module['path']]==module['sha256']
assert len(evidence['topology']['shared'])==15 and len(evidence['topology']['selected'])==6
assert set(evidence['topology']['shared'])==set(evidence['topology']['worker'])&set(evidence['topology']['publicAPI'])
assert not set(evidence['topology']['selected'])&set(evidence['topology']['shared'])
selected=set(evidence['topology']['selected'])
for m in evidence['topology']['modules']:
 if m['path'] not in selected:
  for edge in m['edges']: assert edge.get('resolved') not in {'file://'+path for path in selected}
for name,key in [('private-worker.mjs','sha256'),('private-worker.mjs.map','mapSha256')]: assert sha((A/'run'/name).read_bytes())==evidence['bundle'][key]
registration=evidence['bundle']['registration']
assert sha((A/'run/private-worker.map-registration.json').read_bytes())==registration['sha256']
assert registration['helperSha256']==source['benchmarks/experiments/vitest5-private-bootstrap/map-registration.mjs']
for name,digest in evidence['fixtures'].items():assert sha((A/'run/fixture'/name).read_bytes())==digest
assert 'tests 11' in (A/'focused.stdout.log').read_text() and 'pass 11' in (A/'focused.stdout.log').read_text() and 'fail 0' in (A/'focused.stdout.log').read_text()
events=[json.loads(line) for line in (A/'run/events.jsonl').read_text().splitlines()]
setup=[e for e in events if e['type']=='setup']; assert len(setup)==16
assert len({e['pid'] for e in setup})==16 and len({e['realm'] for e in setup})==16
names=['arithmetic add','arithmetic multiply','fresh contamination state','hooks setup and mocks','mapped stack']
normal=[]; deadline=[]
for scenario,modes in evidence['runs'].items():
 for mode,result in modes.items():
  status=read(A/'run'/f'{scenario}-{mode}'/'status.json')
  assert status['remaining']==[] and status['residualLive']==[] and status['noLiveDescendants']
  assert not status.get('killError') and not status.get('launchError')
  if scenario=='deadline':
   assert result['deadline'] and status['deadlineFired'] and status['signal']=='SIGKILL' and status['durationMs']<10000
   group=status['observedAtDeadline']; assert len(group)>=3
   descendants=[e for e in events if e['type']=='descendant' and any(r['pid']==e['pid'] and r['ppid']==e['ppid'] for r in group)]
   assert len(descendants)==1
   workers=read(A/'run'/f'{scenario}-{mode}'/'workers.json'); assert len(workers)==1
   assert any(r['pid']==workers[0]['pid'] and r['startTime']==workers[0]['startTime'] for r in group)
   deadline+=workers
   continue
  assert not status['deadlineFired'] and status['code']==(0 if scenario=='framework-stack' else 1)
  expected=['inactive','A','B','A'] if scenario=='sequence' else ['inactive']
  assert len(result['workers'])==len(result['reports'])==len(expected)
  assert [e['selector'] for e in setup if e['pid'] in {w['pid'] for w in result['workers']}]==expected
  for w,r,selector in zip(result['workers'],result['reports'],expected):
   assert w['ppid']==w['group'] and w['pid']!=w['ppid'] and w['startTime'].isdigit()
   assert w['exitCode']==0 or w['signal']=='SIGTERM'
   if scenario=='sequence':
    killed={'A':'arithmetic add','B':'arithmetic multiply'}.get(selector)
    assert r['tests']==[{'name':n,'state':'failed' if n==killed else 'passed'} for n in names] and not r['errors']
    assert r['reason']==('failed' if killed else 'passed')
    assert len(r['testErrors'])==(1 if killed else 0)
    if killed: assert r['testErrors'][0]['name']==killed and ('expected 4 to be 3' if selector=='A' else 'expected 7 to be 6') in r['testErrors'][0]['message']
   elif scenario=='framework-stack':assert r['tests']==[{'name':'bootstrap source-map frame','state':'passed'}] and not r['errors'] and not r['testErrors']
   elif scenario=='throw':assert r['testErrors'][0]['message']=='intentional test throw' and r['tests']==[{'name':'intentional throw','state':'failed'}]
   elif scenario=='import-throw':assert r['tests']==[] and r['errors'][0]['message']=='intentional import throw' and 'import-throw.test.ts:1:7' in r['errors'][0]['stack']
   else: assert False,scenario
  normal+=result['workers']
 for component in ['reports'] if scenario!='deadline' else []:
  def comparable(r):return {'reason':r['reason'],'tests':r['tests'],'errors':[e['message'] for e in r['errors']],'testErrors':[(e['name'],e['message']) for e in r['testErrors']]}
  assert list(map(comparable,modes['stock'][component]))==list(map(comparable,modes['candidate'][component]))
assert {w['pid'] for w in normal+deadline}=={e['pid'] for e in setup}
assert len(normal)==14 and len(deadline)==2
bootstrap=[e for e in events if e['type']=='bootstrap-stack']; assert len(bootstrap)==2
original=next(m['path'] for m in evidence['topology']['modules'] if m['relative']=='chunks/base.Cof-UHJf.js')
expectedFrame='    at process.exit (file://'+original+':123:9)'
assert all(e['stack'].splitlines()[1]==expectedFrame for e in bootstrap)
assert len([e for e in events if e['type']=='stack' and 'mapped.ts:2:9' in e['stack']])==8
assert not [e for e in events if e['type']=='late']
assert len([e for e in events if e['type']=='dirtied-clock' and e['now']==123456789])==8
hooks=[e for e in events if e['type']=='hooks' and e['counts']['before']==5];assert len(hooks)==8
assert all(e['counts']=={'count':1,'before':5,'after':5,'all':1} for e in hooks)
runnerLog=(ROOT/'logs/structural/1_Set up job.txt').read_text(encoding='utf-8-sig');assert '24.04.5' in runnerLog and 'ubuntu-24.04' in runnerLog
summary={'status':'native structural qualification verified','head':HEAD,'runId':run['id'],'runURL':run['html_url'],'artifactId':metadata['id'],'artifactDigest':metadata['digest'],'runtime':runtime,'versions':versions,'sourceFilesMatchedToGit':len(source),'sourceFiles':source,'artifactFiles':len(inventory['artifact.zip']),'logFiles':len(inventory['logs.zip']),'focusedTests':11,'focusedPassed':11,'focusedFailed':0,'freshWorkers':16,'freshRealmMarkers':16,'normalWorkers':14,'deadlineWorkers':2,'controllerGroupsEmpty':10,'fullBootstrapFrame':expectedFrame,'helperSha256':registration['helperSha256'],'bundleSha256':evidence['bundle']['sha256'],'mapSha256':evidence['bundle']['mapSha256'],'registrationSha256':registration['sha256'],'compressedInventorySha256':evidence['compressedInstallationSha256'],'uncompressedInventorySha256':evidence['installationSha256'],'limits':'Declared structural fixture only; no product/colord equivalence or performance qualification. Native archive/binary comparison was performed by captured verified-source capture.py; raw binary/archive are publicly reconstructible from recorded URL/hash, not included in artifact.'}
(ROOT/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
(ROOT/'archive-file-hashes.json').write_text(json.dumps(inventory,indent=2)+'\n')
print(json.dumps({k:summary[k] for k in ['status','sourceFilesMatchedToGit','artifactFiles','logFiles','freshWorkers','controllerGroupsEmpty','helperSha256']},indent=2))
