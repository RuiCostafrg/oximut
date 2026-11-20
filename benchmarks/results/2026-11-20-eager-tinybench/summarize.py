"""Reconcile exact historical workload and compare matched tinybench diagnostics."""

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path

PREVIOUS = Path(__file__).resolve().parent.parent / '2026-11-19-fresh-worker-startup/summarize.py'
spec = importlib.util.spec_from_file_location('startup_evidence', PREVIOUS)
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def capture(run, historical, experiment, build):
    qualification, mutants, baselines, batches = startup.selected_workload(run, historical, True)
    startup.require(qualification['outcomes'] == {'killed': 202, 'survived': 4}
                    and qualification['whole_file_selection'] == {'files': 204, 'full': 2},
                    'unexpected outcome or selection counts')
    root = run / 'capture'
    diagnostic = startup.read_json(root / 'diagnostic.json')
    inputs = diagnostic['execution_inputs']
    startup.require(diagnostic['source_commit'] == build['commit']
                    and inputs['binary_sha256'] == experiment['binary_sha256'] == build['binary_sha256'],
                    'capture source/binary mismatch')
    files = build['source_sha256']
    startup.require(all(files[name] == digest for name, digest in diagnostic['helper_sha256'].items())
                    and {p.removeprefix('packages/vitest-adapter/'): h for p, h in files.items()
                         if p.startswith('packages/vitest-adapter/')} == inputs['adapter_source_sha256']
                    and files['package-lock.json'] == experiment['lock_sha256'], 'source inputs changed')
    profiles = root / 'profiles'
    phases = startup.worker_metrics(profiles)
    collection = startup.collection(run, mutants, baselines, batches)
    startup.require(phases['wire_commands'] == {'run-shared-lane-resident-v1': 52}, 'wire protocol changed')
    contracts = [startup.read_json(f) for f in sorted(profiles.glob('startup-contract-*.json'))]
    startup.require(len(contracts) == len({p[0] for p in mutants}), 'missing controller launch contract')
    launch = {key: contracts[0][key] for key in ('argv', 'execArgv', 'serialization', 'stdio')}
    startup.require(all({key: c[key] for key in launch} == launch
                        and c['entry'].endswith('/vitest/dist/workers/forks.js') for c in contracts),
                    'fork contract changed')
    cpu = phases['worker_cpu_ms']
    wall = phases['last_finish_path_ms']
    metrics = dict(worker_cpu_startup_ms=cpu['startup'],
                   worker_cpu_started_to_collection_ms=cpu['through_first_collection'],
                   worker_cpu_fork_to_collection_ms=cpu['startup'] + cpu['through_first_collection'],
                   last_finisher_wall_startup_ms=wall['startup'],
                   last_finisher_wall_dispatch_to_collection_ms=wall['through_first_collection'],
                   last_finisher_wall_fork_to_collection_ms=(wall['startup'] + wall['dispatch_delay']
                                                             + wall['through_first_collection']))
    rows = startup.read_events(root / 'qualification/measurements.jsonl')
    row, = [r for r in rows if r['tool'] == 'oximut']
    smoke = (run / 'bench-smoke.log').read_text()
    startup.require('built-in benchmark provider' in smoke and 'tinybench smoke' in smoke
                    and '|bench|' in smoke, 'missing benchmark-mode result')
    return dict(qualification=qualification, launch=launch, metrics=metrics,
                diagnostic_command_wall_seconds=row['wall_seconds'],
                worker_phases={key: phases[key] for key in ('worker_cpu_ms', 'last_finish_path_ms',
                                                           'worker_wall_median_ms', 'wire_commands')},
                first_collection=collection, smoke=smoke)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native_evidence', type=Path)
    parser.add_argument('historical_capture', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    root = args.native_evidence / 'artifact'
    build = startup.read_json(root / 'build-metadata.json')
    experiment = startup.read_json(root / 'ablation/experiment.json')
    reachability = startup.read_json(root / 'ablation/fork-entry-loads.json')
    startup.require(reachability['stock'] == reachability['provider-only'] and not reachability['lazy']
                    and len(reachability['stock']) == 1, 'unexpected fork entry load observations')
    order = experiment['order']
    startup.require(order == ['stock', 'lazy', 'lazy', 'stock'], 'wrong arm order')
    runs = [capture(root / 'ablation' / f'{i + 1:02}-{arm}', args.historical_capture, experiment, build)
            for i, arm in enumerate(order)]
    startup.require(all(run['launch'] == runs[0]['launch'] for run in runs), 'launch flags differ across arms')
    paired = []
    for stock, lazy in ((runs[0], runs[1]), (runs[3], runs[2])):
        paired.append({key: lazy['metrics'][key] - value for key, value in stock['metrics'].items()})
    summary = dict(scope='Instrumented diagnostics, not performance scores',
                   provenance=startup.read_json(args.native_evidence / 'verification.json'),
                   experiment=experiment, fork_entry_loads=reachability, runs=runs,
                   paired_lazy_minus_stock_ms=paired)
    args.output.write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
