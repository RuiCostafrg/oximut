from pathlib import Path
import sys,json,hashlib,tarfile,zipfile,subprocess,gzip
ROOT=Path('/tmp/oximut-w13-product-43668016410'); raw=ROOT/'raw/raw'; q=raw/'qualification'; HEAD='e9a368face7d8e3fd657383c2c979e2866370a38'
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path.cwd()/'benchmarks'))
import real_project as w, compare_candidates as c, m6
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(); read=lambda p:json.loads(p.read_text())
api=read(ROOT/'run.json'); artifact=read(ROOT/'artifacts.json')['artifacts'][0]
assert api['head_sha']==HEAD and api['conclusion']=='success'
assert 'sha256:'+h(ROOT/'artifact.zip')==artifact['digest']
for name,digest in read(ROOT/'artifact/w13-product-files.json').items(): assert h(raw/name)==digest,name
with tarfile.open(raw/'source.tar.gz') as t:
 members=[m for m in t.getmembers() if m.isfile()]
 refs=''.join(HEAD+':'+m.name+'\n' for m in members).encode()
 payload=subprocess.check_output(['git','cat-file','--batch'],input=refs); pos=0; source={}
 for m in members:
  end=payload.index(b'\n',pos);head=payload[pos:end].split();size=int(head[2]);pos=end+1
  expected=payload[pos:pos+size];pos+=size+1
  actual=t.extractfile(m).read();assert actual==expected,m.name;source[m.name]=hashlib.sha256(actual).hexdigest()
meta=read(raw/'build-metadata.json'); result=read(q/'correctness.json')
assert result['status']=='bounded product-colord correctness gate passed; not scored'
assert meta['after_commit']==HEAD and meta['before_commit']=='5dfd17e53cd7b067586b3ad47363cb3ecee5873e'
prov={v:{'binary_sha256':h(raw/'binaries'/name)} for v,name in [('before','stock'),('after','candidate')]}
assert not c.build_errors(prov,meta,{'before':meta['before_commit'],'after':HEAD},True)
assert result['runtime']['executableSha256']==read(Path('benchmarks/results/2026-11-14-w13-map-base/native/runtime-provenance.json'))['executableSha256']
prepared=read(q/'prepared/integration-manifest.json')
assert prepared['node']['sha256']==result['runtime']['executableSha256']
assert h(q/'prepared/private-worker.mjs')==prepared['bundle']['sha256']
assert h(q/'prepared/private-worker.mjs.map')==prepared['bundle']['mapSha256']
assert h(q/'prepared/private-worker.map-registration.json')==prepared['bundle']['registration']['sha256']
assert prepared['bundle']['registration']['helperSha256']==source['benchmarks/experiments/vitest5-private-bootstrap/map-registration.mjs']
assert h(q/'prepared/validation-files.json.gz')==prepared['validationFiles']['sha256']
assert hashlib.sha256(gzip.decompress((q/'prepared/validation-files.json.gz').read_bytes())).hexdigest()==prepared['validationFiles']['uncompressedSha256']
assert len(prepared['topology']['selected'])==6 and len(prepared['topology']['shared'])==15
assert not set(prepared['topology']['selected'])&set(prepared['topology']['shared'])
summary=[]; all_pids=[]; schema_rows=0
for scope,workers in [('colord',2),('colord',4),('colord-scale',2),('colord-scale',4)]:
 cfg=q/f'{scope}-{workers}w'; manifest=w.load_manifest(scope); count=10 if scope=='colord' else 206
 maps={}; rows={}; reports={}; roots={}
 for label,tool in [('ordinary','vitest'),('stock','oximut'),('stryker','stryker'),('candidate','oximut')]:
  d=cfg/label;row=read(d/'validated.json');obs=read(d/'observation.json');report=read(d/'report.json');project=d/'project';native=Path(row['cwd'])
  assert row['success'] and row['exit_code'] in ((0,1) if tool=='oximut' else (0,))
  assert not row['timed_out'] and not row.get('descendant_cleanup_required') and not row.get('cleanup_error') and not row['memory_errors']
  assert row['memory_method']=='sum-live-smaps_rollup-rss'
  w.verify_inputs(project,manifest)
  assert w.validate_tests(tool,report,native,manifest)==m6.digest(sorted(manifest['expectedTests']))
  if tool=='oximut':
   assert report['summary']['complete'] and report['baseline']['outcome']=='passed' and report['baseline']['tests']=={'passed':102,'failed':0,'skipped':0}
   assert report['baseline']['coverage']['complete'] and not report['baseline']['coverage']['reasons']
   assert not w.parallelism_errors(row,workers) and not c.phase_errors(row,manifest)
   assert not row.get('cache_provenance_count')
   # Reconstruct all exact edited-source identities directly from raw report + raw source.
   actual={}
   for mutant in report['mutants']:
    relative=Path(mutant['file']).relative_to(native).as_posix();text=(project/relative).read_text()
    identity=m6.mutation_identity(relative,text,mutant['location'],mutant['replacement'])
    actual[identity]=mutant['outcome']
   assert len(actual)==count
  elif tool=='stryker':
   actual={};status={'Killed':'killed','Survived':'survived','NoCoverage':'no-coverage'}
   for filename,data in report['files'].items():
    path=Path(filename);relative=path.relative_to(native).as_posix() if path.is_absolute() else path.as_posix();text=(project/relative).read_text()
    assert data['source']==text
    for mutant in data['mutants']:
     if mutant['status']=='Ignored':
      assert mutant['mutatorName'] in report['config']['mutator']['excludedMutations']
      assert mutant['statusReason']==f'Ignored because of excluded mutation "{mutant["mutatorName"]}"';continue
     assert mutant['status'] in status
     actual[m6.mutation_identity(relative,text,mutant['location'],mutant['replacement'])]=status[mutant['status']]
   assert len(actual)==count
  else: actual={}
  if tool!='vitest':
   assert actual=={key:value['outcome'] for key,value in row['mutations'].items()}
   sentinel=manifest['sentinel'];text=(project/sentinel['file']).read_text()
   key=sentinel['file']+':'+hashlib.sha256(text.replace(sentinel['original'],sentinel['replacement'],1).encode()).hexdigest();assert actual[key]=='killed'
   maps[label]=actual
  rows[label]=row;reports[label]=report;roots[label]=str(native);schema_rows+=1
 assert maps['stock']==maps['candidate']==maps['stryker']
 assert {m['id']:m['outcome'] for m in reports['stock']['mutants']}=={m['id']:m['outcome'] for m in reports['candidate']['mutants']}
 audit=read(cfg/'worker-evidence.json');forks=[e for e in audit if e['type']=='fork'];dispatch=[e for e in audit if e['type']=='run']
 assert len(forks)==len(dispatch)==count
 assert {e['selector'] for e in dispatch}=={m['id'] for m in reports['candidate']['mutants']}
 assert {e['pid'] for e in dispatch}=={e['pid'] for e in forks}
 assert all(e['entrypoint']==prepared['bundle']['path'] and e['entrypoint'] in e['command'] and str(e['startTime']).isdigit() for e in forks)
 assert any(e['type']=='stock-baseline' and e['coverage'] for e in audit)
 all_pids += [e['pid'] for e in forks]
 summary.append({'scope':scope,'workers':workers,'identicalSourceEditIdsAndOutcomes':count,'identicalOximutIds':count,'ordinaryTests':102,'baselinePassed':True,'baselineLedgersPassed':True,'sentinelKilled':True,'admissionPauses':rows['candidate']['resources']['admissionPauses'],'bundledFreshForks':len(forks),'rows':4})
assert len(all_pids)==len(set(all_pids))==432
integration=read(raw/'integration/results.json');assert len({e['pid'] for e in integration['observations']})==12
assert 'pass 1' in (raw/'integration.stdout.log').read_text()
assert 'tests 2' in (raw/'hook-failure.stdout.log').read_text() and 'pass 2' in (raw/'hook-failure.stdout.log').read_text() and 'fail 0' in (raw/'hook-failure.stdout.log').read_text()
archiveHashes={}
for name in ['artifact.zip','logs.zip']:
 with zipfile.ZipFile(ROOT/name) as z:
  assert z.testzip() is None;archiveHashes[name]={p:hashlib.sha256(z.read(p)).hexdigest() for p in z.namelist() if not p.endswith('/')}
(ROOT/'archive-file-hashes.json').write_text(json.dumps(archiveHashes,indent=2)+'\n')
(ROOT/'verification.json').write_text(json.dumps({'status':'declared bounded product-colord correctness verified; not general product qualification or performance','head':HEAD,'run':api['html_url'],'artifactDigest':artifact['digest'],'sourceFilesEqualToGit':len(source),'sourceHashes':source,'binaries':prov,'runtime':result['runtime'],'configs':summary,'validatedToolRows':schema_rows,'distinctBundledWorkerPids':len(set(all_pids)),'scopeLimitations':'Prepared artifact installation-bound; baseline pools intentionally stock. No resource-exhaustion negative fixture, arbitrary custom-loader or escaping-descendant qualification. Source/provenance, resource/admission/cleanup gates in tested commands passed. No scored ratios/CIs.'},indent=2)+'\n')
print(json.dumps({'sourceFiles':len(source),'configs':summary,'distinctBundledPids':len(set(all_pids))},indent=2))
