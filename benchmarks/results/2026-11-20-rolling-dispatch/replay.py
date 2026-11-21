"""Replay the recorded compact batches using four serial worker slots."""
import argparse
from collections import Counter
import heapq
import json
from pathlib import Path


def replay(directory):
    totals = Counter()
    for path in sorted(directory.glob('controller-*.jsonl')):
        events = [json.loads(line) for line in path.read_text().splitlines()]
        replies = {e['id']: e['monotonicMs'] for e in events if e['type'] == 'controller-send'}
        exits = {e['childPid']: e['monotonicMs'] for e in events if e['type'] == 'child-exit'}
        requests = [e for e in events if e.get('kind') == 'mutant' and e['type'] == 'controller-request']
        if not requests:
            continue
        totals['controllers'] += 1
        models = {name: [0.0] * 4 for name in ('fork_to_exit', 'request_to_exit')}
        request_durations = []
        previous_reply = None
        for request in requests:
            begin = request['monotonicMs']
            end = replies[request['id']]
            forks = [e for e in events if e['type'] == 'fork' and begin <= e['monotonicMs'] <= end]
            if not 1 <= len(forks) <= 4 or len(forks) != len(request['laneMutantIds']):
                raise ValueError('trace does not match recorded mutant batch')
            first_fork = forks[0]['monotonicMs']
            totals['request_to_first_fork_ms'] += first_fork - begin
            totals['first_to_last_fork_ms'] += forks[-1]['monotonicMs'] - first_fork
            if previous_reply is not None:
                if begin < previous_reply:
                    raise ValueError('mutant batch requests overlap')
                totals['reply_to_next_request_ms'] += begin - previous_reply
            previous_reply = end
            totals['batches'] += 1
            totals['workers'] += len(forks)
            totals['request_to_reply_ms'] += end - begin
            last_exit = max(exits[e['childPid']] for e in forks)
            totals['observed_request_to_last_exit_ms'] += last_exit - begin
            totals['exit_barrier_idle_slot_ms'] += sum(last_exit - exits[e['childPid']] for e in forks)
            totals['exit_barrier_idle_slot_ms'] += (4 - len(forks)) * (last_exit - begin)
            for fork in forks:
                exit_at = exits[fork['childPid']]
                if not begin <= fork['monotonicMs'] <= exit_at <= end:
                    raise ValueError('out-of-order lifecycle')
                request_durations.append(exit_at - begin)
                for name, origin in (('fork_to_exit', fork['monotonicMs']), ('request_to_exit', begin)):
                    slots = models[name]
                    heapq.heapreplace(slots, slots[0] + exit_at - origin)
        for name, slots in models.items():
            totals[name + '_rolling_ms'] += max(slots)
        # With fixed per-fork durations, sorting within a controller and taking
        # adjacent sets of four minimizes the sum of batch maxima.
        totals['oracle_sorted_batches_ms'] += sum(sorted(request_durations, reverse=True)[::4])
        totals['fractional_packing_floor_ms'] += sum(request_durations) / 4
    if (totals['controllers'], totals['batches'], totals['workers']) != (2, 52, 206):
        raise ValueError('expected the qualified four-worker colord-scale trace')
    return dict(totals)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profiles', type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.profiles), indent=2))
