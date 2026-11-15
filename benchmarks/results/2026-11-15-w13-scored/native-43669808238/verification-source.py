"""Read-only independent recovery verification of the complete scored campaign."""
from pathlib import Path
import gzip,hashlib,json,subprocess,sys,tarfile,tomllib,zipfile
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path.cwd()/'benchmarks'))
import real_project as w
import compare_candidates as c
import m6
import w13_score as score
import w13_score_summary as summary
ROOT=Path('/tmp/oximut-w13-score-43669808238');raw=ROOT/'raw/raw'
HEAD='88b7d6712648979dc7438a4044abf0f63650d463';STOCK='5dfd17e53cd7b067586b3ad47363cb3ecee5873e'
read=lambda p:json.loads(p.read_text());h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
api=read(ROOT/'run.json');artifact=read(ROOT/'artifacts.json')['artifacts'][0]
assert api['head_sha']==HEAD and api['status']=='completed' and api['conclusion']=='success'
assert artifact['workflow_run']['head_sha']==HEAD and 'sha256:'+h(ROOT/'artifact.zip')==artifact['digest']
inventory=read(ROOT/'artifact/w13-score-files.json')
for name,digest in inventory.items():assert h(raw/name)==digest,name
sources={}
for archive,revision in [('source.tar.gz',HEAD),('stock-source.tar.gz',STOCK)]:
 with tarfile.open(raw/archive) as t:
  members=[m for m in t.getmembers() if m.isfile()];pos=0;source={}
  payload=subprocess.check_output(['git','cat-file','--batch'],input=''.join(revision+':'+m.name+'\n' for m in members).encode())
  for member in members:
   end=payload.index(b'\n',pos);header=payload[pos:end].split();size=int(header[2]);pos=end+1
   expected=payload[pos:pos+size];pos+=size+1;actual=t.extractfile(member).read();assert actual==expected,member.name
   source[member.name]=hashlib.sha256(actual).hexdigest()
  cargo=tomllib.loads(t.extractfile('Cargo.toml').read().decode())
  assert all(member+'/Cargo.toml' in source for member in cargo['workspace']['members'])
  sources[revision]=source
subprocess.run(['git','diff','--exit-code',STOCK,HEAD,'--','Cargo.toml','Cargo.lock','rust-toolchain.toml','crates','experiments/oxc-smoke'],check=True)
subprocess.run(['git','diff','--exit-code','e9a368face7d8e3fd657383c2c979e2866370a38',HEAD,'--','packages/vitest-adapter','benchmarks/experiments/vitest5-private-bootstrap'],check=True)
meta=read(raw/'build-metadata.json');assert meta['before_commit']==STOCK and meta['after_commit']==HEAD
provenance={v:{'binary_sha256':h(raw/'binaries'/n)} for v,n in [('before','stock'),('after','candidate')]}
assert not c.build_errors(provenance,meta,{'before':STOCK,'after':HEAD},True)
assert 'Ran 11 tests' in (raw/'harness.stderr.log').read_text()
global_result=read(raw/'campaign-summary.json');assert global_result['correctness_gate']['passed']
assert global_result==summary.summarize(raw)
configs=[];pids=[];setup_count=0;tool_count=0;owners=set();all_observations=[]
for scope,workers in summary.CONFIGS:
 d=raw/f'{scope}-{workers}w';result=read(d/'score.json');rows=read(d/'prepared-rows.json');setups=read(d/'setup-rows.json')
 evaluations,cold=score.evaluate(rows,setups,result['execution_inputs'],meta,result['commits'],workers,scope)
 assert evaluations==result['evaluations'] and cold==read(d/'first-use-rows.json')
 assert all(r['correctness_gate']['passed'] and r['build_integrity']['passed'] for r in evaluations.values())
 assert len(rows)==36 and len(setups)==6 and result['status']=='completed'
 assert result['head']==HEAD and result['qualified_source']=='e9a368face7d8e3fd657383c2c979e2866370a38'
 assert result['runtime']['sha256']=='0b52710a27133a3baf45f0d1f10689ab874f50e5821d8e611486d17e20bcacdd'
 assert not result['harness_metadata']['git_status']
 manifest=w.load_manifest(scope);count=10 if scope=='colord' else 206;maps=[];rawids={}
 for row in [*rows,*setups]:
  assert row['exit_code'] in (0,1) and not row['timed_out'] and not row['descendant_cleanup_required']
  assert not row.get('cleanup_error') and not row['memory_errors'] and row['memory_method']=='sum-live-smaps_rollup-rss'
  assert all(row[k]>0 for k in c.METRICS)
  assert len(row['sample_gaps_seconds'])==row['samples']-1 and max(row['sample_gaps_seconds'])==row['max_sample_gap_seconds']
  assert 'peak_rss_snapshot' not in row
  all_observations.append(row)
 for row in rows:
  index,variant,tool=row['campaign_round'],row['variant'],row['tool'];location=d/f'round-{index}'/variant/tool
  report=read(location/'report.json');project=location/'project';native=Path(row['cwd'])
  w.verify_inputs(project,manifest)
  assert w.validate_tests(tool,report,native,manifest)==m6.digest(sorted(manifest['expectedTests']))
  assert ('OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP' in row['experiment_env'])==((variant,tool)==('after','oximut'))
  if tool=='oximut':
   assert not w.parallelism_errors(row,workers) and not c.phase_errors(row,manifest)
   assert report['summary']['complete'] and report['baseline']['outcome']=='passed'
   assert report['baseline']['coverage']['complete'] and not report['baseline']['coverage']['reasons']
   actual={m6.mutation_identity(Path(m['file']).relative_to(native).as_posix(),(project/Path(m['file']).relative_to(native)).read_text(),m['location'],m['replacement']):m['outcome'] for m in report['mutants']}
   rawids[index,variant]={m['id']:m['outcome'] for m in report['mutants']}
  elif tool=='stryker':
   actual={};status={'Killed':'killed','Survived':'survived','NoCoverage':'no-coverage'}
   for filename,source in report['files'].items():
    p=Path(filename);relative=p.relative_to(native).as_posix() if p.is_absolute() else p.as_posix();text=(project/relative).read_text();assert source['source']==text
    for mutant in source['mutants']:
     if mutant['status']=='Ignored':
      assert mutant['mutatorName'] in report['config']['mutator']['excludedMutations'];continue
     actual[m6.mutation_identity(relative,text,mutant['location'],mutant['replacement'])]=status[mutant['status']]
  else:actual={}
  if tool!='vitest':
   assert len(actual)==count and actual=={k:v['outcome'] for k,v in row['mutations'].items()}
   sentinel=manifest['sentinel'];text=(project/sentinel['file']).read_text();key=sentinel['file']+':'+hashlib.sha256(text.replace(sentinel['original'],sentinel['replacement'],1).encode()).hexdigest();assert actual[key]=='killed'
   maps.append(actual)
  if (variant,tool)==('after','oximut'):
   prepared=location/'prepared';spec=read(prepared/'integration-manifest.json');assert spec['owner'] not in owners;owners.add(spec['owner']);events=[json.loads(line) for line in (prepared/'integration-events.jsonl').read_text().splitlines()]
   forks=[e for e in events if e['type']=='fork'];runs=[e for e in events if e['type']=='run'];valid=[e['controller'] for e in events if e['type']=='validated-controller'];controllers={e['controller'] for e in events}
   assert len(forks)==len(runs)==count and len({e['pid'] for e in forks})==count
   assert {e['pid'] for e in forks}=={e['pid'] for e in runs} and {e['selector'] for e in runs}=={m['id'] for m in report['mutants']}
   assert len(valid)==len(controllers) and set(valid)==controllers
   assert all(e['owner']==spec['owner'] for e in events)
   assert all(e['entrypoint']==spec['bundle']['path'] and e['entrypoint'] in e['command'] for e in forks)
   assert spec['node']['sha256']==result['runtime']['sha256'] and len(spec['topology']['selected'])==6 and len(spec['topology']['shared'])==15
   assert h(prepared/'private-worker.mjs')==spec['bundle']['sha256'] and h(prepared/'private-worker.mjs.map')==spec['bundle']['mapSha256']
   assert h(prepared/'private-worker.map-registration.json')==spec['bundle']['registration']['sha256']
   assert spec['bundle']['registration']['helperSha256']==sources[HEAD]['benchmarks/experiments/vitest5-private-bootstrap/map-registration.mjs']
   assert h(prepared/'validation-files.json.gz')==spec['validationFiles']['sha256']
   assert hashlib.sha256(gzip.decompress((prepared/'validation-files.json.gz').read_bytes())).hexdigest()==spec['validationFiles']['uncompressedSha256']
   assert score.worker_evidence(prepared,report)==row['worker_evidence']
   inventory_bytes=gzip.decompress((prepared/'validation-files.json.gz').read_bytes());files=json.loads(inventory_bytes)
   installed={f['path']:f['sha256'] for f in files};assert len(files)==spec['validationFiles']['count']
   for name in ['private-worker.mjs','private-worker.mjs.map','private-worker.map-registration.json','rolldown-runtime.js']:
    assert h(prepared/name)==installed[str(native.parent/'prepared'/name)]
   for module in spec['topology']['modules']:assert installed[module['path']]==module['sha256']
   for source,digest in sources[HEAD].items():
    full=meta['after_source']+'/'+source
    if full in installed:assert installed[full]==digest,source
   pids += [e['pid'] for e in forks]
 for i in range(6):
  if (i,'before') in rawids and (i,'after') in rawids:assert rawids[i,'before']==rawids[i,'after']
 assert len({m6.digest(m) for m in maps})==1
 configs.append({'scope':scope,'workers':workers,'tool_rows':len(rows),'full_setup_rows':len(setups),'campaign_status':result['status'],'failures':result['failures']})
 tool_count+=len(rows);setup_count+=len(setups)
assert tool_count==144 and setup_count==24 and len(pids)==len(set(pids))==2592 and len(owners)==24
archive_hashes={}
for name in ['artifact.zip','logs.zip']:
 with zipfile.ZipFile(ROOT/name) as z:
  assert z.testzip() is None;archive_hashes[name]={p:hashlib.sha256(z.read(p)).hexdigest() for p in z.namelist() if not p.endswith('/')}
(ROOT/'archive-file-hashes.json').write_text(json.dumps(archive_hashes,indent=2)+'\n')
(ROOT/'verification.json').write_text(json.dumps({'status':'Complete bounded paired scored evidence verified; measurement/scope limits apply','head':HEAD,'run':api['html_url'],'artifact_digest':artifact['digest'],'raw_files_verified':len(inventory),'source_hashes':sources,'build_metadata':meta,'configs':configs,'tool_rows':tool_count,'full_setup_rows':setup_count,'fresh_bundled_pids':len(set(pids)),'unique_prepared_artifacts':len(owners),'runtime':result['runtime'],'global_sample_gaps':score.gap_summary(all_observations),'mission_results':global_result['targets_met'],'published_ratios':True},indent=2)+'\n')
print(json.dumps({'configs':configs,'tool_rows':tool_count,'full_setup_rows':setup_count,'fresh_bundled_pids':len(set(pids))},indent=2))
