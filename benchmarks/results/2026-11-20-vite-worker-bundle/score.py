"""Verify the native unprofiled snapshot campaign and recover its rollup."""

import argparse
import hashlib
import importlib.util
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


def oximut_report(location, row):
    report = Path(row['report_file'])
    return read_json(location / report.parent.name / report.name)


def test_selection(location, row):
    mutants = oximut_report(location, row)['mutants']
    return {mutant['id']: {**mutant['selection'],
                           'files': [str(Path(file).relative_to(row['cwd']))
                                     for file in mutant['selection']['files']]}
            for mutant in mutants}


def baseline_tests(location, row):
    coverage = oximut_report(location, row)['baseline']['coverage']
    assert coverage['complete']
    identities = {(str(Path(test['file']).relative_to(row['cwd'])), test['name'])
                  for test in coverage['tests']}
    assert len(coverage['tests']) == len(identities) == 102
    return identities


def verify_factory_capture(root, reference, build, *, shared, mutants_only, reused_build):
    module_path = Path(__file__).resolve().parents[1] / '2026-11-19-fresh-worker-startup/summarize.py'
    spec = importlib.util.spec_from_file_location('fresh_worker_startup_summary', module_path)
    startup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(startup)
    capture = root / 'factory-capture'
    diagnostic = read_json(capture / 'diagnostic.json')
    assert diagnostic['source_commit'] == build['commit']
    assert diagnostic['execution_inputs']['binary_sha256'] == build['binary_sha256']
    assert diagnostic['snapshot_sha256'] == digest(root / 'factory-image/vitest.blob')
    assert diagnostic.get('snapshot_mutants_only', False) == mutants_only
    assert all(build['source_sha256'][name] == checksum
               for name, checksum in diagnostic['helper_sha256'].items())
    verified, forks, baselines, batches = startup.selected_workload(
        root, reference.parent.parent, require_launch_mapping=True, capture_name=capture.name)
    observed = capture / 'snapshot-forks'
    restored = {(row['ppid'], row['pid']): row['projectFactories'] for p in observed.glob('*.json')
                for row in [read_json(p)]}
    assert restored.keys() == (forks.keys() if mutants_only else forks.keys() | baselines)
    assert {fork for fork, entries in restored.items() if entries} == forks.keys()
    pid_to_selector = {pid: selector for (_, pid), selector in forks.items()}
    assert len(pid_to_selector) == 206
    report = read_json(capture / 'qualification/00-oximut/report.json')
    by_id = {row['id']: row for row in report['mutants']}
    bundles = {int(p.stem): read_json(p) for p in observed.glob('*.bundled')}
    assert {pid: row['selector'] for pid, row in bundles.items()} == pid_to_selector
    for row in bundles.values():
        selection = by_id[row['selector']]['selection']
        assert row['entries'] and all(entry.get('factory') for entry in row['entries'])
        assert any(by_id[row['selector']]['file'] in entry['modules'] for entry in row['entries'])
        if selection['mode'] == 'files':
            assert {entry['file'] for entry in row['entries']} <= set(selection['files'])
    metrics = startup.worker_metrics(capture / 'profiles')
    project_images = {p.name: {'bytes': p.stat().st_size, 'sha256': digest(p)}
                      for p in capture.glob('project-*.blob')}
    assert len(project_images) == (1 if shared else 2)
    launches = {}
    for path in (capture / 'profiles').glob('startup-contract-*.json'):
        image, = [arg.removeprefix('--snapshot-blob=') for arg in read_json(path)['execArgv']
                  if arg.startswith('--snapshot-blob=')]
        launches[int(path.stem.removeprefix('startup-contract-'))] = Path(image).name
    assert launches.keys() == {controller for controller, _ in forks}
    assert set(launches.values()) == project_images.keys()
    if reused_build:
        cached, = list(capture.glob('project-build-*.json'))
        emitted, = list(capture.glob('emit-*'))
        assert (emitted / 'src/index.js').is_file()
        assert Path(read_json(cached)['image']).name in project_images
    return {'qualification': verified, 'worker_cpu_ms': metrics['worker_cpu_ms'],
            'last_finish_path_ms': metrics['last_finish_path_ms'],
            'project_images': project_images,
            'first_collection': startup.collection(root, forks, baselines, batches,
                                                   capture_name=capture.name, factory_forks=forks.keys())}


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
                    'worker-snapshot-windowed-score', 'worker-snapshot-late-score',
                    'worker-snapshot-factory-score', 'worker-snapshot-shared-image-score',
                    'worker-snapshot-single-image-score', 'worker-snapshot-reuse-build-score')
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

    if mode in ('worker-snapshot-rolling-score', 'worker-snapshot-windowed-score',
                'worker-snapshot-late-score'):
        check_wrapper('before', 'score-wrapper.sh')
        check_wrapper('after', mode.removeprefix('worker-snapshot-') + '-wrapper.sh')
    elif mode == 'worker-snapshot-factory-score':
        check_wrapper('before', 'late-score-wrapper.sh')
        check_wrapper('after', 'factory-score-wrapper.sh')
    elif mode == 'worker-snapshot-shared-image-score':
        check_wrapper('before', 'factory-score-wrapper.sh')
        check_wrapper('after', 'shared-image-score-wrapper.sh')
    elif mode == 'worker-snapshot-single-image-score':
        check_wrapper('before', 'shared-image-score-wrapper.sh')
        check_wrapper('after', 'single-image-score-wrapper.sh')
    elif mode == 'worker-snapshot-reuse-build-score':
        check_wrapper('before', 'single-image-score-wrapper.sh')
        check_wrapper('after', 'reuse-build-score-wrapper.sh')
    else:
        assert provenance['before']['binary_sha256'] == build['binary_sha256']
        check_wrapper('after', 'score-wrapper.sh')
    adapter = {name.removeprefix('packages/vitest-adapter/'): checksum
               for name, checksum in build['source_sha256'].items()
               if name.startswith('packages/vitest-adapter/')}

    historical = rows(reference)
    historical_selection = test_selection(reference.parent, historical['oximut'])
    historical_tests = baseline_tests(reference.parent, historical['oximut'])
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
                assert row.get('test_identity_sha256') == historical[name].get('test_identity_sha256')
                if name != 'vitest':
                    assert row['mutation_identity_outcome_sha256'] == historical[name]['mutation_identity_outcome_sha256']
                    assert len(outcomes(row)) == 206 and outcomes(row) == outcomes(historical[name])
            oximut = trial['oximut']
            assert oximut['selection_mode_counts'] == historical['oximut']['selection_mode_counts']
            assert test_selection(location, oximut) == historical_selection
            assert baseline_tests(location, oximut) == historical_tests
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
        **({'factory_diagnostic': verify_factory_capture(
            root, reference, build, shared=mode != 'worker-snapshot-factory-score',
            mutants_only=mode in ('worker-snapshot-single-image-score', 'worker-snapshot-reuse-build-score'),
            reused_build=mode == 'worker-snapshot-reuse-build-score')}
           if mode in ('worker-snapshot-factory-score', 'worker-snapshot-shared-image-score',
                       'worker-snapshot-single-image-score', 'worker-snapshot-reuse-build-score') else {}),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive_dir', type=Path)
    parser.add_argument('historical_measurements', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive_dir, args.historical_measurements), indent=2))
