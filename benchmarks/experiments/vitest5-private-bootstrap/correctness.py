#!/usr/bin/env python3
"""One correctness-only run per tool/config. No campaign ratios or target relaxation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'benchmarks'))
import real_project as workload
import compare_candidates as campaign
import m6

STOCK = '5dfd17e'  # resolved and pinned to the full SHA in the build metadata
sha = workload.measurement.file_hash


def validate_oximut_gates(row, manifest, workers):
    assert not workload.parallelism_errors(row, workers), 'Matched concurrency/admission gate failed'
    assert not campaign.phase_errors(row, manifest), 'Original/instrumented baseline validation ledger failed'


def main(args):
    output = args.output.resolve(); output.mkdir()
    result = {'status': 'incomplete', 'policy': 'Correctness only: one run per configuration, no ratios/CIs/score; existing scope, sentinel, build and budget gates unchanged', 'configs': []}
    try:
        workload.measurement.enable_subreaper()
        head = subprocess.check_output(['git','rev-parse','HEAD'], cwd=REPO, text=True).strip()
        stock = subprocess.check_output(['git','rev-parse',STOCK], cwd=REPO, text=True).strip()
        metadata = json.loads(args.build_metadata.read_text())
        provenance = {v:{'binary_sha256':sha(getattr(args,v+'_binary'))} for v in ['before','after']}
        assert not campaign.build_errors(provenance,metadata,{'before':stock,'after':head},True), 'Strict isolated build integrity failed'
        accepted = json.loads((REPO/'benchmarks/results/2026-11-14-w13-map-base/native/runtime-provenance.json').read_text())
        info = json.loads(subprocess.check_output(['node','-p','JSON.stringify({version:process.version,arch:process.arch,platform:process.platform,execPath:process.execPath})'],text=True))
        assert info['version']=='v26.3.1' and info['platform']=='linux' and info['arch']=='x64'
        assert sha(Path(info['execPath']))==accepted['executableSha256'], 'Node differs from accepted upstream-verified native executable'
        result['runtime']={**info,'executableSha256':sha(Path(info['execPath'])),'acceptedUpstreamVerification':accepted}
        result['buildMetadata']=metadata
        bundle=output/'prepared'
        with (output/'prepare.stdout.log').open('w') as stdout,(output/'prepare.stderr.log').open('w') as stderr:
            cmd=['node','--experimental-import-meta-resolve',str(Path(__file__).with_name('integration.mjs')),str(bundle)]
            run=subprocess.run(cmd,cwd=REPO,stdout=stdout,stderr=stderr,timeout=180)
        (output/'prepare.status.json').write_text(json.dumps({'argv':cmd,'exitCode':run.returncode},indent=2)+'\n')
        assert run.returncode==0,'Bundle preparation failed'
        prepared=json.loads((bundle/'integration-manifest.json').read_text()); result['preparation']=prepared['preparation']
        auditPath=bundle/'integration-events.jsonl'
        for scope in ['colord','colord-scale']:
            manifest=workload.load_manifest(scope); expected_count=10 if scope=='colord' else 206
            for workers in [2,4]:
                workload.require_matched_concurrency(workers)
                config=output/f'{scope}-{workers}w';config.mkdir(); rows={}; before_audit=0
                if auditPath.exists(): before_audit=len(auditPath.read_text().splitlines())
                for label,tool in [('ordinary','vitest'),('stock','oximut'),('stryker','stryker'),('candidate','oximut')]:
                    directory=config/label;directory.mkdir(); project=directory/'project';report=directory/'report.json'
                    workload.prepare(project,manifest,workers)
                    source=args.stock_source if label=='stock' else REPO
                    binary=args.before_binary if label=='stock' else args.after_binary
                    opts=SimpleNamespace(binary=binary,adapter=source/'packages/vitest-adapter/m2-runner.mjs',adapter_source_dir=source/'packages/vitest-adapter',concurrency=workers,oximut_arg=[],diagnostic_unrestricted=False)
                    inputs=workload.execution_inputs(opts)
                    env={**os.environ,'CI':'true','NO_COLOR':'1','OXIMUT_BENCH_REPORT':str(report)}
                    env.pop('OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP',None)
                    if label=='candidate':env['OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP']=str(bundle/'integration-manifest.json')
                    command=workload.command(tool,opts,project,report,manifest)
                    row=workload.measurement.run_command(command,project,directory/'output.log',1200,.02,16_777_216,env)
                    row.update(execution_inputs=inputs,tool=tool,round=0,warmup=False,workload_sha256=workload.workload_digest(manifest),mutator_families=manifest['families'])
                    # Preserve raw observation before any correctness assertion.
                    m6.write_json(directory/'observation.json',row)
                    assert workload.execution_inputs(opts)==inputs,'Adapter/binary changed during command'
                    row.update(workload.validate(tool,report,project,row,manifest))
                    assert not row.get('memory_errors') and row['memory_method']=='sum-live-smaps_rollup-rss'
                    assert not campaign.reap_descendants(),'Unexpected remaining descendant after command'
                    if tool=='oximut':
                        validate_oximut_gates(row,manifest,workers)
                    row['success']=True;rows[label]=row;m6.write_json(directory/'validated.json',row)
                for label in ['stock','candidate']:
                    assert len(rows[label]['mutations'])==expected_count
                    equality=m6.compare(rows[label],rows['stryker'])
                    assert equality['ratio_eligible'],'Exact edited-file scope/classification gate failed'
                    assert rows[label]['declared_test_identity_sha256']==rows['ordinary']['declared_test_identity_sha256']
                assert m6.compare(rows['stock'],rows['candidate'])['ratio_eligible'],'Stock/candidate mutants diverged'
                raw=json.loads((config/'candidate/report.json').read_text())
                stock_raw=json.loads((config/'stock/report.json').read_text())
                assert {m['id']:m['outcome'] for m in raw['mutants']}=={m['id']:m['outcome'] for m in stock_raw['mutants']},'Exact product IDs/outcomes diverged'
                audit=[json.loads(line) for line in auditPath.read_text().splitlines()[before_audit:]]
                dispatch=[e for e in audit if e['type']=='run'];forks=[e for e in audit if e['type']=='fork']
                executed={m['id'] for m in raw['mutants'] if m['outcome']!='no-coverage'}
                assert {e['selector'] for e in dispatch}==executed and len(dispatch)==len(executed),'Bundle did not execute every required mutant exactly once'
                assert len(forks)==len(dispatch) and len({e['pid'] for e in forks})==len(forks),'Fresh fork evidence missing/reused'
                assert {e['pid'] for e in forks}=={e['pid'] for e in dispatch}
                assert all(e['entrypoint']==prepared['bundle']['path'] and prepared['bundle']['path'] in e['command'] for e in forks)
                assert any(e['type']=='stock-baseline' and e['command']=='run' for e in audit),'Missing stock baseline provenance'
                m6.write_json(config/'worker-evidence.json',audit)
                result['configs'].append({'workload':scope,'workers':workers,'mutants':expected_count,'status':'exact stock/candidate/Stryker correctness passed','bundledWorkers':len(forks)})
                m6.write_json(output/'correctness.json',result)
        result['status']='bounded product-colord correctness gate passed; not scored'
    except Exception as error:
        result['status']='failed';result['error']=repr(error)
        raise
    finally:
        m6.write_json(output/'correctness.json',result)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path)
    for name in ['before-binary','after-binary','stock-source','build-metadata']:p.add_argument('--'+name,type=Path,required=True)
    main(p.parse_args())
