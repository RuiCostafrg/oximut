"""Compare native bootstrap captures while keeping profiled results outside scores."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '2026-11-19-main-bottlenecks'))
from summarize import read_json, require, verify_capture, worker_metrics


def phase_samples(capture, phase):
    profiles = capture / 'profiles'
    sample_dir = capture / f'{phase}-cpu'
    expected = set()
    for timeline in profiles.glob('controller-*.jsonl'):
        for event in map(json.loads, timeline.read_text().splitlines()):
            selector = event.get('selector')
            if event['type'] == 'fork' and selector and selector[:2] < '20':
                expected.add(event['childPid'])
    seen = set()
    counts = Counter()
    execution_stacks = Counter()
    before_preload = []
    after_preload = []
    for path in sample_dir.glob('*.cpuprofile'):
        seen.add(int(path.stem.rsplit('-', 1)[1]))
        profile = read_json(path)
        require(profile['endTime'] > profile['startTime'] and
                len(profile['timeDeltas']) == len(profile['samples']) > 0, 'invalid CPU profile')
        if phase == 'startup':
            checkpoints = profile['oximut']
            preload = sum(checkpoints['preloadCpu'].values()) / 1000
            started = sum(checkpoints['startedCpu'].values()) / 1000
            require(0 < preload < started, 'invalid startup CPU checkpoints')
            before_preload.append(preload)
            after_preload.append(started - preload)
        frames = {node['id']: node['callFrame'] for node in profile['nodes']}
        parents = {child: node['id'] for node in profile['nodes'] for child in node.get('children', [])}
        for sample in profile['samples']:
            frame = frames[sample]
            url = frame['url']
            if url.startswith('file://'):
                url = url.removeprefix('file://')
            if '/node_modules/' in url:
                url = url.split('/node_modules/', 1)[1]
                if '/.oximut-snapshot.' in url:
                    url = 'vitest/dist/(snapshot seed)'
            elif url.startswith('/'):
                url = Path(url).name
            counts[(url, frame['functionName'])] += 1
            if phase == 'execution':
                functions = set()
                cursor = sample
                while cursor is not None:
                    functions.add(frames[cursor]['functionName'])
                    cursor = parents.get(cursor)
                for function in ('processError', 'mapSourcePosition', '(idle)'):
                    execution_stacks[function] += function in functions
    require(seen == expected and seen, 'worker phase profile inventory differs from sampled selectors')
    return {'forks': len(seen), 'samples': counts.total(),
            'top_self_samples': [{'url': url, 'function': function, 'samples': count}
                                 for (url, function), count in counts.most_common(25)],
            **({'inclusive_samples': execution_stacks} if phase == 'execution' else {}),
            **({'median_cpu_ms_before_preload': statistics.median(before_preload),
                'median_cpu_ms_preload_to_started': statistics.median(after_preload)} if phase == 'startup' else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('historical_capture', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--snapshot', action='store_true')
    mode.add_argument('--snapshot-accounting', action='store_true')
    mode.add_argument('--followup', choices=('stack', 'collection-cpu', 'execution-cpu',
                                            'native-project', 'baseline-tier', 'compact-errors',
                                            'specialize-selector', 'bundle-project', 'composed',
                                            'late-runtime'))
    args = parser.parse_args()
    root = args.run / 'artifact'
    verified = read_json(args.run / 'verification.json')
    build_sources = read_json(root / 'build-metadata.json')['source_sha256']
    _, _, prior_rows, _ = verify_capture(args.historical_capture, 'worker-phases')
    if args.snapshot_accounting:
        arms = {}
        wrapper = 'benchmarks/experiments/vitest-fork-bundle/shared-image-score-wrapper.sh'
        for name, binary in (('capture', verified['binary_sha256']),
                             ('snapshot-accounting-capture', build_sources[wrapper])):
            capture = root / name
            diagnostic, resources, rows, inventory = verify_capture(capture, 'rust-accounting-self')
            require(diagnostic['source_commit'] == verified['commit'], 'accounting source revision differs')
            require(diagnostic['execution_inputs']['binary_sha256'] == binary, 'accounting executable differs')
            require(all(build_sources.get(helper) == checksum
                        for helper, checksum in diagnostic['helper_sha256'].items()), 'accounting helper differs')
            for tool, row in rows.items():
                prior = prior_rows[tool]
                require(row['declared_test_identity_sha256'] == prior['declared_test_identity_sha256']
                        and row.get('test_identity_sha256') == prior.get('test_identity_sha256')
                        and not row['descendant_cleanup_required'], 'accounting workload or cleanup differs')
                if tool != 'vitest':
                    require(row['mutation_identity_outcome_sha256'] == prior['mutation_identity_outcome_sha256']
                            and len(row['mutations']) == 206, 'accounting mutant identities or outcomes differ')
            oximut = rows['oximut']
            require(oximut['selection_mode_counts'] == prior_rows['oximut']['selection_mode_counts'],
                    'accounting file selections differ')
            # The CLI report contains the identity map and original-relative
            # paths independently of the process-tree profiler.
            from score import baseline_tests, test_selection
            previous = args.historical_capture / 'qualification'
            current = capture / 'qualification'
            require(baseline_tests(current, oximut) == baseline_tests(previous, prior_rows['oximut'])
                    and test_selection(current, oximut) == test_selection(previous, prior_rows['oximut']),
                    'accounting Oximut baseline identities or selected files differ')
            arms[name] = {'phase_wall_ms': resources['phaseWallMs'],
                          'resources': {key: oximut[key] for key in ('wall_seconds', 'cpu_seconds',
                                                                   'peak_aggregate_rss_bytes')},
                          'accounting': inventory['accounting_diagnostics']}
        print(json.dumps({'diagnostic_only': True, 'run': verified['run_url'], 'commit': verified['commit'],
                          'artifact_sha256': verified['artifact_sha256'], 'arms': arms}, indent=2))
        return
    arms = {}
    candidate = 'snapshot-capture' if args.snapshot else 'bundled-capture'
    names = ['capture', candidate]
    if args.followup == 'stack':
        names = ['capture', 'stack-measure-capture', 'stack-skip-capture']
    if args.followup in ('collection-cpu', 'execution-cpu'):
        names = ['capture', f'{args.followup}-capture']
    if args.followup in ('composed', 'late-runtime'):
        names = ['capture', 'composed-capture']
    if args.followup == 'late-runtime':
        names.append('late-runtime-capture')
    if args.followup in ('native-project', 'baseline-tier', 'compact-errors', 'specialize-selector',
                         'bundle-project'):
        names = ['capture', 'snapshot-offline-capture', f'{args.followup}-capture']
    use_snapshot = args.snapshot or args.followup
    offline = root / 'snapshot-offline-capture'
    if args.snapshot and offline.is_dir():
        names.append(offline.name)
    for name in names:
        capture = root / name
        snapshot_arm = name != 'capture' and use_snapshot
        diagnostic, _, rows, inventory = verify_capture(capture, 'worker-phases')
        require(diagnostic['source_commit'] == verified['commit'], 'capture commit differs')
        require(diagnostic['execution_inputs']['binary_sha256'] == verified['binary_sha256'],
                'different measured binary')
        require(all(build_sources.get(helper) == checksum
                    for helper, checksum in diagnostic['helper_sha256'].items()),
                'capture helper differs from archived source')
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
        if snapshot_arm:
            snapshot = 'snapshot-late-runtime' if name == 'late-runtime-capture' else 'snapshot'
            with (root / snapshot / 'vitest.blob').open('rb') as blob:
                require(hashlib.file_digest(blob, 'sha256').hexdigest() == diagnostic['snapshot_sha256'],
                        'snapshot hash differs')
            profiles = {tuple(map(int, p.stem.split('-')[1:])) for p in (capture / 'profiles').glob('worker-*-*.json')}
            observed = {(entry['ppid'], entry['pid']) for p in (capture / 'snapshot-forks').glob('*.json')
                        for entry in [read_json(p)] if p.stem == str(entry['pid'])}
            require(len(profiles) == 214 and observed == profiles, 'did not restore every measured fork')
            seed_reads = {p.stem.removeprefix('seed-reads-'): read_json(p)
                          for p in capture.glob('seed-reads-*.json')}
            if seed_reads:
                controller_pids = {e['timeline'].removeprefix('controller-').removesuffix('.jsonl')
                                   for e in inventory['observed']}
                require(seed_reads.keys() == controller_pids, 'controller source reads incomplete')
                require(all(type(value[k]) is int and value[k] >= 0 for value in seed_reads.values()
                            for k in ('calls', 'bytes', 'readNs')), 'invalid source read counters')
            elif offline.is_dir() or args.followup:
                require(False, 'missing source-read instrumentation')
        metrics = worker_metrics(capture / 'profiles')
        arms[name] = {'resources': {tool: {k: row[k] for k in ('wall_seconds', 'cpu_seconds',
                                                          'peak_aggregate_rss_bytes')}
                                     for tool, row in rows.items()},
                      **{k: metrics[k] for k in ('worker_cpu_ms', 'worker_wall_median_ms',
                                           'last_finish_path_ms', 'controller_cpu_ms',
                                           'controller_mutant_request_cpu_ms', 'worker_rpc_totals')}}
        if snapshot_arm and seed_reads:
            arms[name]['seed_reads'] = {k: sum(e[k] for e in seed_reads.values())
                                       for k in ('calls', 'bytes', 'readNs')}
        if name.startswith('stack-'):
            records = [read_json(p) for p in capture.glob('stack-timing-*.json')]
            require({str(record['pid']) for record in records} == set(seed_reads),
                    'stack timings missing for a traced controller')
            require(all(record['mode'] == diagnostic['controller_stack_timing'] and record['loaded'] == 1
                        for record in records), 'stack timing route mismatch')
            arms[name]['stack_timing'] = {key: sum(record[key] for record in records)
                                          for key in ('calls', 'errors', 'wallNs', 'userUs', 'systemUs')}
        if name in ('collection-cpu-capture', 'execution-cpu-capture'):
            phase = name.split('-')[0]
            arms[name][f'{phase}_cpu_samples'] = phase_samples(capture, phase)
        composed = name in ('composed-capture', 'late-runtime-capture')
        if name == 'compact-errors-capture' or composed:
            arms[name]['compacted_assertions'] = diagnostic['compacted_assertions']
        if name == 'specialize-selector-capture' or composed:
            arms[name]['specialized_switches'] = diagnostic['specialized_switches']
        if name == 'bundle-project-capture' or composed:
            oximut = rows['oximut']
            report_path = Path(oximut['report_file'])
            report = read_json(capture / 'qualification' / report_path.parent.name / report_path.name)
            sampled = [read_json(p) for p in (capture / 'snapshot-forks').glob('*.bundled')]
            mutants = {row['id']: row for row in report['mutants']}
            require(len(sampled) == 206 and {row['selector'] for row in sampled} == mutants.keys(),
                    'bundled selectors differ from qualified mutant report')
            for row in sampled:
                mutant = mutants[row['selector']]
                require(any(mutant['file'] in entry['modules'] for entry in row['entries']),
                        'bundled test did not include the selected mutant source')
                if mutant['selection']['mode'] == 'files':
                    require({entry['file'] for entry in row['entries']} <= set(mutant['selection']['files']),
                            'bundled evaluation escaped the selected test files')
            arms[name]['bundled_test_entries'] = diagnostic['bundled_test_entries']
        if composed:
            startup_summary = Path(__file__).resolve().parents[1] / '2026-11-19-fresh-worker-startup/summarize.py'
            spec = importlib.util.spec_from_file_location('fresh_worker_startup_summary', startup_summary)
            startup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(startup)
            _, forks, baselines, batches = startup.selected_workload(
                root, args.historical_capture, require_launch_mapping=True, capture_name=name)
            arms[name]['first_collection'] = startup.collection(
                root, forks, baselines, batches, capture_name=name)
            if (capture / 'startup-cpu').exists():
                arms[name]['startup_cpu_samples'] = phase_samples(capture, 'startup')
            if name == 'late-runtime-capture' and (capture / 'execution-cpu').exists():
                arms[name]['execution_cpu_samples'] = phase_samples(capture, 'execution')
            if name == 'composed-capture' and (root / 'snapshot-launch-replay.json').is_file():
                arms[name]['startup_replay'] = startup.replay(root, snapshot=True)
        if name == 'native-project-capture':
            oximut = rows['oximut']
            report_path = Path(oximut['report_file'])
            report = read_json(capture / 'qualification' / report_path.parent.name / report_path.name)
            mutants = {mutant['id']: mutant['file'] for mutant in report['mutants']}
            observed = [read_json(p) for p in (capture / 'native-project').glob('native-*.json')]
            forks = {}
            for timeline in (capture / 'profiles').glob('controller-*.jsonl'):
                for event in map(json.loads, timeline.read_text().splitlines()):
                    if event['type'] == 'fork' and event.get('selector'):
                        forks[event['childPid']] = (int(timeline.stem.split('-')[-1]), event['selector'])
            require(len(observed) == len(forks) == len(mutants) == 206, 'native fork inventory mismatch')
            require({row['pid']: (row['ppid'], row['selector']) for row in observed} == forks,
                    'native fork identity or selector mismatch')
            require(all(mutants[row['selector']] in row['residentsLoaded'] for row in observed),
                    'a native fork never loaded the source under its selector')
            arms[name]['native_forks_loaded_target'] = len(observed)
    extra = {}
    if use_snapshot:
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
