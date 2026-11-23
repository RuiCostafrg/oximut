#!/usr/bin/env python3
"""Summarize an OXIMUT_TRACE_TIMELINE directory: where per-mutant time and idle CPU go.

Diagnostic only. Input is the `<role>-<pid>.jsonl` files written by the Rust scheduler
(crates/cli/src/trace.rs) and by the adapter (packages/vitest-adapter/timeline.cjs).
Every line carries `t`, milliseconds since the Unix epoch with sub-millisecond precision
(Rust: SystemTime; Node: performance.timeOrigin + performance.now()), so lines from all
processes share one clock. Output: a JSON summary and a short text report.

Mutants are keyed by their full mutant id. A shared-lane batch is one scheduler job whose
`lanes` lists the mutant ids; a plain mutant job carries `mutant`.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

# Metrics in ms per mutant, as (name, start event, end event). Each event is
# (role, name); a mutant's events are collected from every process by its id.
SEGMENTS = [
    ('fork_latency', ('pool', 'spawn_requested'), ('pool', 'spawned')),
    ('fork_ipc_to_zygote', ('pool', 'spawn_sent'), ('zygote', 'spawn_received')),
    ('fork_queue_in_zygote', ('zygote', 'spawn_received'), ('zygote', 'fork_begin')),
    ('fork_syscall', ('zygote', 'fork_begin'), ('zygote', 'forked')),
    ('fork_return_to_controller', ('zygote', 'forked'), ('pool', 'spawned')),
    ('forked_to_child_entered', ('zygote', 'forked'), ('worker', 'fork_entered')),
    ('start_to_run', ('controller', 'lane_start_sent'), ('controller', 'lane_run_sent')),
    ('start_to_started', ('controller', 'lane_start_sent'), ('controller', 'lane_started')),
    ('started_to_run', ('controller', 'lane_started'), ('controller', 'lane_run_sent')),
    ('run_to_result', ('controller', 'lane_run_sent'), ('controller', 'lane_result')),
    ('run_to_collect_begin', ('controller', 'lane_run_sent'), ('worker', 'collect_begin')),
    ('collect', ('worker', 'collect_begin'), ('worker', 'collect_end')),
    ('execute', ('worker', 'execute_begin'), ('worker', 'execute_end')),
    ('execute_end_to_result', ('worker', 'execute_end'), ('controller', 'lane_result')),
    ('result_to_reply', ('controller', 'lane_result'), ('batch', 'reply_sent')),
    ('result_to_exit', ('controller', 'lane_result'), ('controller', 'lane_exit')),
    ('fork_request_to_exit', ('controller', 'lane_fork_requested'), ('controller', 'lane_exit')),
]

# Precollect only: segments of the mutants whose worker was forked from a template
# (pool `template_fork_*` events). A templated worker's second start and run is its
# controller `lane_start_sent` to the end of its (shortened) collect.
TEMPLATED_SEGMENTS = [
    ('templated_fork_to_attach', ('pool', 'template_fork_requested'), ('pool', 'template_fork_attached')),
    ('templated_start_to_collect_end', ('controller', 'lane_start_sent'), ('worker', 'collect_end')),
    ('templated_collect', ('worker', 'collect_begin'), ('worker', 'collect_end')),
]


def percentile(values, q):
    """Linear-interpolated percentile of a non-empty list (q in 0..100)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def stats(values):
    """Median, p90, mean, min, max and count; None when there is nothing to measure."""
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {'n': len(values), 'median': percentile(values, 50), 'p90': percentile(values, 90),
            'mean': sum(values) / len(values), 'min': min(values), 'max': max(values)}


def load_events(directory):
    """Every event of every `*.jsonl` file, sorted by time. Malformed lines are counted."""
    events, malformed = [], 0
    for path in sorted(Path(directory).glob('*.jsonl')):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                float(event['t'])
                event['event'], event['role']
            except (ValueError, KeyError, TypeError):
                malformed += 1
                continue
            events.append(event)
    events.sort(key=lambda event: event['t'])
    return events, malformed


def mutants_of(event):
    """Mutant ids a scheduler/controller job event refers to."""
    lanes = event.get('lanes')
    if lanes:
        return [lane for lane in lanes if lane]
    return [event['mutant']] if event.get('mutant') else []


def first_after(events, start):
    """First event in a time-sorted list at or after `start`."""
    return next((event for event in events if event['t'] >= start), None)


def analyze(directory):
    events, malformed = load_events(directory)
    counts = {}
    for event in events:
        key = f"{event['role']}.{event['event']}"
        counts[key] = counts.get(key, 0) + 1
    summary = {'events': len(events), 'malformed_lines': malformed, 'event_counts': counts,
               'clock': 'ms since Unix epoch (Rust SystemTime; Node performance.timeOrigin + performance.now())'}
    if not events:
        return summary
    by = {}
    for event in events:
        by.setdefault((event['role'], event['event']), []).append(event)

    # Events per mutant. Several events of one kind for one mutant (a rerun) keep the first.
    per_mutant = {}
    worker_pid_mutant = {}
    for event in events:
        if event.get('mutant') and event['role'] != 'scheduler':
            per_mutant.setdefault(event['mutant'], {}).setdefault((event['role'], event['event']), event['t'])
        if event['role'] == 'zygote' and event['event'] == 'forked':
            worker_pid_mutant[event.get('workerPid')] = event.get('mutant')
    for event in by.get(('worker', 'first_message'), []):
        mutant = worker_pid_mutant.get(event['pid'])
        if mutant:
            per_mutant.setdefault(mutant, {}).setdefault(('worker', 'first_message'), event['t'])

    # Scheduler jobs: one dispatch/reply pair per job on a slot. Mutant jobs are the mutation phase.
    jobs = []
    pending = {}
    for event in by_scheduler(events):
        if event['event'] == 'dispatch_begin':
            pending[event['slot']] = {'slot': event['slot'], 'job': event.get('job'), 'kind': event.get('kind'),
                                      'mutants': mutants_of(event), 'begin': event['t']}
        elif event['event'] == 'dispatch_sent' and event['slot'] in pending:
            pending[event['slot']]['sent'] = event['t']
        elif event['event'] == 'reply' and event['slot'] in pending:
            job = pending.pop(event['slot'])
            job['reply'] = event['t']
            job['ok'] = event.get('ok')
            jobs.append(job)
    mutant_jobs = [job for job in jobs if job['kind'] == 'mutant']
    summary['jobs'] = {'by_kind': {kind: sum(job['kind'] == kind for job in jobs)
                                   for kind in sorted({job['kind'] for job in jobs})},
                       'unanswered': len(pending)}

    # Controller side of each job: request_received .. reply_sent, matched to the scheduler job by time.
    requests = [e for e in by.get(('controller', 'request_received'), []) if e.get('command') == 'run' or e.get('lanes')]
    replies = by.get(('controller', 'reply_sent'), [])
    for job in mutant_jobs:
        request = first_after(requests, job['sent'] if 'sent' in job else job['begin'])
        if request and request['t'] <= job['reply']:
            job['request_received'] = request['t']
            answer = first_after(replies, request['t'])
            if answer and answer['t'] <= job['reply']:
                job['reply_sent'] = answer['t']
                for mutant in job['mutants']:
                    per_mutant.setdefault(mutant, {})[('batch', 'reply_sent')] = answer['t']
            phases = {}
            for name in ('batch_enter', 'context_ready', 'pool_run_begin', 'pool_run_end', 'pool_closed', 'batch_done'):
                hit = first_after(by.get(('controller', name), []), request['t'])
                if hit and hit['t'] <= job.get('reply_sent', job['reply']):
                    phases[name] = hit['t']
            job['controller_phases'] = phases

    # Per-mutant segments.
    segments = {}
    for name, (role_a, event_a), (role_b, event_b) in SEGMENTS:
        values = []
        for marks in per_mutant.values():
            start, end = marks.get((role_a, event_a)), marks.get((role_b, event_b))
            if start is not None and end is not None:
                values.append(end - start)
        segments[name] = stats(values)
    summary['per_mutant_ms'] = segments
    templated_mutants = [marks for marks in per_mutant.values() if ('pool', 'template_fork_requested') in marks]
    if templated_mutants:
        summary['precollect_per_mutant_ms'] = {
            'templated_mutants': len(templated_mutants),
            **{name: stats([marks[b] - marks[a] for marks in templated_mutants if a in marks and b in marks])
               for name, a, b in TEMPLATED_SEGMENTS}}
    builds = [e for e in by.get(('controller', 'template_build_end'), [])]
    if builds:
        summary['template_builds'] = {
            'count': len(builds), 'ready': sum(1 for e in builds if e.get('ready')),
            'total_ms': sum(e.get('ms', 0) for e in builds),
            'builds': [{'key': e.get('key'), 'ready': e.get('ready'), 'ms': e.get('ms')} for e in builds]}
    summary['negative_segment_samples'] = {
        name: sum(1 for marks in per_mutant.values()
                  if (marks.get((a[0], a[1])) is not None and marks.get((b[0], b[1])) is not None
                      and marks[(b[0], b[1])] < marks[(a[0], a[1])]))
        for name, a, b in SEGMENTS}

    # Scheduler/controller transport and the gap between consecutive jobs on a slot.
    transport = {'dispatch_encode_and_write': [], 'dispatch_to_request_received': [],
                 'controller_reply_to_scheduler_reply': [], 'reply_to_next_dispatch': []}
    last_reply = {}
    for job in sorted(mutant_jobs, key=lambda job: job['begin']):
        if 'sent' in job:
            transport['dispatch_encode_and_write'].append(job['sent'] - job['begin'])
        if 'request_received' in job and 'sent' in job:
            transport['dispatch_to_request_received'].append(job['request_received'] - job['sent'])
        if 'reply_sent' in job:
            transport['controller_reply_to_scheduler_reply'].append(job['reply'] - job['reply_sent'])
        if job['slot'] in last_reply:
            transport['reply_to_next_dispatch'].append(job['begin'] - last_reply[job['slot']])
        last_reply[job['slot']] = job['reply']
    summary['scheduler_ms'] = {name: stats(values) for name, values in transport.items()}

    # Controller phases of a batch/job.
    phase_values = {'request_to_pool_run_begin': [], 'pool_run': [], 'pool_drain': [], 'pool_closed_to_reply_sent': []}
    for job in mutant_jobs:
        marks = job.get('controller_phases') or {}
        if 'request_received' in job and 'pool_run_begin' in marks:
            phase_values['request_to_pool_run_begin'].append(marks['pool_run_begin'] - job['request_received'])
        if 'pool_run_begin' in marks and 'pool_run_end' in marks:
            phase_values['pool_run'].append(marks['pool_run_end'] - marks['pool_run_begin'])
        if 'pool_run_end' in marks and 'pool_closed' in marks:
            phase_values['pool_drain'].append(marks['pool_closed'] - marks['pool_run_end'])
        if 'pool_closed' in marks and 'reply_sent' in job:
            phase_values['pool_closed_to_reply_sent'].append(job['reply_sent'] - marks['pool_closed'])
    summary['controller_batch_ms'] = {name: stats(values) for name, values in phase_values.items()}

    # Mutation phase and per-slot busy/idle fractions.
    if mutant_jobs:
        window_start = min(job['begin'] for job in mutant_jobs)
        window_end = max(job['reply'] for job in mutant_jobs)
        window = window_end - window_start
        slots = {}
        for job in mutant_jobs:
            slots.setdefault(job['slot'], []).append(job)
        per_slot = {}
        for slot, items in sorted(slots.items()):
            busy = sum(job['reply'] - job['begin'] for job in items)
            per_slot[str(slot)] = {'jobs': len(items), 'busy_ms': busy,
                                   'busy_fraction': busy / window if window else None,
                                   'idle_fraction': 1 - busy / window if window else None}
        summary['mutation_phase'] = {'start': window_start, 'end': window_end, 'duration_ms': window,
                                     'jobs': len(mutant_jobs),
                                     'mutants': sum(len(job['mutants']) or 1 for job in mutant_jobs),
                                     'slots': per_slot}
        # Lane workers (shared-lane batches): how much of width x window a lane worker is alive/running.
        widths = [len(job['mutants']) for job in mutant_jobs if len(job['mutants']) > 0]
        width = max(widths) if widths else 0
        lane_live = sum(marks[('controller', 'lane_exit')] - marks[('controller', 'lane_fork_requested')]
                        for marks in per_mutant.values()
                        if ('controller', 'lane_exit') in marks and ('controller', 'lane_fork_requested') in marks)
        lane_run = sum(marks[('controller', 'lane_result')] - marks[('controller', 'lane_run_sent')]
                       for marks in per_mutant.values()
                       if ('controller', 'lane_result') in marks and ('controller', 'lane_run_sent') in marks)
        if width and window:
            summary['lane_occupancy'] = {
                'width': width,
                'live_fraction': lane_live / (width * window),
                'run_to_result_fraction': lane_run / (width * window),
                'note': 'sum over mutants of lane worker lifetime (fork requested to exit) or run-to-result time, '
                        'divided by width x mutation-phase duration'}
        else:
            summary['lane_occupancy'] = None
        # First dispatch and worker-level milestones relative to process start.
        start = by.get(('scheduler', 'cli_start'), [None])[0]
        first_dispatch = {kind: min(job['begin'] for job in jobs if job['kind'] == kind)
                          for kind in sorted({job['kind'] for job in jobs})}
        summary['startup_ms'] = {
            'cli_start_to_first_mutant_dispatch': window_start - start['t'] if start else None,
            'cli_start_to_first_dispatch_by_kind': ({kind: t - start['t'] for kind, t in first_dispatch.items()}
                                                    if start else None),
            'cli_start_to_run_end': (max(event['t'] for event in events) - start['t']) if start else None}
        zygote_ready = by.get(('pool', 'zygote_ready'), [])
        zygote_start = by.get(('pool', 'zygote_start'), [])
        if zygote_start and zygote_ready:
            summary['zygote_startup_ms'] = {'start_to_ready': zygote_ready[0]['t'] - zygote_start[0]['t'],
                                            'first_mutant_dispatch_to_zygote_start':
                                                zygote_start[0]['t'] - window_start}
        # Lane-batch tail skew: first lane result to last lane result inside each batch.
        skews, wasted, durations = [], 0.0, 0.0
        for job in mutant_jobs:
            results = [per_mutant.get(m, {}).get(('controller', 'lane_result')) for m in job['mutants']]
            results = [t for t in results if t is not None]
            if len(job['mutants']) > 1 and len(results) > 1:
                skews.append(max(results) - min(results))
                wasted += sum(max(results) - t for t in results)
                durations += (max(results) - job['begin']) * len(results)
        summary['lane_tail_skew_ms'] = stats(skews)
        # Controller event-loop utilization (performance.eventLoopUtilization(), cumulative,
        # sampled at batch events) between the first and last sample inside the mutation phase.
        samples = sorted((e for e in events if e['role'] == 'controller' and 'eluActive' in e
                          and window_start <= e['t'] <= window_end), key=lambda e: e['t'])
        if len(samples) >= 2:
            active = samples[-1]['eluActive'] - samples[0]['eluActive']
            idle = samples[-1]['eluIdle'] - samples[0]['eluIdle']
            summary['controller_event_loop'] = {
                'utilization': active / (active + idle) if active + idle else None,
                'active_ms': active, 'idle_ms': idle, 'from': samples[0]['event'], 'to': samples[-1]['event'],
                'span_ms': samples[-1]['t'] - samples[0]['t']}
        # Per batch pool_run: how long lanes sat finished while the slowest lane ran.
        pool_rows = []
        for job in mutant_jobs:
            marks = job.get('controller_phases') or {}
            results = [per_mutant.get(m, {}).get(('controller', 'lane_result')) for m in job['mutants']]
            results = [t for t in results if t is not None]
            if 'pool_run_begin' in marks and 'pool_run_end' in marks and results:
                run = marks['pool_run_end'] - marks['pool_run_begin']
                slowest = max(results) - marks['pool_run_begin']
                # Lanes that never reported a result waited the whole run.
                waits = [max(results) - t for t in results] + [run - slowest] * (len(job['mutants']) - len(results))
                pool_rows.append({'pool_run': run, 'slowest_lane': slowest,
                                  'after_slowest_lane': run - slowest, 'lanes': len(job['mutants']),
                                  'lane_wait_mean': sum(waits) / len(waits), 'lane_wait_max': max(waits),
                                  'lane_wait_fraction': sum(waits) / (run * len(waits)) if run else None})
        if pool_rows:
            summary['pool_run_breakdown_ms'] = {
                'batches': len(pool_rows),
                'pool_run': stats([r['pool_run'] for r in pool_rows]),
                'slowest_lane': stats([r['slowest_lane'] for r in pool_rows]),
                'after_slowest_lane': stats([r['after_slowest_lane'] for r in pool_rows]),
                'lane_wait_for_slowest_mean': stats([r['lane_wait_mean'] for r in pool_rows]),
                'lane_wait_for_slowest_max': stats([r['lane_wait_max'] for r in pool_rows]),
                'lane_wait_fraction': stats([r['lane_wait_fraction'] for r in pool_rows
                                             if r['lane_wait_fraction'] is not None])}
        summary['lane_tail_skew_wasted_lane_fraction'] = wasted / durations if durations else None
    return summary


def by_scheduler(events):
    return [event for event in events if event['role'] == 'scheduler']


def fmt(entry):
    if not entry:
        return 'n/a'
    return f"med {entry['median']:8.1f}  p90 {entry['p90']:8.1f}  n={entry['n']}"


def render(summary):
    lines = [f"events: {summary.get('events', 0)} (malformed lines: {summary.get('malformed_lines', 0)})"]
    if not summary.get('events'):
        return '\n'.join(lines + ['no trace events found'])
    phase = summary.get('mutation_phase')
    if phase:
        lines.append(f"mutation phase: {phase['duration_ms']:.0f} ms, {phase['mutants']} mutants in {phase['jobs']} jobs")
        for slot, item in phase['slots'].items():
            lines.append(f"  slot {slot}: {item['jobs']} jobs, busy {item['busy_fraction']:.1%}, idle {item['idle_fraction']:.1%}")
    occupancy = summary.get('lane_occupancy')
    if occupancy:
        lines.append(f"lane workers (width {occupancy['width']}): alive {occupancy['live_fraction']:.1%}, "
                     f"run-to-result {occupancy['run_to_result_fraction']:.1%} of width x phase")
    startup = summary.get('startup_ms') or {}
    if startup.get('cli_start_to_first_mutant_dispatch') is not None:
        lines.append(f"cli start -> first mutant dispatch: {startup['cli_start_to_first_mutant_dispatch']:.0f} ms; "
                     f"-> end of trace: {startup['cli_start_to_run_end']:.0f} ms")
    zygote = summary.get('zygote_startup_ms')
    if zygote:
        lines.append(f"zygote start -> ready: {zygote['start_to_ready']:.0f} ms")
    builds = summary.get('template_builds')
    if builds:
        lines.append(f"template builds: {builds['count']} ({builds['ready']} ready), total {builds['total_ms']:.0f} ms")
    loop = summary.get('controller_event_loop')
    if loop and loop['utilization'] is not None:
        lines.append(f"controller event loop over the mutation phase: {loop['utilization']:.1%} busy "
                     f"({loop['active_ms']:.0f} active / {loop['idle_ms']:.0f} idle ms, {loop['from']} .. {loop['to']})")
    lines.append('per mutant (ms):')
    for name, entry in summary.get('per_mutant_ms', {}).items():
        lines.append(f"  {name:36} {fmt(entry)}")
    precollect = summary.get('precollect_per_mutant_ms')
    if precollect:
        lines.append(f"precollect, templated mutants ({precollect['templated_mutants']}) (ms):")
        for name, entry in precollect.items():
            if name != 'templated_mutants':
                lines.append(f"  {name:36} {fmt(entry)}")
    lines.append('scheduler / controller (ms, per job):')
    for name, entry in summary.get('scheduler_ms', {}).items():
        lines.append(f"  {name:36} {fmt(entry)}")
    for name, entry in summary.get('controller_batch_ms', {}).items():
        lines.append(f"  {name:36} {fmt(entry)}")
    pool = summary.get('pool_run_breakdown_ms')
    if pool:
        lines.append(f"pool_run per batch ({pool['batches']} batches, ms):")
        for name in ('pool_run', 'slowest_lane', 'after_slowest_lane', 'lane_wait_for_slowest_mean',
                     'lane_wait_for_slowest_max'):
            lines.append(f"  {name:36} {fmt(pool[name])}")
        fraction = pool['lane_wait_fraction']
        if fraction:
            lines.append(f"  lanes idle waiting for the slowest lane: {fraction['mean']:.1%} of pool_run (mean over batches)")
    skew = summary.get('lane_tail_skew_ms')
    lines.append(f"lane batch tail skew (first to last lane result, ms): {fmt(skew)}")
    if summary.get('lane_tail_skew_wasted_lane_fraction') is not None:
        lines.append(f"  lane time spent waiting for the batch tail: {summary['lane_tail_skew_wasted_lane_fraction']:.1%}")
    negatives = {k: v for k, v in summary.get('negative_segment_samples', {}).items() if v}
    if negatives:
        lines.append(f'WARNING segments with negative samples (clock or event order problem): {negatives}')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('directory', type=Path, help='OXIMUT_TRACE_TIMELINE directory')
    parser.add_argument('--json', type=Path, help='Write the JSON summary here (default: <directory>/timeline-summary.json)')
    args = parser.parse_args(argv)
    if not args.directory.is_dir():
        parser.error(f'not a directory: {args.directory}')
    summary = analyze(args.directory)
    (args.json or args.directory / 'timeline-summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(render(summary))
    return 0 if summary.get('events') else 1


if __name__ == '__main__':
    sys.exit(main())
