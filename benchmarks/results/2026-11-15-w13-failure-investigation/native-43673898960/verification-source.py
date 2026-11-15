"""Read-only verification of ONE unscored capture. No scores, ratios or requalification."""
from pathlib import Path
import gzip,hashlib,json,subprocess,sys,tarfile,zipfile
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path.cwd()/'benchmarks'))
import real_project as w
import compare_candidates as c
import m6
ROOT=Path('/tmp/oximut-w13-unscored-43673898960');raw=ROOT/'raw/raw';HEAD='a3254ef2aeae8218055be18b4e8b67487395b4e2'
read=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
api=read(ROOT/'run.json');artifact=read(ROOT/'artifacts.json')['artifacts'][0]
assert api['head_sha']==HEAD and api['status']=='completed' and api['conclusion']=='success'
assert 'sha256:'+sha(ROOT/'artifact.zip')==artifact['digest']
inventory=read(ROOT/'artifact/w13-failure-diagnostic-files.json')
for name,digest in inventory.items():assert sha(raw/name)==digest,name
sources={}
for archive,rev in [('diagnostic-source.tar.gz',HEAD),('original-candidate-source.tar.gz','88b7d6712648979dc7438a4044abf0f63650d463'),('original-stock-source.tar.gz','5dfd17e53cd7b067586b3ad47363cb3ecee5873e')]:
 with tarfile.open(raw/archive) as t:
  members=[m for m in t.getmembers() if m.isfile()];pos=0;hashes={}
  body=subprocess.check_output(['git','cat-file','--batch'],input=''.join(rev+':'+m.name+'\n' for m in members).encode())
  for m in members:
   e=body.index(b'\n',pos);n=int(body[pos:e].split()[2]);pos=e+1;expected=body[pos:pos+n];pos+=n+1;actual=t.extractfile(m).read()
   assert actual==expected,m.name;hashes[m.name]=hashlib.sha256(actual).hexdigest()
  sources[rev]=hashes
meta=read(raw/'original-build-metadata.json');proof={v:{'binary_sha256':sha(raw/'binaries'/label)} for v,label in [('before','stock'),('after','candidate')]}
assert not c.build_errors(proof,meta,{'before':meta['before_commit'],'after':meta['after_commit']},True)
subprocess.run(['git','diff','--exit-code',meta['after_commit'],HEAD,'--','Cargo.toml','Cargo.lock','rust-toolchain.toml','crates','experiments/oxc-smoke'],check=True)
focused=read(raw/'focused.observation.json');assert focused['success'] and not focused['descendant_cleanup_required'] and not focused['memory_errors']
assert 'pass 9' in (raw/'focused.output.log').read_text() and 'fail 0' in (raw/'focused.output.log').read_text()
faults=[];births=[]
for path in sorted((raw/'focused-details').glob('*.json')):
 data=read(path);assert data['width']==4
 assert all(r['outcome']=='infrastructure-error' for r in data['failed']['lanes'])
 assert len(data['recovered']['lanes'])==4 and all(r['outcome']=='passed' for r in data['recovered']['lanes'])
 assert any('original nested '+data['stage'] in json.dumps(record) for record in data['records'])
 assert any(r['stage'] in ['batch-unhandled','batch-run'] for r in data['records'])
 assert len({(b['pid'],b['ticks']) for b in data['births']})==len(data['births'])
 faults.append({'stage':data['stage'],'births':len(data['births']),'diagnostic_events':len(data['records']),'failed_outcomes':[r['outcome'] for r in data['failed']['lanes']],'recovery':'four lanes passed; test verified every recorded original PID/birth gone before owner close'})
 births += data['births']
assert len(faults)==5 and len({b['pid'] for b in births})==len(births)==39
q=raw/'replay';result=read(q/'diagnostic-result.json')
assert result['head']==HEAD and result['status']=='original failure not reproduced in ONE unscored pair; NOT resolved or requalified'
assert result['exact_ids_outcomes_equal'] and result['diagnostic_events']==[]
assert result['runtime']['sha256']=='0b52710a27133a3baf45f0d1f10689ab874f50e5821d8e611486d17e20bcacdd'
assert read(q/'prepare.observation.json')['success']
manifest=w.load_manifest('colord-scale');maps={};rows={};reports={}
for role in ['stock','diagnostic_candidate']:
 directory=q/role;row=read(directory/'validated.json');report=read(directory/'report.json');native=Path(row['cwd']);project=directory/'project'
 assert row['exit_code']==1 and report['summary']['complete']
 assert not row['timed_out'] and not row['descendant_cleanup_required'] and not row.get('cleanup_error') and not row['memory_errors']
 assert row['memory_method']=='sum-live-smaps_rollup-rss' and not row.get('peak_rss_snapshot')
 assert w.validate_tests('oximut',report,native,manifest)==m6.digest(sorted(manifest['expectedTests']))
 assert not c.phase_errors(row,manifest) and not w.parallelism_errors(row,4)
 w.verify_inputs(project,manifest)
 actual={m6.mutation_identity(Path(m['file']).relative_to(native).as_posix(),(project/Path(m['file']).relative_to(native)).read_text(),m['location'],m['replacement']):m['outcome'] for m in report['mutants']}
 assert len(actual)==206 and actual=={k:v['outcome'] for k,v in row['mutations'].items()}
 assert bool(row['experiment_env'].get('OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP'))==(role=='diagnostic_candidate')
 maps[role]=actual;rows[role]=row;reports[role]=report
assert maps['stock']==maps['diagnostic_candidate']
assert {m['id']:m['outcome'] for m in reports['stock']['mutants']}=={m['id']:m['outcome'] for m in reports['diagnostic_candidate']['mutants']}
spec=read(q/'prepared/integration-manifest.json');bundle=spec['bundle'];registration=bundle['registration']
assert len(spec['topology']['selected'])==6 and len(spec['topology']['shared'])==15 and not set(spec['topology']['selected'])&set(spec['topology']['shared'])
assert sha(q/'prepared/private-worker.mjs')==bundle['sha256'] and sha(q/'prepared/private-worker.mjs.map')==bundle['mapSha256']
assert sha(q/'prepared/private-worker.map-registration.json')==registration['sha256']
assert registration['helperSha256']==sources[HEAD]['benchmarks/experiments/vitest5-private-bootstrap/map-registration.mjs']
assert sha(q/'prepared/validation-files.json.gz')==spec['validationFiles']['sha256']
inv=gzip.decompress((q/'prepared/validation-files.json.gz').read_bytes());assert hashlib.sha256(inv).hexdigest()==spec['validationFiles']['uncompressedSha256']
installed={f['path']:f['sha256'] for f in json.loads(inv)}
for module in spec['topology']['modules']:assert installed[module['path']]==module['sha256']
for path,digest in sources[HEAD].items():
 name='/home/runner/work/oximut/oximut/'+path
 if name in installed:assert installed[name]==digest
trace=[json.loads(l) for l in (q/'prepared/integration-events.jsonl').read_text().splitlines()]
forks=[e for e in trace if e['type']=='fork'];runs=[e for e in trace if e['type']=='run']
assert len(forks)==len(runs)==len({e['pid'] for e in forks})==206
assert {e['pid'] for e in forks}=={e['pid'] for e in runs}
assert {e['selector'] for e in runs}=={m['id'] for m in reports['diagnostic_candidate']['mutants']}
assert all(e['entrypoint']==bundle['path'] and e['entrypoint'] in e['command'] for e in forks)
assert not (q/'prepared/failure-diagnostics.jsonl').exists()
archives={}
for name in ['artifact.zip','logs.zip']:
 with zipfile.ZipFile(ROOT/name) as z:
  assert z.testzip() is None;archives[name]={p:hashlib.sha256(z.read(p)).hexdigest() for p in z.namelist() if not p.endswith('/')}
(ROOT/'archive-file-hashes.json').write_text(json.dumps(archives,indent=2)+'\n')
record={'status':'ONE unscored capture recovered; original failure NOT reproduced, NOT resolved or requalified','run':api['html_url'],'head':HEAD,'artifact_digest':artifact['digest'],'raw_files_verified':len(inventory),'source_hashes':sources,'runtime':result['runtime'],'binaries':proof,'focused_tests':9,'four_lane_faults':faults,'distinct_fault_test_pids':39,'candidate_distinct_pids':206,'exact_scope_and_outcomes':True,'native_result':result,'limits':'One stock/candidate invocation, no original cause recovered, no scoring/ratios or requalification. Old failed campaign remains invalid; no more replays.'}
(ROOT/'verification.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:record[k] for k in ['status','raw_files_verified','focused_tests','distinct_fault_test_pids','candidate_distinct_pids']},indent=2))
