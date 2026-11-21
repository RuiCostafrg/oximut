"""Verify the native unprofiled snapshot campaign and recover its rollup."""

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import zipfile


def read_json(path):
    return json.loads(path.read_text())


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(path):
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(entries) == 3 and {row['tool'] for row in entries} == {'oximut', 'stryker', 'vitest'}
    return {row['tool']: row for row in entries}


def outcomes(row):
    return {identity: mutant['outcome'] for identity, mutant in row['mutations'].items()}


def test_selection(location, row):
    report = Path(row['report_file'])
    mutants = read_json(location / report.parent.name / report.name)['mutants']
    return {mutant['id']: {**mutant['selection'],
                           'files': [str(Path(file).relative_to(row['cwd']))
                                     for file in mutant['selection']['files']]}
            for mutant in mutants}


def verify(directory, reference):
    artifact, = read_json(directory / 'artifacts.json')['artifacts']
    checksum = digest(directory / 'artifact.zip')
    assert f'sha256:{checksum}' == artifact['digest']
    with zipfile.ZipFile(directory / 'artifact.zip') as archive:
        assert archive.testzip() is None
        paths = [Path(name) for name in archive.namelist()]
        assert all(not path.is_absolute() and '..' not in path.parts for path in paths)
        archive.extractall(directory / 'artifact')

    run = read_json(directory / 'run.json')
    root = directory / 'artifact'
    mode_file = root / 'snapshot-score-mode.txt'
    mode = mode_file.read_text().strip() if mode_file.exists() else 'worker-snapshot-score'
    assert mode in ('worker-snapshot-score', 'worker-snapshot-compact-score',
                    'worker-snapshot-composed-score', 'worker-snapshot-rolling-score',
                    'worker-snapshot-windowed-score')
    build = read_json(root / 'build-metadata.json')
    benchmark = root / 'snapshot-score'
    metadata = read_json(benchmark / 'metadata.json')
    summary = read_json(benchmark / 'summary.json')
    assert run['conclusion'] == 'success' and run['head_sha'] == build['commit'] == metadata['git_revision']
    assert metadata['before_commit'] == metadata['after_commit'] == build['commit']
    assert digest(root / 'source.tar.gz') == build['source_archive_sha256']
    assert 'Ubuntu 24.04' in build['os_release'] and len(build['cpu_affinity']) == 4
    assert summary['build_integrity']['passed'] and summary['correctness_gate']['passed']
    assert summary['concurrency'] == 4 and summary['measured_pairs'] == 5 and summary['warmup_pairs'] == 1
    provenance = metadata['runtime_provenance']
    assert build['binary_sha256'] == digest(root / 'oximut')
    wrapper_dir = 'benchmarks/experiments/vitest-fork-bundle/'

    def check_wrapper(variant, name):
        wrapper = wrapper_dir + name
        assert provenance[variant]['binary'].endswith('/' + wrapper)
        assert provenance[variant]['binary_sha256'] == build['source_sha256'][wrapper]

    if mode == 'worker-snapshot-rolling-score':
        check_wrapper('before', 'score-wrapper.sh')
        check_wrapper('after', 'rolling-score-wrapper.sh')
    elif mode == 'worker-snapshot-windowed-score':
        check_wrapper('before', 'score-wrapper.sh')
        check_wrapper('after', 'windowed-score-wrapper.sh')
    else:
        assert provenance['before']['binary_sha256'] == build['binary_sha256']
        check_wrapper('after', 'score-wrapper.sh')
    adapter = {name.removeprefix('packages/vitest-adapter/'): checksum
               for name, checksum in build['source_sha256'].items()
               if name.startswith('packages/vitest-adapter/')}

    historical = rows(reference)
    historical_selection = test_selection(reference.parent, historical['oximut'])
    assert len(historical_selection) == 206
    measurements = {'before': [], 'after': []}
    attempts = read_json(benchmark / 'attempts.json')
    assert len(attempts) == 12
    assert {(entry['variant'], entry['round']) for entry in attempts} == {
        (variant, index) for variant in measurements for index in range(6)}
    assert all(entry['exit_code'] == 0 and not entry['descendant_cleanup_required'] for entry in attempts)
    for variant in measurements:
        for index in range(6):
            location = benchmark / f'{index:02d}-{variant}'
            trial = rows(location / 'measurements.jsonl')
            trial_metadata = read_json(location / 'metadata.json')
            assert trial_metadata['binary_sha256'] == provenance[variant]['binary_sha256']
            assert trial_metadata['execution_inputs']['adapter_source_sha256'] == adapter
            assert trial_metadata['versions'] == metadata['versions']
            machine = trial_metadata['machine']
            assert machine['machine'] == 'x86_64' and len(machine['cpu_affinity']) == 4
            for name, row in trial.items():
                assert row['success'] and not row['descendant_cleanup_required']
                assert not row['memory_errors']
                for key in ('workload_sha256', 'declared_test_identity_sha256'):
                    assert row[key] == historical[name][key], (variant, index, name, key)
                if name != 'vitest':
                    assert row['mutation_identity_outcome_sha256'] == historical[name]['mutation_identity_outcome_sha256']
                    assert len(outcomes(row)) == 206 and outcomes(row) == outcomes(historical[name])
            oximut = trial['oximut']
            assert oximut['selection_mode_counts'] == historical['oximut']['selection_mode_counts']
            assert test_selection(location, oximut) == historical_selection
            assert oximut['baseline_test_counts'] == {'passed': 102, 'failed': 0, 'skipped': 0}
            resources = oximut['resources']
            assert resources['effectiveWorkers'] == resources['maxActiveJobs'] == 4
            if index:
                measurements[variant].append(trial)

    metrics = ('wall_seconds', 'cpu_seconds', 'peak_aggregate_rss_bytes')
    medians = {}
    cpu_deficits = {}
    longest_sample_gaps = {}
    reported_test_ranges = {}
    for variant, trials in measurements.items():
        medians[variant] = {tool: {metric: statistics.median(trial[tool][metric] for trial in trials)
                                  for metric in metrics} for tool in ('oximut', 'stryker')}
        ratios = summary['comparisons'][f'{variant}_vs_stryker']['metrics']
        for metric in metrics:
            pairs = [trial['oximut'][metric] / trial['stryker'][metric] for trial in trials]
            assert pairs == ratios[metric]['paired_ratios']
            assert statistics.median(pairs) == ratios[metric]['median']
        cpu_deficits[variant] = statistics.median(
            trial['oximut']['cpu_seconds'] - 2 * trial['stryker']['wall_seconds'] for trial in trials)
        longest_sample_gaps[variant] = {tool: max(trial[tool]['max_sample_gap_seconds'] for trial in trials)
                                       for tool in ('oximut', 'stryker')}
        test_counts = [trial['oximut']['reported_tests_sum_including_cached'] for trial in trials]
        reported_test_ranges[variant] = [min(test_counts), max(test_counts)]

    return {
        'run': run['html_url'],
        'commit': build['commit'],
        **({'mode': mode} if mode_file.exists() else {}),
        'artifact_sha256': checksum,
        'binary_sha256': build['binary_sha256'],
        'wrapper_sha256': provenance['after']['binary_sha256'],
        'cpu_model': next(line.split(':', 1)[1].strip() for line in build['cpu'].splitlines()
                          if line.startswith('Model name:')),
        'versions': metadata['versions'],
        'qualification_rows': 36,
        'medians': medians,
        'comparisons': summary['comparisons'],
        'targets_met': summary['targets_met'],
        'longest_sample_gaps_seconds': longest_sample_gaps,
        'reported_mutant_test_count_ranges': reported_test_ranges,
        'necessary_cpu_seconds_saved_median_at_four_cpus': cpu_deficits,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive_dir', type=Path)
    parser.add_argument('historical_measurements', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive_dir, args.historical_measurements), indent=2))
