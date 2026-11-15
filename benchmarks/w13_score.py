#!/usr/bin/env python3
"""Bounded W13 paired scoring. Never builds, retries, profiles or changes candidate semantics."""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_candidates as campaign
import m6
import real_project as workload

REPO = workload.ROOT
HOOK = 'OXIMUT_EXPERIMENT_PRIVATE_BOOTSTRAP'
STOCK = '5dfd17e53cd7b067586b3ad47363cb3ecee5873e'
PROBE = REPO / 'benchmarks/experiments/vitest5-private-bootstrap'
METRICS = campaign.METRICS
POLICY = {
    'preparation': 'Fresh owned artifact per candidate invocation, including warmup. Full external Node command measured from spawn through exit and descendant reaping. No amortization or subtraction.',
    'prepared': 'Actual CLI process tree, including controller inventory validation, stock baselines, per-worker validation, map consumption and all existing diagnostics. Nothing subtracted.',
    'first_use': 'Per-round setup+candidate wall and wait4 CPU sums; maximum of the two sequential process-tree RSS peaks. Totals formed before paired ratios/bootstrap.',
    'memory': 'One harness sum-live-smaps_rollup-rss peak per command; never add Rust RSS. Sampled observations can miss short peaks; shared pages counted per process; harness excluded. Requested interval is not achieved interval; all actual sample gaps retained.',
    'excluded': 'Dependency install, isolated release builds, fixture provisioning and post-command validators are outside every tool timing. Candidate artifact preparation is explicitly included in first-use results. No profilers.',
    'scope': 'Only declared colord10/scale206, workers2/4, persistent shared controller, fresh OS test forks, selection and no cache. No product/general noninferiority claim.',
}


def require_mode(workers, extra_args=()):
    if workers not in (2, 4) or extra_args:
        raise ValueError('W13 scoring supports only workers2/4 persistent shared fresh-process mode; no mode overrides')


def trial_plan(warmups=1, repetitions=5):
    """Keep campaign's paired variant balance, rotate its tool order over six rounds."""
    for index, warmup, variant, tools in campaign.plan(warmups, repetitions):
        shift = (index // 2) % len(tools)
        yield index, warmup, variant, tools[shift:] + tools[:shift]


def clean_env(ambient):
    # Refuse contamination rather than silently clean unsupported inherited modes.
    invalid = [k for k, v in ambient.items() if v and (
        k.startswith('OXIMUT_') or k.startswith('STRYKER_') or
        k in ('NODE_OPTIONS', 'NODE_PATH', 'VITEST_POOL_ID', 'VITEST_WORKER_ID'))]
    if invalid:
        raise ValueError(f'Unsupported inherited scoring environment: {sorted(invalid)}')
    return {**ambient, 'CI': 'true', 'NO_COLOR': '1'}


def command_env(base, report, variant, tool, prepared_dir=None):
    if HOOK in base:
        raise ValueError('Candidate hook must not be inherited')
    env = {**base, 'OXIMUT_BENCH_REPORT': str(report)}
    if prepared_dir is not None:
        if (variant, tool) != ('after', 'oximut'):
            raise ValueError('Artifact hook is only allowed on candidate Oximut')
        env[HOOK] = str(prepared_dir / 'integration-manifest.json')
    elif (variant, tool) == ('after', 'oximut'):
        raise ValueError('Candidate requires its own measured fresh preparation')
    return env


def require_observation(row, *, setup=False):
    failures = [key for key in ('timed_out', 'descendant_cleanup_required', 'cleanup_error',
                               'spawn_error', 'memory_errors', 'output_truncated') if row.get(key)]
    if row.get('exit_code') not in ((0,) if setup else (0, 1)):
        failures.append('exit_code')
    if row.get('memory_method') != 'sum-live-smaps_rollup-rss':
        failures.append('memory_method')
    for metric in METRICS:
        value = row.get(metric)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            failures.append(metric)
    if failures:
        raise ValueError(f'Failed external measurement: {failures}')


def first_use(candidate, setup):
    require_observation(setup, setup=True)
    require_observation(candidate)
    total = copy.deepcopy(candidate)
    for key in ('wall_seconds', 'cpu_seconds', 'cpu_user_seconds', 'cpu_system_seconds'):
        total[key] = candidate[key] + setup[key]
    total['peak_aggregate_rss_bytes'] = max(candidate['peak_aggregate_rss_bytes'], setup['peak_aggregate_rss_bytes'])
    total['cost_scope'] = 'first-use: full preparation command + prepared execution'
    total['sequential_components'] = {
        'setup_observation': setup['observation_file'],
        'prepared_observation': candidate['observation_file'],
        'metrics': {name: {'setup': setup[name], 'execution': candidate[name], 'total': total[name]}
                    for name in METRICS},
        'native_phase_ledger_scope': 'CLI execution only, unchanged; preparation is separate',
    }
    return total


def gap_summary(rows):
    values = sorted(gap for row in rows for gap in row.get('sample_gaps_seconds', []))
    if not values:
        return {'count': 0}
    return {'count': len(values), 'min': values[0], 'median': statistics.median(values),
            'p95': values[math.ceil(.95 * len(values))-1], 'p99': values[math.ceil(.99 * len(values))-1],
            'max': values[-1], 'unit': 'seconds', 'requested_interval_seconds': .02}


def worker_evidence(prepared_dir, candidate_report):
    spec = json.loads((prepared_dir / 'integration-manifest.json').read_text())
    events = [json.loads(line) for line in (prepared_dir / 'integration-events.jsonl').read_text().splitlines()]
    runs = [e for e in events if e['type'] == 'run']
    forks = [e for e in events if e['type'] == 'fork']
    validated = [e['controller'] for e in events if e['type'] == 'validated-controller']
    controllers = {e['controller'] for e in events}
    executed = {m['id'] for m in candidate_report['mutants'] if m['outcome'] != 'no-coverage'}
    if ({e['selector'] for e in runs} != executed or len(runs) != len(executed)
            or len(forks) != len(runs) or len({e['pid'] for e in forks}) != len(forks)
            or {e['pid'] for e in forks} != {e['pid'] for e in runs}
            or not all(e['entrypoint'] == spec['bundle']['path'] and spec['bundle']['path'] in e['command'] for e in forks)
            # Main retires controllers after 50 requests. Every actual controller
            # still owes one full validation, including recycled controllers.
            or set(validated) != controllers or len(validated) != len(controllers)
            or not all(e['owner'] == spec['owner'] for e in events)
            or len([e for e in events if e['type'] == 'validated-fork-artifact']) != len(forks)
            or not any(e['type'] == 'stock-baseline' and e['command'] == 'run' for e in events)):
        raise ValueError('Actual bundled fresh-fork/selector/validation/baseline evidence failed')
    return {'events': len(events), 'fresh_forks': len(forks), 'validated_controllers': sorted(controllers),
            'manifest_sha256': workload.measurement.file_hash(prepared_dir / 'integration-manifest.json'),
            'owner': spec['owner'], 'bundle': spec['bundle'], 'validationFiles': spec['validationFiles'],
            'internal_preparation_diagnostic': spec['preparation']}


def evaluate(rows, setups, provenance, metadata, commits, workers, scope, failures=()):
    keyed = {r['campaign_round']: r for r in setups}
    errors = list(failures)
    expected = set(range(6))
    if set(keyed) != expected or len(setups) != len(expected):
        errors.append('Missing/duplicate preparation observations')
    cold = []
    try:
        for row in rows:
            cold.append(first_use(row, keyed[row['campaign_round']])
                        if row['variant'] == 'after' and row['tool'] == 'oximut' else copy.deepcopy(row))
    except (KeyError, ValueError) as error:
        errors.append(str(error))
    opts = dict(require_build_integrity=True, build_metadata=metadata, candidate_commits=commits,
                concurrency=workers, workload_name=scope)
    prepared = campaign.evaluate(rows, 1, 5, provenance, errors, **opts)
    first = campaign.evaluate(cold, 1, 5, provenance, errors, **opts)
    return {'prepared': prepared, 'first_use': first}, cold


def main(args):
    require_mode(args.workers)
    workload.require_matched_concurrency(args.workers)
    env = clean_env(os.environ)
    output = args.output.resolve()
    if output.is_relative_to(REPO):
        raise ValueError('All generated scoring inputs/outputs must be outside the source/install tree')
    output.mkdir()
    rows, setups, failures = [], [], []
    result = {'status': 'incomplete', 'policy': POLICY, 'scope': args.workload, 'workers': args.workers,
              'order': list(trial_plan())}
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
        metadata = json.loads(args.build_metadata.read_text())
        opts = {v: SimpleNamespace(binary=getattr(args, v + '_binary').resolve(),
                adapter=source / 'packages/vitest-adapter/m2-runner.mjs',
                adapter_source_dir=source / 'packages/vitest-adapter', concurrency=args.workers,
                oximut_arg=['--isolation', 'fresh-process'], diagnostic_unrestricted=False)
                for v, source in [('before', args.stock_source.resolve()), ('after', REPO)]}
        provenance = {v: workload.execution_inputs(o) for v, o in opts.items()}
        commits = {'before': STOCK, 'after': head}
        # Scoring-only commits may differ; executable adapter/probe inputs must be
        # byte-identical to the exact source whose native gates were accepted.
        qualified = subprocess.check_output(['git', 'rev-parse', args.qualified_source], cwd=REPO, text=True).strip()
        qualified_files = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', qualified,
            'packages/vitest-adapter', 'benchmarks/experiments/vitest5-private-bootstrap'], cwd=REPO, text=True).splitlines()
        executable_files = [p for p in qualified_files if Path(p).suffix in ('.ts', '.mjs', '.json', '.py')]
        subprocess.run(['git', 'diff', '--exit-code', qualified, head, '--', *executable_files], cwd=REPO, check=True)
        errors = campaign.build_errors(provenance, metadata, commits, True)
        if errors:
            raise ValueError(errors)
        accepted = json.loads((REPO / 'benchmarks/results/2026-11-14-w13-map-base/native/runtime-provenance.json').read_text())
        node = json.loads(subprocess.check_output(['node', '-p', 'JSON.stringify({version:process.version,arch:process.arch,platform:process.platform,execPath:process.execPath})'], text=True))
        if (node['version'], node['platform'], node['arch']) != ('v26.3.1', 'linux', 'x64') or workload.measurement.file_hash(Path(node['execPath'])) != accepted['executableSha256']:
            raise ValueError('Runtime is not accepted upstream-verified native Node26.3.1 linux-x64')
        pins = {'@stryker-mutator/core': '10.1.1', '@stryker-mutator/vitest-runner': '10.1.1', 'vitest': '5.1.0', 'vite': '8.3.5'}
        for name, version in pins.items():
            if json.loads((REPO / 'node_modules' / name / 'package.json').read_text())['version'] != version:
                raise ValueError(f'Unsupported installed dependency: {name}')
        result.update(head=head, commits=commits, qualified_source=qualified, unchanged_qualified_files=executable_files,
                      runtime={**node, 'sha256': accepted['executableSha256'], 'accepted_upstream_verification': accepted},
                      build_metadata=metadata, execution_inputs=provenance, pins=pins,
                      environment={'CI': 'true', 'NO_COLOR': '1', 'hook': 'candidate Oximut only', 'inherited': 'environment.json.gz'},
                      harness_metadata=workload.measurement.metadata())
        with gzip.open(output / 'environment.json.gz', 'wt') as f:
            json.dump({k: v for k, v in env.items() if k in ('PATH', 'LANG', 'TZ', 'CI', 'NO_COLOR') or k.startswith('LC_')}, f, sort_keys=True)
        manifest = workload.load_manifest(args.workload)
        expected_count = 10 if args.workload == 'colord' else 206
        raw_ids = {}
        for index, warmup, variant, tools in trial_plan():
            for tool in tools:
                directory = output / f'round-{index}' / variant / tool
                directory.mkdir(parents=True)
                project, report = directory / 'project', directory / 'report.json'
                workload.prepare(project, manifest, args.workers)
                prepared_dir = None
                if variant == 'after' and tool == 'oximut':
                    prepared_dir = directory / 'prepared'
                    cmd = ['node', '--experimental-import-meta-resolve', str(PROBE / 'integration.mjs'), str(prepared_dir)]
                    setup = workload.measurement.run_command(cmd, REPO, directory / 'prepare.output.log', 180, .02, 16_777_216, env, record_sample_gaps=True)
                    setup.update(campaign_round=index, warmup=warmup, observation_file=str(directory / 'prepare.observation.json'), head=head, mode='fresh artifact; full external preparation command')
                    setups.append(setup)
                    m6.write_json(Path(setup['observation_file']), setup)
                    require_observation(setup, setup=True)
                    if campaign.reap_descendants():
                        raise ValueError('Preparation left descendants')
                trial_env = command_env(env, report, variant, tool, prepared_dir)
                command = workload.command(tool, opts[variant], project, report, manifest)
                row = workload.measurement.run_command(command, project, directory / 'output.log', 1200, .02, 16_777_216, trial_env, record_sample_gaps=True)
                row.update(campaign_round=index, round=0, warmup=warmup, variant=variant, tool=tool,
                           head=head, execution_inputs=provenance[variant], workload_sha256=workload.workload_digest(manifest),
                           mutator_families=manifest['families'], observation_file=str(directory / 'observation.json'),
                           experiment_env={k: v for k, v in trial_env.items() if k.startswith('OXIMUT_')},
                           cost_scope='prepared execution' if prepared_dir else 'stock execution')
                rows.append(row)
                m6.write_json(Path(row['observation_file']), row)
                require_observation(row)
                row.update(workload.validate(tool, report, project, row, manifest))
                if workload.execution_inputs(opts[variant]) != provenance[variant]:
                    raise ValueError('Binary/adapter inputs changed during trial')
                if campaign.reap_descendants():
                    raise ValueError('Product command left descendants')
                if tool == 'oximut':
                    errors = workload.parallelism_errors(row, args.workers) + campaign.phase_errors(row, manifest)
                    if errors:
                        raise ValueError(errors)
                    raw = json.loads(report.read_text())
                    raw_ids[(index, variant)] = {m['id']: m['outcome'] for m in raw['mutants']}
                    if len(raw_ids[(index, variant)]) != expected_count:
                        raise ValueError('Unexpected exact product mutant count')
                    if prepared_dir:
                        row['worker_evidence'] = worker_evidence(prepared_dir, raw)
                row['success'] = True
                m6.write_json(directory / 'validated.json', row)
            # No subsequent invocation after a substantive group invariant failure.
            group = [r for r in rows if r['campaign_round'] == index and r['variant'] == variant]
            compared, _ = workload.compare_rows(group)
            if len(compared) != 1 or not compared[0]['ratio_eligible']:
                raise ValueError('Exact stock/Stryker/inventory/classification group gate failed')
            if all((index, v) in raw_ids for v in campaign.VARIANTS) and raw_ids[(index, 'before')] != raw_ids[(index, 'after')]:
                raise ValueError('Exact stock/candidate product mutant IDs/outcomes diverged')
            reference = next(r for r in rows if r['tool'] == 'oximut')
            if any(not m6.compare(reference, r)['ratio_eligible'] for r in rows if r['tool'] != 'vitest'):
                raise ValueError('Mutation outcomes/edited-source identities changed across rounds')
        result['status'] = 'completed'
    except Exception as error:
        failures.append(repr(error))
        result['status'] = 'failed; stop campaign, no retries or ratios'
        raise
    finally:
        if 'provenance' in locals() and 'metadata' in locals() and 'commits' in locals():
            evaluations, cold = evaluate(rows, setups, provenance, metadata, commits, args.workers, args.workload, failures)
            result['evaluations'] = evaluations
            m6.write_json(output / 'first-use-rows.json', cold)
        result.update(failures=failures, sample_gaps={'setup': gap_summary(setups),
                      **{v + '/' + t: gap_summary([r for r in rows if r['variant'] == v and r['tool'] == t])
                         for v in campaign.VARIANTS for t in ('vitest', 'oximut', 'stryker')}})
        m6.write_json(output / 'prepared-rows.json', rows)
        m6.write_json(output / 'setup-rows.json', setups)
        m6.write_json(output / 'score.json', result)
    if not all(r['correctness_gate']['passed'] for r in result['evaluations'].values()):
        raise ValueError('Final strict campaign evaluation failed; ratios suppressed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, choices=(2, 4), required=True)
    parser.add_argument('--workload', choices=('colord', 'colord-scale'), required=True)
    parser.add_argument('--qualified-source', required=True, help='Exact native structural+product requalification source SHA')
    for name in ('before-binary', 'after-binary', 'stock-source', 'build-metadata'):
        parser.add_argument('--' + name, type=Path, required=True)
    main(parser.parse_args())
