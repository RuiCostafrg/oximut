"""Revalidate the worker and accounting captures and summarize observed boundaries."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import profile_native


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text())


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def verify_capture(root, mode):
    capture = read_json(root / 'diagnostic.json')
    qualification = root / 'qualification'
    require(capture['node_capture_mode'] == mode and capture['concurrency'] == 4
            and capture['workload'] == 'colord-scale' and capture['passed'],
            f'invalid diagnostic result: {root}')
    gate_errors = profile_native.qualification_errors(qualification, 0, 'colord-scale')
    require(not gate_errors, str(gate_errors))
    if mode == 'worker-phases':
        inventory = profile_native.controller_capture_inventory(
            root / 'profiles', qualification, worker_phases=True)
    else:
        inventory = profile_native.accounting_capture_inventory(qualification)
    require(not inventory['errors'] and inventory == capture['captures'], str(inventory['errors']))
    report = read_json(qualification / '00-oximut' / 'report.json')
    rows = {row['tool']: row for row in read_events(qualification / 'measurements.jsonl')}
    return capture, report['resources'], rows, inventory


def fork_metrics(directory, controller_pid, fork, events, request_ms):
    def stamp(event_type, key=None, value=None):
        return next(e['monotonicMs'] for e in events
                    if e['type'] == event_type and (key is None or e.get(key) == value))

    worker_pid = fork['childPid']
    cpu = read_json(directory / f'worker-{controller_pid}-{worker_pid}.json')['checkpoints']
    started = stamp('child-message', 'messageType', 'started')
    dispatch = stamp('child-send', 'messageType', 'run')
    collect = stamp('child-message', 'rpcMethod', 'onCollected')
    finish = stamp('child-message', 'messageType', 'testfileFinished')
    exit_ms = stamp('child-exit')
    counts = next(e['counts'] for e in events if e['type'] == 'child-exit')
    cpu_phases = {}
    for metric in ('user', 'system'):
        startup, collection, finished, stopped = [
            cpu[k][metric] / 1000 for k in ('started', 'firstCollection', 'finished', 'stopped')]
        cpu_phases[metric] = dict(startup=startup, through_first_collection=collection - startup,
                                  after_first_collection=finished - collection,
                                  stop=stopped - finished)
    return {
        'controller_pid': controller_pid, 'pid': worker_pid,
        'fork_wall_ms': dict(request_to_fork=fork['monotonicMs'] - request_ms,
                             startup=started - fork['monotonicMs'], dispatch_delay=dispatch - started,
                             through_first_collection=collect - dispatch,
                             after_first_collection=finish - collect, stop=exit_ms - finish),
        'cpu_ms': cpu_phases, 'controller_finish_ms': finish, 'controller_exit_ms': exit_ms,
        'rpc_counts': counts,
    }


def worker_metrics(directory):
    batches, gaps, forks, baselines = [], [], [], []
    controller_cpu = 0
    mutant_request_cpu = 0
    for timeline in sorted(directory.glob('controller-*.jsonl')):
        events = read_events(timeline)
        controller_pid = events[0]['pid']
        children = defaultdict(list)
        for event in events:
            if 'childPid' in event:
                children[event['childPid']].append(event)
        replies = {e['id']: e for e in events if e['type'] == 'controller-send'}
        exits = [e for e in events if e['type'] == 'controller-exit']
        controller_cpu += sum(exits[0]['selfCpuUsage'].values()) / 1000
        previous_reply = None
        for request in (e for e in events if e['type'] == 'controller-request'
                        and e['command'] == 'run'):
            begin = request['monotonicMs']
            reply = replies[request['id']]
            end = reply['monotonicMs']
            workers = [fork_metrics(directory, controller_pid, e, children[e['childPid']], begin)
                       for e in events if e['type'] == 'fork' and begin <= e['monotonicMs'] <= end]
            if request['kind'] == 'baseline':
                baselines.extend(workers)
                continue
            if previous_reply is not None:
                gaps.append(begin - previous_reply)
            previous_reply = end
            forks.extend(workers)
            cpu = sum(reply['selfCpuUsage'][k] - request['selfCpuUsage'][k]
                      for k in ('user', 'system')) / 1000
            mutant_request_cpu += cpu
            last_finish = max(workers, key=lambda e: e['controller_finish_ms'])
            last_finish_ms = last_finish['controller_finish_ms']
            last_exit_ms = max(e['controller_exit_ms'] for e in workers)
            phases = dict(last_finish['fork_wall_ms'])
            phases.pop('stop')
            phases['finish_to_reply'] = end - last_finish_ms
            require(all(v >= 0 for v in phases.values()), 'out-of-order batch boundaries')
            batches.append({
                'controller_pid': controller_pid, 'wire_command': request['wireCommand'],
                'first_mutant_batch_in_controller': not any(
                    b['controller_pid'] == controller_pid for b in batches),
                'width': len(workers), 'request_to_reply_ms': end - begin,
                'last_finish_path_ms': phases,
                'last_exit_to_reply_ms': end - last_exit_ms,
                'finish_skew_ms': last_finish_ms - min(e['controller_finish_ms'] for e in workers),
                'controller_cpu_ms': cpu,
            })
    require(len(forks) == 206 and len({f['pid'] for f in forks}) == 206, 'expected 206 fresh mutant forks')
    cpu_ms = {phase: sum(f['cpu_ms'][k][phase] for f in forks for k in ('user', 'system'))
              for phase in forks[0]['cpu_ms']['user']}
    path_ms = {phase: sum(b['last_finish_path_ms'][phase] for b in batches)
               for phase in batches[0]['last_finish_path_ms']}
    return {
        'fork_count': len(forks), 'batch_count': len(batches), 'baseline_fork_count': len(baselines),
        'multi_file_forks': sum(f['rpc_counts']['onCollected'] > 1 for f in forks),
        'wire_commands': dict(Counter(b['wire_command'] for b in batches)),
        'worker_cpu_ms': cpu_ms,
        'worker_cpu_by_kind_ms': {k: sum(sum(f['cpu_ms'][k].values()) for f in forks)
                                  for k in ('user', 'system')},
        'controller_cpu_ms': controller_cpu, 'controller_mutant_request_cpu_ms': mutant_request_cpu,
        'worker_wall_median_ms': {k: statistics.median(f['fork_wall_ms'][k] for f in forks)
                                  for k in forks[0]['fork_wall_ms']},
        'worker_wall_p90_ms': {k: statistics.quantiles([f['fork_wall_ms'][k] for f in forks],
                                                      n=10, method='inclusive')[-1]
                               for k in forks[0]['fork_wall_ms']},
        'worker_rpc_totals': {method: sum(f['rpc_counts'].get(method, 0) for f in forks)
                              for method in ('fetch', 'fetchModule', 'resolveId', 'onCollected')},
        'last_finish_path_ms': path_ms, 'request_to_reply_sum_ms': sum(path_ms.values()),
        'request_to_reply_median_ms': statistics.median(b['request_to_reply_ms'] for b in batches),
        'same_controller_gap_sum_ms': sum(gaps), 'same_controller_gap_median_ms': statistics.median(gaps),
        'same_controller_gap_count': len(gaps),
        'finish_skew_median_ms': statistics.median(b['finish_skew_ms'] for b in batches),
        'last_exit_to_reply_median_ms': statistics.median(b['last_exit_to_reply_ms'] for b in batches),
        'batches': batches, 'mutant_forks': forks, 'baseline_forks': baselines,
        'same_controller_gaps_ms': gaps,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workers', type=Path)
    parser.add_argument('accounting', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    worker_capture, worker_resources, worker_rows, _ = verify_capture(args.workers, 'worker-phases')
    account_capture, account_resources, account_rows, inventory = verify_capture(args.accounting, 'rust-accounting-self')
    require(worker_capture['source_commit'] == account_capture['source_commit'], 'different source commits')
    require(worker_rows['oximut']['mutations'] == account_rows['oximut']['mutations'],
            'different mutation outcomes between captures')
    summary = dict(
        source_commit=worker_capture['source_commit'],
        worker_diagnostic=worker_metrics(args.workers / 'profiles'),
        accounting_diagnostic=inventory['accounting_diagnostics'],
        raw_observations=[{
            'scope': scope,
            'resources': {tool: {k: row[k] for k in ('wall_seconds', 'cpu_seconds', 'cpu_user_seconds',
                                                     'cpu_system_seconds', 'peak_aggregate_rss_bytes',
                                                     'max_sample_gap_seconds')}
                          for tool, row in rows.items()},
            'phase_wall_ms': resources['phaseWallMs'],
            'outcomes': dict(Counter(m['outcome'] for m in rows['oximut']['mutations'].values())),
        } for scope, rows, resources in (
            ('worker-phases', worker_rows, worker_resources),
            ('rust-accounting-self', account_rows, account_resources),
        )],
    )
    args.output.write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
