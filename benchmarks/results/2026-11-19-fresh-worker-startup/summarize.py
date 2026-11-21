"""Verify native startup and first-collection evidence against the prior scale workload."""
import argparse
from collections import Counter, defaultdict
import importlib.util
import json
import math
from pathlib import Path
import statistics

BENCHMARKS = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'previous_bottleneck_summary', BENCHMARKS / 'results/2026-11-19-main-bottlenecks/summarize.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
read_events, read_json, require = previous.read_events, previous.read_json, previous.require
verify_capture, worker_metrics = previous.verify_capture, previous.worker_metrics


def stats(values):
    values = list(values)
    return {'sum': math.fsum(values), 'median': statistics.median(values),
            'p90': statistics.quantiles(values, n=10, method='inclusive')[-1] if len(values) > 1 else values[0]}


def replay(root, *, snapshot=False):
    filename = 'snapshot-launch-replay.json' if snapshot else 'startup-replay.json'
    data = read_json(root / filename)
    require((data['arch'], data['node'], data['width'], data['rounds']) == ('x64', 'v26.3.1', 4, 12),
            'unexpected startup replay dimensions')
    profiles = root / ('composed-capture' if snapshot else 'capture') / 'profiles'
    contracts = [read_json(profiles / name) for name in data['contracts']]
    require(contracts and all(c['execArgv'] == contracts[0]['execArgv'] for c in contracts),
            'missing or different launch flags')
    stock_flags = contracts[0]['execArgv']
    if snapshot:
        blob_flags = [arg for arg in stock_flags if arg.startswith('--snapshot-blob=')]
        require(len(blob_flags) == 1, 'missing or duplicate snapshot flag')
        base_flags = [arg for arg in stock_flags if arg not in blob_flags]
        expected_flags = {'vitest': base_flags, 'snapshot': base_flags + blob_flags,
                          'snapshot-small-pool': base_flags + ['--v8-pool-size=1'] + blob_flags}
    cells = defaultdict(list)
    pids = set()
    primes = set()
    for record in data['records']:
        mode, kind, pid = record['mode'], record['kind'], record['pid']
        if snapshot:
            require(record['execArgv'] == expected_flags[kind], 'launch comparison changed flags')
        start, stop = record['start'], record['stop']
        clean_exit = (record['code'], record['signal']) == (None, 'SIGTERM')
        if snapshot:
            clean_exit |= (record['code'], record['signal']) == (0, None)
        require(pid not in pids and clean_exit, 'freshness/exit failed')
        pids.add(pid)
        priming = record['phase'] == 'prime'
        require((record['beforeCache']['files'] > 0) == (mode == 'populated' and not priming)
                and (record['afterCache']['files'] > 0) == (mode != 'disabled'), 'cache isolation failed')
        boundaries = [int(record['forkNs']), int(start['preload']['ns']), int(start['beforeStart']['ns']),
                      int(start['response']['ns']), int(start['receiptNs']), int(stop['receiptNs']), int(record['exitNs'])]
        require(boundaries == sorted(boundaries)
                and int(start['sendNs']) <= boundaries[2]
                and boundaries[4] <= int(stop['sendNs']) <= int(stop['response']['ns']) <= boundaries[5],
                'invalid replay wall boundaries')
        cpu = [checkpoint['cpuUs'] for checkpoint in
               (start['preload'], start['beforeStart'], start['response'], stop['response'])]
        for k in ('user', 'system'):
            numbers = [c[k] for c in cpu]
            require(all(type(v) is int and v >= 0 for v in numbers) and numbers == sorted(numbers),
                    'invalid replay CPU counters')
        lane = record['lane']
        if priming:
            require(mode == 'populated' and record['round'] == -1 and (kind, lane) not in primes, 'invalid cache primes')
            primes.add((kind, lane))
            continue
        require(record['phase'] == 'measure', 'unknown replay phase')
        cells[kind, mode].append({'round': record['round'], 'lane': lane,
            'wall_fork_to_preload_ms': (boundaries[1] - boundaries[0]) / 1e6,
            'wall_preload_to_start_message_ms': (boundaries[2] - boundaries[1]) / 1e6,
            'wall_start_message_to_ack_ms': (boundaries[3] - boundaries[2]) / 1e6,
            'wall_fork_to_started_received_ms': (boundaries[4] - boundaries[0]) / 1e6,
            'wall_fork_to_exit_ms': (boundaries[6] - boundaries[0]) / 1e6,
            'user_to_started_ms': cpu[2]['user'] / 1000,
            'system_to_started_ms': cpu[2]['system'] / 1000,
            'cpu_fork_to_preload_ms': sum(cpu[0].values()) / 1000,
            'cpu_preload_to_start_message_ms': (sum(cpu[1].values()) - sum(cpu[0].values())) / 1000,
            'cpu_start_message_to_ack_ms': (sum(cpu[2].values()) - sum(cpu[1].values())) / 1000,
            'cpu_to_started_ms': sum(cpu[2].values()) / 1000,
            'cpu_started_to_stopped_ms': (sum(cpu[3].values()) - sum(cpu[2].values())) / 1000,
            **({'exited_without_signal': int(record['code'] == 0)} if snapshot else {}),
            'user_to_stopped_ms': cpu[3]['user'] / 1000,
            'system_to_stopped_ms': cpu[3]['system'] / 1000,
            'finished_cache_files': record['afterCache']['files'],
            'finished_cache_bytes': record['afterCache']['bytes'],
        })
    modes = ('cold', 'populated', 'disabled')
    if snapshot:
        expected_cells = {('vitest', 'populated')} | {
            (kind, mode) for kind in ('snapshot', 'snapshot-small-pool') for mode in ('populated', 'disabled')}
    else:
        expected_cells = {(kind, mode) for kind in ('minimal', 'vitest') for mode in modes}
    require(set(cells) == expected_cells and primes == {
        (kind, lane) for kind, mode in expected_cells if mode == 'populated' for lane in range(4)},
        'missing replay group')
    rolled_up = {}
    wave_means = {}
    for (kind, mode), rows in cells.items():
        expected = {(round_no, lane) for round_no in range(data['rounds']) for lane in range(4)}
        require(len(rows) == len(expected) and {(r['round'], r['lane']) for r in rows} == expected,
                'invalid measured replay wave')
        keys = set(rows[0]) - {'round', 'lane'}
        rolled_up[f'{kind}_{mode}'] = {**{key: stats(r[key] for r in rows) for key in sorted(keys)}, 'forks': len(rows)}
        wave_means[kind, mode] = [{key: statistics.mean(r[key] for r in rows if r['round'] == round_no)
                                  for key in keys} for round_no in range(data['rounds'])]
    deltas = {}
    if snapshot:
        pairs = [
            ('snapshot_less_stock', ('snapshot', 'populated'), ('vitest', 'populated')),
            ('snapshot_disabled_less_populated', ('snapshot', 'disabled'), ('snapshot', 'populated')),
            ('snapshot_small_pool_less_default', ('snapshot-small-pool', 'populated'), ('snapshot', 'populated')),
            ('snapshot_both_less_default', ('snapshot-small-pool', 'disabled'), ('snapshot', 'populated')),
        ]
    else:
        pairs = [(f'vitest_less_minimal_{mode}', ('vitest', mode), ('minimal', mode)) for mode in modes]
        pairs.extend((f'vitest_{mode}_less_populated', ('vitest', mode), ('vitest', 'populated'))
                     for mode in ('disabled', 'cold'))
    delta_keys = ('cpu_to_started_ms', 'wall_fork_to_started_received_ms',
                  'cpu_preload_to_start_message_ms', 'cpu_started_to_stopped_ms')
    if snapshot:
        delta_keys += ('cpu_fork_to_preload_ms', 'wall_fork_to_exit_ms')
    for name, a, b in pairs:
        deltas[name] = {key: stats(x[key] - y[key] for x, y in zip(wave_means[a], wave_means[b], strict=True))
                        for key in delta_keys}
    return {'exec_argv': contracts[0]['execArgv'], 'node_options': data['nodeOptions'], 'versions': {
                'node': data['node'], 'v8': data['v8'], 'node_path': data['execPath']},
            'process_count': len(pids), 'priming_forks': len(primes), 'cells': rolled_up, 'wave_mean_deltas': deltas}


def selected_workload(root, prior, require_launch_mapping, capture_name='capture'):
    location = root / capture_name
    capture, _, rows, inventory = verify_capture(location, 'worker-phases')
    _, _, old_rows, _ = verify_capture(prior, 'worker-phases')
    report = read_json(location / 'qualification/00-oximut/report.json')
    old_report = read_json(prior / 'qualification/00-oximut/report.json')

    def comparable(row):
        return {key: row.get(key) for key in ('workload_sha256', 'test_identity_sha256',
                 'declared_test_identity_sha256', 'mutation_identity_outcome_sha256',
                 'test_counts', 'baseline_test_counts')}

    def relative(file, cwd):
        return str(Path(file).relative_to(cwd))

    def mutants(group, tool):
        row = group[tool]
        normalized = {}
        for key, entry in row['mutations'].items():
            entry = dict(entry)
            if tool == 'oximut':
                selection = entry['selection']
                entry['selection'] = {**selection, 'files': sorted(
                    relative(file, row['cwd']) for file in selection['files'])}
            normalized[key] = entry
            if tool == 'stryker':
                # Stryker's ordinal IDs may depend on discovery order.
                normalized[key].pop('id')
        return normalized

    def tests(document, cwd):
        coverage = document['baseline']['coverage']
        require(coverage['complete'] and len(coverage['tests']) == 102, 'incomplete baseline attribution')
        identities = sorted((relative(t['file'], cwd), t['name']) for t in coverage['tests'])
        require(len(set(identities)) == 102, 'duplicate baseline tests')
        return identities

    for tool, row in rows.items():
        require(row['success'] and not row['descendant_cleanup_required'] and comparable(row) == comparable(old_rows[tool]),
                f'{tool} qualification or historical qualification mismatch')
        if tool != 'vitest':
            require(len(row['mutations']) == 206 and mutants(rows, tool) == mutants(old_rows, tool),
                    f'{tool} exact mutant outcomes or selection differ')
    baseline = tests(report, rows['oximut']['cwd'])
    require(baseline == tests(old_report, old_rows['oximut']['cwd']), 'baseline tests differ')
    require(inventory['workload_reconciled'], 'incomplete fork identity reconciliation')
    normalized = mutants(rows, 'oximut')
    by_id = {m['id']: m for m in normalized.values()}
    profiles = location / 'profiles'
    mapped = {}
    baselines = set()
    batches = []
    for timeline in sorted(profiles.glob('controller-*.jsonl')):
        events = read_events(timeline)
        ppid = events[0]['pid']
        sends = defaultdict(list)
        for event in events:
            if event['type'] == 'child-send' and event['messageType'] == 'run':
                sends[event['childPid']].append(event)
        requests = [e for e in events if e['type'] == 'controller-request' and e['command'] == 'run']
        first_mutant = True
        for request in requests:
            reply = next(e for e in events if e['type'] == 'controller-send' and e['id'] == request['id'])
            forks = [e for e in events if e['type'] == 'fork' and
                     request['monotonicMs'] <= e['monotonicMs'] <= reply['monotonicMs']]
            keys = [(ppid, fork['childPid']) for fork in forks]
            if request['kind'] == 'baseline':
                baselines.update(keys)
                continue
            ids = request['laneMutantIds']
            require(len(keys) == len(ids) and len(keys) <= 4, 'mutant batch width mismatch')
            if require_launch_mapping:
                require(sorted(ids) == sorted(fork['selector'] for fork in forks),
                        'fork environment selectors differ from batch mutant identities')
            for key, fork in zip(keys, forks, strict=True):
                require(key not in mapped, 'reused worker in mutant batches')
                if require_launch_mapping:
                    selector = fork['selector']
                    selection = by_id[selector]['selection']
                    expected = selection['files'] if selection['mode'] == 'files' else sorted({f for f, _ in baseline})
                    run, = sends[key[1]]
                    require(all(f['fields'] == ['filepath'] for f in run['files'])
                            and sorted(relative(f['filepath'], rows['oximut']['cwd']) for f in run['files']) == expected,
                            f'worker selection changed for {selector}')
                mapped[key] = fork.get('selector')
            finishes = [e for e in events if e['type'] == 'child-message' and e['messageType'] == 'testfileFinished'
                        and (ppid, e['childPid']) in keys]
            require(len(finishes) == len(keys), 'missing worker finish boundaries')
            last = max(finishes, key=lambda e: e['monotonicMs'])
            batches.append({'first_in_controller': first_mutant, 'keys': keys, 'last': (ppid, last['childPid'])})
            first_mutant = False
    require(len(mapped) == 206 and len(baselines) == 8 and len(batches) == 52, 'unexpected scale fork inventory')
    summary = {'commit': capture['source_commit'], 'outcomes': dict(Counter(m['outcome'] for m in by_id.values())),
               'whole_file_selection': dict(Counter(m['selection']['mode'] for m in by_id.values())),
               'tests': len(baseline), 'mutant_forks': len(mapped), 'baseline_forks': len(baselines),
               'batches': len(batches), 'launch_selectors_and_files_verified': require_launch_mapping,
               'outcome_sha256': rows['oximut']['mutation_identity_outcome_sha256'],
               'declared_tests_sha256': rows['oximut']['declared_test_identity_sha256']}
    return summary, mapped, baselines, batches


def union(intervals):
    total = end = 0
    for start, stop in sorted(intervals):
        total += max(0, stop - max(start, end))
        end = max(end, stop)
    return total


def collection(root, mutant_forks, baseline_forks, batches, capture_name='capture', *, factory_forks=()):
    location = root / capture_name
    forks = {}
    for file in sorted((location / 'collection').glob('collection-*.json')):
        row = read_json(file)
        key = row['ppid'], row['pid']
        require(file.name == f'collection-{key[0]}-{key[1]}.json' and key not in forks,
                'mismatched collection worker identity')
        elapsed = row['runToFirstCollectionMs']
        require(math.isfinite(elapsed) and elapsed > 0 and row['outstanding'] == 0, 'incomplete fetch intervals')
        spans = row['spans']
        require(all(0 <= e['startMs'] <= e['endMs'] <= elapsed for e in spans), 'invalid RPC interval')
        timing = location / f'profiles/worker-{key[0]}-{key[1]}.json'
        cpu = read_json(timing)['checkpoints']
        full_cpu = (sum(cpu['firstCollection'].values()) - sum(cpu['started'].values())) / 1000
        compile_cpu = sum(sum(c['cpuUs'].values()) for c in row['compiles']) / 1000
        compile_wall = sum(c['wallMs'] for c in row['compiles'])
        evaluation = row['evaluation']
        if key in factory_forks:
            require(not spans and not row['compiles'] and not evaluation,
                    'factory fork went through the module evaluator')
        else:
            require(0 <= compile_cpu <= full_cpu and 0 < compile_wall <= elapsed,
                    'invalid synchronous module compile evidence')
            require(evaluation and all(e.get('selfTime') is not None and math.isfinite(e['selfTime'])
                                       and 0 <= e['duration'] <= elapsed for e in evaluation),
                    'incomplete evaluator timing')
        fetches = [e for e in spans if e['method'] != 'resolve']
        fetch_union = union([(e['startMs'], e['endMs']) for e in fetches])
        forks[key] = ({'elapsed_ms': elapsed, 'fetch_union_ms': fetch_union,
                       'outside_fetch_union_ms': elapsed - fetch_union, 'worker_cpu_ms': full_cpu,
                       'sync_compile_cpu_ms': compile_cpu, 'sync_compile_wall_ms': compile_wall,
                       'inline_evaluator_self_ms': sum(e['selfTime'] for e in evaluation if e['kind'] == 'inline'),
                       'external_evaluator_self_ms': sum(e['selfTime'] for e in evaluation if e['kind'] == 'external'),
                       'evaluator_self_negative_entries': sum(e['selfTime'] < 0 for e in evaluation),
                       'compile_count': len(row['compiles']), 'evaluator_entries': len(evaluation)}, spans)
    require(set(forks) == mutant_forks.keys() | baseline_forks, 'collection artifacts differ from fork inventory')
    result = {}
    groups = {'all_mutants': list(mutant_forks), 'last_finishers': [b['last'] for b in batches],
              'first_batch_mutants': [k for b in batches if b['first_in_controller'] for k in b['keys']],
              'steady_batch_mutants': [k for b in batches if not b['first_in_controller'] for k in b['keys']]}
    for name, group in groups.items():
        rows, span_lists = zip(*(forks[k] for k in group), strict=True)
        spans = [e for sub in span_lists for e in sub]
        methods = {}
        for method in sorted({e['method'] for e in spans}):
            subset = [e for e in spans if e['method'] == method]
            entries = [e['snapshotEntries'] for e in subset if e['snapshotEntries'] is not None]
            methods[method] = {'calls': len(subset), 'latency_ms': stats(e['endMs'] - e['startMs'] for e in subset),
                               'response_kinds': dict(Counter(e['responseKind'] for e in subset)),
                               **({'snapshot_keys': stats(entries), 'empty_snapshots': entries.count(0)} if entries else {})}
        result[name] = {'workers': len(group), 'methods': methods,
                        'stats': {k: stats(row[k] for row in rows) for k in rows[0]}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('startup_only', type=Path)
    parser.add_argument('collection_run', type=Path)
    parser.add_argument('prior_capture', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    startup = args.startup_only / 'artifact'
    combined = args.collection_run / 'artifact'
    initial, _, _, _ = selected_workload(startup, args.prior_capture, False)
    workload, mutants, baselines, batches = selected_workload(combined, args.prior_capture, True)
    phases = worker_metrics(combined / 'capture/profiles')
    result = {
        'scope': 'Native diagnostic observations with instrumentation, kept separate from performance scores.',
        'provenance': [read_json(d / 'verification.json') for d in (args.startup_only, args.collection_run)],
        'qualification': [initial, workload],
        'startup_only_replay': replay(startup),
        'combined_replay': replay(combined),
        'qualified_worker_phase_totals': {k: phases[k] for k in ('worker_cpu_ms', 'last_finish_path_ms',
                                                               'worker_wall_median_ms', 'wire_commands')},
        'first_collection': collection(combined, mutants, baselines, batches),
    }
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
