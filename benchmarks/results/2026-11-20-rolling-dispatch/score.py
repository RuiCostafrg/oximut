"""Qualify the saved native rolling campaign against the historical scale capture."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import zipfile


def read_rows(path):
    return {row['tool']: row for row in map(json.loads, path.read_text().splitlines())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive_dir', type=Path)
    parser.add_argument('historical_measurements', type=Path)
    args = parser.parse_args()
    directory = args.archive_dir
    artifact, = json.loads((directory / 'artifacts.json').read_text())['artifacts']
    with (directory / 'artifact.zip').open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    assert f'sha256:{checksum}' == artifact['digest']
    with zipfile.ZipFile(directory / 'artifact.zip') as archive:
        assert archive.testzip() is None
    root = directory / 'artifact/real-project-comparison'
    summary = json.loads((root / 'summary.json').read_text())
    run = json.loads((directory / 'run.json').read_text())
    build = json.loads((root.parent / 'real-project-binaries/build-metadata.json').read_text())
    assert run['conclusion'] == 'success' and build['after_commit'] == run['head_sha']
    assert summary['build_integrity']['passed'] and summary['correctness_gate']['passed']
    assert summary['concurrency'] == 4 and summary['measured_pairs'] == 5 and summary['warmup_pairs'] == 1
    historical = read_rows(args.historical_measurements)
    phases = {}
    minimum_cpu_savings = {}
    for variant in ('before', 'after'):
        mutation_ms = []
        cpu_deficits = []
        for index in range(6):
            location = root / f'{index:02}-{variant}'
            rows = read_rows(location / 'measurements.jsonl')
            assert set(rows) == {'oximut', 'stryker', 'vitest'}
            for name, row in rows.items():
                assert row['success'] and not row['descendant_cleanup_required']
                for key in ('workload_sha256', 'declared_test_identity_sha256'):
                    assert row[key] == historical[name][key], (variant, index, name, key)
                if name != 'vitest':
                    assert row['mutation_identity_outcome_sha256'] == historical[name]['mutation_identity_outcome_sha256']
                    identities = {k: m['outcome'] for k, m in row['mutations'].items()}
                    expected = {k: m['outcome'] for k, m in historical[name]['mutations'].items()}
                    assert len(identities) == 206 and identities == expected
            assert rows['oximut']['selection_mode_counts'] == historical['oximut']['selection_mode_counts']
            assert rows['oximut']['baseline_test_counts'] == {'passed': 102, 'failed': 0, 'skipped': 0}
            resources = rows['oximut']['resources']
            assert resources['effectiveWorkers'] == resources['maxActiveJobs'] == 4
            machine = json.loads((location / 'metadata.json').read_text())['machine']
            assert machine['machine'] == 'x86_64' and len(machine['cpu_affinity']) == 4
            if index:
                mutation_ms.append(resources['phaseWallMs']['mutationExecution'])
                cpu_deficits.append(rows['oximut']['cpu_seconds'] - 2 * rows['stryker']['wall_seconds'])
        phases[variant] = statistics.median(mutation_ms)
        minimum_cpu_savings[variant] = statistics.median(cpu_deficits)
    print(json.dumps({'run': run['html_url'], 'artifact_sha256': checksum, 'build': build,
                      'comparisons': summary['comparisons'], 'qualification_rows': 36,
                      'mutation_phase_wall_ms_median': phases,
                      'necessary_cpu_seconds_saved_median_at_four_cpus': minimum_cpu_savings}, indent=2))


if __name__ == '__main__':
    main()
