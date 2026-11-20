"""Compare native bootstrap captures while keeping profiled results outside scores."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '2026-11-19-main-bottlenecks'))
from summarize import read_json, require, verify_capture, worker_metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('historical_capture', type=Path)
    parser.add_argument('--snapshot', action='store_true')
    args = parser.parse_args()
    root = args.run / 'artifact'
    verified = read_json(args.run / 'verification.json')
    _, _, prior_rows, _ = verify_capture(args.historical_capture, 'worker-phases')
    arms = {}
    candidate = 'snapshot-capture' if args.snapshot else 'bundled-capture'
    for name in ('capture', candidate):
        capture = root / name
        diagnostic, _, rows, inventory = verify_capture(capture, 'worker-phases')
        require(diagnostic['source_commit'] == verified['commit'], 'capture commit differs')
        require(diagnostic['execution_inputs']['binary_sha256'] == verified['binary_sha256'],
                'different measured binary')
        require(inventory['workload_reconciled'], 'forks could not be joined to the historical workload')
        for tool, row in rows.items():
            prior = prior_rows[tool]
            require(row['declared_test_identity_sha256'] == prior['declared_test_identity_sha256'],
                    'test identities changed')
            require(row.get('test_identity_sha256') == prior.get('test_identity_sha256'),
                    'test tool identity fingerprint changed')
            require(not row['descendant_cleanup_required'], 'unexpected descendants')
            if tool != 'vitest':
                require(len(row['mutations']) == 206 and
                        row['mutation_identity_outcome_sha256'] == prior['mutation_identity_outcome_sha256'],
                        'mutant identities or outcomes changed')
        require(rows['oximut']['baseline_test_counts'] == {'passed': 102, 'failed': 0, 'skipped': 0},
                'test baseline mismatch')
        require(rows['oximut']['selection_mode_counts'] == prior_rows['oximut']['selection_mode_counts'],
                'whole-file configuration changed')
        if name == 'bundled-capture':
            routes = (root / 'bundle/routes.json').read_bytes()
            require(hashlib.sha256(routes).hexdigest() == diagnostic['bundle_routes_sha256'],
                    'route hash differs')
            entries = sorted((capture / 'bundled-forks').glob('*.json'))
            pids = {int(p.stem) for p in entries if read_json(p)['entry'].endswith('/workers/forks.mjs')}
            expected = {int(p.stem.split('-')[-1]) for p in (capture / 'profiles').glob('worker-*-*.json')}
            require(len(pids) == 214 and pids == expected, 'did not route every measured fork')
        if name == 'snapshot-capture':
            with (root / 'snapshot/vitest.blob').open('rb') as blob:
                require(hashlib.file_digest(blob, 'sha256').hexdigest() == diagnostic['snapshot_sha256'],
                        'snapshot hash differs')
            profiles = {tuple(map(int, p.stem.split('-')[1:])) for p in (capture / 'profiles').glob('worker-*-*.json')}
            observed = {(entry['ppid'], entry['pid']) for p in (capture / 'snapshot-forks').glob('*.json')
                        for entry in [read_json(p)] if p.stem == str(entry['pid'])}
            require(len(profiles) == 214 and observed == profiles, 'did not restore every measured fork')
        metrics = worker_metrics(capture / 'profiles')
        arms[name] = {'resources': {tool: {k: row[k] for k in ('wall_seconds', 'cpu_seconds',
                                                          'peak_aggregate_rss_bytes')}
                                     for tool, row in rows.items()},
                      **{k: metrics[k] for k in ('worker_cpu_ms', 'worker_wall_median_ms',
                                           'last_finish_path_ms', 'controller_cpu_ms',
                                           'controller_mutant_request_cpu_ms', 'worker_rpc_totals')}}
    extra = {}
    if args.snapshot:
        extra['snapshot_bytes'] = (root / 'snapshot/vitest.blob').stat().st_size
        extra['setup_resources'] = {}
        for phase in ('snapshot-bundle', 'snapshot-build'):
            # Preserve build user/system/wall/RSS separately from the measured
            # commands. A per-invocation build would count in a scored run.
            extra['setup_resources'][phase] = (root / f'{phase}.time').read_text()
    print(json.dumps({'diagnostic_only': True, 'run': verified['run_url'], 'commit': verified['commit'],
                      'artifact_sha256': verified['artifact_sha256'], 'arms': arms, **extra}, indent=2))


if __name__ == '__main__':
    main()
