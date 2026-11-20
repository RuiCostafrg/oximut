"""Join the controller's decisions to completed first-collection worker RPCs."""
import argparse
from collections import Counter, defaultdict
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'startup_summary', Path(__file__).resolve().parents[1] / '2026-11-19-fresh-worker-startup/summarize.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)
read_events, read_json, require = startup.read_events, startup.read_json, startup.require


def analyze(root, historical, name):
    qualification, mutants, baselines, batches = startup.selected_workload(
        root, historical, True, capture_name=name)
    capture = read_json(root / name / 'diagnostic.json')
    require(capture['lane_local_fetch'] == (name == 'local'), 'wrong experiment mode')
    worker = startup.collection(root, mutants, baselines, batches, capture_name=name)
    phase = startup.worker_metrics(root / name / 'profiles')
    profiles = root / name / 'controller-fetch'
    per_rpc = defaultdict(list)
    lifetime = Counter()
    settled = {}
    supervisors = set()
    for trace in sorted(profiles.glob('fetch-controller-*.jsonl')):
        pid = int(trace.stem.rsplit('-', 1)[1])
        supervisors.add(pid)
        for event in read_events(trace):
            event_type = event['type']
            path = event.get('path')
            if event_type == 'write-done':
                previous = settled.get((pid, path))
                event['rewrite'] = previous is not None
                event['same_generation_rewrite'] = previous == event['generation']
                settled[pid, path] = event['generation']
            if event_type == 'fetch-result' and event.get('tmp'):
                event['artifact_generation'] = settled.get((pid, event['tmp']))
            if event.get('rpc') is not None:
                require(type(event['worker']) is int and event['worker'] > 0, 'missing fork PID on RPC event')
                per_rpc[pid, event['worker'], event['rpc']].append(event)
            else:
                lifetime[event_type] += 1
    require(supervisors == {parent for parent, _ in mutants}, 'missing controller traces')
    require(lifetime['batch'] == len(batches), 'missing shared-lane batch boundaries')

    counts = Counter()
    by_lane = defaultdict(Counter)
    fetch_after_snapshot = defaultdict(Counter)
    for parent, pid in mutants:
        trace = read_json(root / name / 'collection' / f'collection-{parent}-{pid}.json')
        require(sum(span['method'] == 'fetchWarmModules' for span in trace['spans']) == 1,
                'expected one memoized snapshot per fresh fork')
        snapshot = None
        joined = set()
        shared_results = set()
        for span in sorted(trace['spans'], key=lambda span: span['startMs']):
            method = span['method']
            if method not in ('fetch', 'fetchWarmModules'):
                continue
            require(span['rpc'] not in joined, 'duplicate worker RPC identity')
            joined.add(span['rpc'])
            events = per_rpc[parent, pid, span['rpc']]
            calls = [event for event in events if event['type'] == 'rpc']
            require(len(calls) == 1 and calls[0]['method'] == method,
                    f'missing or mismatched controller RPC {parent}/{pid}/{span}')
            kinds = Counter(event['type'] for event in events)
            lane = calls[0]['project']
            require(lane.startswith('oximut-lane-') and calls[0]['generation'] > 0, 'nonresident mutant RPC')
            if method == 'fetchWarmModules':
                snapshot, = (event for event in events if event['type'] == 'snapshot')
                require(snapshot['entries'] == span['snapshotEntries'], 'snapshot size differs across IPC')
                for counter in (counts, by_lane[lane]):
                    counter['snapshots'] += 1
                    counter['snapshot_keys'] += snapshot['entries']
                    counter['snapshot_roots'] += snapshot['roots']
                    counter['snapshot_nodes'] += snapshot['nodes']
                    counter['snapshot_transformed_nodes'] += snapshot['transformed']
                    counter['snapshot_tmp_nodes'] += snapshot['temporary']
                    empty = snapshot['entries'] == 0
                    counter['empty'] += int(empty)
                    counter['empty_no_roots'] += int(empty and snapshot['roots'] == 0)
                    counter['empty_no_transforms'] += int(empty and snapshot['transformed'] == 0)
                    counter['empty_after_shared_root'] += int(
                        empty and not shared_results.isdisjoint(calls[0]['files']))
                continue
            fetch, = (event for event in events if event['type'] == 'fetch-result')
            if kinds['share-hit']:
                shared_results.add(fetch['id'])
            require(bool(fetch.get('tmp')) == (span['responseKind'] == 'tmpFile'), 'tmp kind differs across IPC')
            if fetch.get('tmp'):
                require(fetch['artifact_generation'] == fetch['generation'],
                        'temporary artifact reply lacks a successful write in the active generation')
            graphs = [event for event in events if event['type'] == 'graph']
            require(len(graphs) <= 1, 'ambiguous native graph fetch')
            split = ('entry' if calls[0].get('importer') is None else
                     'missing snapshot' if snapshot is None else
                     'empty snapshot' if snapshot['entries'] == 0 else 'nonempty snapshot')
            if split == 'nonempty snapshot':
                url = calls[0]['url']
                raw = url[5:].replace('__x00__', '\0', 1) if url.startswith('/@id/') else url
                counts['fallback_with_snapshot_key'] += int(
                    url in snapshot['keys'] or raw in snapshot['keys'])
            for counter in (counts, by_lane[lane], fetch_after_snapshot[split]):
                counter['fallbacks'] += 1
                counter['tmp_replies'] += int(bool(fetch.get('tmp')))
                for event_type in ('share-hit', 'share-miss', 'share-bypass', 'tmp-reuse',
                                   'write-start', 'write-done', 'write-join', 'write-failed'):
                    counter[event_type] += kinds[event_type]
                if graphs:
                    counter['native_graph_hot' if graphs[0]['cached'] else 'native_graph_cold'] += 1
                else:
                    counter['no_native_graph_observation'] += 1
                counter['tmp_path_rewrites'] += sum(event.get('rewrite', False) for event in events)
                counter['tmp_path_rewrites_same_generation'] += sum(
                    event.get('same_generation_rewrite', False) for event in events)
                counter['transformed_code_bytes_submitted_to_write'] += sum(
                    event['codeBytes'] for event in events if event['type'] == 'write-start')
                if kinds['write-done']:
                    begin, = (event for event in events if event['type'] == 'write-start')
                    end, = (event for event in events if event['type'] == 'write-done')
                    require(begin['path'] == end['path'] and int(begin['ns']) <= int(end['ns']),
                            'write begin and settlement disagree')
                    counter['write_await_ns'] += int(end['ns']) - int(begin['ns'])
    require(counts['snapshots'] == len(mutants), 'missing mutant snapshots')
    require(counts['fallbacks'] == worker['all_mutants']['methods']['fetch']['calls'], 'fallback count mismatch')
    require(counts['write-start'] == counts['write-done'] + counts['write-failed'], 'unfinished write')
    require(counts['tmp_replies'] == (counts['share-hit'] + counts['tmp-reuse']
            + counts['write-done'] + counts['write-join']), 'tmp reply origins do not reconcile')
    if name == 'local':
        require(not (counts['share-hit'] or counts['share-miss'] or counts['share-bypass']),
                'lane-local variant invoked the cross-lane share')
    else:
        require(counts['share-hit'] > 0, 'shared control had no hits')
    return {
        'qualification': qualification,
        'generation_boundaries': dict(lifetime),
        'first_collection_controller': dict(counts),
        'by_lane': {key: dict(value) for key, value in sorted(by_lane.items())},
        'fallback_by_snapshot': {key: dict(value) for key, value in sorted(fetch_after_snapshot.items())},
        'diagnostic_timing': {
            'first_collection': worker,
            'last_finish_path_ms': phase['last_finish_path_ms'],
            'controller_mutant_request_cpu_ms': phase['controller_mutant_request_cpu_ms'],
            'worker_cpu_ms': phase['worker_cpu_ms'],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('historical_capture', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    control = read_json(args.artifact / 'capture/diagnostic.json')
    local = read_json(args.artifact / 'local/diagnostic.json')
    require(all(control[key] == local[key] for key in
                ('source_commit', 'execution_inputs', 'helper_sha256', 'node_options')),
            'control and candidate have unequal inputs')
    result = {
        'scope': 'Overhead-bearing native diagnostics, no performance score',
        'shared': analyze(args.artifact, args.historical_capture, 'capture'),
        'lane_local': analyze(args.artifact, args.historical_capture, 'local'),
    }
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
