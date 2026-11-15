"""Verify raw recovery of a FAILED campaign. No success verifier, scoring or ratios."""
from pathlib import Path
import collections,datetime,gzip,hashlib,json,statistics,subprocess,sys,tarfile,tomllib,zipfile
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path.cwd()/'benchmarks'))
import real_project as w
import compare_candidates as c
import m6
import w13_score as score
import w13_score_summary as summary
ROOT=Path('/tmp/oximut-w13-score-43669808238');raw=ROOT/'raw/raw'
HEAD='88b7d6712648979dc7438a4044abf0f63650d463';STOCK='5dfd17e53cd7b067586b3ad47363cb3ecee5873e';QUALIFIED='e9a368face7d8e3fd657383c2c979e2866370a38'
read=lambda p:json.loads(p.read_text());h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
api=read(ROOT/'run.json');artifact=read(ROOT/'artifacts.json')['artifacts'][0]
assert api['head_sha']==HEAD and api['status']=='completed' and api['conclusion']=='failure'
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
subprocess.run(['git','diff','--exit-code',QUALIFIED,HEAD,'--','packages/vitest-adapter','benchmarks/experiments/vitest5-private-bootstrap'],check=True)
for path in ['benchmarks/run.py','benchmarks/m6.py','benchmarks/real_project.py','benchmarks/compare_candidates.py','benchmarks/targets.py','benchmarks/w13_score.py','benchmarks/w13_score_summary.py']:
 assert h(Path(path))==sources[HEAD][path]
meta=read(raw/'build-metadata.json');assert meta['before_commit']==STOCK and meta['after_commit']==HEAD
provenance={v:{'binary_sha256':h(raw/'binaries'/n)} for v,n in [('before','stock'),('after','candidate')]}
assert not c.build_errors(provenance,meta,{'before':STOCK,'after':HEAD},True)
assert 'Ran 11 tests' in (raw/'harness.stderr.log').read_text()
global_result=read(raw/'campaign-summary.json');assert not global_result['correctness_gate']['passed']
assert global_result==summary.summarize(raw)
assert all(not evaluation['comparisons'] and not evaluation['targets_met'] for cfg in global_result['configs'].values() for evaluation in cfg['evaluations'].values())
start=datetime.datetime.fromisoformat(api['created_at'].replace('Z','+00:00')).timestamp();end=datetime.datetime.fromisoformat(api['updated_at'].replace('Z','+00:00')).timestamp()
configs=[];pids=[];owners=set();all_observations=[];failed=None;setup_count=0;tool_count=0;artifacts=[]
for scope,workers in summary.CONFIGS:
 d=raw/f'{scope}-{workers}w';result=read(d/'score.json');rows=read(d/'prepared-rows.json');setups=read(d/'setup-rows.json')
 assert result['head']==HEAD and result['qualified_source']==QUALIFIED
 assert result['runtime']['sha256']=='0b52710a27133a3baf45f0d1f10689ab874f50e5821d8e611486d17e20bcacdd'
 assert not result['harness_metadata']['git_status']
 expected=[(i,v,t) for i,_,v,tools in score.trial_plan() for t in tools]
 actual_keys=[(r['campaign_round'],r['variant'],r['tool']) for r in rows]
 assert actual_keys==expected[:len(rows)]
 timed_order=sorted(actual_keys,key=lambda k:(d/f'round-{k[0]}'/k[1]/k[2]/'observation.json').stat().st_mtime_ns)
 assert timed_order==actual_keys
 manifest=w.load_manifest(scope);count=10 if scope=='colord' else 206;maps=[];rawids={};diagnostics=[];failed_rows=0
 for row in [*rows,*setups]:
  assert not row['timed_out'] and not row['descendant_cleanup_required'] and not row.get('cleanup_error')
  assert not row['memory_errors'] and row['memory_method']=='sum-live-smaps_rollup-rss' and not row.get('output_truncated')
  assert all(row[k]>0 for k in c.METRICS)
  assert len(row['sample_gaps_seconds'])==row['samples']-1 and max(row['sample_gaps_seconds'])==row['max_sample_gap_seconds']
  assert 'peak_rss_snapshot' not in row
  all_observations.append(row)
 for stage in setups:
  assert stage['head']==HEAD and stage['exit_code']==0
  native=Path(next(r['cwd'] for r in rows if r['campaign_round']==stage['campaign_round'] and r['variant']=='after' and r['tool']=='oximut'))
  assert stage['command']==['node','--experimental-import-meta-resolve',meta['after_source']+'/benchmarks/experiments/vitest5-private-bootstrap/integration.mjs',str(native.parent/'prepared')]
  assert stage['cwd']==meta['after_source']
  assert (d/f'round-{stage["campaign_round"]}'/'after/oximut/prepare.observation.json').stat().st_mtime_ns<(d/f'round-{stage["campaign_round"]}'/'after/oximut/observation.json').stat().st_mtime_ns
 for row in rows:
  index,variant,tool=row['campaign_round'],row['variant'],row['tool'];location=d/f'round-{index}'/variant/tool
  report=read(location/'report.json');project=location/'project';native=Path(row['cwd']);obs=read(location/'observation.json')
  assert start<=(location/'observation.json').stat().st_mtime<=end
  assert all(obs[k]==row[k] for k in c.METRICS)
  w.verify_inputs(project,manifest)
  assert ('OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP' in row['experiment_env'])==((variant,tool)==('after','oximut'))
  if row['exit_code'] not in (0,1):
   failed_rows+=1;assert (scope,workers,index,variant,tool,row['exit_code'])==('colord-scale',4,0,'after','oximut',2)
   assert not report['summary']['complete'] and report['summary']['infrastructureError']==4
  assert w.validate_tests(tool,report,native,manifest)==m6.digest(sorted(manifest['expectedTests']))
  if tool=='oximut':
   assert row['command'][row['command'].index('--execution')+1]=='persistent'
   assert row['command'][row['command'].index('--isolation')+1]=='fresh-process'
   assert '--no-shared-controller' not in row['command'] and '--no-cache' in row['command']
   checkrow={**row,'resources':report['resources']}
   assert not w.parallelism_errors(checkrow,workers) and not c.phase_errors(checkrow,manifest)
   assert report['baseline']['outcome']=='passed' and report['baseline']['tests']=={'passed':102,'failed':0,'skipped':0}
   assert report['baseline']['coverage']['complete'] and not report['baseline']['coverage']['reasons']
   actual={m6.mutation_identity(Path(m['file']).relative_to(native).as_posix(),(project/Path(m['file']).relative_to(native)).read_text(),m['location'],m['replacement']):m['outcome'] for m in report['mutants']}
   rawids[index,variant]={m['id']:m for m in report['mutants']}
  elif tool=='stryker':
   actual={};status={'Killed':'killed','Survived':'survived','NoCoverage':'no-coverage'}
   for filename,source in report['files'].items():
    p=Path(filename);relative=p.relative_to(native).as_posix() if p.is_absolute() else p.as_posix();text=(project/relative).read_text();assert source['source']==text
    for mutant in source['mutants']:
     if mutant['status']=='Ignored':assert mutant['mutatorName'] in report['config']['mutator']['excludedMutations'];continue
     actual[m6.mutation_identity(relative,text,mutant['location'],mutant['replacement'])]=status[mutant['status']]
  else:actual={}
  if tool!='vitest':
   assert len(actual)==count
   if row['exit_code']!=2:assert actual=={k:v['outcome'] for k,v in row['mutations'].items()};maps.append(actual)
   sentinel=manifest['sentinel'];text=(project/sentinel['file']).read_text();key=sentinel['file']+':'+hashlib.sha256(text.replace(sentinel['original'],sentinel['replacement'],1).encode()).hexdigest();assert actual[key]=='killed'
  if (variant,tool)==('after','oximut'):
   prepared=location/'prepared';spec=read(prepared/'integration-manifest.json');events=[json.loads(l) for l in (prepared/'integration-events.jsonl').read_text().splitlines()]
   assert spec['owner'] not in owners;owners.add(spec['owner'])
   counts=dict(collections.Counter(e['type'] for e in events));forks=[e for e in events if e['type']=='fork'];runs=[e for e in events if e['type']=='run']
   valid=[e['controller'] for e in events if e['type']=='validated-controller'];controllers={e['controller'] for e in events}
   assert len(valid)==len(controllers) and set(valid)==controllers and all(e['owner']==spec['owner'] for e in events)
   assert len({e['pid'] for e in forks})==len(forks)==len(runs)
   assert {e['pid'] for e in forks}=={e['pid'] for e in runs}
   assert all(e['entrypoint']==spec['bundle']['path'] and e['entrypoint'] in e['command'] for e in forks)
   if row['exit_code']==2:
    assert counts['validated-fork-artifact']==206 and len(forks)==205
    missing=set(rawids[index,variant])-{e['selector'] for e in runs};assert missing=={'4c4dd13e31689434138c647f612e775284ce81b1544891e102cfd99539e2a1d0'}
    bad=[{'id':m['id'],'relative_file':Path(m['file']).relative_to(native).as_posix(),'location':m['location'],'candidate_outcome':m['outcome'],'stock_outcome':rawids[index,'before'][m['id']]['outcome'],'tests':m['tests'],'errors':m['errors']} for m in report['mutants'] if m['outcome']=='infrastructure-error']
    assert len(bad)==4 and collections.Counter(m['stock_outcome'] for m in bad)=={'killed':3,'survived':1}
    assert {key for key,m in rawids[index,variant].items() if m['outcome']!=rawids[index,'before'][key]['outcome']}=={m['id'] for m in bad}
    failed={'relative_directory':str(location.relative_to(raw)),'summary':report['summary'],'mismatches':bad,'trace_counts':counts,'missing_dispatch_selectors':sorted(missing),'observation':{k:row[k] for k in ['exit_code','wall_seconds','cpu_seconds','peak_aggregate_rss_bytes','memory_method','timed_out','descendant_cleanup_required','memory_errors']},'cause':'Unattributed batch error; precise underlying start/aggregate error is not exposed. Trace gap lies between configured artifact validation and recorded fork/start dispatch. No root cause is proven.'}
   else:assert len(forks)==count and {e['selector'] for e in runs}==set(rawids[index,variant]);assert score.worker_evidence(prepared,report)==row['worker_evidence']
   assert spec['node']['sha256']==result['runtime']['sha256'] and len(spec['topology']['selected'])==6 and len(spec['topology']['shared'])==15
   assert not set(spec['topology']['selected'])&set(spec['topology']['shared'])
   for fn,digest in [('private-worker.mjs',spec['bundle']['sha256']),('private-worker.mjs.map',spec['bundle']['mapSha256']),('private-worker.map-registration.json',spec['bundle']['registration']['sha256'])]:assert h(prepared/fn)==digest
   assert spec['bundle']['registration']['helperSha256']==sources[HEAD]['benchmarks/experiments/vitest5-private-bootstrap/map-registration.mjs']
   assert h(prepared/'validation-files.json.gz')==spec['validationFiles']['sha256']
   inv=gzip.decompress((prepared/'validation-files.json.gz').read_bytes());assert hashlib.sha256(inv).hexdigest()==spec['validationFiles']['uncompressedSha256']
   files=json.loads(inv);assert len(files)==spec['validationFiles']['count'];installed={f['path']:f['sha256'] for f in files}
   for fn in ['private-worker.mjs','private-worker.mjs.map','private-worker.map-registration.json','rolldown-runtime.js']:assert h(prepared/fn)==installed[str(native.parent/'prepared'/fn)]
   for module in spec['topology']['modules']:assert installed[module['path']]==module['sha256']
   for path,digest in sources[HEAD].items():
    full=meta['after_source']+'/'+path
    if full in installed:assert installed[full]==digest
   artifacts.append({'configuration':f'{scope}-{workers}w','round':index,'owner':spec['owner'],'manifest_sha256':h(prepared/'integration-manifest.json'),'bundle':spec['bundle'],'inventory':spec['validationFiles']})
   mapslog=[json.loads(l) for l in (prepared/'map-events.jsonl').read_text().splitlines()]
   diagnostics.append({'round':index,'warmup':index==0,'controllers':len(valid),'forks':len(forks),'dispatches':len(runs),'configured_forks':counts['validated-fork-artifact'],'controller_validation_wall_seconds':sum(e['wallMs'] for e in events if e['type']=='validated-controller')/1000,'controller_validation_cpu_seconds':sum(sum(e['cpu'].values()) for e in events if e['type']=='validated-controller')/1e6,'worker_validation_wall_seconds':sum(e['wallMs'] for e in events if e['type']=='validated-fork-artifact')/1000,'worker_validation_cpu_seconds':sum(sum(e['cpu'].values()) for e in events if e['type']=='validated-fork-artifact')/1e6,'map_events':dict(collections.Counter(e['type'] for e in mapslog)),'map_lazy_read_bytes':sum(e.get('bytes',0) for e in mapslog if e['type']=='map-loaded'),'map_partial_callback_cpu_seconds':sum(sum(e.get('callbackCPU',{}).values()) for e in mapslog)/1e6,'validation_inventory_entries':len(files),'validation_inventory_json_bytes':len(inv),'bundle_bytes':(prepared/'private-worker.mjs').stat().st_size,'map_bytes':(prepared/'private-worker.mjs.map').stat().st_size})
   pids += [e['pid'] for e in forks]
 for i in range(6):
  if (i,'before') in rawids and (i,'after') in rawids and not failed_rows:assert {k:m['outcome'] for k,m in rawids[i,'before'].items()}=={k:m['outcome'] for k,m in rawids[i,'after'].items()}
 assert len({m6.digest(m) for m in maps})==1
 configs.append({'configuration':f'{scope}-{workers}w','tool_rows':len(rows),'full_preparations':len(setups),'status':result['status'],'failed_rows':failed_rows,'sample_gaps':result['sample_gaps'],'diagnostics':diagnostics})
 tool_count+=len(rows);setup_count+=len(setups)
assert tool_count==113 and setup_count==19 and len(owners)==19 and len(pids)==len(set(pids))==1561 and failed
archive_hashes={}
for fn in ['artifact.zip','logs.zip']:
 with zipfile.ZipFile(ROOT/fn) as z:
  assert z.testzip() is None;archive_hashes[fn]={p:hashlib.sha256(z.read(p)).hexdigest() for p in z.namelist() if not p.endswith('/')}
(ROOT/'archive-file-hashes.json').write_text(json.dumps(archive_hashes,indent=2)+'\n')
result={'status':'Raw failed-campaign recovery verified; scoring INVALID/INCOMPLETE, no ratios or acceptance','run':api['html_url'],'head':HEAD,'qualified_source':QUALIFIED,'artifact_id':artifact['id'],'artifact_digest':artifact['digest'],'raw_files_verified':len(inventory),'source_hashes':sources,'runtime':read(raw/'colord-2w/score.json')['runtime'],'build_metadata':meta,'configs':configs,'failure':failed,'tool_rows':tool_count,'full_preparations':setup_count,'unique_artifacts':len(owners),'distinct_recorded_bundled_pids':len(set(pids)),'expected_complete':{'tool_rows':144,'full_preparations':24,'unique_artifacts':24,'distinct_bundled_pids':2592},'global_sample_gaps':score.gap_summary(all_observations),'artifact_provenance':artifacts,'success_verifier_executed':False,'ratios_published':False,'mission':{'wall':'unassessed: correctness gate failed','cpu':'unassessed: correctness gate failed','rss':'unassessed: correctness gate failed'}}
(ROOT/'failure-recovery.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['status','raw_files_verified','tool_rows','full_preparations','unique_artifacts','distinct_recorded_bundled_pids','global_sample_gaps']},indent=2))
