#!/usr/bin/env python3
"""One unscored stock/candidate scale206 workers4 diagnostic pair. Never retries or scores."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import real_project as w
import compare_candidates as c
import m6
REPO=w.ROOT;PROBE=REPO/'benchmarks/experiments/vitest5-private-bootstrap'
read=lambda p:json.loads(p.read_text());sha=w.measurement.file_hash

def main(args):
 output=args.output.resolve();output.mkdir()
 result={'status':'incomplete','policy':'ONE UN-SCORED diagnostic pair only. All original scored results remain invalid/suppressed. No ratios, bootstrap, requalification or retries. Exact original release binaries reused; current TS adapter override selected explicitly.','roles':{}}
 try:
  w.require_matched_concurrency(4)
  head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
  meta=read(args.original_build_metadata)
  assert meta['before_commit']=='5dfd17e53cd7b067586b3ad47363cb3ecee5873e' and meta['after_commit']=='88b7d6712648979dc7438a4044abf0f63650d463'
  prov={v:{'binary_sha256':sha(getattr(args,v+'_binary'))} for v in ['before','after']}
  assert not c.build_errors(prov,meta,{'before':meta['before_commit'],'after':meta['after_commit']},True)
  subprocess.run(['git','diff','--exit-code',meta['after_commit'],head,'--','Cargo.toml','Cargo.lock','rust-toolchain.toml','crates','experiments/oxc-smoke'],check=True)
  node=json.loads(subprocess.check_output(['node','-p','JSON.stringify({version:process.version,arch:process.arch,platform:process.platform,execPath:process.execPath})'],text=True))
  assert (node['version'],node['arch'],node['platform'])==('v26.3.1','x64','linux')
  assert sha(Path(node['execPath']))=='0b52710a27133a3baf45f0d1f10689ab874f50e5821d8e611486d17e20bcacdd'
  result.update(head=head,original_build_metadata=meta,runtime={**node,'sha256':sha(Path(node['execPath']))},binary_provenance=prov)
  base={**os.environ,'CI':'true','NO_COLOR':'1'}
  assert not any(k.startswith('OXIMUT_') and v for k,v in base.items()),'Inherited experiment/profile env unsupported'
  assert not base.get('NODE_OPTIONS')
  bundle=output/'prepared';cmd=['node','--experimental-import-meta-resolve',str(PROBE/'integration.mjs'),str(bundle)]
  setup=w.measurement.run_command(cmd,REPO,output/'prepare.output.log',180,.02,16_777_216,base)
  m6.write_json(output/'prepare.observation.json',setup)
  assert setup['success'],'Diagnostic preparation failed; no product replay'
  spec=read(bundle/'integration-manifest.json')
  result['prepared_manifest_sha256']=sha(bundle/'integration-manifest.json')
  manifest=w.load_manifest('colord-scale')
  for role in ['stock','diagnostic_candidate']:
   directory=output/role;directory.mkdir();project=directory/'project';report=directory/'report.json';w.prepare(project,manifest,4)
   source=args.stock_source if role=='stock' else REPO
   opts=SimpleNamespace(binary=args.before_binary if role=='stock' else args.after_binary,adapter=source/'packages/vitest-adapter/m2-runner.mjs',adapter_source_dir=source/'packages/vitest-adapter',concurrency=4,oximut_arg=['--isolation','fresh-process'],diagnostic_unrestricted=False)
   env={**base,'OXIMUT_BENCH_REPORT':str(report)}
   if role!='stock':env['OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP']=str(bundle/'integration-manifest.json')
   inputs=w.execution_inputs(opts);command=w.command('oximut',opts,project,report,manifest)
   row=w.measurement.run_command(command,project,directory/'output.log',180,.02,16_777_216,env)
   row.update(tool='oximut',execution_inputs=inputs,experiment_env={k:v for k,v in env.items() if k.startswith('OXIMUT_')},source=head if role!='stock' else meta['before_commit'])
   m6.write_json(directory/'observation.json',row)
   evidence={'exit_code':row['exit_code'],'validation':'not yet evaluated'};result['roles'][role]=evidence
   assert inputs==w.execution_inputs(opts),'Adapter/binary changed during invocation'
   assert not c.reap_descendants(),'Invocation left descendants'
   try:
    assert not row['timed_out'] and not row['descendant_cleanup_required'] and not row.get('cleanup_error') and not row['memory_errors']
    row.update(w.validate('oximut',report,project,row,manifest))
    assert not w.parallelism_errors(row,4) and not c.phase_errors(row,manifest)
    assert len(row['mutations'])==206
    evidence['validation']='existing correctness/resource/phase/inventory gates passed for this invocation'
    m6.write_json(directory/'validated.json',row)
   except Exception as error:evidence['validation_error']=repr(error)
   if report.exists():
    raw=read(report);evidence['summary']=raw['summary'];evidence['nonordinary_mutants']=[m for m in raw['mutants'] if m['outcome'] not in ['killed','survived','no-coverage']]
   m6.write_json(output/'diagnostic-result.json',result)
  events=[json.loads(line) for line in (bundle/'integration-events.jsonl').read_text().splitlines()]
  diagnostic=bundle/'failure-diagnostics.jsonl';details=[json.loads(line) for line in diagnostic.read_text().splitlines()] if diagnostic.exists() else []
  result['trace_counts']={kind:sum(e['type']==kind for e in events) for kind in set(e['type'] for e in events)}
  result['diagnostic_events']=details
  if all('validation_error' not in r for r in result['roles'].values()):
   stock=read(output/'stock/report.json');candidate=read(output/'diagnostic_candidate/report.json')
   same={m['id']:m['outcome'] for m in stock['mutants']}=={m['id']:m['outcome'] for m in candidate['mutants']}
   result['exact_ids_outcomes_equal']=same
   result['status']='original failure not reproduced in ONE unscored pair; NOT resolved or requalified' if same else 'outcome divergence captured; stop, no retries'
  else:result['status']='diagnostic invocation failed; original cause only established if captured bounded events prove it; stop, no retries'
 except Exception as error:
  result['status']='diagnostic prerequisite/capture failed; stop, no retries';result['error']=repr(error);raise
 finally:m6.write_json(output/'diagnostic-result.json',result)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
 for name in ['before-binary','after-binary','stock-source','original-build-metadata']:p.add_argument('--'+name,type=Path,required=True)
 main(p.parse_args())
